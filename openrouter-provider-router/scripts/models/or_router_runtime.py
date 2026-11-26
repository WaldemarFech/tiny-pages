#!/usr/bin/env python3
# tiny-pages | Copyright 2026 Waldemar Fech | Apache-2.0 | https://github.com/WaldemarFech/tiny-pages
# SPDX-License-Identifier: Apache-2.0
"""Runtime installer of the OpenRouter router: points EVERYTHING that keeps it alive at one checkout - the global opencode plugin shim(s),
the recorded interpreter and the Windows task "Jarvis OR Router Cycle" (cycle every 10 min, no console window).

Why: on 2026-11-02 shim and task pointed into a temporary worktree (~/jarvis-worktrees/wkb-dashboard). The task was created by hand and
was documented only in the KB. After the PRs are merged the cleanup executor closes that worktree and the router, the page data and the
plugin would die silently. Run this from a CLEAN checkout of main (not a worktree); it refuses temporary paths unless --allow-temporary.

  python scripts/models/or_router_runtime.py plan                  # what would be installed (no changes)
  python scripts/models/or_router_runtime.py install [--dry-run]   # shim(s) + python.txt + scheduled task, then verify
  python scripts/models/or_router_runtime.py status                # where the task and the shim point, do the files exist
  python scripts/models/or_router_runtime.py uninstall             # remove the task (shims: delete the files named by `status`)
The checkout is the one this script lives in (or --repo). Windows only for install/status/uninstall.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from xml.sax.saxutils import escape

sys.path.insert(0, str(Path(__file__).resolve().parent))
import or_provider_router as R  # noqa: E402

TASK = "Jarvis OR Router Cycle"
INTERVAL_MIN = 10
DESCRIPTION = "OpenRouter router cycle every 10 min (price snapshots, key usage, export, publish) while opencode is idle"
TEMP_MARKERS = ("/jarvis-worktrees/", "/.claude/worktrees/", "/scratchpad/", "/appdata/local/temp/", "/jarvis-pending-deletion/")
NO_WINDOW = 0x08000000 if os.name == "nt" else 0


def is_temporary(path: Path | str) -> bool:
    p = "/" + str(path).replace("\\", "/").lower().strip("/") + "/"
    return any(m in p for m in TEMP_MARKERS)


def pythonw(python: str | None = None) -> str:
    """Windowless interpreter next to python.exe (pythonw.exe), else the interpreter itself."""
    exe = Path(python or sys.executable)
    cand = exe.with_name("pythonw.exe")
    return str(cand if cand.exists() else exe)


def render_task_xml(command: str, script: Path, interval_min: int = INTERVAL_MIN, start: str = "2026-01-01T00:00:00") -> str:
    """Task Scheduler XML (UTF-16 file for `schtasks /Create /XML`): repeat every interval_min, never two at once, no console, current user, normal priority."""
    args = f'-B "{script}" cycle'
    return f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.3" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo><Description>{escape(DESCRIPTION)}</Description></RegistrationInfo>
  <Principals><Principal id="Author"><LogonType>InteractiveToken</LogonType></Principal></Principals>
  <Settings>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <StartWhenAvailable>true</StartWhenAvailable>
    <ExecutionTimeLimit>PT9M</ExecutionTimeLimit>
    <Priority>5</Priority>
    <UseUnifiedSchedulingEngine>true</UseUnifiedSchedulingEngine>
  </Settings>
  <Triggers><TimeTrigger><StartBoundary>{start}</StartBoundary><Repetition><Interval>PT{int(interval_min)}M</Interval><Duration>P3650D</Duration><StopAtDurationEnd>true</StopAtDurationEnd></Repetition></TimeTrigger></Triggers>
  <Actions Context="Author"><Exec><Command>{escape(command)}</Command><Arguments>{escape(args)}</Arguments></Exec></Actions>
</Task>
"""


