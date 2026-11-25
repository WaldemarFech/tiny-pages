// bun test ./.opencode/plugins/or-provider-router.test.ts - pure pin/idle-gap logic, no opencode runtime.
import { expect, test } from "bun:test"
import { isStale, pickOrder } from "./or-provider-router.ts"

const entry = { order: ["morph/fp8", "relace/fp4", "wafer/us"], tolerated: ["morph/fp8", "relace/fp4", "wafer/us"] }
const GAP = 30 * 60_000

test("new session takes the decision's first provider", () => {
  expect(pickOrder(entry, undefined, 1000, GAP)).toEqual({ tag: "morph/fp8", order: entry.order })
})

test("active session keeps its pinned provider even if the decision changed", () => {
  const r = pickOrder(entry, { tag: "relace/fp4", last: 1000 }, 1000 + 5 * 60_000, GAP)
  expect(r?.tag).toBe("relace/fp4")
  expect(r?.order).toEqual(["relace/fp4", "morph/fp8", "wafer/us"])
})

test("after the idle gap the session adopts the current decision (cache is gone, switching is free)", () => {
  expect(pickOrder(entry, { tag: "relace/fp4", last: 0 }, GAP + 1, GAP)?.tag).toBe("morph/fp8")
})

test("a pinned provider the decision no longer tolerates is dropped immediately", () => {
  const e = { order: ["morph/fp8", "wafer/us"], tolerated: ["morph/fp8", "wafer/us"] }
  expect(pickOrder(e, { tag: "relace/fp4", last: 1000 }, 2000, GAP)?.tag).toBe("morph/fp8")
})

test("empty or missing order -> no routing (fail-open)", () => {
  expect(pickOrder({}, undefined, 1, GAP)).toBeNull()
  expect(pickOrder({ order: [] }, undefined, 1, GAP)).toBeNull()
})

// ----------------------------------------------------------------------------- interpreter + lock (review 2026-10-31)
import { mkdtempSync, readFileSync, rmSync, utimesSync, writeFileSync } from "node:fs"
import { tmpdir } from "node:os"
import { join } from "node:path"
import { acquireLock, lockIsStale, resolvePython } from "./or-provider-router.ts"

test("resolvePython prefers ORR_PYTHON, then the recorded absolute interpreter, then falls back", () => {
  const yes = () => true, no = () => false
  expect(resolvePython({ ORR_PYTHON: "/x/py" }, "/rec/py\n", yes, "linux")).toBe("/x/py")
  expect(resolvePython({}, "/rec/py\n", yes, "linux")).toBe("/rec/py")
  expect(resolvePython({}, "/rec/py\n", no, "win32")).toBe("python") // recorded interpreter vanished
  expect(resolvePython({ JARVIS_FORWARD_PYTHON: "/fwd/py" }, null, no, "linux")).toBe("/fwd/py")
  expect(resolvePython({}, "  ", yes, "linux")).toBe("python3")
})

test("lockIsStale is a pure age check", () => {
  expect(lockIsStale(1000, 1000 + 299_999, 300_000)).toBe(false)
  expect(lockIsStale(1000, 1000 + 300_000, 300_000)).toBe(true)
})

test("acquireLock: exclusive create, fresh lock of another process is never taken, stale lock is replaced atomically", () => {
  const dir = mkdtempSync(join(tmpdir(), "orr-lock-"))
  try {
    const lock = join(dir, "refresh.lock")
    const now = Date.now()
    expect(acquireLock(lock, now)).toBe(true)
    writeFileSync(lock, "other-process")
    expect(acquireLock(lock, now)).toBe(false) // fresh -> untouched
    expect(readFileSync(lock, "utf8")).toBe("other-process")
    const old = (now - 10 * 60_000) / 1000
    utimesSync(lock, old, old)
    expect(acquireLock(lock, now)).toBe(true) // stale -> replaced
    expect(readFileSync(lock, "utf8")).toBe(String(process.pid))
  } finally {
    rmSync(dir, { recursive: true, force: true })
  }
})

test("a decision older than the limit is ignored (fail open), a fresh or recently refreshed one is used", () => {
  const H = 3600_000
  expect(isStale({ updated: 1000 }, undefined, 1000 * 1000 + 25 * H, 24 * H)).toBe(true)
  expect(isStale({ updated: 1000 }, undefined, 1000 * 1000 + 2 * H, 24 * H)).toBe(false)
  expect(isStale({}, 1000, 1000 * 1000 + 30 * H, 24 * H)).toBe(true)
  expect(isStale({}, undefined, 5, 24 * H)).toBe(true) // no timestamp at all: do not trust it
  expect(isStale(undefined, undefined, 5, 24 * H)).toBe(true)
})
