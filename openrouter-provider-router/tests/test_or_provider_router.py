"""Tests for scripts/models/or_provider_router.py (no network: endpoints injected, DB built in tmp)."""
from __future__ import annotations

import importlib.util
import json
import sqlite3
import time
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "scripts" / "models" / "or_provider_router.py"
spec = importlib.util.spec_from_file_location("or_provider_router", SRC)
R = importlib.util.module_from_spec(spec)
spec.loader.exec_module(R)

MODEL = "deepseek/deepseek-v4.1-flash-1017"
CFG = dict(R.DEFAULTS)
PROF = {"window": "week", "hit": 0.937, "out_ratio": 0.0025, "prompt_per_req": 250_000, "prompt_per_day": 770e6,
        "prompt_per_active_min": 770_000, "active_sessions": 1}


def ep(tag, i, o, cr, **kw):
    d = {"tag": tag, "name": tag.split("/")[0], "in": i, "out": o, "cr": cr, "ctx": 1_048_576, "maxout": 943_718,
         "quant": "fp8", "uptime": 100.0, "status": 0}
    d.update(kw)
    return d


OI = ep("open-inference/fp4", 0.0141, 0.144, 0.013)
MORPH = ep("morph/fp8", 0.022, 0.192, 0.004)
RELACE = ep("relace/fp4", 0.035, 0.5, 0.005)


def test_cache_read_price_dominates_ranking():
    """Open Inference has the cheapest in/out, but at 94 % cache hits Morph is cheaper overall."""
    assert R.unit_cost(MORPH, PROF) < R.unit_cost(RELACE, PROF) < R.unit_cost(OI, PROF)
    low = {**PROF, "hit": 0.3}  # at a low hit rate the ranking flips (breakeven ~47 %)
    assert R.unit_cost(OI, low) < R.unit_cost(MORPH, low)


def test_initial_pick_and_order():
    d = R.decide([OI, MORPH, RELACE], PROF, None, CFG, 1000.0)
    assert d["current"] == "morph/fp8" and d["order"][0] == "morph/fp8" and d["switched"]
    assert d["order"][1:] == ["relace/fp4", "open-inference/fp4"]


def test_hysteresis_band_holds_near_ties_in_both_directions():
    near = ep("morph/fp8", 0.035, 0.5, 0.0048)  # ~4 % cheaper than relace: inside the 10 % band
    d = R.decide([near, RELACE], PROF, {"current": "relace/fp4", "since": 0.0}, CFG, 5.0)
    assert d["current"] == "relace/fp4" and not d["switched"] and d["reason"].startswith("hold")
    d2 = R.decide([near, RELACE], PROF, {"current": "morph/fp8", "since": 0.0}, CFG, 5.0)  # and back: no flapping
    assert d2["current"] == "morph/fp8" and not d2["switched"]


def test_default_switches_at_once_when_gain_exceeds_band_no_waiting_time():
    better = ep("morph/fp8", 0.035, 0.5, 0.003)  # ~23 % cheaper
    d = R.decide([better, RELACE], PROF, {"current": "relace/fp4", "since": 999.0}, CFG, 1000.0)  # switched 1 s ago
    assert d["current"] == "morph/fp8" and d["switched"]


def test_warm_session_leaves_a_provider_that_just_became_expensive():
    pricey = ep("relace/fp4", 0.2, 2.0, 0.05)
    d = R.decide([MORPH, pricey], PROF, {"current": "relace/fp4", "since": 0.0}, CFG, 10.0)
    assert d["current"] == "morph/fp8"
    assert "relace/fp4" not in d["tolerated"] and "morph/fp8" in d["tolerated"]
    assert d["warm"]["relace/fp4"]["leave"] and d["warm"]["relace/fp4"]["payback_min"] <= CFG["payback_max_min"]


def test_warm_session_keeps_a_slightly_dearer_provider():
    near = ep("relace/fp4", 0.0235, 0.2, 0.0042)  # ~5 % dearer than morph: below the band
    d = R.decide([MORPH, near], PROF, None, CFG, 1.0)
    assert "relace/fp4" in d["tolerated"] and not d["warm"]["relace/fp4"]["leave"]


def test_payback_limit_keeps_warm_session_when_gain_is_slow():
    slow = {**PROF, "prompt_per_active_min": 1_000}  # barely any traffic: a switch never repays
    d = R.decide([MORPH, RELACE], slow, None, CFG, 1.0)
    assert d["warm"]["relace/fp4"]["leave"] is False and "relace/fp4" in d["tolerated"]


