#!/usr/bin/env python3
# tiny-pages | Copyright 2026 Waldemar Fech | Apache-2.0 | https://github.com/WaldemarFech/tiny-pages
# SPDX-License-Identifier: Apache-2.0
"""Publish step of the router dashboard: pipe <state>/router.json into the tiny-pages container (VPS host -> pct exec).

Everything from the config ends up in an ssh command line, so host, port, container id, key and remote path are validated strictly
(see publish_target) and the remote command is built with shlex.quote. Invalid config -> False, no process started.
Only the primary model's router.json is published (router-<slug>.json files of further models stay local).
"""
from __future__ import annotations

import os
import posixpath
import re
import shlex
import subprocess
from pathlib import Path

REMOTE_RE = re.compile(r"/srv/pages/[\w./-]+\.json", re.ASCII)
HOST_RE = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?", re.ASCII)


def _num(v, lo: int, hi: int) -> int | None:
    """Strict integer (an int or a string of ASCII digits; bool and everything else rejected) within [lo, hi]."""
    if type(v) is int or (isinstance(v, str) and v.isascii() and v.isdigit()):
        return int(v) if lo <= int(v) <= hi else None
    return None


def publish_target(cfg: dict) -> tuple[str, int, int, str, str] | None:
    """Validated (host, port, ct, key, remote_path) or None. Everything lands in an ssh command line, so nothing is taken on trust."""
    rp, host, key = cfg.get("remote_path"), cfg.get("ssh_host"), cfg.get("ssh_key") or str(Path.home() / ".ssh" / "pve_netcup")
    ct, port = _num(cfg.get("ct"), 100, 2**31 - 1), _num(cfg.get("ssh_port"), 1, 65535)
    if not (isinstance(rp, str) and REMOTE_RE.fullmatch(rp) and ".." not in rp.split("/")):
        return None
    if not (isinstance(host, str) and HOST_RE.fullmatch(host) and isinstance(key, str) and key and "\0" not in key and not key.startswith("-")):
        return None
    return (host, port, ct, key, rp) if ct is not None and port is not None else None


FILE_RE = re.compile(r"[\w-]+\.json", re.ASCII)


def publish(sd: Path, cfg: dict, filename: str = "router.json") -> bool:
    """Pipe <state>/<filename> (default: the primary model's router.json) into the tiny-pages container through the VPS host (pct exec),
    next to the configured remote_path. Off unless config.json has publish=true. Invalid host/port/ct/remote_path/filename -> False, no process."""
    if not cfg.get("publish") or not FILE_RE.fullmatch(filename):
        return False
    if filename != "router.json" and isinstance(cfg.get("remote_path"), str):
        cfg = {**cfg, "remote_path": posixpath.join(posixpath.dirname(cfg["remote_path"]), filename)}
    tgt = publish_target(cfg)
    if tgt is None:
        return False
    host, port, ct, key, rp = tgt
    remote = f"pct exec {ct} -- sh -c " + shlex.quote(f"cat > {shlex.quote(rp + '.tmp')} && mv {shlex.quote(rp + '.tmp')} {shlex.quote(rp)}")
    # CREATE_NO_WINDOW on Windows: the cycle runs detached from the opencode plugin without a console; without the flag every ssh
    # allocates its own console -> a CMD window pops up on the owner's desktop every publish (2026-11-01).
    no_window = 0x08000000 if os.name == "nt" else 0
    try:
        with (sd / filename).open("rb") as fh:
            r = subprocess.run(["ssh", "-i", key, "-p", str(port), "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", "-o", "StrictHostKeyChecking=yes",
                                f"root@{host}", remote], stdin=fh, capture_output=True, timeout=60, creationflags=no_window)
        return r.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False
