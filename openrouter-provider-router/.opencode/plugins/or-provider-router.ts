// tiny-pages | Copyright 2026 Waldemar Fech | Apache-2.0 | https://github.com/WaldemarFech/tiny-pages
// SPDX-License-Identifier: Apache-2.0
/**
 * OpenRouter provider router for opencode: applies the decision of scripts/models/or_provider_router.py per request.
 *
 * - Routing: for OpenRouter models that have an entry in <state>/decision.json, sets provider.order (+ allow_fallbacks).
 *   A session stays on its provider while it is active (cache warm); after cache_ttl_min without a request the provider cache is gone,
 *   so the session adopts the current decision. A pinned provider that the decision no longer tolerates (far too expensive) is dropped at once.
 *   (An explicit provider.order disables OpenRouter's own sticky routing - this pin replaces it.)
 * - Refresh: when decision.json is older than refresh_min a detached `or_router_export.py cycle` (decide, key-usage sample, export, publish)
 *   is started (lock file, no scheduler needed), and a timer repeats the check every refresh_min so the dashboard stays fresh even when no
 *   openrouter request is flowing.
 * - Log: usage.jsonl gets a line whenever a session's provider changes (the report joins it with opencode.db for measured hit rate/cost per provider).
 *
 * Two runtimes, one file (no import of a runtime package, so the same bytes load everywhere):
 *   - opencode V2 (>= 2.x, desktop + bundled server): default export is a definition `{ id, setup(ctx) }`; hooks are registered on the
 *     owning domain (`ctx.session.hook("context", ...)`), request options arrive as one mutable event.
 *   - opencode V1 (1.18.29+): the same object also carries `server()`, which returns the legacy `chat.params` hook map.
 *
 * Fail-open: no decision / unreadable file / any error -> options untouched, OpenRouter routes by default.
 * Switch off: env ORR_DISABLE=1.
 */
import { spawn } from "node:child_process"
import { appendFileSync, existsSync, mkdirSync, readFileSync, renameSync, statSync, unlinkSync, writeFileSync } from "node:fs"
import { homedir } from "node:os"
import { dirname, join, resolve } from "node:path"
import { fileURLToPath } from "node:url"

const HERE = dirname(fileURLToPath(import.meta.url))
const DIR = process.env.ORR_STATE_DIR || join(homedir(), ".local", "share", "opencode", "or-router")
const LOCK_STALE_MS = 300_000
const PLUGIN_ID = "jarvis.or-provider-router"
const SCRIPTS = [process.env.ORR_SCRIPT, resolve(HERE, "..", "..", "scripts", "models", "or_provider_router.py")]

type Entry = { order?: string[]; tolerated?: string[] }
type Pin = { tag: string; last: number }

/** Pure: which provider order a request gets. Exported for tests. */
export function pickOrder(entry: Entry, pin: Pin | undefined, now: number, idleGapMs: number): { order: string[]; tag: string } | null {
  const order = (entry.order ?? []).filter((t) => typeof t === "string" && t)
  if (!order.length) return null
  const tolerated = entry.tolerated ?? order
  if (pin && now - pin.last < idleGapMs && tolerated.includes(pin.tag)) {
    return { tag: pin.tag, order: [pin.tag, ...order.filter((t) => t !== pin.tag)] }
  }
  return { tag: order[0], order }
}

/** Pure: interpreter for the refresh cycle. ORR_PYTHON, then the absolute path `install-shim` recorded in <state>/python.txt (if it still exists),
 *  then JARVIS_FORWARD_PYTHON, then a bare name as the last resort. */
export function resolvePython(env: Record<string, string | undefined>, recorded: string | null, exists: (p: string) => boolean, platform: string): string {
  const rec = (recorded ?? "").trim()
  if (env.ORR_PYTHON) return env.ORR_PYTHON
  if (rec && exists(rec)) return rec
  return env.JARVIS_FORWARD_PYTHON || (platform === "win32" ? "python" : "python3")
}

function pythonExe(): string {
  let rec: string | null = null
  try { rec = readFileSync(join(DIR, "python.txt"), "utf8") } catch {}
  return resolvePython(process.env, rec, existsSync, process.platform)
}

/** Pure: a lock file older than staleMs belonged to a dead/hung run. */
export function lockIsStale(mtimeMs: number, now: number, staleMs: number): boolean {
  return now - mtimeMs >= staleMs
}

/** Take the refresh lock. Create-exclusive first ("wx"); only when that fails look at its age. A stale lock is replaced by an atomic rename, after
 *  re-checking right before that its mtime is unchanged (a lock another process just refreshed is never touched). Small residual race accepted:
 *  two processes both seeing the same stale lock may both start one cycle. */
export function acquireLock(lock: string, now: number, staleMs = LOCK_STALE_MS): boolean {
  try {
    writeFileSync(lock, String(process.pid), { flag: "wx" })
    return true
  } catch {}
  const tmp = `${lock}.${process.pid}.tmp`
  try {
    const seen = statSync(lock).mtimeMs
    if (!lockIsStale(seen, now, staleMs)) return false
    if (statSync(lock).mtimeMs !== seen) return false
    writeFileSync(tmp, String(process.pid), { flag: "wx" })
    renameSync(tmp, lock)
    return true
  } catch {
    try { unlinkSync(tmp) } catch {}
    return false
  }
}