def test_vanished_current_switches_immediately():
    d = R.decide([MORPH, RELACE], PROF, {"current": "gone/fp8", "since": 999.0}, CFG, 1000.0)
    assert d["current"] == "morph/fp8" and "no longer eligible" in d["reason"]


def test_eligibility_filters():
    bad = [ep("a", 0.01, 0.1, 0.001, uptime=50.0), ep("b", 0.01, 0.1, 0.001, status=-2), ep("c", 0.01, 0.1, 0.001, ctx=8000),
           ep("d", 0.01, 0.1, 0.001, maxout=1000), ep("e", 0.0, 0.0, 0.0), MORPH]
    assert [e["tag"] for e in R.eligible(bad, CFG)] == ["morph/fp8"]
    assert [e["tag"] for e in R.eligible([MORPH, RELACE], {**CFG, "deny": ["morph"]})] == ["relace/fp4"]
    assert [e["tag"] for e in R.eligible([MORPH, RELACE], {**CFG, "allow": ["relace/fp4"]})] == ["relace/fp4"]


def test_no_eligible_endpoint_is_not_ok():
    assert R.decide([], PROF, None, CFG, 1.0)["ok"] is False


def test_miss_penalty_is_per_session():
    assert abs(R.miss_penalty(MORPH, PROF) - 250_000 * (0.022 - 0.004) / 1e6) < 1e-9


def test_price_flanks_detect_changes_appear_vanish_and_baseline():
    old = R.price_snapshot([MORPH, RELACE], CFG)
    assert R.price_flanks({}, old, 1.0) == []  # first run is the baseline, not an event
    new = R.price_snapshot([ep("morph/fp8", 0.03, 0.192, 0.004), OI], CFG)
    ev = R.price_flanks(old, new, 2.0)
    kinds = {(e["tag"], e["kind"], e.get("field")) for e in ev}
    assert ("morph/fp8", "change", "in") in kinds and ("open-inference/fp4", "appeared", None) in kinds and ("relace/fp4", "vanished", None) in kinds
    assert not [e for e in ev if e["tag"] == "morph/fp8" and e.get("field") in ("out", "cr")]  # unchanged fields stay silent
    assert R.price_flanks(new, new, 3.0) == []


def test_append_jsonl_trims_to_newest_lines(tmp_path):
    f = tmp_path / "x.jsonl"
    for i in range(50):
        R.append_jsonl(f, [{"i": i, "pad": "x" * 40}], max_bytes=800, keep=10)
    lines = f.read_text(encoding="utf-8").splitlines()
    assert len(lines) <= 20 and json.loads(lines[-1])["i"] == 49


def _db(tmp: Path, now: float) -> Path:
    p = tmp / "o.db"
    con = sqlite3.connect(p)
    con.execute("create table message (id text, session_id text, time_created integer, time_updated integer, data text)")

    def add(i, sid, t, model, prov, tok):
        data = {"role": "assistant", "modelID": model, "providerID": prov, "cost": 0.01,
                "tokens": {"input": tok[0], "output": tok[1], "reasoning": 0, "cache": {"read": tok[2], "write": 0}}}
        con.execute("insert into message values (?,?,?,?,?)", (i, sid, int(t * 1000), int(t * 1000), json.dumps(data)))

    add("1", "s1", now - 60, MODEL, "openrouter", (1000, 100, 9000))
    add("2", "s1", now - 3 * 86400, MODEL, "openrouter", (1000, 100, 9000))
    add("3", "s2", now - 60, MODEL, "opencode", (999999, 1, 1))  # other provider: ignored
    add("4", "s3", now - 60, "other/model", "openrouter", (999999, 1, 1))  # other model: ignored
    add("5", "s4", now - 20 * 86400, MODEL, "openrouter", (999999, 1, 1))  # outside window
    con.commit()
    con.close()
    return p


def test_read_usage_filters_and_profile_fallbacks(tmp_path):
    now = time.time()
    rows = R.read_usage(_db(tmp_path, now), MODEL, int((now - 7 * 86400) * 1000))
    assert [r["sid"] for r in rows] == ["s1", "s1"]
    s = R.summarize(rows)
    assert s["prompt"] == 20_000 and abs(s["hit"] - 0.9) < 1e-9
    prof = R.choose_profile(rows, CFG, now)  # far below min_profile_tokens -> configured defaults
    assert prof["window"] == "default" and prof["hit"] == CFG["fallback_hit"]
    prof2 = R.choose_profile(rows, {**CFG, "min_profile_tokens": 1000}, now)
    assert prof2["window"] == "today" and abs(prof2["hit"] - 0.9) < 1e-9 and prof2["prompt_per_active_min"] == 10_000


