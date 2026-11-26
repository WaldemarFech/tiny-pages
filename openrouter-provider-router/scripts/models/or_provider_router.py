#!/usr/bin/env python3
# tiny-pages | Copyright 2026 Waldemar Fech | Apache-2.0 | https://github.com/WaldemarFech/tiny-pages
# SPDX-License-Identifier: Apache-2.0
"""Deterministic, cache-aware OpenRouter provider router (decision engine).

Why: the cheapest list price is not the cheapest provider. With ~94 % cache reads the cache-read
price dominates (2026-10-31: Open Inference lowest in/out but 2.4x Morph in total). And every
provider switch re-reads the whole context uncached, so flapping on tiny price differences costs more
than it saves. This script measures the REAL token profile from opencode's own DB, pulls live
endpoint prices from OpenRouter's public API (no key), projects the cost of every provider and decides
with hysteresis. It never talks to a model; same inputs -> same decision.

Output: <state>/decision.json, read by the opencode plugin (.opencode/plugins/or-provider-router.ts), which
sets provider.order per request and keeps a session on its provider until it has been idle for
cache_ttl_min (cache expired -> switching is free) unless a switch would pay back quickly (decision.json "tolerated").

Switching (all tunable, env ORR_<NAME> or <state>/config.json) - there is NO fixed waiting time:
  * default choice (what new/idle sessions get; their cache is gone after cache_ttl_min, so switching is free): the cheapest
    endpoint, but a near-tie is not chased (hysteresis band rel_gain_min, both directions -> no flapping on a 1 ct difference);
  * warm sessions (cache still hot) keep their provider unless the switch to their ACTUAL destination - the default (order[0]),
    which can be a held provider up to rel_gain_min dearer than the cheapest - clears the band AND pays back its cache-miss cost
    within payback_max_min of their own activity - so a provider that suddenly turns expensive is left at once;
  * every run diffs the live prices against the last snapshot: a change is logged as an edge (price_events.jsonl) and the
    decision is recomputed (it is recomputed on every run; the plugin re-runs every refresh_min).

Usage:
  python scripts/models/or_provider_router.py decide [--model ID] [--dry-run] [--json]
  python scripts/models/or_provider_router.py report [--model ID] [--days N]
  python scripts/models/or_provider_router.py status
  python scripts/models/or_provider_router.py install-shim     # global opencode plugin shim for this checkout
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sqlite3
import sys
import tempfile
import time
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

SCHEMA = 1
DEFAULT_MODEL = "deepseek/deepseek-v4.1-flash-1017"
ENDPOINTS_URL = "https://openrouter.ai/api/v1/models/{model}/endpoints"
USER_AGENT = "jarvis-or-provider-router/1.0"
DEFAULTS: dict[str, Any] = {
    "models": [DEFAULT_MODEL],
    "min_profile_tokens": 20_000_000,  # prompt tokens needed before 'today' is trusted over 'week'
    "week_days": 7,
    "fallback_hit": 0.90,  # profile when there is no usable history
    "fallback_out_ratio": 0.0025,
    "fallback_prompt_per_req": 150_000,
    "fallback_prompt_per_day": 100_000_000,
    "min_uptime": 95.0,  # percent, uptime_last_30m: needed to BECOME the choice
    "leave_uptime": 85.0,  # percent: the CURRENT choice is only dropped below this (hysteresis: one failed health sample must not flush warm caches)
    "min_context": 200_000,
    "min_max_output": 32_000,
    "deny": [],  # endpoint tags / provider names never to use
    "allow": [],  # non-empty = only these
    "top_k": 4,  # providers listed in `order` (current first, then cheapest)
    "rel_gain_min": 0.10,  # hysteresis band: a different endpoint must be this much cheaper (relative) to become the default
    "payback_max_min": 60.0,  # warm session leaves its provider if the switch repays its cache-miss cost within this many active minutes
    "fallback_prompt_per_min": 2_500_000,  # prompt tokens per active session-minute without history
    "cache_ttl_min": 10.0,  # provider prompt cache assumed gone after this idle time (OpenRouter sticky window); plugin adopts the default after it
    "refresh_min": 5.0,  # plugin: re-run the cycle (price poll + decision) when decision.json is older than this
    "ranks_min_gap_s": 120.0,  # price snapshot for the provider watchlist: at most one row per this many seconds
    "ranks_keep_bytes": 4_000_000,  # ~12 days of 2-minute snapshots, enough for the 7-day watch rule
    "watch_days": 7,  # a provider drops off the watchlist after this long without being rank 1 (owner rule 2026-11-02)
    "watch_peak_rel": 0.8,  # a cost at or below this share of the provider's own median counts as a cheap phase ("Peak")
}


def state_dir() -> Path:
    return Path(os.environ.get("ORR_STATE_DIR") or Path.home() / ".local" / "share" / "opencode" / "or-router")


def db_path() -> Path:
    return Path(os.environ.get("ORR_DB") or Path.home() / ".local" / "share" / "opencode" / "opencode.db")


def load_cfg(sd: Path | None = None) -> dict[str, Any]:
    cfg = dict(DEFAULTS)
    f = (sd or state_dir()) / "config.json"
    try:
        cfg.update(json.loads(f.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        pass
    for k, dv in DEFAULTS.items():
        v = os.environ.get("ORR_" + k.upper())
        if v is None:
            continue
        try:
            cfg[k] = json.loads(v) if isinstance(dv, (list, dict)) else type(dv)(v)
        except (ValueError, TypeError):
            print(f"or_provider_router: ignored invalid ORR_{k.upper()} (expected {type(dv).__name__}{' as JSON' if isinstance(dv, (list, dict)) else ''})", file=sys.stderr)
    return cfg


def write_text_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".{os.getpid()}.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def write_atomic(path: Path, doc: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(doc, fh, indent=1, ensure_ascii=False)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


# ----------------------------------------------------------------------------- live prices
def parse_endpoints(doc: dict) -> list[dict]:
    out = []
    for e in (doc.get("data") or {}).get("endpoints") or []:
        p = e.get("pricing") or {}

        bad = False

        def per_m(k: str) -> float:
            nonlocal bad
            try:
                v = float(p.get(k) or 0) * 1e6
            except (TypeError, ValueError):
                bad = True
                return 0.0
            if not math.isfinite(v) or v < 0:  # nan/inf, or -1 = OpenRouter's "dynamic pricing" marker: not a price we can rank
                bad = True
                return 0.0
            return v

        try:
            up = None if e.get("uptime_last_30m") is None else float(e["uptime_last_30m"])
            if up is not None and not math.isfinite(up):
                up = None
        except (TypeError, ValueError):
            up = None
        row = {
            "tag": e.get("tag") or e.get("provider_name"), "name": e.get("provider_name"),
            "in": per_m("prompt"), "out": per_m("completion"),
            # no cache-read price = no cache discount (full input price), never a free cache (review finding 2026-10-31)
            "cr": per_m("input_cache_read") if p.get("input_cache_read") not in (None, "") else per_m("prompt"),
            "ctx": e.get("context_length") or 0, "maxout": e.get("max_completion_tokens") or 0,
            "quant": e.get("quantization"), "uptime": up, "status": e.get("status") or 0,
        }
        if not bad:
            out.append(row)
    return [e for e in out if e["tag"]]


def fetch_endpoints(model: str, opener: Callable = urllib.request.urlopen, timeout: float = 20) -> list[dict]:
    req = urllib.request.Request(ENDPOINTS_URL.format(model=model), headers={"User-Agent": USER_AGENT})
    with opener(req, timeout=timeout) as r:
        return parse_endpoints(json.load(r))


def eligible(eps: list[dict], cfg: dict, current: str | None = None) -> list[dict]:
    """Candidates. `current` (the tag in use) gets the lower leave_uptime floor: entering needs min_uptime, leaving only happens below leave_uptime."""
    res = []
    for e in eps:
        names = {str(e["tag"]).lower(), str(e["name"]).lower(), str(e["tag"]).split("/")[0].lower()}
        if cfg["allow"] and not names & {str(x).lower() for x in cfg["allow"]}:
            continue
        if names & {str(x).lower() for x in cfg["deny"]}:
            continue
        if e["status"] != 0 or e["ctx"] < cfg["min_context"] or e["maxout"] < cfg["min_max_output"]:
            continue
        floor = cfg["min_uptime"] if e["tag"] != current else min(cfg["min_uptime"], cfg.get("leave_uptime", cfg["min_uptime"]))
        if e["uptime"] is not None and e["uptime"] < floor:
            continue
        if e["in"] <= 0 and e["out"] <= 0:
            continue
        res.append(e)
    return res


# ----------------------------------------------------------------------------- measured profile
def _usage_row(sid: str, ts: int, j: dict) -> dict:
    """One assistant message -> the router's flat token record."""
    t = j.get("tokens") or {}
    c = t.get("cache") or {}
    return {"sid": sid, "ts": ts, "in": t.get("input", 0) or 0, "out": (t.get("output", 0) or 0) + (t.get("reasoning", 0) or 0),
            "cr": c.get("read", 0) or 0, "cw": c.get("write", 0) or 0, "cost": j.get("cost", 0) or 0}


