#!/usr/bin/env python3
"""Trending step of the router cycle: refresh the OpenRouter day/week leaderboard (scripts/models/openrouter_trending.py) at most every
`trending_hours` and publish <state>/models_trending.json next to router.json. A failed fetch keeps the old file (stale beats broken)."""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import openrouter_trending as T  # noqa: E402
import or_router_publish as P  # noqa: E402

NAME = "models_trending.json"
STATE = ".trending_state"


def refresh_and_publish(sd: Path, cfg: dict, now: float, refresh=None) -> str:
    """Returns 'fresh' (nothing to do), 'published' or 'local' (written, not published). Raises on fetch/parse errors - the cycle isolates the step."""
    out = sd / NAME
    hours = float(cfg.get("trending_hours", 6))
    marker = sd / ".trending_published"  # touched only after a successful publish, so a failed upload is retried next cycle
    if out.exists() and now - out.stat().st_mtime < hours * 3600:
        if marker.exists() and marker.stat().st_mtime >= out.stat().st_mtime:
            return "fresh"
        return _publish(sd, cfg, marker)
    when = datetime.fromtimestamp(now, timezone.utc)
    doc = (refresh or T.refresh)(state=T.load_state(sd / STATE), now=when)
    T.atomic_write_json(out, doc)
    T.atomic_write_json(sd / STATE, T._state_from_doc(doc, when))
    return _publish(sd, cfg, marker)


def _publish(sd: Path, cfg: dict, marker: Path) -> str:
    if P.publish(sd, cfg, NAME):
        marker.write_text("ok", encoding="utf-8")
        return "published"
    return "local"