def _db_v2(tmp: Path, now: float, with_v1: bool = True) -> Path:
    """opencode V2 schema: `session_message` rows with `type` and the model nested; V1 `message` rows stay readable next to it."""
    p = tmp / "o2.db"
    con = sqlite3.connect(p)
    con.execute("create table session_message (id text, session_id text, type text, seq integer, time_created integer, time_updated integer, data text)")
    if with_v1:
        con.execute("create table message (id text, session_id text, time_created integer, time_updated integer, data text)")
        con.execute("insert into message values (?,?,?,?,?)", ("old", "s0", int((now - 90) * 1000), int((now - 90) * 1000), json.dumps(
            {"role": "assistant", "modelID": MODEL, "providerID": "openrouter", "cost": 0.01,
             "tokens": {"input": 0, "output": 0, "cache": {"read": 1000, "write": 0}}})))

    def add(i, sid, t, typ, model, prov, tok, cost=0.02):
        data = {"model": {"providerID": prov, "id": model}, "agent": "build", "cost": cost,
                "tokens": {"input": tok[0], "output": tok[1], "reasoning": 5, "cache": {"read": tok[2], "write": tok[3]}}}
        con.execute("insert into session_message values (?,?,?,?,?,?,?)", (i, sid, typ, 0, int(t * 1000), int(t * 1000), json.dumps(data)))

    add("1", "s1", now - 60, "assistant", MODEL, "openrouter", (1000, 100, 9000, 200))
    add("2", "s2", now - 60, "user", MODEL, "openrouter", (999999, 0, 0, 0))  # not an assistant turn: ignored
    add("3", "s3", now - 60, "assistant", MODEL, "opencode", (999999, 1, 1, 0))  # other provider: ignored
    add("4", "s4", now - 60, "assistant", "other/model", "openrouter", (999999, 1, 1, 0))  # other model: ignored
    con.commit()
    con.close()
    return p


def test_read_usage_reads_v2_session_message_and_keeps_v1_history(tmp_path):
    now = time.time()
    rows = R.read_usage(_db_v2(tmp_path, now), MODEL, int((now - 7 * 86400) * 1000))
    assert [r["sid"] for r in rows] == ["s0", "s1"]  # v1 history + v2 row, chronological
    r = rows[-1]
    assert (r["in"], r["cr"], r["cw"], r["out"]) == (1000, 9000, 200, 105)  # output + reasoning
    s = R.summarize(rows)
    assert s["prompt"] == 11_200 and abs(s["hit"] - 10_000 / 11_200) < 1e-9
    assert sum(r["cost"] for r in rows) == 0.03


def test_read_usage_works_on_a_v2_only_database(tmp_path):
    now = time.time()
    rows = R.read_usage(_db_v2(tmp_path, now, with_v1=False), MODEL, int((now - 7 * 86400) * 1000))
    assert [r["sid"] for r in rows] == ["s1"]



def test_run_decide_persists_logs_and_keeps_state(tmp_path, monkeypatch):
    now = time.time()
    monkeypatch.setenv("ORR_DB", str(_db(tmp_path, now)))
    first = R.run_decide(MODEL, CFG, now, tmp_path, eps=[MORPH, RELACE])
    doc = json.loads((tmp_path / "decision.json").read_text(encoding="utf-8"))
    assert doc["models"][MODEL]["current"] == first["current"] == "morph/fp8"
    assert doc["config"] == {"cache_ttl_min": CFG["cache_ttl_min"], "refresh_min": CFG["refresh_min"]}
    again = R.run_decide(MODEL, CFG, now + 600, tmp_path, eps=[MORPH, RELACE])
    assert not again["switched"] and again["since"] == first["since"] and again["flanks"] == 0
    # a price rise is an edge: logged and the decision is recomputed in the same run
    up = ep("morph/fp8", 0.5, 2.0, 0.2)
    third = R.run_decide(MODEL, CFG, now + 1200, tmp_path, eps=[up, RELACE])
    assert third["flanks"] == 3 and third["current"] == "relace/fp4" and third["switched"]
    events = [json.loads(ln) for ln in (tmp_path / "price_events.jsonl").read_text(encoding="utf-8").splitlines()]
    assert {e["field"] for e in events} == {"in", "out", "cr"}
    decisions = [json.loads(ln) for ln in (tmp_path / "decisions.jsonl").read_text(encoding="utf-8").splitlines()]
    assert len(decisions) == 3 and decisions[-1]["switched"] and decisions[-1]["flanks"] == 3
    # an empty endpoint list must not destroy the last good decision
    R.run_decide(MODEL, CFG, now + 1800, tmp_path, eps=[])
    kept = json.loads((tmp_path / "decision.json").read_text(encoding="utf-8"))["models"][MODEL]
    assert kept["current"] == "relace/fp4" and kept["last_error"]


