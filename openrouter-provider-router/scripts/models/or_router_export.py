#!/usr/bin/env python3
"""Public dashboard data for the OpenRouter provider router: expectation vs measurement, rankings, forecast, logs.

Reads the router's own files (<state>/decision.json, decisions.jsonl, price_events.jsonl, usage.jsonl, keyusage.jsonl) and
opencode.db, and writes ONE sanitized document <state>/router.json for the static page wannkannichballern.wfech.de.
Whitelist-built: no keys, session ids, paths, account limit/balance. Read-only visualisation, nothing here controls anything.

  cycle    decide -> sample key usage -> export -> publish (what the opencode plugin starts every refresh_min)
  export   build router.json only (several models: router.json = first model, plus router-<slug>.json per model)
  publish  push router.json to the tiny-pages container (needs config.json: {"publish": true})

Expectation vs measurement:
  * cache hit   expected = hit rate the router assumed when it decided   | measured = cache_read / prompt of routed requests
  * tokens/day  expected = prompt_per_day the router believed that morning | measured = real prompt tokens of the day
  * cost        expected = real tokens x list prices of the provider that served them | measured = delta of the API key usage counter
  * regret      what the served provider cost vs the cheapest one in hindsight
Only requests whose provider is known (plugin log / decision log) count as "routed".

Prices of the past: a request is priced with the prices valid when it was served. Every decisions.jsonl row carries a compact
price table ("prices": tag -> [in, out, cache_read], written by or_provider_router.run_decide for the order/current/cheapest tags);
a request uses the latest row at or before its timestamp that lists its tag (PriceBook), else the live price. Attribution and pricing use
ALL fetched endpoints, so a provider that is ineligible or gone today keeps its history. (Chosen over replaying price_events.jsonl backwards:
one lookup, no event bookkeeping, and the table is exactly what the router believed.)
Day boundaries come from calendar arithmetic (day_window), not midnight - k*86400, so DST days (23/25 h) are right.
The cache badge compares measured hit with the hit rate the router ASSUMED at decision time (time-weighted over the window), not with
data derived from the same history.
Robustness: cycle() runs decide / key sample / export / publish as isolated steps; export never leaves router.json silently stale:
without eligible endpoints (or when the fetch/build fails) the last known document is re-written with "error" and "error_at".
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.request
from bisect import bisect_right
from collections import defaultdict
from datetime import date, datetime, timedelta
from datetime import time as dtime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import or_provider_router as R  # noqa: E402
import or_router_publish as P  # noqa: E402
import or_router_trending as TR  # noqa: E402

SCHEMA = 1
CFG: dict = {
    "hit_ok_pp": 3.0, "hit_watch_pp": 8.0,  # |measured - expected| cache hit in percentage points
    "tok_ok": 0.25, "tok_watch": 0.50,  # relative deviation of tokens/day forecast
    "cost_ok": 0.10, "cost_watch": 0.25,  # relative deviation expected vs measured spend
    "reg_ok": 0.05, "reg_watch": 0.15,  # regret relative to the hindsight-best cost
    "min_cost_samples": 3, "keyusage_min_gap_s": 120,
    "publish": False, "ssh_host": "your-vps.example", "ssh_port": 1001, "ssh_key": "", "ct": 100,
    "remote_path": "/srv/pages/deepseek-offpeak/router.json", "node_label": "X1",
}
KEY_URL = "https://openrouter.ai/api/v1/key"


def load_cfg(sd: Path) -> dict:
    cfg = {**R.load_cfg(sd), **CFG}
    try:
        cfg.update({k: v for k, v in json.loads((sd / "config.json").read_text(encoding="utf-8")).items() if k in CFG})
    except (OSError, ValueError):
        pass
    return cfg


def read_jsonl(path: Path, since: float = 0.0) -> list[dict]:
    out: list[dict] = []
    try:
        for ln in path.read_text(encoding="utf-8").splitlines():
            try:
                j = json.loads(ln)
            except ValueError:
                continue
            if j.get("ts", 0) >= since:
                out.append(j)
    except OSError:
        pass
    return out


# ----------------------------------------------------------------------------- measured spend (API key usage counter)
def sample_key_usage(sd: Path, now: float, cfg: dict, opener=urllib.request.urlopen) -> bool:
    """Append the usage counter of the opencode OpenRouter key, plus its real spend aggregates (today/week/month, remaining limit).
    The key is read, used and never stored, logged or exported; the aggregates are what OpenRouter actually bills (2026-11-01: the page
    showed router arithmetic where the owner expected real money)."""
    f = sd / "keyusage.jsonl"
    last = read_jsonl(f)[-1:] or [{"ts": 0}]
    if now - last[0]["ts"] < cfg["keyusage_min_gap_s"]:
        return False
    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        try:
            auth = json.loads((R.db_path().parent / "auth.json").read_text(encoding="utf-8"))
            key = (auth.get("openrouter") or {}).get("key")
        except (OSError, ValueError):
            key = None
    if not key:
        return False
    try:
        req = urllib.request.Request(KEY_URL, headers={"Authorization": "Bearer " + key, "User-Agent": R.USER_AGENT})
        with opener(req, timeout=20) as r:
            data = json.load(r)["data"]
        usage = float(data["usage"])
    except Exception:  # network/format problems must not break the cycle
        return False
    row: dict = {"ts": now, "usage": usage}
    for k in ("usage_daily", "usage_weekly", "usage_monthly", "limit", "limit_remaining"):
        try:
            row[k] = float(data[k])
        except (KeyError, TypeError, ValueError):
            pass  # a field OpenRouter does not send stays absent, never 0
    if isinstance(data.get("limit_reset"), str):
        row["limit_reset"] = data["limit_reset"]  # "daily" | "weekly" | "monthly": what limit_remaining refers to
    R.append_jsonl(f, [row], keep=20000, max_bytes=3_000_000)
    return True


# ----------------------------------------------------------------------------- attribution + costs
def attribute(rows: list[dict], usage_log: list[dict], decisions: list[dict]) -> list[tuple[dict, str | None, str]]:
    """Which endpoint served each request, and how well-founded that answer is: 'pin' = the plugin logged this session's provider (true
    attribution), 'decision' = the router default of that moment (a guess: the request may have gone anywhere on OpenRouter), '' = unknown."""
    by_sid: dict[str, list[dict]] = defaultdict(list)
    for u in usage_log:
        by_sid[u.get("sid")].append(u)
    dts = [d["ts"] for d in decisions]
    out = []
    for r in rows:
        t = r["ts"] / 1000
        tag = None
        src = ""
        pins = by_sid.get(r["sid"]) or []
        k = bisect_right([p["ts"] for p in pins], t + 1) - 1
        if k >= 0:
            tag, src = pins[k].get("tag"), "pin"
        else:
            j = bisect_right(dts, t) - 1
            if j >= 0:
                tag, src = decisions[j].get("current"), "decision"
        out.append((r, tag, src))
    return out


def msg_cost(r: dict, ep: dict) -> float:
    return ((r["in"] + r["cw"]) * ep["in"] + r["out"] * ep["out"] + r["cr"] * ep["cr"]) / 1e6


def status(dev: float | None, ok: float, watch: float) -> str:
    if dev is None:
        return "na"
    return "ok" if dev <= ok else "watch" if dev <= watch else "off"


def local_day(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d")


def day_window(today: date, k: int, tz=None) -> tuple[float, float, str]:
    """[start, end) epoch seconds and ISO label of the local calendar day `k` days before `today`. Calendar arithmetic: DST days are 23/25 h long."""
    d = today - timedelta(days=k)
    return (datetime.combine(d, dtime.min, tzinfo=tz).timestamp(), datetime.combine(d + timedelta(days=1), dtime.min, tzinfo=tz).timestamp(), d.isoformat())


class PriceBook:
    """Prices valid at a moment: latest decisions row at/before it that lists the tag in its "prices" table, else the live price (all fetched endpoints)."""
    LOOKBACK = 24  # rows searched backwards for a tag that dropped out of the latest table

    def __init__(self, decisions: list[dict], live: dict[str, dict]):
        self.rows, self.ts, self.live = decisions, [d["ts"] for d in decisions], live

    def at(self, tag: str, t: float) -> dict | None:
        j = bisect_right(self.ts, t) - 1
        for i in range(j, max(-1, j - self.LOOKBACK), -1):
            p = (self.rows[i].get("prices") or {}).get(tag)
            if p:
                return {"in": p[0], "out": p[1], "cr": p[2]}
        return self.live.get(tag)


def hour_start(ts: float) -> int:
    return int(ts // 3600 * 3600)


def agg(rows: list[dict]) -> dict:
    s = R.summarize(rows)
    return {"msgs": s["n"], "prompt": s["prompt"], "output": s["output"], "hit": round(s["hit"], 4), "out_ratio": round(s["out_ratio"], 5)}


# ----------------------------------------------------------------------------- sections
def ranking(eps: list[dict], prof: dict, prompt: float, current: str | None, msgs: int = 0, min_tokens: float = 0) -> dict:
    rows = sorted(({"tag": e["tag"], "name": e["name"], "quant": e["quant"], "in": e["in"], "out": e["out"], "cr": e["cr"],
                    "unit": R.unit_cost(e, prof), "usd": prompt * R.unit_cost(e, prof) / 1e6} for e in eps), key=lambda x: x["unit"])
    top = rows[0]["unit"] if rows else 0
    for x in rows:
        x["delta"] = (x["unit"] / top - 1) if top else 0
        x["unit"], x["usd"] = round(x["unit"], 6), round(x["usd"], 4)
        x["delta"] = round(x["delta"], 4)
    return {"profile": {"hit": round(prof["hit"], 4), "out_ratio": round(prof["out_ratio"], 5), "prompt": round(prompt)},
            "winner": rows[0]["tag"] if rows else None, "current": current, "msgs": msgs, "thin": prompt < min_tokens, "rows": rows[:14]}


def session_rankings(rows: list[dict], eps: list[dict], current: str | None, live: dict | None = None, limit: int = 12) -> list[dict]:
    if not eps:
        return []
    by: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by[r["sid"]].append(r)
    sess = sorted((v for v in by.values() if len(v) >= 5), key=lambda v: -max(x["ts"] for x in v))[:limit]
    epmap = live if live is not None else {e["tag"]: e for e in eps}
    out = []
    for i, v in enumerate(sess, 1):
        s = R.summarize(v)
        prof = {"hit": s["hit"], "out_ratio": s["out_ratio"]}
        rk = sorted(eps, key=lambda e: R.unit_cost(e, prof))
        best = rk[0]
        cur = epmap.get(current)
        out.append({"n": i, "start": min(x["ts"] for x in v) / 1000, "end": max(x["ts"] for x in v) / 1000, "msgs": s["n"], "prompt": s["prompt"],
                    "hit": round(s["hit"], 4), "best": best["tag"], "usd_best": round(s["prompt"] * R.unit_cost(best, prof) / 1e6, 4),
                    "usd_current": round(s["prompt"] * R.unit_cost(cur, prof) / 1e6, 4) if cur else None})
    return out


def forecast(rows: list[dict], epmap: dict[str, dict], d: dict, prof: dict, now: float) -> dict:
    """Hour-of-day profile of the last 7 days -> next 24 h; plus the last 48 h of actual traffic. USD at current/best/alternative endpoint."""
    cur, best = epmap.get(d.get("current")), epmap.get(d.get("best"))
    alt = next((epmap[t] for t in (d.get("order") or []) if t in epmap and t not in (d.get("current"), d.get("best"))), None)
    per_hod: dict[int, list[float]] = defaultdict(list)
    hours: dict[int, float] = defaultdict(float)
    for r in rows:
        hours[hour_start(r["ts"] / 1000)] += r["in"] + r["cw"] + r["cr"]
    days = max(1, len({local_day(h) for h in hours}))
    for h, v in hours.items():
        per_hod[datetime.fromtimestamp(h).hour].append(v)
    h0 = hour_start(now)
    series = []
    for k in range(-47, 25):
        h = h0 + k * 3600
        actual = k <= 0
        tok = hours.get(h, 0.0) if actual else sum(per_hod.get(datetime.fromtimestamp(h).hour, [0.0])) / days
        usd = lambda e: round(tok * R.unit_cost(e, prof) / 1e6, 4) if e else None  # noqa: E731
        series.append({"h": h, "kind": "actual" if actual else "forecast", "prompt": round(tok), "usd_current": usd(cur), "usd_best": usd(best), "usd_alt": usd(alt)})
    return {"alt": alt["tag"] if alt else None, "series": series}


def real_spend(keyuse: list[dict], now: float) -> dict:
    """Real money from the key counter, as deltas of its samples. OpenRouter's usage_daily resets at UTC midnight, so the page needs
    rolling windows (owner 2026-11-02: right after midnight "24 h" showed 7 cents and looked like a broken router). Windows whose
    start falls into a long sampling gap (opencode idle) use the last known counter value and are flagged in "approx"."""
    if not keyuse:
        return {}
    ts = [r["ts"] for r in keyuse]
    approx: list[str] = []

    def usage_at(t: float) -> tuple[float | None, bool]:
        if t < ts[0]:
            return None, True
        j = bisect_right(ts, t) - 1
        if j >= len(ts) - 1:
            return keyuse[-1]["usage"], True
        if ts[j] == t:
            return keyuse[j]["usage"], True
        a, b = keyuse[j], keyuse[j + 1]
        if b["ts"] - a["ts"] > 6 * 3600:  # interpolating across an idle gap would invent spending
            return a["usage"], False
        return a["usage"] + (b["usage"] - a["usage"]) * (t - a["ts"]) / (b["ts"] - a["ts"]), True

    def window(k: str, t: float) -> float | None:
        base, exact = usage_at(t)
        if base is None:
            return None
        if not exact:
            approx.append(k)
        return round(max(0.0, keyuse[-1]["usage"] - base), 4)

    last = keyuse[-1]
    midnight = day_window(datetime.fromtimestamp(now).date(), 0)[0]
    out = {"usage": round(last["usage"], 4), "since": ts[0], "at": last["ts"], "coverage_h": round((ts[-1] - ts[0]) / 3600, 1),
           "d24": window("d24", now - 86400), "today": window("today", midnight), "week": window("week", now - 7 * 86400)}
    out["approx"] = approx
    return out


def _costs(prices: dict, prof: dict) -> dict[str, float]:
    """Unit cost of every listed endpoint at one usage profile. Prices are [input, output, cache-read] per 1M tokens."""
    out = {}
    for tag, p in (prices or {}).items():
        try:
            out[tag] = R.unit_cost({"in": float(p[0]), "out": float(p[1]), "cr": float(p[2])}, prof)
        except (TypeError, ValueError, IndexError, KeyError):
            continue
    return out


def sample_ranks(sd: Path, now: float, cfg: dict, eps_all: list[dict], model: str) -> bool:
    """Append a price snapshot of every endpoint. Raw list prices (not unit costs) keep old samples comparable at today's profile, so the
    page can show when a provider became the cheapest and how long such a cheap phase lasted."""
    f = sd / "ranks.jsonl"
    last = read_jsonl(f)[-1:]
    if last and now - last[0]["ts"] < cfg["ranks_min_gap_s"]:
        return False
    prices = {}
    for e in eps_all or []:
        try:
            prices[e["tag"]] = [round(float(e["in"]), 6), round(float(e["out"]), 6), round(float(e["cr"]), 6)]
        except (TypeError, ValueError, KeyError):
            continue
    if not prices:
        return False
    R.append_jsonl(f, [{"ts": round(now, 1), "model": model, "p": prices}], keep=200000, max_bytes=cfg["ranks_keep_bytes"])
    return True


def price_points(sd: Path, decisions: list[dict], prof: dict, since: float, model: str | None = None) -> list[tuple[float, dict[str, float]]]:
    """{ts: {tag: unit cost}} at the given profile: the sampled snapshots, plus the older decision rows (they carry prices too)."""
    pts: dict[float, dict[str, float]] = {}
    for d in decisions:
        if d.get("ts", 0) >= since:  # older history: decisions.jsonl kept 100 rows, ~20 min apart
            c = _costs(d.get("prices") or {}, prof)
            if c:
                pts[round(float(d["ts"]), 1)] = c
    for row in read_jsonl(sd / "ranks.jsonl"):
        if row.get("ts", 0) >= since and row.get("model") in (None, model):
            c = _costs(row.get("p") or {}, prof)
            if c:
                pts[round(float(row["ts"]), 1)] = c
    return sorted(pts.items())


def watch(sd: Path, now: float, cfg: dict, prof: dict, decisions: list[dict], live: dict[str, dict], model: str | None = None) -> dict:
    """Providers worth watching: every provider that was rank 1 (cheapest for our usage) inside the window. A provider drops off the
    list when it has not been cheapest for `watch_days` (owner rule 2026-11-02). Shows the cost curve per hour, the rank-1 phases and
    the cheap phases (clearly below the provider's own median), so a short price dip is visible instead of averaged away."""
    days, since = cfg["watch_days"], now - cfg["watch_days"] * 86400
    pts = price_points(sd, decisions, prof, since, model)
    spans: list[list] = []  # [tag, start, end] phases in which that tag was the cheapest
    for (t0, c0), (t1, _c1) in zip(pts, pts[1:]):
        if not c0:
            continue
        w = min(c0, key=lambda k: c0[k])
        if spans and spans[-1][0] == w and spans[-1][2] >= t0 - 1:
            spans[-1][2] = t1
        else:
            spans.append([w, t0, t1])
    if spans and pts and pts[-1][0] >= now - max(3 * cfg["ranks_min_gap_s"], 900):
        spans[-1][2] = max(spans[-1][2], now)  # the open phase is still running
    elif not spans and pts and pts[-1][1]:
        c0 = pts[-1][1]  # a single snapshot still tells who is cheapest right now
        spans.append([min(c0, key=lambda k: c0[k]), pts[-1][0], now])

    per: dict[str, list[tuple[float, float]]] = defaultdict(list)
    for t, c in pts:
        for tag, v in c.items():
            per[tag].append((t, v))

    def median(v: list[float]) -> float:
        s = sorted(v)
        k = len(s)
        return (s[(k - 1) // 2] + s[k // 2]) / 2 if k else 0.0

    first_span = {s[0]: s[1] for s in reversed(spans)}
    tags = []
    for tag, series in per.items():
        vals = [v for _, v in series]
        med = median(vals)
        rank1 = [s for s in spans if s[0] == tag]
        last_rank1 = max((s[2] for s in rank1), default=None)
        if last_rank1 is None or now - last_rank1 > days * 86400:
            continue  # rule: not cheapest for `watch_days` -> stop watching
        cheap = []
        prev_cheap = False
        for t, v in series:
            isc = bool(med) and v <= cfg["watch_peak_rel"] * med
            if isc:
                if prev_cheap:
                    cheap[-1][1] = t
                    cheap[-1][2] = min(cheap[-1][2], v)
                else:
                    cheap.append([t, t, v])
            prev_cheap = isc
        hours: list[float | None] = [None] * 24
        for h in range(24):
            hv = [v for t, v in series if datetime.fromtimestamp(t).hour == h]
            if hv:
                hours[h] = round(median(hv), 6)
        span_d = (series[-1][0] - series[0][0]) / 86400 if len(series) > 1 else 0.0
        # an hour-of-day median from a few hours of samples would be a guess, not a pattern
        best = sorted((v, h) for h, v in enumerate(hours) if v is not None)[:4] if span_d >= 3 else []
        cur_cost = series[-1][1]
        tags.append({"tag": tag, "now": round(cur_cost, 6), "rank": (sorted(pts[-1][1].values()).index(cur_cost) + 1) if pts and tag in pts[-1][1] else None,
                     "last_rank1": last_rank1, "since": first_span.get(tag), "rank1": [[s[1], s[2]] for s in rank1[-12:]],
                     "cheap": cheap[-12:], "median": round(med, 6), "min": round(min(vals), 6),
                     "min_at": min(series, key=lambda x: x[1])[0], "best_hours": [h for _, h in best], "hours_days": round(span_d, 1),
                     "hours": hours, "samples": len(series)})
    tags.sort(key=lambda x: (-(x["last_rank1"] or 0)))
    watched = {x["tag"] for x in tags}

    # chart: one value per hour for the watched providers (median of the samples in that hour), 7 days = 168 points
    b0 = int(since // 3600 * 3600)
    series = {}
    for tag in watched:
        buckets: dict[int, list[float]] = defaultdict(list)
        for t, v in per.get(tag, []):
            buckets[int(t // 3600 * 3600)].append(v)
        series[tag] = [[h, round(median(v), 6)] for h, v in sorted(buckets.items())]
    last = pts[-1][1] if pts else {}
    return {"days": days, "peak_rel": cfg["watch_peak_rel"], "now": now, "samples": len(pts),
            "first_sample": pts[0][0] if pts else None, "rank1": (min(last, key=lambda k: last[k]) if last else None),
            "spans": [[s[0], s[1], s[2]] for s in spans[-120:]], "tags": tags, "series": series,
            "live": {e["tag"]: {"in": e["in"], "out": e["out"], "cr": e["cr"]} for e in live.values() if e.get("tag")}}


def build(model: str, cfg: dict, now: float, sd: Path, eps_all: list[dict], db: Path | None = None) -> dict:
    live = {e["tag"]: e for e in eps_all}  # ALL fetched endpoints: attribution + pricing of history (an ineligible provider keeps its past)
    doc = {}
    try:
        doc = json.loads((sd / "decision.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    d = (doc.get("models") or {}).get(model) or {}
    eps = R.eligible(eps_all, cfg, d.get("current"))  # candidates for rankings / forecast / provider table (the provider in use keeps its lower uptime floor)
    rows = R.read_usage(db or R.db_path(), model, int((now - 8 * 86400) * 1000))
    week_rows = [r for r in rows if r["ts"] >= (now - cfg["week_days"] * 86400) * 1000]
    today_d = datetime.fromtimestamp(now).date()
    midnight = day_window(today_d, 0)[0]
    today_rows = [r for r in rows if r["ts"] / 1000 >= midnight]
    prof = d.get("profile") or R.choose_profile(week_rows, cfg, now)
    decisions = [x for x in read_jsonl(sd / "decisions.jsonl") if x.get("model") == model]
    usage_log = [x for x in read_jsonl(sd / "usage.jsonl") if x.get("model") == model]
    events = [x for x in read_jsonl(sd / "price_events.jsonl") if x.get("model") == model]
    keyuse = read_jsonl(sd / "keyusage.jsonl")
    book = PriceBook(decisions, live)
    attr = []  # (message, serving tag, how the tag is founded, its cost at the prices valid when it was served)
    for r, t, src in attribute(rows, usage_log, decisions):
        e_then = book.at(t, r["ts"] / 1000) if t else None
        if e_then:
            attr.append((r, t, src, msg_cost(r, e_then)))
    cur = d.get("current")
    pin_tok = sum(r["in"] + r["cw"] + r["cr"] for r, _, src, _ in attr if src == "pin")
    all_tok = sum(r["in"] + r["cw"] + r["cr"] for r, _, _, _ in attr)
    attribution = {"tokens": all_tok, "pinned": pin_tok, "share": round(pin_tok / all_tok, 4) if all_tok else None}

    # rankings: today / week / defaults
    w_s, t_s = R.summarize(week_rows), R.summarize(today_rows)
    dflt = {"hit": cfg["fallback_hit"], "out_ratio": cfg["fallback_out_ratio"]}
    thin = cfg["min_profile_tokens"] / 4  # below this the window is too small to trust its cache-hit/output profile
    rankings = {"today": ranking(eps, t_s if t_s["prompt"] else dflt, t_s["prompt"], cur, t_s["n"], thin),
                "week": ranking(eps, w_s if w_s["prompt"] else dflt, w_s["prompt"], cur, w_s["n"], thin),
                "default": ranking(eps, dflt, cfg["fallback_prompt_per_day"], cur),
                "sessions": session_rankings(week_rows, eps, cur, live)}
    rankings["today"]["empty"] = not t_s["prompt"]

    # control: cache hit per hour + per provider. Expectation = the hit rate the router assumed when it decided (decisions.jsonl).
    hit_h: dict[int, list] = defaultdict(lambda: [0, 0, 0])
    prov: dict[str, list] = defaultdict(lambda: [0, 0, 0, 0.0, 0.0])
    dts = [x["ts"] for x in decisions]
    exp_hit = lambda t: (decisions[bisect_right(dts, t) - 1]["hit"] if dts and bisect_right(dts, t) > 0 else prof["hit"])  # noqa: E731

    def exp_mean(t0: float, t1: float) -> float:
        """Time-weighted mean of the assumed hit rate over [t0, t1] (starts at the first decision: before it nothing was assumed)."""
        t0 = max(t0, dts[0]) if dts else t0
        if t1 <= t0:
            return exp_hit(t1)
        pts = [t0] + [t for t in dts if t0 < t < t1] + [t1]
        return sum((b - a) * exp_hit(a) for a, b in zip(pts, pts[1:])) / (t1 - t0)

    for r, tag, _, c in attr:
        h = hour_start(r["ts"] / 1000)
        prompt = r["in"] + r["cw"] + r["cr"]
        a = hit_h[h]
        a[0] += r["cr"]; a[1] += prompt; a[2] += 1
        p = prov[tag]
        p[0] += r["cr"]; p[1] += prompt; p[2] += 1; p[3] += c; p[4] += exp_hit(r["ts"] / 1000) * prompt
    hit_hourly = [{"h": h, "measured": round(a[0] / a[1], 4) if a[1] else None, "expected": round(exp_hit(h + 1800), 4), "n": a[2]}
                  for h, a in sorted(hit_h.items()) if h >= now - 48 * 3600]
    hit_by_provider = [{"tag": t, "measured": round(p[0] / p[1], 4) if p[1] else None, "expected": round(p[4] / p[1] if p[1] else exp_hit(now), 4), "msgs": p[2],
                        "usd_expected": round(p[3], 4)} for t, p in sorted(prov.items(), key=lambda kv: -kv[1][2])]
    r24 = [(r, t) for r, t, _, _ in attr if r["ts"] / 1000 >= now - 86400]
    m_hit = (sum(r["cr"] for r, _ in r24) / max(1, sum(r["in"] + r["cw"] + r["cr"] for r, _ in r24))) if r24 else None
    exp24_hit = exp_mean(now - 86400, now)
    hit_dev = abs(m_hit - exp24_hit) * 100 if m_hit is not None else None

    # control: tokens per day forecast vs actual (forecast = what the router believed at the start of that day)
    tokens_daily = []
    for k in range(6, -1, -1):
        a0, a1, label = day_window(today_d, k)
        day_rows = [r for r in rows if a0 <= r["ts"] / 1000 < a1]
        j = bisect_right(dts, a0 + 3600) - 1
        fc = decisions[j]["prompt_per_day"] if j >= 0 else None
        act = sum(r["in"] + r["cw"] + r["cr"] for r in day_rows)
        tokens_daily.append({"day": label, "forecast": round(fc) if fc else None, "actual": act, "partial": k == 0})
    done = [x for x in tokens_daily if not x["partial"] and x["forecast"] and x["actual"]]
    tok_dev = abs(done[-1]["actual"] / done[-1]["forecast"] - 1) if done else None

    # control: expected cost (tokens x list price of the serving endpoint then) vs measured key spend between usage samples
    cost_hourly: dict[int, list] = defaultdict(lambda: [0.0, 0.0])
    exp24 = mea24 = 0.0
    nsamp = 0
    for a, b in zip(keyuse, keyuse[1:]):
        if b["ts"] - a["ts"] > 6 * 3600:  # sampling gap (opencode idle): the delta cannot be attributed to the routed requests of a gap interval
            continue
        exp = sum(c for r, _, _, c in attr if a["ts"] < r["ts"] / 1000 <= b["ts"])
        mea = max(0.0, b["usage"] - a["usage"])
        c = cost_hourly[hour_start(b["ts"])]
        c[0] += exp; c[1] += mea
        if b["ts"] >= now - 86400:
            exp24 += exp; mea24 += mea; nsamp += 1
    cost_series = [{"h": h, "expected": round(c[0], 5), "measured": round(c[1], 5)} for h, c in sorted(cost_hourly.items()) if h >= now - 48 * 3600]
    cost_dev = abs(mea24 / exp24 - 1) if exp24 > 0 and nsamp >= cfg["min_cost_samples"] else None

    # control: regret in hindsight per day (alternatives priced at the prices valid when each request was served)
    regret_days = []
    for k in range(6, -1, -1):
        a0, a1, label = day_window(today_d, k)
        dr = [(r, t, src, c) for r, t, src, c in attr if a0 <= r["ts"] / 1000 < a1]
        if not dr:
            continue
        chosen = sum(c for _, _, _, c in dr)

        def alt_total(tg: str) -> float | None:
            ps = [book.at(tg, r["ts"] / 1000) for r, _, _, _ in dr]
            return None if None in ps else sum(msg_cost(r, p) for (r, _, _, _), p in zip(dr, ps))

        # candidates: today's eligible endpoints plus whatever served that day (valid alternatives at their time even if gone today)
        totals = [x for x in map(alt_total, {e["tag"] for e in eps} | {t for _, t, _, _ in dr}) if x is not None]
        best_total = min(totals + [chosen])
        regret_days.append({"day": label, "chosen": round(chosen, 4), "best": round(best_total, 4), "regret": round(chosen - best_total, 4), "msgs": len(dr)})
    reg_rel = (sum(x["regret"] for x in regret_days) / max(1e-9, sum(x["best"] for x in regret_days))) if regret_days else None

    badges = {"cache": {"status": status(hit_dev, cfg["hit_ok_pp"], cfg["hit_watch_pp"]), "dev": None if hit_dev is None else round(hit_dev, 2),
                        "expected": round(exp24_hit, 4), "measured": None if m_hit is None else round(m_hit, 4)},
              "tokens": {"status": status(tok_dev, cfg["tok_ok"], cfg["tok_watch"]), "dev": None if tok_dev is None else round(tok_dev, 4)},
              "cost": {"status": status(cost_dev, cfg["cost_ok"], cfg["cost_watch"]), "dev": None if cost_dev is None else round(cost_dev, 4),
                       "expected": round(exp24, 4), "measured": round(mea24, 4), "samples": nsamp},
              "regret": {"status": status(reg_rel, cfg["reg_ok"], cfg["reg_watch"]), "dev": None if reg_rel is None else round(reg_rel, 4)}}

    exp_today = sum(c for r, _, _, c in attr if r["ts"] / 1000 >= midnight)
    exp_week = sum(c for r, _, _, c in attr if r["ts"] / 1000 >= now - cfg["week_days"] * 86400)
    # calibration: what the key really billed over a rolling 24 h against what our arithmetic expected for the same traffic
    real = real_spend(keyuse, now)
    tok24 = sum(r["in"] + r["cw"] + r["cr"] for r, _, _, _ in attr if r["ts"] / 1000 >= now - 86400)
    real24 = real.get("d24")
    calib = {"usd": real24, "expected": round(exp24, 4) if exp24 else None, "tokens": tok24,
             "factor": round(real24 / exp24, 3) if real24 and exp24 else None,
             "eff_per_m": round(real24 / tok24 * 1e6, 4) if real24 and tok24 else None}
    sw = [x for x in decisions if x.get("switched")]
    log = sorted([{"ts": x["ts"], "type": "switch", "from": None, "to": x["current"], "text": x["reason"]} for x in sw[-30:]]
                 + [{"ts": e["ts"], "type": "price", "tag": e["tag"], "kind": e["kind"], "field": e.get("field"), "old": e.get("old"), "new": e.get("new")} for e in events[-60:]], key=lambda x: -x["ts"])[:80]
    last_dec = decisions[-1] if decisions else {}
    return {
        "schema": SCHEMA, "generated_at": now, "model": model, "node": cfg["node_label"], "error": None if eps else "no eligible endpoint",
        "freshness": {"decision": d.get("updated"), "last_message": max((r["ts"] for r in rows), default=0) / 1000 or None, "key_sample": keyuse[-1]["ts"] if keyuse else None,
                      "runs_24h": sum(1 for x in decisions if x["ts"] >= now - 86400), "first_decision": decisions[0]["ts"] if decisions else None},
        "overview": {"current": cur, "best": d.get("best"), "order": d.get("order"), "since": d.get("since"), "reason": d.get("reason"), "tolerated": d.get("tolerated"),
                     "warm": d.get("warm"), "profile": prof, "attribution": attribution, "last_run": {"ts": last_dec.get("ts"), "flanks": last_dec.get("flanks")},
                     "today": {**agg(today_rows), "expected_usd": round(exp_today, 4)}, "week": {**agg(week_rows), "expected_usd": round(exp_week, 4)}},
        "spend": ({k: round(float(keyuse[-1][k]), 4) for k in ("usage", "usage_daily", "usage_weekly", "usage_monthly", "limit_remaining") if k in keyuse[-1]}
                  | {"at": keyuse[-1]["ts"], "limit_reset": keyuse[-1].get("limit_reset"), "real": real, "calibration": calib}) if keyuse else None,
        "thresholds": {k: cfg[k] for k in ("rel_gain_min", "payback_max_min", "cache_ttl_min", "refresh_min", "top_k", "min_uptime", "min_context", "hit_ok_pp", "hit_watch_pp",
                                           "tok_ok", "tok_watch", "cost_ok", "cost_watch", "reg_ok", "reg_watch")},
        "rankings": rankings,
        "watch": watch(sd, now, cfg, prof, decisions, live, model),
        "forecast": forecast(rows, live, d, prof, now),
        "log": log,
        "control": {"badges": badges, "hit_hourly": hit_hourly, "hit_by_provider": hit_by_provider, "tokens_daily": tokens_daily, "cost_hourly": cost_series, "regret_daily": regret_days},
        "providers": [{k: e[k] for k in ("tag", "name", "quant", "in", "out", "cr", "ctx", "uptime")} for e in sorted(eps, key=lambda e: R.unit_cost(e, prof))[:25]],
    }


# ----------------------------------------------------------------------------- publish + cycle
publish = P.publish
publish_target = P.publish_target


STALE_KEYS = ("rankings", "watch", "forecast", "providers")  # sections that depend on usable live prices: kept from the last good document on error


def read_json(path: Path) -> dict:
    try:
        j = json.loads(path.read_text(encoding="utf-8"))
        return j if isinstance(j, dict) else {}
    except (OSError, ValueError):
        return {}


def export(model: str, cfg: dict, now: float, sd: Path, eps_all: list[dict] | None, out: Path | None = None) -> dict:
    """Write the sanitized document. `eps_all` = all fetched endpoints, None = the fetch failed. Never silently stale: on any problem the last
    known document is written again with "error" (class names only, nothing that could leak a path) and "error_at"."""
    out = out or sd / "router.json"
    prev = read_json(out)
    data, err = None, None
    if eps_all is None:
        err = "endpoint fetch failed"
    else:
        try:
            data = build(model, cfg, now, sd, eps_all)
            err = data.get("error")
        except Exception as e:  # a broken section must not keep the page on yesterday's numbers without saying so
            err = f"export failed ({type(e).__name__})"
    if data is None:
        data = prev or {"schema": SCHEMA, "model": model, "node": cfg["node_label"], "generated_at": now}
    elif err and prev:
        data.update({k: prev[k] for k in STALE_KEYS if prev.get(k)})
    if err:
        data["error"], data["error_at"] = err, now
    R.write_atomic(out, data)
    return data


def slug(model: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", model).strip("-.") or "model"


def export_all(cfg: dict, now: float, sd: Path, fetched: dict[str, list[dict]]) -> dict[str, str]:
    """router.json is the PRIMARY (first) model's document - the page and publish read it. With several models every model also gets its own
    router-<slug>.json, so models never overwrite each other. Returns {step: error class}."""
    errs: dict[str, str] = {}
    models = cfg["models"]
    for i, m in enumerate(models):
        try:
            if len(models) > 1:
                data = export(m, cfg, now, sd, fetched.get(m), out=sd / f"router-{slug(m)}.json")
                if i == 0:
                    R.write_atomic(sd / "router.json", data)
            else:
                export(m, cfg, now, sd, fetched.get(m))
        except Exception as e:
            errs["export:" + m] = type(e).__name__
    return errs


def acquire_lock(sd: Path, max_age: float = 600.0) -> bool:
    """One cycle at a time: the opencode plugin and the scheduled sampler must not append to the same jsonl files together."""
    f = sd / "cycle.lock"
    try:
        if time.time() - f.stat().st_mtime < max_age:
            return False
        f.unlink()  # older than max_age: the run that created it died
    except OSError:
        pass
    try:
        fd = os.open(f, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except OSError:
        return False
    try:
        os.write(fd, str(os.getpid()).encode())
    finally:
        os.close(fd)
    return True


def cycle(cfg: dict, now: float, sd: Path) -> dict[str, str]:
    """decide -> key sample -> export -> publish, every step on its own: one failing step never blocks the others. Returns {step: error class}."""
    errs: dict[str, str] = {}
    if not acquire_lock(sd):
        return errs  # another cycle is running right now; it writes the same files

    def step(name: str, fn):
        try:
            return fn()
        except Exception as e:
            errs[name] = type(e).__name__
            return None

    try:
        fetched: dict[str, list[dict]] = {}
        for m in cfg["models"]:
            eps = step("fetch:" + m, lambda m=m: R.fetch_endpoints(m))
            if eps is not None:
                fetched[m] = eps
                step("decide:" + m, lambda m=m: R.run_decide(m, cfg, now, sd, eps=fetched[m]))
                step("ranks:" + m, lambda m=m: sample_ranks(sd, now, cfg, fetched[m], m))
        step("keyusage", lambda: sample_key_usage(sd, now, cfg))
        errs.update(export_all(cfg, now, sd, fetched))
        step("publish", lambda: publish(sd, cfg))
        step("trending", lambda: TR.refresh_and_publish(sd, cfg, now))
    finally:
        try:
            (sd / "cycle.lock").unlink()
        except OSError:
            pass
    return errs


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("cmd", choices=("cycle", "export", "publish"))
    a = ap.parse_args(argv)
    sd = R.state_dir()
    cfg = load_cfg(sd)
    now = time.time()
    if a.cmd == "cycle":
        errs = cycle(cfg, now, sd)
        if errs:
            print("cycle steps failed: " + ", ".join(f"{k} ({v})" for k, v in errs.items()), file=sys.stderr)
    elif a.cmd == "export":
        fetched = {}
        for m in cfg["models"]:
            try:
                fetched[m] = R.fetch_endpoints(m)
            except Exception:
                pass  # export_all writes the explicit fetch error
        errs = export_all(cfg, now, sd, fetched)
        if errs:
            print("export failed: " + ", ".join(f"{k} ({v})" for k, v in errs.items()), file=sys.stderr)
    else:
        print("published" if publish(sd, cfg) else "not published (publish=false or ssh failed)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