def read_usage(db: Path, model: str, since_ms: int) -> list[dict]:
    """Assistant messages of `model` via OpenRouter since `since_ms` (ms epoch).

    Two schemas live in opencode.db: V1 writes flat rows into `message` (role/providerID/modelID at top level); opencode V2 writes
    `session_message` rows with `type='assistant'` and the model nested as {"providerID", "id"} (2026-11-01: after the V2 switch the
    dashboard silently saw zero traffic, because only `message` was read). Both are read so a V1->V2 migration keeps its history."""
    rows: list[dict] = []
    try:
        con = sqlite3.connect(f"{db.resolve().as_uri()}?mode=ro", uri=True, timeout=10)
    except sqlite3.Error:
        return rows
    try:
        try:
            q = "select session_id, time_created, data from message where time_created >= ? and data like ?"
            for sid, tc, data in con.execute(q, (since_ms, f"%{model}%")):
                try:
                    j = json.loads(data)
                except ValueError:
                    continue
                if j.get("role") != "assistant" or j.get("providerID") != "openrouter" or j.get("modelID") != model:
                    continue
                rows.append(_usage_row(sid, tc, j))
        except sqlite3.Error:  # table absent in a fresh V2 database
            pass
        try:
            q = "select session_id, time_created, data from session_message where type = 'assistant' and time_created >= ? and data like ?"
            for sid, tc, data in con.execute(q, (since_ms, f"%{model}%")):
                try:
                    j = json.loads(data)
                except ValueError:
                    continue
                m = j.get("model") or {}
                if m.get("providerID") != "openrouter" or (m.get("id") or m.get("modelID")) != model:
                    continue
                rows.append(_usage_row(sid, tc, j))
        except sqlite3.Error:  # V1 database
            pass
    except sqlite3.Error:
        pass
    finally:
        con.close()
    rows.sort(key=lambda r: r["ts"])
    return rows


