# tiny-pages | Copyright 2026 Waldemar Fech | Apache-2.0 | https://github.com/WaldemarFech/tiny-pages`n// SPDX-License-Identifier: Apache-2.0
"""Runtime installer: temporary-path refusal, task XML, install/verify/uninstall with a fake process runner (no real schtasks)."""
from __future__ import annotations

import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest import mock

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts" / "models"
sys.path.insert(0, str(SCRIPTS))
import or_router_runtime as RT  # noqa: E402

NS = {"t": "http://schemas.microsoft.com/windows/2004/02/mit/task"}


@pytest.fixture
def pytest_tmp_is_not_temporary(monkeypatch):
    """pytest's tmp_path lives under AppData/Local/Temp, which the real marker list (rightly) treats as temporary."""
    monkeypatch.setattr(RT, "TEMP_MARKERS", ("/jarvis-worktrees/",))


def fake_repo(tmp_path, name="main-checkout"):
    repo = tmp_path / name
    (repo / "scripts" / "models").mkdir(parents=True)
    (repo / ".opencode" / "plugins").mkdir(parents=True)
    (repo / "scripts" / "models" / "or_router_export.py").write_text("# stub\n", encoding="utf-8")
    (repo / ".opencode" / "plugins" / "or-provider-router.ts").write_text("// stub\n", encoding="utf-8")
    return repo


def test_temporary_paths_are_recognised():
    assert RT.is_temporary("C:\\Users\\example\\jarvis-worktrees\\wkb-dashboard")
    assert RT.is_temporary("C:/Users/example/.claude/worktrees/x")
    assert RT.is_temporary("C:/Users/example/AppData/Local/Temp/claude/foo/scratchpad/x")
    assert not RT.is_temporary("C:/Users/example/Desktop/Jarvis")
    assert not RT.is_temporary("C:/Users/example/jarvis-live/releases/abc")


def test_task_xml_is_valid_and_runs_the_cycle_every_interval_without_overlap():
    xml = RT.render_task_xml("C:\\Python\\pythonw.exe", Path("C:/repo/scripts/models/or_router_export.py"), 10)
    root = ET.fromstring(xml.replace('encoding="UTF-16"', 'encoding="UTF-8"'))
    assert root.find(".//t:Exec/t:Command", NS).text == "C:\\Python\\pythonw.exe"
    args = root.find(".//t:Exec/t:Arguments", NS).text
    assert args.startswith("-B ") and args.endswith(" cycle") and "or_router_export.py" in args
    assert root.find(".//t:Repetition/t:Interval", NS).text == "PT10M"
    assert root.find(".//t:MultipleInstancesPolicy", NS).text == "IgnoreNew"
    assert root.find(".//t:Priority", NS).text == "5"  # Task Scheduler default 7 = below normal: starved at 100 % CPU (2026-11-02: cycles took 6+ min)
    assert "&" not in RT.render_task_xml("C:\\a&b\\pythonw.exe", Path("C:/r/or_router_export.py")).split("<Command>")[1].split("</Command>")[0].replace("&amp;", "")


def test_install_refuses_a_temporary_checkout_and_a_non_router_directory(tmp_path):
    with pytest.raises(SystemExit, match="temporary"):
        RT.install(fake_repo(tmp_path / "jarvis-worktrees"), dry_run=True)
    with pytest.raises(SystemExit, match="not a router checkout"):
        RT.install(tmp_path, dry_run=True)


def test_dry_run_changes_nothing(tmp_path, pytest_tmp_is_not_temporary):
    runner = mock.Mock()
    shim = mock.Mock()
    out = RT.install(fake_repo(tmp_path), dry_run=True, runner=runner, shim=shim)
    assert out["dry_run"] and out["task"] == "Jarvis OR Router Cycle"
    runner.assert_not_called()
    shim.assert_not_called()


def test_install_writes_shim_registers_task_and_verifies_where_it_points(tmp_path, pytest_tmp_is_not_temporary):
    repo = fake_repo(tmp_path)
    script = str((repo / "scripts" / "models" / "or_router_export.py").resolve())
    calls = []

    def runner(cmd, **kw):
        calls.append(cmd)
        if cmd[0] == "schtasks":
            return mock.Mock(returncode=0, stdout="", stderr="")
        return mock.Mock(returncode=0, stderr="", stdout=f"<Exec><Command>C:\\Python\\pythonw.exe</Command><Arguments>-B \"{script}\" cycle</Arguments></Exec>")

    shim = mock.Mock(return_value=[tmp_path / "home" / "or-provider-router.ts"])
    out = RT.install(repo, runner=runner, shim=shim)
    shim.assert_called_once()
    assert shim.call_args.kwargs["repo"] == repo.resolve()
    create = next(c for c in calls if c[0] == "schtasks")
    assert create[:3] == ["schtasks", "/Create", "/TN"] and create[3] == "Jarvis OR Router Cycle" and "/XML" in create and "/F" in create
    assert out["verified"] is True and out["task_now"]["script_exists"] is True and not out["task_now"]["temporary"]


def test_install_fails_loudly_when_the_task_cannot_be_registered(tmp_path, pytest_tmp_is_not_temporary):
    runner = mock.Mock(return_value=mock.Mock(returncode=1, stdout="", stderr="Zugriff verweigert"))
    with pytest.raises(SystemExit, match="schtasks /Create failed"):
        RT.install(fake_repo(tmp_path), runner=runner, shim=mock.Mock(return_value=[]))


def test_status_flags_a_task_that_points_into_a_temporary_worktree():
    out = "<Command>C:\\Python\\pythonw.exe</Command><Arguments>-B \"C:\\Users\\example\\jarvis-worktrees\\wkb-dashboard\\scripts\\models\\or_router_export.py\" cycle</Arguments>"
    runner = mock.Mock(return_value=mock.Mock(returncode=0, stdout=out, stderr=""))
    st = RT.task_action(runner)
    assert st["exists"] and st["temporary"] is True and st["script"].endswith("or_router_export.py")
    assert RT.task_action(mock.Mock(return_value=mock.Mock(returncode=1, stdout="", stderr="")))["exists"] is False


def test_uninstall_deletes_only_the_named_task():
    runner = mock.Mock(return_value=mock.Mock(returncode=0, stdout="", stderr=""))
    assert RT.uninstall(runner)["task_removed"] is True
    assert runner.call_args[0][0] == ["schtasks", "/Delete", "/TN", "Jarvis OR Router Cycle", "/F"]
