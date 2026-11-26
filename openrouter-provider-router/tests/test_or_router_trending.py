# tiny-pages | Copyright 2026 Waldemar Fech | Apache-2.0 | https://github.com/WaldemarFech/tiny-pages`n// SPDX-License-Identifier: Apache-2.0
"""Trending step of the router cycle and the generalised publish (file name next to remote_path)."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest import mock

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts" / "models"
sys.path.insert(0, str(SCRIPTS))
import or_router_publish as P  # noqa: E402
import or_router_trending as TR  # noqa: E402

CFG = {"publish": True, "ssh_host": "pve.example.org", "ssh_port": 1001, "ct": 100, "ssh_key": "/k", "remote_path": "/srv/pages/deepseek-offpeak/router.json"}


def doc(day_date="2026-10-30"):
    return {"schema": 1, "source": {"day_date": day_date}, "day": [{"key": "a", "name": "A", "id": "x/a"}], "week": [{"key": "b", "name": "B", "id": "x/b"}], "free_models": []}


def test_stale_file_is_refreshed_written_with_state_and_published(tmp_path):
    calls = []

    def fake_refresh(state=None, now=None):
        calls.append(state)
        return doc()

    with mock.patch.object(P, "publish", return_value=True) as pub:
        assert TR.refresh_and_publish(tmp_path, CFG, 1_800_000_000.0, refresh=fake_refresh) == "published"
    assert json.loads((tmp_path / "models_trending.json").read_text(encoding="utf-8"))["source"]["day_date"] == "2026-10-30"
    state = json.loads((tmp_path / ".trending_state").read_text(encoding="utf-8"))
    assert state["day"][0]["key"] == "a" and calls == [{}]  # first run: empty diff state
    pub.assert_called_once_with(tmp_path, CFG, "models_trending.json")


def test_fresh_file_is_not_refreshed(tmp_path):
    (tmp_path / "models_trending.json").write_text("{}", encoding="utf-8")
    mtime = (tmp_path / "models_trending.json").stat().st_mtime
    (tmp_path / ".trending_published").write_text("ok", encoding="utf-8")

    def boom(**kw):
        raise AssertionError("must not fetch")

    assert TR.refresh_and_publish(tmp_path, CFG, mtime + 3600, refresh=boom) == "fresh"
    assert TR.refresh_and_publish(tmp_path, {**CFG, "trending_hours": 0.5}, mtime + 3600, refresh=lambda **kw: doc()) in ("local", "published")


def test_failed_fetch_keeps_the_old_file(tmp_path):
    f = tmp_path / "models_trending.json"
    f.write_text('{"old": true}', encoding="utf-8")

    def broken(**kw):
        raise RuntimeError("offline")

    try:
        TR.refresh_and_publish(tmp_path, {**CFG, "trending_hours": 0}, f.stat().st_mtime + 10, refresh=broken)
        raise AssertionError("expected the error to propagate to the cycle step")
    except RuntimeError:
        pass
    assert json.loads(f.read_text(encoding="utf-8")) == {"old": True}


def test_publish_filename_goes_next_to_remote_path_and_is_validated(tmp_path):
    (tmp_path / "models_trending.json").write_text("{}", encoding="utf-8")
    with mock.patch.object(P.subprocess, "run") as run:
        run.return_value = mock.Mock(returncode=0)
        assert P.publish(tmp_path, CFG, "models_trending.json") is True
        cmd = run.call_args[0][0]
        assert any("/srv/pages/deepseek-offpeak/models_trending.json" in part for part in cmd)
    with mock.patch.object(P.subprocess, "run") as run:
        for bad in ("../x.json", "a b.json", "x.json; rm", "x.txt", ""):
            assert P.publish(tmp_path, CFG, bad) is False
        run.assert_not_called()


def test_failed_publish_is_retried_next_cycle_without_fetching_again(tmp_path):
    (tmp_path / "models_trending.json").write_text("{}", encoding="utf-8")
    mtime = (tmp_path / "models_trending.json").stat().st_mtime

    def boom(**kw):
        raise AssertionError("must not fetch")

    with mock.patch.object(P, "publish", return_value=False):
        assert TR.refresh_and_publish(tmp_path, CFG, mtime + 60, refresh=boom) == "local"
    with mock.patch.object(P, "publish", return_value=True):
        assert TR.refresh_and_publish(tmp_path, CFG, mtime + 120, refresh=boom) == "published"
    assert TR.refresh_and_publish(tmp_path, CFG, mtime + 180, refresh=boom) == "fresh"
