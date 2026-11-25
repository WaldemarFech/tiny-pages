#!/usr/bin/env python3
"""Regenerate holidays.js from the official Chinese holiday API.

Runs monthly on the tiny-pages container. Keeps the previous file when the
fetch fails or a year is not published yet (the API returns an empty object
for future years). Only holiday=true entries are written; make-up workdays are
ignored on purpose.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import pathlib
import sys
import tempfile
import urllib.request

API = "https://timor.tech/api/holiday/year/{year}"
OUT = pathlib.Path("/srv/pages/deepseek-offpeak/preis-uhr/holidays.js")
NAMES = {
    "元旦": "元旦",
    "春节": "春节",
    "除夕": "春节",
    "初一": "春节",
    "初二": "春节",
    "初三": "春节",
    "初四": "春节",
    "初五": "春节",
    "初六": "春节",
    "初七": "春节",
    "清明节": "清明节",
    "劳动节": "劳动节",
    "端午节": "端午节",
    "中秋节": "中秋节",
    "国庆节": "国庆节",
}


def fetch(year: int) -> dict[str, str]:
    req = urllib.request.Request(
        API.format(year=year),
        headers={"User-Agent": "jarvis-tiny-pages/1.0"},
    )
    with urllib.request.urlopen(req, timeout=20) as response:
        payload = json.load(response)
    if payload.get("code") != 0:
        raise ValueError(f"unexpected response for {year}: {payload.get('code')}")
    result: dict[str, str] = {}
    for key, item in sorted(payload.get("holiday", {}).items()):
        if not item.get("holiday"):
            continue
        name = item.get("name", "")
        result[item["date"]] = NAMES.get(name, name or "Feiertag")
    return result


def main() -> int:
    start_year = dt.date.today().year
    merged: dict[str, str] = {}
    for year in range(start_year, start_year + 2):
        try:
            merged.update(fetch(year))
        except Exception as exc:  # keep the old file on any failure
            print(f"warn: {year}: {exc}", file=sys.stderr)

    if not merged:
        print("error: no holidays fetched, keeping existing file", file=sys.stderr)
        return 1

    lines = [
        "// Chinese public holidays (official State Council schedule), Beijing calendar day.",
        "// Generated from https://timor.tech/api/holiday/year/<year> by",
        "// scripts/update_holidays.py. Keep this file static so the page needs no",
        "// runtime API call. Make-up workdays are intentionally not listed: peak hours",
        "// stay Mon-Fri, holidays only remove peak.",
        "export const CN_HOLIDAYS = {",
    ]
    for date, name in sorted(merged.items()):
        lines.append(f"  '{date}': '{name}',")
    lines.append("};")
    content = "\n".join(lines) + "\n"

    OUT.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=OUT.parent, prefix=".holidays-", suffix=".js")
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(content)
    os.chmod(tmp, 0o644)
    os.replace(tmp, OUT)
    print(f"wrote {OUT} with {len(merged)} holiday days")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