def test_parse_endpoints_converts_per_million():
    doc = {"data": {"endpoints": [{"tag": "morph/fp8", "provider_name": "Morph", "context_length": 1048576, "max_completion_tokens": 943718,
                                   "pricing": {"prompt": "0.000000022", "completion": "0.000000192", "input_cache_read": "0.000000004"},
                                   "uptime_last_30m": 100, "status": 0}]}}
    e = R.parse_endpoints(doc)[0]
    assert (e["in"], e["out"], e["cr"]) == (0.022, 0.192, 0.004)


def test_env_overrides_config(monkeypatch, tmp_path):
    monkeypatch.setenv("ORR_REL_GAIN_MIN", "0.2")
    monkeypatch.setenv("ORR_DENY", '["morph"]')
    c = R.load_cfg(tmp_path)
    assert c["rel_gain_min"] == 0.2 and c["deny"] == ["morph"]
    assert "dwell_h" not in c  # no fixed waiting time anywhere


def test_install_shim_points_to_the_checkout_plugin_and_records_the_interpreter(tmp_path):
    import sys
    state = tmp_path / "state"
    dests = R.install_shim(tmp_path / "home", state, repo=tmp_path / "repo")
    dest = dests[0]
    text = dest.read_text(encoding="utf-8")
    assert dest.name == "or-provider-router.ts" and "export { default } from" in text
    assert ".opencode/plugins/or-provider-router.ts" in text
    assert len(dests) == 1  # no harness config root at tmp_path/repo -> only the user config
    assert (state / "python.txt").read_text(encoding="utf-8").strip() == sys.executable  # the plugin spawns THIS interpreter, not a bare "python"


def test_install_shim_also_covers_the_harness_isolated_config_root(tmp_path):
    """The TW harness server runs opencode with its own XDG_CONFIG_HOME: without a shim there it never routes (spend without a decision)."""
    repo = tmp_path / "repo"
    harness = repo / ".jarvis" / "runtime" / "forward" / "harness" / "xdg-server" / "opencode" / "plugins"
    harness.mkdir(parents=True)
    dests = R.install_shim(tmp_path / "home", tmp_path / "state", repo=repo)
    assert len(dests) == 2 and "xdg-server" in dests[1].parts and dests[1].name == "or-provider-router.ts"
    assert (harness / "or-provider-router.ts").exists()



def test_tolerance_is_measured_against_the_actual_destination_not_best():
    """A held provider A (4.8 % dearer than best B, inside the band) is where dropped sessions land (order[0]).
    C is 13 % dearer than B but only 8.7 % dearer than A: moving C -> A would not pass the band, so C is tolerated."""
    flat = lambda tag, x: ep(tag, x, 0.0, x)  # noqa: E731  unit cost == x, switching costs nothing -> only the band matters
    b, a, c = flat("b/fp8", 1.00), flat("a/fp8", 1.05), flat("c/fp8", 1.15)
    prof = {**PROF, "hit": 0.9, "out_ratio": 0.0}
    d = R.decide([b, a, c], prof, {"current": "a/fp8", "since": 0.0}, CFG, 10.0)
    assert d["best"] == "b/fp8" and d["current"] == "a/fp8" and d["order"][0] == "a/fp8" and not d["switched"]
    assert "c/fp8" in d["tolerated"], "a session on c would be moved to a (order[0]) - that switch is inside the band"
    assert d["warm"]["c/fp8"]["rel"] < CFG["rel_gain_min"]  # evaluated against the destination (a), not best (b)
    assert "b/fp8" in d["tolerated"] and "a/fp8" in d["tolerated"]
    # sanity: when the default really is the best, c is still dropped
    d2 = R.decide([b, c], prof, {"current": "b/fp8", "since": 0.0}, CFG, 10.0)
    assert "c/fp8" not in d2["tolerated"]


