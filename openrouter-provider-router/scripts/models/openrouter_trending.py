#!/usr/bin/env python3
"""OpenRouter trending top-10 (day and week) with prices, free/stealth flags and diff.

Public data sources (verified 2026-10-25, no API key required):
  * https://openrouter.ai/api/frontend/v1/rankings/models?view=day
  * https://openrouter.ai/api/frontend/v1/rankings/models?view=week
      Same JSON the public rankings page loads. It is not part of the documented
      /api/v1 surface, but it is the site's own public, unauthenticated feed.
      Rows are per UTC date and per `variant_permaslug` (standard/batch/free),
      with total_prompt_tokens and total_completion_tokens.
  * https://openrouter.ai/api/v1/models
      Official public catalog: id, canonical_slug, name, context_length and
      per-token pricing (prompt, completion, input_cache_read,
      input_cache_write). Used for prices, provider, context and links.

Ranking metric: total_prompt_tokens + total_completion_tokens over the window.
"day" = the newest complete date in the day feed, "week" = every date the week
feed returns (the same 6-7 day window the public page uses).

Classification:
  free    = prompt AND completion both cost 0 USD per 1M tokens.
  stealth = free but not labelled as free (no `:free` variant/suffix and no
            "(free)" in the name) and not the `openrouter/*` router itself.
            These are the unlisted/zero-cost preview models (oxalpha/space-bunny
            pattern) that must not be mistaken for a listed free offer.

Output: models_trending.json (atomic write). A failed fetch never overwrites the
previous file; the previous JSON and timestamp stay in place.

Usage:
  python scripts/models/openrouter_trending.py --publish /srv/pages/deepseek-offpeak \
      --state /var/lib/tiny-pages/models_trending_state.json
  python scripts/models/openrouter_trending.py --json
  python scripts/models/openrouter_trending.py --json --publish DIR --state FILE
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA = 1
RANKINGS_URL = "https://openrouter.ai/api/frontend/v1/rankings/models"
CATALOG_URL = "https://openrouter.ai/api/v1/models"
DEFAULT_NAME = "models_trending.json"
STATE_NAME = "models_trending_state.json"
USER_AGENT = "jarvis-openrouter-trending/1.0"
TOP_N = 10
ROUTER_IDS = {"openrouter/auto", "openrouter/free"}
PRICE_FIELDS = (
    ("prompt", "prompt"),
    ("completion", "completion"),
    ("input_cache_read", "cache_read"),
    ("input_cache_write", "cache_write"),
)


class TrendingError(RuntimeError):
    pass


def _get_json(url: str, timeout: int = 30, attempts: int = 3) -> dict[str, Any]:
    last: Exception | None = None
    for attempt in range(attempts):
        req = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                payload = json.loads(r.read().decode("utf-8", "replace"))
            if not isinstance(payload, dict):
                raise TrendingError(f"unexpected payload type from {url}: {type(payload).__name__}")
            return payload
        except Exception as exc:  # noqa: BLE001 - network/JSON both mean "keep old data"
            last = exc
            if attempt + 1 < attempts:
                time.sleep(1.5 * (attempt + 1))
    raise TrendingError(f"fetch failed after {attempts} attempts: {url}: {last}")


def _num(value: Any) -> float | None:
    try:
        return float(str(value).strip())
    except (TypeError, ValueError, AttributeError):
        return None


def price_per_million(value: Any) -> float | None:
    n = _num(value)
    return None if n is None or n < 0 else round(n * 1_000_000, 6)  # -1 = dynamic pricing marker, not a price


def _date_key(row: dict[str, Any]) -> str:
    return str(row.get("date") or "")[:10]


def _row_key(row: dict[str, Any]) -> str:
    return str(row.get("variant_permaslug") or row.get("model_permaslug") or "")


def _tokens(row: dict[str, Any]) -> int:
    return int(row.get("total_prompt_tokens") or 0) + int(row.get("total_completion_tokens") or 0)


def window_dates(rows: list[dict[str, Any]], mode: str) -> tuple[list[str], str | None]:
    dates = sorted({_date_key(r) for r in rows if _date_key(r)})
    if not dates:
        raise TrendingError(f"no dated rows in {mode} rankings")
    if mode == "day":
        return [dates[-1]], dates[-1]
    return dates, None


def aggregate(rows: list[dict[str, Any]], dates: list[str]) -> list[tuple[str, int]]:
    """Sum prompt+completion tokens per variant_permaslug inside the window."""
    wanted = set(dates)
    totals: dict[str, int] = {}
    for row in rows:
        if _date_key(row) not in wanted:
            continue
        key = _row_key(row)
        if not key:
            continue
        totals[key] = totals.get(key, 0) + _tokens(row)
    return sorted(totals.items(), key=lambda kv: (-kv[1], kv[0]))


def catalog_index(catalog: dict[str, Any]) -> tuple[dict[str, dict], dict[str, list[dict]]]:
    by_id: dict[str, dict] = {}
    by_slug: dict[str, list[dict]] = {}
    for entry in catalog.get("data") or []:
        if not isinstance(entry, dict) or not entry.get("id"):
            continue
        by_id[str(entry["id"])] = entry
        slug = str(entry.get("canonical_slug") or "")
        if slug:
            by_slug.setdefault(slug, []).append(entry)
    if not by_id:
        raise TrendingError("catalog has no models")
    return by_id, by_slug


def resolve_entry(permaslug: str, variant: str, by_id: dict[str, dict], by_slug: dict[str, list[dict]]) -> dict | None:
    """Map a ranking row (dated permaslug + variant) to its catalog entry."""
    suffix = f":{variant}" if variant in ("free", "batch") else ""
    exact = by_id.get(permaslug + suffix)
    if exact:
        return exact
    candidates = by_slug.get(permaslug) or []
    if variant in ("free", "batch"):
        for entry in candidates:
            if str(entry.get("id") or "").endswith(f":{variant}"):
                return entry
    else:
        for entry in candidates:
            if ":" not in str(entry.get("id") or ""):
                return entry
    return candidates[0] if candidates else None


def classify(pricing: dict | None, model_id: str, variant: str, name: str) -> tuple[bool, bool]:
    """(free, stealth) for a zero-cost listing; see module docstring."""
    pricing = pricing or {}
    prompt, completion = _num(pricing.get("prompt")), _num(pricing.get("completion"))
    if prompt != 0.0 or completion != 0.0:
        return False, False
    labelled_free = (
        variant == "free"
        or model_id.endswith(":free")
        or "(free)" in name.lower()
    )
    # only OpenRouter's own routers are exempt: cloaked previews were published under openrouter/<codename> too (review 2026-10-31)
    is_router = model_id in ROUTER_IDS or re.search(r"\brouter\b", name.lower()) is not None
    stealth = (not labelled_free) and not is_router
    return True, stealth


def provider_of(entry: dict | None, model_id: str, name: str) -> str:
    if entry and entry.get("name"):
        full = str(entry["name"])
        if ": " in full:
            return full.split(": ", 1)[0].strip()
    if name and ": " in name:
        return name.split(": ", 1)[0].strip()
    return model_id.split("/", 1)[0]


def short_name(name: str, provider: str) -> str:
    prefix = provider + ": "
    return name[len(prefix):] if name.startswith(prefix) else name


def build_entries(
    ranked: list[tuple[str, int]],
    variant_by_key: dict[str, str],
    permaslug_by_key: dict[str, str],
    catalog: dict[str, Any],
    top_n: int = TOP_N,
) -> list[dict[str, Any]]:
    by_id, by_slug = catalog_index(catalog)
    entries: list[dict[str, Any]] = []
    for rank, (key, tokens) in enumerate(ranked[:top_n], 1):
        permaslug = permaslug_by_key.get(key, key.split(":", 1)[0])
        variant = variant_by_key.get(key, "standard")
        entry = resolve_entry(permaslug, variant, by_id, by_slug)
        model_id = str((entry or {}).get("id") or permaslug)
        name = str((entry or {}).get("name") or permaslug)
        pricing = dict((entry or {}).get("pricing") or {})
        if variant == "free":
            # A free ranking variant is 0 USD even when the catalog only lists
            # the paid base model (seen 2026-10-25: minimax/minimax-m3.1-flash).
            if not model_id.endswith(":free"):
                model_id += ":free"
            if "(free)" not in name.lower():
                name += " (free)"
            pricing = {"prompt": "0", "completion": "0", "input_cache_read": "0", "input_cache_write": "0"}
        provider = provider_of(entry, model_id, name)
        free, stealth = classify(pricing, model_id, variant, name)
        entries.append({
            "rank": rank,
            "key": key,
            "id": model_id,
            "name": name,
            "short": short_name(name, provider),
            "provider": provider,
            "context": (entry or {}).get("context_length"),
            "tools": "tools" in ((entry or {}).get("supported_parameters") or []),
            "created": (entry or {}).get("created"),
            "pricing": {out: price_per_million(pricing.get(src)) for src, out in PRICE_FIELDS},
            "link": f"https://openrouter.ai/models/{model_id}",
            "free": free,
            "stealth": stealth,
            "tokens": tokens,
            "variant": variant,
        })
    return entries


def _variant_maps(rows: list[dict[str, Any]]) -> tuple[dict[str, str], dict[str, str]]:
    variant_by_key: dict[str, str] = {}
    permaslug_by_key: dict[str, str] = {}
    for row in rows:
        key = _row_key(row)
        if not key:
            continue
        variant_by_key[key] = str(row.get("variant") or "standard")
        permaslug_by_key[key] = str(row.get("model_permaslug") or key.split(":", 1)[0])
    return variant_by_key, permaslug_by_key


def _state_entries(entries: list[dict[str, Any]]) -> list[dict[str, str]]:
    return [{"key": e["key"], "name": e["name"], "id": e["id"]} for e in entries]


def compute_diff(entries: list[dict[str, Any]], previous: list[dict[str, Any]]) -> dict[str, list[dict[str, str]]]:
    current = {e["key"]: e for e in entries}
    before = {str(p.get("key")): p for p in previous if isinstance(p, dict) and p.get("key")}
    new = [{"key": k, "name": current[k]["name"], "id": current[k]["id"]} for k in current if k not in before]
    gone = [{"key": k, "name": str(before[k].get("name") or k), "id": str(before[k].get("id") or "")}
            for k in before if k not in current]
    return {"new": new, "gone": gone}


NEW_DAYS = 14  # a catalog entry younger than this counts as "new"
FREE_LIMIT = 40


def build_free_models(catalog: dict[str, Any], now_ts: float, limit: int = FREE_LIMIT) -> list[dict[str, Any]]:
    """Every zero-cost catalog entry (not only the ones in the rankings). `alert` = stealth AND new: the oxalpha/space-bunny case."""
    out: list[dict[str, Any]] = []
    for entry in catalog.get("data") or []:
        if not isinstance(entry, dict) or not entry.get("id"):
            continue
        model_id, name = str(entry["id"]), str(entry.get("name") or entry["id"])
        free, stealth = classify(entry.get("pricing"), model_id, "standard", name)
        if not free:
            continue
        created = entry.get("created")
        age = (now_ts - created) / 86400 if isinstance(created, (int, float)) else None
        provider = provider_of(entry, model_id, name)
        is_new = age is not None and age <= NEW_DAYS
        out.append({
            "id": model_id, "name": name, "short": short_name(name, provider), "provider": provider,
            "context": entry.get("context_length"), "tools": "tools" in (entry.get("supported_parameters") or []),
            "created": created, "age_days": None if age is None else round(age, 1), "new": is_new,
            "stealth": stealth, "alert": bool(stealth and is_new), "expires": entry.get("expiration_date"),
            "link": f"https://openrouter.ai/models/{model_id}",
        })
    out.sort(key=lambda m: (not m["alert"], not m["stealth"], not m["new"], -(m["created"] or 0)))
    return out[:limit]


def build_doc(
    day_payload: dict[str, Any],
    week_payload: dict[str, Any],
    catalog: dict[str, Any],
    state: dict[str, Any] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    day_rows = [r for r in (day_payload.get("data") or []) if isinstance(r, dict)]
    week_rows = [r for r in (week_payload.get("data") or []) if isinstance(r, dict)]
    if not day_rows or not week_rows:
        raise TrendingError("rankings payload has no rows")

    day_dates, day_stamp = window_dates(day_rows, "day")
    week_dates, _ = window_dates(week_rows, "week")
    day_ranked = aggregate(day_rows, day_dates)
    week_ranked = aggregate(week_rows, week_dates)
    if not day_ranked or not week_ranked:
        raise TrendingError("rankings window is empty")

    day_variant, day_slug = _variant_maps(day_rows)
    week_variant, week_slug = _variant_maps(week_rows)
    day = build_entries(day_ranked, day_variant, day_slug, catalog)
    week = build_entries(week_ranked, week_variant, week_slug, catalog)

    state = state or {}
    generated = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    return {
        "schema": SCHEMA,
        "generated_at": generated,
        "source": {
            "rankings_day_url": f"{RANKINGS_URL}?view=day",
            "rankings_week_url": f"{RANKINGS_URL}?view=week",
            "catalog_url": CATALOG_URL,
            "metric": "total_prompt_tokens + total_completion_tokens",
            "day_date": day_stamp,
            "week_dates": week_dates,
            "note": "public unauthenticated OpenRouter feed; not part of /api/v1",
        },
        "counts": {"catalog": len(catalog.get("data") or []), "day_rows": len(day_rows), "week_rows": len(week_rows)},
        "day": day,
        "week": week,
        "baseline": not (state.get("day") or state.get("week")),
        "free_models": build_free_models(catalog, (now or datetime.now(timezone.utc)).timestamp()),
        "free_stealth": [
            {"scope": scope, **entry}
            for scope, entries in (("day", day), ("week", week))
            for entry in entries
            if entry["free"]
        ],
        "diff": {
            "day": compute_diff(day, (state.get("day") or [])),
            "week": compute_diff(week, (state.get("week") or [])),
        },
    }


def _state_from_doc(doc: dict[str, Any], now: datetime) -> dict[str, Any]:
    return {
        "version": SCHEMA,
        "updated": now.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "day": _state_entries(doc.get("day") or []),
        "week": _state_entries(doc.get("week") or []),
    }


def atomic_write_json(path: Path, payload: Any) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=1, sort_keys=False)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def load_state(path: Path | None) -> dict[str, Any]:
    if not path or not Path(path).exists():
        return {}
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def refresh(
    timeout: int = 30,
    day_url: str = f"{RANKINGS_URL}?view=day",
    week_url: str = f"{RANKINGS_URL}?view=week",
    catalog_url: str = CATALOG_URL,
    state: dict[str, Any] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    day_payload = _get_json(day_url, timeout)
    week_payload = _get_json(week_url, timeout)
    catalog = _get_json(catalog_url, timeout)
    return build_doc(day_payload, week_payload, catalog, state=state, now=now)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Publish OpenRouter day/week top-10 with prices and diff.")
    ap.add_argument("--publish", type=Path, metavar="DIR", help="write <DIR>/models_trending.json")
    ap.add_argument("--out", type=Path, help="explicit output path (default: ./models_trending.json with --write)")
    ap.add_argument("--state", type=Path, help="diff state path (default: next to output, hidden)")
    ap.add_argument("--json", action="store_true", help="print the document to stdout")
    ap.add_argument("--timeout", type=int, default=30)
    ap.add_argument("--day-url", default=f"{RANKINGS_URL}?view=day")
    ap.add_argument("--week-url", default=f"{RANKINGS_URL}?view=week")
    ap.add_argument("--catalog-url", default=CATALOG_URL)
    args = ap.parse_args(argv)

    out: Path | None = args.out
    if args.publish:
        out = args.publish / DEFAULT_NAME
    if not args.json and out is None:
        ap.error("nothing to do: use --json and/or --publish DIR / --out FILE")
    state_path = args.state or ((out.with_name("." + DEFAULT_NAME + ".state")) if out else None)

    try:
        doc = refresh(args.timeout, args.day_url, args.week_url, args.catalog_url, load_state(state_path))
        if out is not None:
            atomic_write_json(out, doc)
            atomic_write_json(state_path, _state_from_doc(doc, datetime.now(timezone.utc)))
        if args.json:
            print(json.dumps(doc, ensure_ascii=False, indent=1))
    except Exception as exc:  # noqa: BLE001 - fail-safe: old JSON stays untouched
        print(f"openrouter_trending: error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
