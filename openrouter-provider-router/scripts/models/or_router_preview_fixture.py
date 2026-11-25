#!/usr/bin/env python3
"""Synthetic router.json for VISUAL QA of the dashboard only - the numbers are invented, the file is never published.

    python scripts/models/or_router_preview_fixture.py OUT.json [--hours 36] [--seed 7]

Replays ~36 h of routed traffic through the REAL decision engine (or_provider_router.run_decide) and exporter (or_router_export.build):
  * a temp state dir + SQLite DB (opencode message schema), deleted afterwards - the real state dir and DB are never read or written;
  * no network: 6 providers come from the built-in table below; the key-usage counter is simulated (list prices x tokens x 3 % noise);
  * 5 sessions in bursts (hit rate 85-97 %, a cold first request after idle or a provider switch), the plugin's pin logic is mimicked
    (a session keeps its provider while warm and tolerated, else takes the default);
  * two price edges (open-inference cache-read drops at +14 h -> one real provider switch; morph input gets a bit cheaper at +27 h);
  * the document carries "synthetic": true and node label FIXTURE. OUT.json must not live in the real router state dir.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import or_provider_router as R  # noqa: E402
import or_router_export as X  # noqa: E402

MODEL = R.DEFAULT_MODEL
STEP = 300  # seconds between decision runs (= the plugin's refresh_min)
# tag, in, out, cache-read ($ per M tokens)
TABLE = [("morph/fp8", 0.022, 0.192, 0.004), ("relace/fp4", 0.035, 0.5, 0.005), ("open-inference/fp4", 0.0141, 0.144, 0.013),
         ("deepseek/fp8", 0.14, 0.28, 0.0028), ("novita/fp8", 0.03, 0.3, 0.006), ("wafer/us", 0.05, 0.4, 0.01)]


def endpoints(t: float, t0: float) -> list[dict]:
    """The provider table with the two scripted price edges applied for time t."""
    out = []
    for tag, i, o, cr in TABLE:
        if tag == "open-inference/fp4" and t >= t0 + 14 * 3600:
            cr = 0.003
        if tag == "morph/fp8" and t >= t0 + 27 * 3600:
            i = 0.02
        out.append({"tag": tag, "name": tag.split("/")[0], "in": i, "out": o, "cr": cr, "ctx": 1_048_576, "maxout": 943_718,
                    "quant": tag.split("/")[1], "uptime": 99.8, "status": 0})
    return out


def traffic(rng: random.Random, t0: float, t1: float, sessions: int = 5) -> list[tuple[float, str, float, float]]:
    """(ts, session, prompt tokens, base hit rate) of bursty sessions."""
    reqs = []
    for k in range(sessions):
        sid, base, t = f"ses_fx_{k + 1}", rng.uniform(0.89, 0.95), t0 + rng.uniform(0, 4 * 3600)
        while t < t1:
            end, ctx, x = min(t + rng.uniform(30, 150) * 60, t1), rng.uniform(80_000, 200_000), t
            while x < end:
                ctx = min(ctx * rng.uniform(1.0, 1.02), 400_000)
                reqs.append((x, sid, ctx, base))
                x += rng.uniform(40, 150)
            t = end + rng.uniform(15, 240) * 60
    return sorted(reqs)


def pick(entry: dict, pin: tuple[str, float] | None, now: float, ttl_s: float) -> str:
    """Same rule as the plugin's pickOrder."""
    order = entry.get("order") or []
    if pin and now - pin[1] < ttl_s and pin[0] in (entry.get("tolerated") or order):
        return pin[0]
    return order[0]


def generate(out: Path, now: float | None = None, hours: float = 36, seed: int = 7) -> dict:
    now = now if now is not None else time.time()
    real = R.state_dir().resolve()
    if out.resolve().parent == real:
        raise SystemExit("refusing to write synthetic data into the real router state dir")
    rng = random.Random(seed)
    t0 = now - hours * 3600
    cfg = {**R.DEFAULTS, **X.CFG, "node_label": "FIXTURE", "publish": False, "fallback_prompt_per_day": 250_000_000}  # no history before the first hour
    reqs = traffic(rng, t0, now)
    old_db = os.environ.get("ORR_DB")
    with tempfile.TemporaryDirectory(prefix="orr-fixture-") as tmp:
        sd, db = Path(tmp) / "state", Path(tmp) / "opencode.db"
        sd.mkdir()
        con = sqlite3.connect(db)
        con.execute("create table message (id text, session_id text, time_created integer, time_updated integer, data text)")
        os.environ["ORR_DB"] = str(db)
        try:
            pins: dict[str, tuple[str, float]] = {}
            usage, n, i = 12.34, 0, 0
            t = t0
            while t < now:
                eps = endpoints(t, t0)
                price = {e["tag"]: e for e in eps}
                entry = R.run_decide(MODEL, cfg, t, sd, eps=eps)
                end, rows, log, spent = min(t + STEP, now), [], [], 0.0
                while i < len(reqs) and reqs[i][0] < end:
                    ts, sid, ctx, base = reqs[i]
                    i += 1
                    prev = pins.get(sid)
                    tag = pick(entry, prev, ts, cfg["cache_ttl_min"] * 60)
                    cold = prev is None or prev[0] != tag or ts - prev[1] >= cfg["cache_ttl_min"] * 60
                    pins[sid] = (tag, ts)
                    if prev is None or prev[0] != tag:
                        log.append({"ts": ts, "sid": sid, "model": MODEL, "tag": tag})
                    hit = 0.15 if cold else min(0.97, max(0.85, rng.gauss(base, 0.015)))
                    out_t = ctx * 0.0025 * rng.uniform(0.7, 1.3)
                    tok = {"input": round(ctx * (1 - hit)), "output": round(out_t), "reasoning": 0, "cache": {"read": round(ctx * hit), "write": 0}}
                    data = {"role": "assistant", "modelID": MODEL, "providerID": "openrouter", "cost": 0.0, "tokens": tok}
                    n += 1
                    rows.append((str(n), sid, int(ts * 1000), int(ts * 1000), json.dumps(data)))
                    p = price[tag]
                    spent += (tok["input"] * p["in"] + tok["output"] * p["out"] + tok["cache"]["read"] * p["cr"]) / 1e6
                con.executemany("insert into message values (?,?,?,?,?)", rows)
                con.commit()
                R.append_jsonl(sd / "usage.jsonl", log)
                usage += spent * (1 + rng.uniform(-0.03, 0.03))  # the key counter: true spend with 3 % measurement noise
                R.append_jsonl(sd / "keyusage.jsonl", [{"ts": end, "usage": round(usage, 6)}], keep=20000, max_bytes=3_000_000)
                t += STEP
            R.run_decide(MODEL, cfg, now, sd, eps=endpoints(now, t0))
            data = X.build(MODEL, cfg, now, sd, endpoints(now, t0), db=db)
        finally:
            con.close()
            if old_db is None:
                os.environ.pop("ORR_DB", None)
            else:
                os.environ["ORR_DB"] = old_db
    data["synthetic"] = True
    R.write_atomic(out, data)
    return data


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("out", type=Path, help="where to write the synthetic router.json")
    ap.add_argument("--hours", type=float, default=36)
    ap.add_argument("--seed", type=int, default=7)
    a = ap.parse_args(argv)
    d = generate(a.out, hours=a.hours, seed=a.seed)
    hp = d["control"]["hit_by_provider"]
    print(f"wrote {a.out} ({sum(x['msgs'] for x in hp)} msgs on {len(hp)} providers, synthetic)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