def test_endpoint_without_cache_price_gets_no_free_cache():
    """Review 2026-10-31: a missing cache-read price parsed as 0.0 made such a provider look ~10x cheaper than it is."""
    doc = {"data": {"endpoints": [
        {"tag": "nocache/fp8", "provider_name": "NoCache", "context_length": 1048576, "max_completion_tokens": 943718, "status": 0, "uptime_last_30m": 100,
         "pricing": {"prompt": "0.000000014", "completion": "0.000000144"}},
        {"tag": "freecache/fp8", "provider_name": "FreeCache", "context_length": 1048576, "max_completion_tokens": 943718, "status": 0, "uptime_last_30m": 100,
         "pricing": {"prompt": "0.000000014", "completion": "0.000000144", "input_cache_read": "0"}}]}}
    nocache, freecache = R.parse_endpoints(doc)
    assert nocache["cr"] == nocache["in"] == 0.014  # no discount
    assert freecache["cr"] == 0.0  # an explicit 0 stays 0
    d = R.decide([MORPH, nocache], PROF, None, CFG, 1.0)
    assert d["current"] == "morph/fp8"  # at 94 % cache hits the provider without a cache price loses


def test_failed_run_is_not_logged_as_a_switch(tmp_path, monkeypatch):
    now = time.time()
    monkeypatch.setenv("ORR_DB", str(_db(tmp_path, now)))
    R.run_decide(MODEL, CFG, now, tmp_path, eps=[MORPH, RELACE])
    R.run_decide(MODEL, CFG, now + 60, tmp_path, eps=[])
    R.run_decide(MODEL, CFG, now + 120, tmp_path, eps=[])
    rows = [json.loads(ln) for ln in (tmp_path / "decisions.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [r["switched"] for r in rows] == [True, False, False]
    assert rows[1]["reason"].startswith("failed run")


def test_one_failed_health_sample_does_not_flush_the_current_provider():
    """Review 2026-11-02: entering needs min_uptime, the CURRENT provider only leaves below leave_uptime (hysteresis)."""
    dip = {**MORPH, "uptime": 93.33}
    assert [e["tag"] for e in R.eligible([dip, RELACE], CFG)] == ["relace/fp4"]  # newcomer: out
    assert "morph/fp8" in [e["tag"] for e in R.eligible([dip, RELACE], CFG, "morph/fp8")]  # incumbent: stays
    assert "morph/fp8" not in [e["tag"] for e in R.eligible([{**MORPH, "uptime": 70.0}, RELACE], CFG, "morph/fp8")]  # really down: leaves
    d = R.decide([dip, RELACE], PROF, {"current": "morph/fp8", "since": 1.0}, CFG, 100.0)
    assert d["current"] == "morph/fp8" and not d["switched"] and "morph/fp8" in d["tolerated"]


def test_parse_endpoints_drops_nan_negative_and_tolerates_string_uptime():
    def raw(tag, prompt, uptime="99.5"):
        return {"tag": tag, "provider_name": tag, "pricing": {"prompt": prompt, "completion": "0.0000002", "input_cache_read": "0.00000001"},
                "context_length": 1_000_000, "max_completion_tokens": 100_000, "uptime_last_30m": uptime}
    eps = R.parse_endpoints({"data": {"endpoints": [raw("a", "0.00000002"), raw("nan", "nan"), raw("dyn", "-1"), raw("inf", "inf"), raw("s", "0.00000002", "oops")]}})
    assert [e["tag"] for e in eps] == ["a", "s"] and eps[0]["uptime"] == 99.5 and eps[1]["uptime"] is None


def test_failed_run_keeps_updated_so_the_plugin_stale_check_still_fires(tmp_path):
    sd = tmp_path
    now = 1_000_000.0
    R.run_decide(MODEL, CFG, now, sd, eps=[MORPH, RELACE])
    R.run_decide(MODEL, CFG, now + 600, sd, eps=[])
    m = json.loads((sd / "decision.json").read_text(encoding="utf-8"))["models"][MODEL]
    assert m["updated"] == now and m["checked"] == now + 600 and m["last_error"]


def test_invalid_env_value_is_reported_not_swallowed(monkeypatch, capsys, tmp_path):
    monkeypatch.setenv("ORR_DENY", "morph")  # not JSON
    cfg = R.load_cfg(tmp_path)
    assert cfg["deny"] == [] and "ORR_DENY" in capsys.readouterr().err