/** Remove the lock only if it is still ours (a replaced stale lock belongs to the newer run). */
function releaseLock(lock: string) {
  try { if (readFileSync(lock, "utf8") === String(process.pid)) unlinkSync(lock) } catch {}
}

/** Pure: a decision that was not refreshed for maxAgeMs is ignored (fail open: OpenRouter routes by default) instead of being applied forever. */
export function isStale(entry: { updated?: number } | undefined, generatedAt: number | undefined, now: number, maxAgeMs: number): boolean {
  const updated = (entry?.updated ?? generatedAt ?? 0) * 1000
  return !updated || now - updated > maxAgeMs
}

let cache: { mtime: number; doc: any } | null = null
function readDecision(): any {
  try {
    const f = join(DIR, "decision.json")
    const m = statSync(f).mtimeMs
    if (!cache || cache.mtime !== m) cache = { mtime: m, doc: JSON.parse(readFileSync(f, "utf8")) }
    return cache.doc
  } catch {
    return null
  }
}

let lastSpawn = 0
function refreshIfStale(doc: any, now: number) {
  try {
    const refreshMs = (doc?.config?.refresh_min ?? 5) * 60_000
    const age = doc ? now - cache!.mtime : Infinity
    if (age < refreshMs || now - lastSpawn < 120_000) return
    lastSpawn = now
    const script = SCRIPTS.find((s) => s && existsSync(s))
    if (!script) return
    mkdirSync(DIR, { recursive: true })
    const lock = join(DIR, "refresh.lock")
    if (!acquireLock(lock, now)) return
    const cycle = join(dirname(script), "or_router_export.py")  // decide -> key-usage sample -> export -> publish
    const args = existsSync(cycle) ? [cycle, "cycle"] : [script, "decide"]
    const child = spawn(pythonExe(), ["-X", "utf8", ...args], { cwd: DIR, detached: true, stdio: "ignore", windowsHide: true })
    child.on("exit", () => releaseLock(lock))
    child.on("error", () => releaseLock(lock))
    child.unref()
  } catch {}
}

/** One routing decision per request: the provider order for this session's model, or null (fail open). Shared by every runtime adapter. */
function route(tag: string, modelID: string | undefined, sessionID: string | undefined): { order: string[]; tag: string } | null {
  try {
    if (process.env.ORR_DISABLE === "1") return null
    if (tag !== "openrouter") return null
    const now = Date.now()
    const doc = readDecision()
    refreshIfStale(doc, now)
    const entry: Entry | undefined = modelID ? doc?.models?.[modelID] : undefined
    if (!entry || isStale(entry, doc.generated_at, now, 24 * 3600_000)) return null
    const pick = pickOrder(entry, pins.get(sessionID), now, (doc.config?.cache_ttl_min ?? 10) * 60_000)
    if (!pick) return null
    const prev = pins.get(sessionID)
    pins.set(sessionID, { tag: pick.tag, last: now })
    if (!prev || prev.tag !== pick.tag) {
      mkdirSync(DIR, { recursive: true })
      appendFileSync(join(DIR, "usage.jsonl"), JSON.stringify({ ts: now / 1000, sid: sessionID, model: modelID, tag: pick.tag }) + "\n")
    }
    return pick
  } catch {
    return null
  }
}

const pins = new Map<string, Pin>()

/** The provider block OpenRouter expects in the request body. */
function providerOptions(pick: { order: string[] }) {
  return { order: pick.order, allow_fallbacks: true }
}

/** V1 adapter: `chat.params` gets input+output; options are handed to the provider as providerOptions. */
const v1Hooks = async () => ({
  "chat.params": async (input: any, output: any) => {
    const pick = route(input.provider?.info?.id ?? input.model?.providerID, input.model?.api?.id ?? input.model?.id, input.sessionID)
    if (!pick) return
    output.options = { ...(output.options ?? {}), provider: { ...(output.options?.provider ?? {}), ...providerOptions(pick) } }
  },
})

/** V2 adapter: one mutable request event per model call; `options` takes the provider option object used by the protocol. */
async function v2Setup(ctx: any) {
  try {
    await ctx.session.hook(
      "context",
      (event: any) => {
        const pick = route(event.model?.providerID, event.model?.id, event.sessionID)
        if (!pick) return
        event.options.provider = { ...(event.options?.provider ?? {}), ...providerOptions(pick) }
      },
      { providerID: "openrouter" },
    )
  } catch {}
  // Keep the dashboard fresh without traffic: the cycle is started here and re-checked every minute (spawns only when decision.json is
  // older than refresh_min; the lock makes sure concurrent opencode instances run one cycle).
  const timer = setInterval(() => refreshIfStale(readDecision(), Date.now()), 60_000)
  if (typeof timer.unref === "function") timer.unref()
  return () => clearInterval(timer)
}

const plugin = {
  id: PLUGIN_ID,
  setup: v2Setup,
  async server() { return v1Hooks() },
}

export const OrProviderRouter = plugin
export default plugin