def plan(repo: Path, python: str | None = None) -> dict:
    repo = Path(repo).resolve()
    script = repo / "scripts" / "models" / "or_router_export.py"
    plugin = repo / ".opencode" / "plugins" / "or-provider-router.ts"
    return {"repo": str(repo), "temporary": is_temporary(repo), "script": str(script), "script_exists": script.exists(), "plugin": str(plugin),
            "plugin_exists": plugin.exists(), "task": TASK, "interval_min": INTERVAL_MIN, "command": pythonw(python),
            "state": str(R.state_dir())}


def _run(cmd: list[str], runner=subprocess.run, **kw):
    return runner(cmd, capture_output=True, text=True, timeout=60, creationflags=NO_WINDOW, **kw)


def task_action(runner=subprocess.run) -> dict:
    """What the scheduled task runs now: {'exists', 'command', 'arguments', 'script', 'script_exists'}."""
    ps = f"(Export-ScheduledTask -TaskName '{TASK}' -ErrorAction Stop) | Out-String"
    r = _run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps], runner)
    if r.returncode != 0:
        return {"exists": False}
    cmd = re.search(r"<Command>(.*?)</Command>", r.stdout, re.S)
    arg = re.search(r"<Arguments>(.*?)</Arguments>", r.stdout, re.S)
    script = re.search(r'"([^"]*or_router_export\.py)"', arg.group(1) if arg else "")
    sp = script.group(1) if script else None
    return {"exists": True, "command": cmd.group(1).strip() if cmd else None, "arguments": arg.group(1).strip() if arg else None, "script": sp,
            "script_exists": bool(sp and Path(sp).exists()), "temporary": bool(sp and is_temporary(sp))}


def install(repo: Path, allow_temporary: bool = False, dry_run: bool = False, runner=subprocess.run, shim=None, python: str | None = None) -> dict:
    p = plan(repo, python)
    if not (p["script_exists"] and p["plugin_exists"]):
        raise SystemExit(f"not a router checkout (missing {p['script'] if not p['script_exists'] else p['plugin']})")
    if p["temporary"] and not allow_temporary:
        raise SystemExit(f"{p['repo']} is a temporary checkout/worktree: the runtime would die with it. Run this from a clean main checkout (or --allow-temporary).")
    if dry_run:
        return {**p, "dry_run": True}
    shims = (shim or R.install_shim)(repo=Path(p["repo"]))
    xml = render_task_xml(p["command"], Path(p["script"]))
    fd, tmp = tempfile.mkstemp(suffix=".xml")
    try:
        with os.fdopen(fd, "w", encoding="utf-16") as fh:
            fh.write(xml)
        r = _run(["schtasks", "/Create", "/TN", TASK, "/XML", tmp, "/F"], runner)
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass
    if r.returncode != 0:
        raise SystemExit(f"schtasks /Create failed: {(r.stderr or r.stdout).strip()[:200]}")
    now = task_action(runner)
    ok = bool(now.get("exists") and now.get("script") and Path(now["script"]).resolve() == Path(p["script"]).resolve())
    return {**p, "shims": [str(s) for s in shims], "task_now": now, "verified": ok}


def uninstall(runner=subprocess.run) -> dict:
    r = _run(["schtasks", "/Delete", "/TN", TASK, "/F"], runner)
    return {"task_removed": r.returncode == 0}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("cmd", choices=("plan", "install", "status", "uninstall"))
    ap.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[2])
    ap.add_argument("--allow-temporary", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    if a.cmd == "plan":
        out = plan(a.repo)
    elif a.cmd == "install":
        out = install(a.repo, a.allow_temporary, a.dry_run)
    elif a.cmd == "status":
        out = {"task": task_action(), "state": str(R.state_dir())}
    else:
        out = uninstall()
    print(json.dumps(out, indent=1, ensure_ascii=False))
    return 0 if out.get("verified", True) is not False else 1


if __name__ == "__main__":
    sys.exit(main())