def summarize(rows: list[dict]) -> dict:
    inp = sum(r["in"] + r["cw"] for r in rows)
    cr = sum(r["cr"] for r in rows)
    out = sum(r["out"] for r in rows)
    prompt = inp + cr
    days = {datetime.fromtimestamp(r["ts"] / 1000).date() for r in rows}
    return {"n": len(rows), "input": inp, "cache_read": cr, "output": out, "prompt": prompt, "cost": sum(r["cost"] for r in rows),
            "hit": cr / prompt if prompt else 0.0, "out_ratio": out / prompt if prompt else 0.0,
            "prompt_per_req": prompt / len(rows) if rows else 0.0, "active_days": max(1, len(days)),
            "sessions": len({r["sid"] for r in rows})}


def choose_profile(rows: list[dict], cfg: dict, now: float) -> dict:
    """today if it has enough tokens, else the last week, else configured defaults."""
    midnight = datetime.fromtimestamp(now).replace(hour=0, minute=0, second=0, microsecond=0).timestamp() * 1000
    week = summarize(rows)
    today = summarize([r for r in rows if r["ts"] >= midnight])
    pick, name = (today, "today") if today["prompt"] >= cfg["min_profile_tokens"] else (week, "week")
    if pick["prompt"] < cfg["min_profile_tokens"] / 4:
        pick, name = ({"hit": cfg["fallback_hit"], "out_ratio": cfg["fallback_out_ratio"], "prompt_per_req": cfg["fallback_prompt_per_req"],
                       "prompt": 0, "n": 0}, "default")
    rate = week["prompt"] / week["active_days"] if week["prompt"] else cfg["fallback_prompt_per_day"]
    session_minutes = {(r["sid"], int(r["ts"] // 60000)) for r in rows}  # one session being active for one minute
    per_min = week["prompt"] / len(session_minutes) if session_minutes and week["prompt"] else cfg["fallback_prompt_per_min"]
    recent = {r["sid"] for r in rows if r["ts"] >= (now - 7200) * 1000}
    return {"window": name, "hit": pick["hit"], "out_ratio": pick["out_ratio"], "prompt_per_req": pick["prompt_per_req"] or cfg["fallback_prompt_per_req"],
            "prompt_per_day": rate, "prompt_per_active_min": per_min, "active_sessions": max(1, len(recent)),
            "sample_prompt_tokens": pick["prompt"], "sample_msgs": pick["n"]}


# ----------------------------------------------------------------------------- cost + decision
def unit_cost(ep: dict, prof: dict) -> float:
    """USD per 1M prompt tokens incl. the output that goes with them, at the measured cache hit rate."""
    h = prof["hit"]
    return (1 - h) * ep["in"] + h * ep["cr"] + prof["out_ratio"] * ep["out"]


def miss_penalty(ep: dict, prof: dict) -> float:
    """USD one provider switch costs a session whose cache is still hot: its context is read once uncached."""
    return prof["prompt_per_req"] * max(0.0, ep["in"] - ep["cr"]) / 1e6


def switch_eval(cur: dict, best: dict, costs: dict, prof: dict, cfg: dict) -> dict:
    """Should a session on `cur` with a hot cache move to `best` (= the destination the plugin will send it to) right now? Pure price/pay-back test, no clock."""
    cc, cb = costs[cur["tag"]], costs[best["tag"]]
    rel = (cc - cb) / cc if cc else 0.0
    gain_min = (cc - cb) * prof["prompt_per_active_min"] / 1e6  # USD saved per active minute of that session
    pen = miss_penalty(best, prof)
    pay = pen / gain_min if gain_min > 0 else None
    leave = rel >= cfg["rel_gain_min"] and pay is not None and pay <= cfg["payback_max_min"]
    return {"rel": round(rel, 4), "gain_per_active_min": round(gain_min, 6), "penalty": round(pen, 6),
            "payback_min": round(pay, 2) if pay is not None else None, "leave": leave}


def decide(eps: list[dict], prof: dict, prev: dict | None, cfg: dict, now: float) -> dict:
    cand = eligible(eps, cfg, (prev or {}).get("current"))
    if not cand:
        return {"ok": False, "reason": "no eligible endpoint", "order": [], "tolerated": []}
    costs = {e["tag"]: unit_cost(e, prof) for e in cand}
    ranked = sorted(cand, key=lambda e: (costs[e["tag"]], e["tag"]))
    best = ranked[0]
    cur_tag = (prev or {}).get("current")
    since = float((prev or {}).get("since") or 0)
    cur = next((e for e in cand if e["tag"] == cur_tag), None)
    reason, switched = "", False
    if cur is None:
        chosen, switched = best, True
        reason = "initial" if not cur_tag else f"{cur_tag} no longer eligible"
    elif cur["tag"] == best["tag"]:
        chosen, reason = cur, "current is best"
    else:
        rel = (costs[cur["tag"]] - costs[best["tag"]]) / costs[cur["tag"]] if costs[cur["tag"]] else 0.0
        if rel >= cfg["rel_gain_min"]:
            chosen, switched = best, True
            reason = f"switch {cur['tag']} -> {best['tag']} (rel gain {rel:.0%} >= band {cfg['rel_gain_min']:.0%}; idle sessions switch for free)"
        else:
            chosen, reason = cur, f"hold {cur['tag']} vs {best['tag']} (rel gain {rel:.1%} inside hysteresis band {cfg['rel_gain_min']:.0%})"
    # a dropped session is sent to order[0] == chosen (which may be a held provider up to the band dearer than best), so every
    # switch is judged against that ACTUAL destination: band and pay-back must both hold for the move chosen -> there
    warm = {e["tag"]: switch_eval(e, chosen, costs, prof, cfg) for e in ranked[:8] if e["tag"] != chosen["tag"]}
    rest = [e["tag"] for e in ranked if e["tag"] != chosen["tag"]]
    return {"ok": True, "current": chosen["tag"], "since": now if switched or not since else since, "best": best["tag"],
            "order": [chosen["tag"]] + rest[: max(0, cfg["top_k"] - 1)],
            "tolerated": [e["tag"] for e in ranked if e["tag"] == chosen["tag"] or not switch_eval(e, chosen, costs, prof, cfg)["leave"]],
            "warm": warm, "costs_per_mprompt": {t: round(c, 6) for t, c in sorted(costs.items(), key=lambda kv: kv[1])[:10]},
            "reason": reason, "switched": switched}


# ----------------------------------------------------------------------------- price edges
def price_snapshot(eps: list[dict], cfg: dict, current: str | None = None) -> dict:
    ok = {e["tag"] for e in eligible(eps, cfg, current)}
    return {e["tag"]: {"in": e["in"], "out": e["out"], "cr": e["cr"], "ok": e["tag"] in ok} for e in eps}


def price_flanks(old: dict, new: dict, now: float) -> list[dict]:
    """Edges between two snapshots: price changes, endpoints appearing/vanishing, eligibility flips. First run (no old) = baseline."""
    if not old:
        return []
    ev: list[dict] = []
    for tag, n in new.items():
        o = old.get(tag)
        if o is None:
            ev.append({"ts": now, "tag": tag, "kind": "appeared", "new": n})
            continue
        for f in ("in", "out", "cr", "ok"):
            if f == "ok":
                differ = o.get(f) != n[f]
            else:
                differ = abs((o.get(f) or 0.0) - n[f]) > 1e-12 + 1e-9 * abs(o.get(f) or 0.0)
            if differ:
                ev.append({"ts": now, "tag": tag, "kind": "change", "field": f, "old": o.get(f), "new": n[f]})
    ev += [{"ts": now, "tag": t, "kind": "vanished"} for t in old if t not in new]
    return ev


def append_jsonl(path: Path, rows: list[dict], max_bytes: int = 2_000_000, keep: int = 4000) -> None:
    if not rows:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as h:
        for r in rows:
            h.write(json.dumps(r, ensure_ascii=False) + "\n")
    if path.stat().st_size > max_bytes:  # bounded: keep the newest lines
        lines = path.read_text(encoding="utf-8").splitlines()[-keep:]
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def price_table(eps: list[dict], d: dict, cfg: dict) -> dict:
    """Compact prices ($/M in, out, cache-read) of the tags a decision can send traffic to (order, current, the cheapest ones): lets the
    dashboard price a past request at the prices valid when it was served, whatever happened to the live prices since."""
    want = set(d.get("order") or []) | set((d.get("costs_per_mprompt") or {})) | ({d["current"]} if d.get("current") else set())
    return {e["tag"]: [round(e["in"], 9), round(e["out"], 9), round(e["cr"], 9)] for e in eligible(eps, cfg, d.get("current")) if e["tag"] in want}


def run_decide(model: str, cfg: dict, now: float, sd: Path, eps: list[dict] | None = None, dry: bool = False) -> dict:
    eps = eps if eps is not None else fetch_endpoints(model)
    rows = read_usage(db_path(), model, int((now - cfg["week_days"] * 86400) * 1000))
    prof = choose_profile(rows, cfg, now)
    f = sd / "decision.json"
    try:
        doc = json.loads(f.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        doc = {}
    try:
        snaps = json.loads((sd / "prices_last.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        snaps = {}
    snap = price_snapshot(eps, cfg, ((doc.get("models") or {}).get(model) or {}).get("current"))
    flanks = [{**e, "model": model} for e in price_flanks(snaps.get(model) or {}, snap, now)]
    d = decide(eps, prof, (doc.get("models") or {}).get(model), cfg, now)
    d["profile"] = {k: (round(v, 4) if isinstance(v, float) else v) for k, v in prof.items()}
    d["updated"] = now
    d["flanks"] = len(flanks)
    ptab = price_table(eps, d, cfg) if d.get("ok") else None
    if not d.get("ok"):  # keep the last good decision, only record the failure
        d = {**(doc.get("models") or {}).get(model, {}), "last_error": d["reason"], "checked": now, "flanks": len(flanks),
             "switched": False, "reason": "failed run: " + str(d["reason"])}  # a failed run is never logged as a switch
    if not dry:
        doc = {"schema": SCHEMA, "generated_at": now, "config": {k: cfg[k] for k in ("cache_ttl_min", "refresh_min")},
               "models": {**(doc.get("models") or {}), model: d}}
        write_atomic(f, doc)
        write_atomic(sd / "prices_last.json", {**snaps, model: snap})
        append_jsonl(sd / "price_events.jsonl", flanks)
        top = list((d.get("costs_per_mprompt") or {}).items())[:3]
        row = {"ts": now, "model": model, "current": d.get("current"), "best": d.get("best"), "switched": bool(d.get("switched")),
               "reason": d.get("reason") or d.get("last_error"), "flanks": len(flanks), "top": top, "hit": prof["hit"],
               "out_ratio": prof["out_ratio"], "prompt_per_day": prof["prompt_per_day"], "window": prof["window"]}
        if ptab:
            row["prices"] = ptab
        append_jsonl(sd / "decisions.jsonl", [row], max_bytes=3_000_000, keep=6000)
    return d


# ----------------------------------------------------------------------------- report
def usage_log(sd: Path) -> list[dict]:
    out = []
    try:
        for ln in (sd / "usage.jsonl").read_text(encoding="utf-8").splitlines():
            try:
                out.append(json.loads(ln))
            except ValueError:
                pass
    except OSError:
        pass
    return out


def run_report(model: str, cfg: dict, days: int, now: float, sd: Path) -> str:
    rows = read_usage(db_path(), model, int((now - days * 86400) * 1000))
    prof = choose_profile(rows, cfg, now)
    s = summarize(rows)
    lines = [f"model {model}: last {days} d: {s['n']} msgs, {s['sessions']} sessions, prompt {s['prompt']/1e6:.1f}M (cache hit {s['hit']:.1%}), "
             f"output {s['output']/1e6:.2f}M, paid ${s['cost']:.2f}", f"profile used: {prof['window']} hit={prof['hit']:.1%} out_ratio={prof['out_ratio']:.4f} "
             f"ctx/req={prof['prompt_per_req']/1e3:.0f}k active_sessions={prof['active_sessions']}"]
    try:
        eps = eligible(fetch_endpoints(model), cfg)
    except Exception as e:  # network is best-effort for a report
        return "\n".join(lines + [f"live prices unavailable: {e}"])
    lines.append(f"{'endpoint':26s} {'$/Mprompt':>10s} {'same traffic ($)':>17s}")
    for e in sorted(eps, key=lambda e: unit_cost(e, prof))[:8]:
        lines.append(f"{e['tag']:26s} {unit_cost(e, prof):10.5f} {s['prompt'] * unit_cost(e, prof) / 1e6:17.2f}")
    log = usage_log(sd)
    if log and rows:  # join plugin choice log with measured cost/hit per provider
        agg: dict[str, list[float]] = {}
        for r in rows:
            m = [x for x in log if x.get("sid") == r["sid"] and x["ts"] <= r["ts"] / 1000 + 1]
            if m:
                a = agg.setdefault(m[-1]["tag"], [0, 0, 0, 0.0])
                a[0] += r["in"] + r["cw"]; a[1] += r["cr"]; a[2] += 1; a[3] += r["cost"]
        for tag, (i, c, n, cost) in sorted(agg.items()):
            lines.append(f"measured {tag}: {n} msgs hit={c / (i + c):.1%} est.cost=${cost:.4f}" if i + c else f"measured {tag}: {n} msgs")
    return "\n".join(lines)


def install_shim(home: Path | None = None, state: Path | None = None, repo: Path | None = None, extra: list[Path] | None = None) -> list[Path]:
    """Global opencode plugin shims that load this checkout's plugin, so every opencode project on this node routes. Re-run after moving
    the checkout. Two config roots exist on a harness node: `~/.config/opencode` (desktop/TUI, opencode V2) and the harness server's
    isolated `XDG_CONFIG_HOME` (`.jarvis/runtime/forward/harness/xdg-server`), which never sees the user config - without its own shim the
    TW harness spends on OpenRouter with no provider routing at all (2026-11-01). `extra` adds further plugin directories (e.g. the live
    harness config root of another checkout). Also records the absolute interpreter in <state>/python.txt: the plugin starts its refresh
    cycle with it (never a bare `python` from an unknown cwd)."""
    write_text_atomic((state or state_dir()) / "python.txt", sys.executable + "\n")
    root = Path(repo) if repo else Path(__file__).resolve().parents[2]
    plugin = root / ".opencode" / "plugins" / "or-provider-router.ts"
    dests = [(home or Path.home()) / ".config" / "opencode" / "plugins" / "or-provider-router.ts"]
    harness = root / ".jarvis" / "runtime" / "forward" / "harness" / "xdg-server" / "opencode" / "plugins"
    if harness.parent.parent.exists():  # only where a harness server config root actually exists
        dests.append(harness / "or-provider-router.ts")
    dests += [Path(d) / "or-provider-router.ts" for d in (extra or [])]
    out = []
    seen: set[str] = set()
    for dest in dests:
        key = str(dest).lower()
        if key in seen:
            continue
        seen.add(key)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text("// Shim written by scripts/models/or_provider_router.py install-shim: loads the repo plugin for every opencode project on this node.\n"
                        f'export {{ default }} from "{plugin.as_posix()}"\n', encoding="utf-8")
        out.append(dest)
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("cmd", choices=("decide", "report", "status", "install-shim"))
    ap.add_argument("--model")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--repo", help="checkout whose .opencode/plugins/or-provider-router.ts the shims load")
    ap.add_argument("--shim-dir", action="append", default=[], help="additional plugin directory for a shim (repeatable), e.g. another checkout's harness xdg-server/opencode/plugins")
    a = ap.parse_args(argv)
    sd = state_dir()
    cfg = load_cfg(sd)
    now = time.time()
    if a.cmd == "install-shim":
        for p in install_shim(None, sd, Path(a.repo) if a.repo else None, [Path(x) for x in a.shim_dir]):
            print(p)
        return 0
    if a.cmd == "status":
        print((sd / "decision.json").read_text(encoding="utf-8") if (sd / "decision.json").exists() else "no decision yet")
        return 0
    for model in [a.model] if a.model else cfg["models"]:
        if a.cmd == "report":
            print(run_report(model, cfg, a.days, now, sd))
            continue
        d = run_decide(model, cfg, now, sd, dry=a.dry_run)
        print(json.dumps({model: d}, indent=1) if a.json else f"{model}: {d.get('current')} order={d.get('order')} -- {d.get('reason') or d.get('last_error')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
