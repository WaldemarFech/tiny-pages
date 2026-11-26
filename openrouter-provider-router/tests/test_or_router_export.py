# tiny-pages | Copyright 2026 Waldemar Fech | Apache-2.0 | https://github.com/WaldemarFech/tiny-pages`n// SPDX-License-Identifier: Apache-2.0
"""Tests for scripts/models/or_router_export.py: sections, expectation-vs-measurement control, sanitization."""
from __future__ import annotations

import importlib.util
import json
import sqlite3
import sys
import os
import time
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts" / "models"
sys.path.insert(0, str(SCRIPTS))
spec = importlib.util.spec_from_file_location("or_router_export", SCRIPTS / "or_router_export.py")
X = importlib.util.module_from_spec(spec)
spec.loader.exec_module(X)
R = X.R

MODEL = "deepseek/deepseek-v4.1-flash-1017"


def ep(tag, i, o, cr):
    return {"tag": tag, "name": tag.split("/")[0], "in": i, "out": o, "cr": cr, "ctx": 1_048_576, "maxout": 943_718, "quant": "fp8", "uptime": 100.0, "status": 0}


MORPH, RELACE = ep("morph/fp8", 0.022, 0.192, 0.004), ep("relace/fp4", 0.035, 0.5, 0.005)


def make_db(tmp: Path, rows: list[tuple]) -> Path:
    p = tmp / "o.db"
    con = sqlite3.connect(p)
    con.execute("create table message (id text, session_id text, time_created integer, time_updated integer, data text)")
    for i, (sid, t, inp, out, cr) in enumerate(rows):
        data = {"role": "assistant", "modelID": MODEL, "providerID": "openrouter", "cost": 0.0,
                "tokens": {"input": inp, "output": out, "reasoning": 0, "cache": {"read": cr, "write": 0}}}
        con.execute("insert into message values (?,?,?,?,?)", (str(i), sid, int(t * 1000), int(t * 1000), json.dumps(data)))
    con.commit()
    con.close()
    return p


