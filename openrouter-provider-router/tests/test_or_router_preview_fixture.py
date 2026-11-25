"""Smoke test for scripts/models/or_router_preview_fixture.py: synthetic data for visual QA, offline, never touches the real state."""
from __future__ import annotations

import importlib.util
import json
import sys
import time
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts" / "models"
sys.path.insert(0, str(SCRIPTS))
spec = importlib.util.spec_from_file_location("or_router_preview_fixture", SCRIPTS / "or_router_preview_fixture.py")
F = importlib.util.module_from_spec(spec)
spec.loader.exec_module(F)


def test_fixture_builds_a_realistic_sanitized_dashboard_document(tmp_path, monkeypatch):
    real = tmp_path / "real-state"
    monkeypatch.setenv("ORR_STATE_DIR", str(real))
    monkeypatch.setenv("ORR_DB", str(tmp_path / "must-not-be-used.db"))

    def no_network(*a, **k):
        raise AssertionError("fixture must not use the network")

    monkeypatch.setattr(F.R, "fetch_endpoints", no_network)
    out = tmp_path / "out" / "router.json"
    out.parent.mkdir()
    now = time.time()
    F.generate(out, now=now)
    d = json.loads(out.read_text(encoding="utf-8"))
    assert d["synthetic"] is True and d["node"] == "FIXTURE" and d["error"] is None
    assert not real.exists() and not (tmp_path / "must-not-be-used.db").exists()  # real state dir / DB untouched
    import os
    assert os.environ["ORR_DB"] == str(tmp_path / "must-not-be-used.db")  # env restored
    assert len(d["providers"]) == 6
    hh = [x for x in d["control"]["hit_hourly"] if x["measured"] is not None and x["n"] >= 10]
    assert len(hh) >= 20 and all(0.80 <= x["measured"] <= 0.98 for x in hh)
    assert sum(1 for x in d["log"] if x["type"] == "price") >= 2 and sum(1 for x in d["log"] if x["type"] == "switch") >= 2  # initial + one real switch
    b = d["control"]["badges"]
    assert b["cost"]["samples"] >= 3 and b["cost"]["status"] in ("ok", "watch") and b["cache"]["status"] != "na"
    assert len(d["forecast"]["series"]) == 72 and d["rankings"]["sessions"]
    text = json.dumps(d)
    for forbidden in ("ses_fx", "orr-fixture", str(tmp_path), "Users"):
        assert forbidden not in text, forbidden


def test_fixture_refuses_the_real_state_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("ORR_STATE_DIR", str(tmp_path))
    try:
        F.generate(tmp_path / "router.json", now=time.time(), hours=1)
    except SystemExit:
        return
    raise AssertionError("must refuse")