def jl(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


def scenario(tmp_path, now, cr_share=0.9, spend_factor=1.0):
    """10 requests of session ses_secret_1 over the last hour on morph; usage samples every 10 min."""
    rows = [("ses_secret_1", now - 3000 + 300 * k, 1_000_000 * (1 - cr_share), 1000, 1_000_000 * cr_share) for k in range(10)]
    db = make_db(tmp_path, rows)
    exp_per_msg = (1_000_000 * (1 - cr_share) * MORPH["in"] + 1000 * MORPH["out"] + 1_000_000 * cr_share * MORPH["cr"]) / 1e6
    jl(tmp_path / "usage.jsonl", [{"ts": now - 3500, "sid": "ses_secret_1", "model": MODEL, "tag": "morph/fp8"}])
    jl(tmp_path / "decisions.jsonl", [{"ts": now - 3600, "model": MODEL, "current": "morph/fp8", "best": "morph/fp8", "switched": True, "reason": "initial",
                                       "flanks": 0, "top": [], "hit": 0.9, "out_ratio": 0.001, "prompt_per_day": 5e8, "window": "week"}])
    ks = []
    usage = 10.0
    for k in range(6):  # samples at -3100 ... -100: each interval covers exactly two requests
        ks.append({"ts": now - 3100 + 600 * k, "usage": usage})
        usage += 2 * exp_per_msg * spend_factor
    jl(tmp_path / "keyusage.jsonl", ks)
    return db


def test_control_expected_matches_measured_when_model_is_right(tmp_path, monkeypatch):
    now = time.time()
    db = scenario(tmp_path, now)
    cfg = {**X.load_cfg(tmp_path), "min_profile_tokens": 1}
    d = X.build(MODEL, cfg, now, tmp_path, [MORPH, RELACE], db=db)
    b = d["control"]["badges"]
    assert b["cache"]["status"] == "ok" and abs(b["cache"]["measured"] - 0.9) < 1e-3
    assert b["cost"]["status"] == "ok" and b["cost"]["samples"] >= 3 and b["cost"]["dev"] < 0.05
    assert d["control"]["hit_by_provider"][0]["tag"] == "morph/fp8"
    assert d["control"]["regret_daily"][0]["regret"] == 0.0 and b["regret"]["status"] == "ok"


def test_control_flags_cost_deviation_and_cache_deviation(tmp_path):
    now = time.time()
    db = scenario(tmp_path, now, cr_share=0.6, spend_factor=2.0)  # measured spend double, hit 60 % vs expected 90 %
    d = X.build(MODEL, {**X.load_cfg(tmp_path), "min_profile_tokens": 1}, now, tmp_path, [MORPH, RELACE], db=db)
    b = d["control"]["badges"]
    assert b["cost"]["status"] == "off" and b["cache"]["status"] == "off"


def test_regret_when_served_provider_is_not_the_cheapest(tmp_path):
    now = time.time()
    db = scenario(tmp_path, now)
    jl(tmp_path / "usage.jsonl", [{"ts": now - 3500, "sid": "ses_secret_1", "model": MODEL, "tag": "relace/fp4"}])
    d = X.build(MODEL, {**X.load_cfg(tmp_path), "min_profile_tokens": 1}, now, tmp_path, [MORPH, RELACE], db=db)
    day = d["control"]["regret_daily"][0]
    assert day["regret"] > 0 and day["chosen"] > day["best"]
    assert d["control"]["badges"]["regret"]["status"] in ("watch", "off")


def test_na_states_without_data(tmp_path):
    db = make_db(tmp_path, [])
    d = X.build(MODEL, X.load_cfg(tmp_path), time.time(), tmp_path, [MORPH, RELACE], db=db)
    assert {v["status"] for v in d["control"]["badges"].values()} == {"na"}
    assert d["rankings"]["today"]["empty"] and d["rankings"]["week"]["thin"]


def test_rankings_forecast_sessions_and_log_sections(tmp_path):
    now = time.time()
    db = scenario(tmp_path, now)
    jl(tmp_path / "price_events.jsonl", [{"ts": now - 100, "model": MODEL, "tag": "morph/fp8", "kind": "change", "field": "in", "old": 0.02, "new": 0.022}])
    d = X.build(MODEL, {**X.load_cfg(tmp_path), "min_profile_tokens": 1}, now, tmp_path, [MORPH, RELACE], db=db)
    assert d["rankings"]["week"]["winner"] == "morph/fp8" and d["rankings"]["default"]["rows"][0]["delta"] == 0
    assert len(d["forecast"]["series"]) == 72 and {s["kind"] for s in d["forecast"]["series"]} == {"actual", "forecast"}
    assert d["rankings"]["sessions"][0]["n"] == 1 and d["rankings"]["sessions"][0]["msgs"] == 10
    assert d["log"][0]["type"] in ("price", "switch") and any(x["type"] == "price" for x in d["log"])
    assert d["providers"][0]["tag"] == "morph/fp8"


def test_export_is_sanitized(tmp_path, monkeypatch):
    now = time.time()
    db = scenario(tmp_path, now)
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-SECRETKEY123")
    d = X.build(MODEL, {**X.load_cfg(tmp_path), "min_profile_tokens": 1}, now, tmp_path, [MORPH, RELACE], db=db)
    text = json.dumps(d)
    for forbidden in ("ses_secret_1", "SECRETKEY123", "sk-or", "Users", "auth.json", "C:\\\\", "/home/"):
        assert forbidden not in text, forbidden


def test_sample_key_usage_appends_and_never_stores_the_key(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-SECRETKEY123")

    class Resp:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self, *a): return json.dumps({"data": {"usage": 12.5, "limit": 20, "usage_daily": 2.5, "usage_weekly": 13.0,
                                                        "usage_monthly": None, "limit_remaining": 7.5}}).encode()

    seen = {}

    def opener(req, timeout=0):
        seen["auth"] = req.get_header("Authorization")
        return Resp()

    cfg = X.load_cfg(tmp_path)
    assert X.sample_key_usage(tmp_path, 1000.0, cfg, opener=opener)
    assert not X.sample_key_usage(tmp_path, 1010.0, cfg, opener=opener)  # too soon after the last sample
    rows = X.read_jsonl(tmp_path / "keyusage.jsonl")
    assert rows == [{"ts": 1000.0, "usage": 12.5, "limit": 20.0, "usage_daily": 2.5, "usage_weekly": 13.0, "limit_remaining": 7.5}]
    assert "SECRETKEY123" not in (tmp_path / "keyusage.jsonl").read_text(encoding="utf-8")
    assert seen["auth"] == "Bearer sk-or-SECRETKEY123"


def test_export_carries_real_spend_and_attribution_coverage(tmp_path):
    """The page must show what OpenRouter actually billed (key aggregates) next to the router arithmetic, and say how much of the
    expectation rests on true provider attribution instead of a guess (2026-11-01: the owner compared real $2.45 with a $0.0007 forecast)."""
    now = time.time()
    db = scenario(tmp_path, now)  # already leaves keyusage rows up to now-100: sample after them, past the min gap
    cfg = {**X.load_cfg(tmp_path), "min_profile_tokens": 1}
    X.sample_key_usage(tmp_path, now + 30, cfg, opener=lambda req, timeout=0: _resp({"usage": 29.0, "usage_daily": 1.1}))
    X.sample_key_usage(tmp_path, now + 150, cfg, opener=lambda req, timeout=0: _resp({"usage": 31.73, "usage_daily": 2.69, "usage_monthly": 2.69, "limit": 20, "limit_remaining": 17.31}))
    d = X.build(MODEL, cfg, now, tmp_path, [MORPH, RELACE], db=db)
    sp = d["spend"]
    assert sp["at"] == now + 150 and sp["usage"] == 31.73 and sp["usage_daily"] == 2.69 and sp["usage_monthly"] == 2.69
    assert "limit" not in sp and sp["limit_remaining"] == 17.31  # the total budget is not published, only what is left
    assert sp["real"]["usage"] == 31.73 and sp["real"]["d24"] is None  # samples cover < 24 h: no invented number
    a = d["overview"]["attribution"]
    assert a["tokens"] > 0 and a["share"] is not None and 0 <= a["share"] <= 1
    # key secrets never leak into the document
    assert "SECRETKEY" not in json.dumps(d)


class _resp:
    def __init__(self, data): self.data = data
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def read(self, *a): return json.dumps({"data": self.data}).encode()



def test_publish_is_off_by_default(tmp_path):
    assert X.publish(tmp_path, X.load_cfg(tmp_path)) is False


# ----------------------------------------------------------------------------- review findings 2026-10-31
DEAD = {**MORPH, "tag": "dead/fp8", "name": "dead", "status": -2}  # not eligible


def test_build_survives_no_eligible_endpoint_and_flags_it(tmp_path):
    now = time.time()
    db = scenario(tmp_path, now)
    cfg = {**X.load_cfg(tmp_path), "min_profile_tokens": 1}
    d = X.build(MODEL, cfg, now, tmp_path, [DEAD], db=db)  # used to raise IndexError in session_rankings / regret
    assert d["error"] == "no eligible endpoint" and d["rankings"]["sessions"] == [] and d["providers"] == []
    assert X.session_rankings([], [], None) == [] and X.ranking([], {"hit": 0.9, "out_ratio": 0.001}, 0, None)["winner"] is None
    ok = X.build(MODEL, cfg, now, tmp_path, [MORPH, RELACE], db=db)
    assert ok["error"] is None


def test_cycle_steps_are_isolated_and_router_json_never_goes_silently_stale(tmp_path, monkeypatch):
    now = time.time()
    monkeypatch.setenv("ORR_DB", str(scenario(tmp_path, now)))
    cfg = {**X.load_cfg(tmp_path), "min_profile_tokens": 1}
    monkeypatch.setattr(R, "fetch_endpoints", lambda model, **kw: [MORPH, RELACE])
    monkeypatch.setattr(X, "sample_key_usage", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    errs = X.cycle(cfg, now, tmp_path)  # key sampling blew up: the rest still ran
    assert set(errs) == {"keyusage"}
    good = json.loads((tmp_path / "router.json").read_text(encoding="utf-8"))
    assert good["error"] is None and good["rankings"]["week"]["winner"] == "morph/fp8"
    # now nothing is eligible: last known data stays, the error is explicit
    monkeypatch.setattr(R, "fetch_endpoints", lambda model, **kw: [DEAD])
    monkeypatch.setattr(R, "run_decide", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("decide broke")))
    errs = X.cycle(cfg, now + 600, tmp_path)
    assert "decide:" + MODEL in errs
    bad = json.loads((tmp_path / "router.json").read_text(encoding="utf-8"))
    assert bad["error"] == "no eligible endpoint" and bad["error_at"] == now + 600
    assert bad["rankings"]["week"]["winner"] == "morph/fp8" and bad["providers"][0]["tag"] == "morph/fp8"
    # the endpoint fetch itself fails: still an explicit error, still the last known data
    monkeypatch.setattr(R, "fetch_endpoints", lambda model, **kw: (_ for _ in ()).throw(OSError("net down")))
    X.cycle(cfg, now + 1200, tmp_path)
    bad2 = json.loads((tmp_path / "router.json").read_text(encoding="utf-8"))
    assert "fetch" in bad2["error"] and "net down" not in bad2["error"] and bad2["providers"][0]["tag"] == "morph/fp8"


def test_cycle_without_any_previous_data_still_writes_an_error_document(tmp_path, monkeypatch):
    monkeypatch.setenv("ORR_DB", str(make_db(tmp_path, [])))
    monkeypatch.setattr(R, "fetch_endpoints", lambda model, **kw: [DEAD])
    X.cycle(X.load_cfg(tmp_path), time.time(), tmp_path)
    doc = json.loads((tmp_path / "router.json").read_text(encoding="utf-8"))
    assert doc["error"] == "no eligible endpoint"


def test_past_requests_use_the_prices_valid_then_and_survive_ineligibility(tmp_path):
    now = time.time()
    db = scenario(tmp_path, now)
    # morph served at 0.022/0.192/0.004 (logged in the decision row); today it is 10x dearer and by now even ineligible
    jl(tmp_path / "decisions.jsonl", [{"ts": now - 3600, "model": MODEL, "current": "morph/fp8", "best": "morph/fp8", "switched": True, "reason": "initial", "flanks": 0,
                                       "top": [], "hit": 0.9, "out_ratio": 0.001, "prompt_per_day": 5e8, "window": "week",
                                       "prices": {"morph/fp8": [0.022, 0.192, 0.004], "relace/fp4": [0.035, 0.5, 0.005]}}])
    cfg = {**X.load_cfg(tmp_path), "min_profile_tokens": 1}
    risen = {**MORPH, "in": 0.22, "out": 1.92, "cr": 0.04}
    d = X.build(MODEL, cfg, now, tmp_path, [risen, RELACE], db=db)
    hp = {x["tag"]: x for x in d["control"]["hit_by_provider"]}
    per_msg = (100_000 * 0.022 + 1000 * 0.192 + 900_000 * 0.004) / 1e6
    assert abs(hp["morph/fp8"]["usd_expected"] - 10 * per_msg) < 1e-3, "priced at today's prices instead of the prices valid at serving time"
    assert d["control"]["badges"]["cost"]["status"] == "ok"
    gone = X.build(MODEL, cfg, now, tmp_path, [{**risen, "status": -2}, RELACE], db=db)  # morph currently ineligible: its history must not vanish
    hp2 = {x["tag"]: x for x in gone["control"]["hit_by_provider"]}
    assert hp2["morph/fp8"]["msgs"] == 10 and abs(hp2["morph/fp8"]["usd_expected"] - 10 * per_msg) < 1e-3
    assert gone["control"]["regret_daily"] and gone["control"]["regret_daily"][0]["msgs"] == 10


def test_run_decide_logs_a_compact_price_table_per_decision(tmp_path, monkeypatch):
    monkeypatch.setenv("ORR_DB", str(make_db(tmp_path, [])))
    R.run_decide(MODEL, X.load_cfg(tmp_path), time.time(), tmp_path, eps=[MORPH, RELACE, DEAD])
    row = X.read_jsonl(tmp_path / "decisions.jsonl")[-1]
    assert row["prices"] == {"morph/fp8": [0.022, 0.192, 0.004], "relace/fp4": [0.035, 0.5, 0.005]}


def test_day_windows_use_calendar_arithmetic_across_dst(tmp_path):
    from datetime import date, datetime
    from zoneinfo import ZoneInfo
    tz = ZoneInfo("Europe/Berlin")
    a, b, label = X.day_window(date(2026, 10, 25), 0, tz)  # clocks went back that day: 25 h
    assert label == "2026-10-25" and b - a == 25 * 3600
    a2, b2, label2 = X.day_window(date(2026, 10, 28), 3, tz)
    assert label2 == "2026-10-25" and a2 == a and b2 == b  # midnight - 3 * 86400 would be one hour off
    assert datetime.fromtimestamp(a, tz).hour == 0 and datetime.fromtimestamp(a2, tz).minute == 0
    # naive/local flavour (whatever the machine tz is): consecutive windows tile without gap or overlap
    ws = [X.day_window(date(2026, 10, 28), k) for k in range(6, -1, -1)]
    assert all(w1[1] == w2[0] for w1, w2 in zip(ws, ws[1:])) and ws[-1][2] == "2026-10-28"


def test_cache_badge_expects_the_time_weighted_assumption_not_the_latest(tmp_path):
    now = time.time()
    db = scenario(tmp_path, now)  # measured hit rate: 0.9
    rows = [{"ts": now - 86400 - 10, "model": MODEL, "current": "morph/fp8", "best": "morph/fp8", "switched": True, "reason": "initial", "flanks": 0, "top": [],
             "hit": 0.95, "out_ratio": 0.001, "prompt_per_day": 5e8, "window": "week"},
            {"ts": now - 43200, "model": MODEL, "current": "morph/fp8", "best": "morph/fp8", "switched": False, "reason": "x", "flanks": 0, "top": [],
             "hit": 0.85, "out_ratio": 0.001, "prompt_per_day": 5e8, "window": "week"}]
    jl(tmp_path / "decisions.jsonl", rows)
    d = X.build(MODEL, {**X.load_cfg(tmp_path), "min_profile_tokens": 1}, now, tmp_path, [MORPH, RELACE], db=db)
    c = d["control"]["badges"]["cache"]
    assert abs(c["expected"] - 0.90) < 0.002, "24 h mean of 0.95 (first half) and 0.85 (second half), not exp_hit(now)=0.85"
    assert c["status"] == "ok" and c["dev"] < 0.5


# ----------------------------------------------------------------------------- publish hardening + several models
def _pub_cfg(tmp_path, **kw):
    (tmp_path / "router.json").write_text("{}", encoding="utf-8")
    return {**X.load_cfg(tmp_path), "publish": True, **kw}


def test_publish_rejects_malicious_remote_path_without_running_anything(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(X.P.subprocess, "run", lambda *a, **k: calls.append(a) or (_ for _ in ()).throw(AssertionError("must not run")))
    for bad in ("/srv/pages/x/router.json'; rm -rf / #", "/srv/pages/x/router.json; id", "/srv/pages/a b/router.json", "/srv/pages/../etc/passwd.json",
                "/etc/cron.d/x.json", "/srv/pages/x/router.json\n", "/srv/pages/x/router.sh", "relative/router.json", "/srv/pages/$(id).json", "/srv/pages/`id`.json"):
        assert X.publish(tmp_path, _pub_cfg(tmp_path, remote_path=bad)) is False, bad
    for k, bad in (("ssh_host", "-oProxyCommand=x"), ("ssh_host", "h; id"), ("ssh_host", "a b"), ("ct", "100; id"), ("ct", 0), ("ct", True),
                   ("ssh_port", 70000), ("ssh_port", "22 -v"), ("ssh_key", "-oFoo")):
        assert X.publish(tmp_path, _pub_cfg(tmp_path, **{k: bad})) is False, (k, bad)
    assert calls == []


def test_publish_builds_a_quoted_command_for_a_valid_config(tmp_path, monkeypatch):
    seen = {}

    class Done:
        returncode = 0

    monkeypatch.setattr(X.P.subprocess, "run", lambda argv, **k: seen.update(argv=argv, flags=k.get("creationflags")) or Done())
    assert X.publish(tmp_path, _pub_cfg(tmp_path)) is True
    argv = seen["argv"]
    assert argv[0] == "ssh" and argv[-2] == "root@your-vps.example"
    if os.name == "nt":
        assert seen["flags"] == 0x08000000  # CREATE_NO_WINDOW: the detached cycle must not pop a CMD window per publish
    remote = argv[-1]
    assert remote.startswith("pct exec 100 -- sh -c ") and "/srv/pages/deepseek-offpeak/router.json.tmp" in remote
    import shlex
    assert shlex.split(remote)[-1] == "cat > /srv/pages/deepseek-offpeak/router.json.tmp && mv /srv/pages/deepseek-offpeak/router.json.tmp /srv/pages/deepseek-offpeak/router.json"


def test_export_all_writes_router_json_for_the_primary_only(tmp_path, monkeypatch):
    now = time.time()
    monkeypatch.setenv("ORR_DB", str(make_db(tmp_path, [])))
    m2 = "other/model-x"
    cfg = {**X.load_cfg(tmp_path), "models": [MODEL, m2]}
    errs = X.export_all(cfg, now, tmp_path, {MODEL: [MORPH, RELACE], m2: [RELACE]})
    assert errs == {}
    primary = json.loads((tmp_path / "router.json").read_text(encoding="utf-8"))
    p2 = json.loads((tmp_path / "router-other-model-x.json").read_text(encoding="utf-8"))
    p1 = json.loads((tmp_path / "router-deepseek-deepseek-v4.1-flash-1017.json").read_text(encoding="utf-8"))
    assert primary["model"] == MODEL == p1["model"] and p2["model"] == m2
    # single model: no extra per-model file
    single = tmp_path / "single"
    X.export_all({**cfg, "models": [MODEL]}, now, single, {MODEL: [MORPH]})
    assert [f.name for f in single.iterdir()] == ["router.json"]

# --------------------------------------------------------------- real billing windows, price snapshots, watchlist
def test_real_spend_uses_rolling_windows_not_the_utc_day(tmp_path):
    """usage_daily resets at UTC midnight; the page needs real 24 h / today / week deltas of the key counter (owner 2026-11-02)."""
    now = time.time()
    rows = [{"ts": now - (200 - k) * 3600, "usage": 10.0 + k * 0.01} for k in range(201)]  # hourly samples, 0.01 $/h
    r = X.real_spend(rows, now)
    assert r["usage"] == 12.0 and r["coverage_h"] == 200.0
    assert abs(r["d24"] - 0.24) < 0.02 and abs(r["week"] - 1.68) < 0.02
    assert 0.0 <= r["today"] <= 3.0  # interpolated at local midnight, whatever the clock says now
    # a sampling gap longer than 6 h (idle machine) still yields the counter delta, but flagged: the page then shows the window as approximate
    gap = [{"ts": now - 2 * 86400, "usage": 10.0}, {"ts": now - 2 * 3600, "usage": 18.0}, {"ts": now, "usage": 19.0}]
    g = X.real_spend(gap, now)
    assert g["d24"] == 9.0 and "d24" in g["approx"]
    assert X.real_spend([{"ts": now - 600, "usage": 4.0}], now)["d24"] is None  # counter starts today: no history
    assert X.real_spend([], now) == {}


def test_sample_ranks_stores_raw_prices_and_respects_the_gap(tmp_path):
    cfg = X.load_cfg(tmp_path)
    now = time.time()
    assert X.sample_ranks(tmp_path, now, cfg, [MORPH, RELACE], MODEL) is True
    assert X.sample_ranks(tmp_path, now + 10, cfg, [MORPH, RELACE], MODEL) is False  # too soon (ranks_min_gap_s)
    rows = X.read_jsonl(tmp_path / "ranks.jsonl")
    assert len(rows) == 1 and rows[0]["model"] == MODEL and rows[0]["ts"] == round(now, 1)
    assert rows[0]["p"]["morph/fp8"] == [MORPH["in"], MORPH["out"], MORPH["cr"]]  # raw list prices, not unit costs
    assert X.sample_ranks(tmp_path, now + 1000, cfg, [], MODEL) is False  # nothing fetched -> no row


def test_watch_lists_rank1_providers_and_drops_a_stale_one(tmp_path):
    """Every provider that was cheapest comes on the list; after watch_days without rank 1 it disappears (owner rule)."""
    now = 1_800_000_000.0
    day = 86400.0

    def row(ts, prices):
        return {"ts": ts, "model": MODEL, "p": prices}

    jl(tmp_path / "ranks.jsonl", [
        row(now - 8 * day, {"a": [1, 1, 0.1], "c": [0.01, 0.01, 0.001]}),  # c was cheapest, but 8 days ago -> off the list
        row(now - 6 * day, {"a": [1, 1, 0.1], "b": [2, 1, 0.2]}),           # a cheapest
        row(now - 6 * day + 3600, {"a": [1, 1, 0.1], "b": [2, 1, 0.2]}),
        row(now - 3600, {"a": [3, 1, 0.3], "b": [2, 1, 0.2]}),              # a got expensive -> b cheapest
        row(now - 300, {"a": [3, 1, 0.3], "b": [2, 1, 0.2]}),
    ])
    prof = {"hit": 0.9, "out_ratio": 0.001}
    w = X.watch(tmp_path, now, X.load_cfg(tmp_path), prof, [], {}, MODEL)
    tags = [t["tag"] for t in w["tags"]]
    assert tags == ["b", "a"]  # most recent rank 1 first, c dropped by the 7-day rule
    b, a = w["tags"]
    assert w["rank1"] == "b" and b["rank"] == 1 and b["since"] == now - 3600
    assert a["last_rank1"] == now - 3600 and a["rank1"] == [[now - 6 * day, now - 3600]]
    assert a["cheap"]  # a's early price is well below its own median -> a cheap phase
    assert not b["cheap"]
    assert w["series"]["b"] and w["series"]["a"] and "c" not in w["series"]
    assert b["hours"][1] is None or isinstance(b["hours"][1], float)


def test_export_carries_watch_and_calibration(tmp_path):
    now = time.time()
    db = scenario(tmp_path, now)
    cfg = {**X.load_cfg(tmp_path), "min_profile_tokens": 1}
    ks = [{"ts": now - 7200 - (23 - k) * 3600, "usage": 9.0 + k * 0.02} for k in range(24)]  # 24 h of near-hourly samples
    jl(tmp_path / "keyusage.jsonl", ks + X.read_jsonl(tmp_path / "keyusage.jsonl"))
    X.sample_ranks(tmp_path, now, cfg, [MORPH, RELACE], MODEL)
    d = X.build(MODEL, cfg, now, tmp_path, [MORPH, RELACE], db=db)
    assert d["watch"]["rank1"] in ("morph/fp8", "relace/fp4")
    assert "morph/fp8" in d["watch"]["series"]
    assert d["watch"]["tags"][0]["samples"] >= 1
    cal = d["spend"]["calibration"]
    assert cal["tokens"] > 0 and cal["usd"] is not None and cal["factor"] is not None
    assert d["spend"]["real"]["d24"] is not None

def test_cycle_lock_keeps_plugin_and_scheduled_run_apart(tmp_path):
    assert X.acquire_lock(tmp_path) is True
    assert X.acquire_lock(tmp_path) is False  # second process skips instead of writing the same files
    lock = tmp_path / "cycle.lock"
    lock.write_text("999", encoding="utf-8")
    os.utime(lock, (time.time() - 3600, time.time() - 3600))  # a crashed run leaves a stale lock
    assert X.acquire_lock(tmp_path) is True
