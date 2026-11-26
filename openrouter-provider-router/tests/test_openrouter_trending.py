# tiny-pages | Copyright 2026 Waldemar Fech | Apache-2.0 | https://github.com/WaldemarFech/tiny-pages`n// SPDX-License-Identifier: Apache-2.0
"""OpenRouter trending parser: window/aggregation, price scaling, free/stealth, diff, fail-safe."""
import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "models"))
import openrouter_trending as T  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def load(name):
    with open(FIXTURES / name, encoding="utf-8") as fh:
        return json.load(fh)


class Trending(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.day = load("openrouter_rankings_day.json")
        cls.week = load("openrouter_rankings_week.json")
        cls.catalog = load("openrouter_models.json")
        cls.now = datetime(2026, 10, 25, 12, 0, tzinfo=timezone.utc)

    def doc(self, state=None):
        return T.build_doc(self.day, self.week, self.catalog, state=state, now=self.now)

    def test_day_window_uses_only_latest_date(self):
        dates, stamp = T.window_dates(self.day["data"], "day")
        self.assertEqual(stamp, "2026-10-24")
        self.assertEqual(dates, ["2026-10-24"])

    def test_week_window_aggregates_all_dates(self):
        dates, stamp = T.window_dates(self.week["data"], "week")
        self.assertIsNone(stamp)
        self.assertEqual(len(dates), 6)
        ranked = T.aggregate(self.week["data"], dates)
        day_only = T.aggregate(self.week["data"], [dates[-1]])
        week_total, last_day_total = dict(ranked), dict(day_only)
        self.assertTrue(all(week_total[k] >= last_day_total.get(k, 0) for k in week_total))
        self.assertTrue(any(week_total[k] > last_day_total.get(k, 0) for k in week_total))

    def test_ranked_lists_and_prices(self):
        doc = self.doc()
        self.assertEqual(len(doc["day"]), 10)
        self.assertEqual(len(doc["week"]), 10)
        self.assertEqual([e["rank"] for e in doc["day"]], list(range(1, 11)))
        top = doc["day"][0]
        self.assertEqual(top["id"], "deepseek/deepseek-v4.1-flash-1017")
        self.assertEqual(top["provider"], "DeepSeek")
        self.assertEqual(top["pricing"], {"prompt": 0.15, "completion": 0.6, "cache_read": 0.003, "cache_write": None})
        self.assertEqual(top["link"], "https://openrouter.ai/models/deepseek/deepseek-v4.1-flash-1017")
        self.assertEqual(top["context"], 1048576)
        self.assertFalse(top["free"] or top["stealth"])

    def test_free_listing_without_catalog_free_entry(self):
        doc = self.doc()
        minimax = next(e for e in doc["week"] if e["short"].startswith("MiniMax M3.1 Flash"))
        self.assertTrue(minimax["free"])
        self.assertFalse(minimax["stealth"])
        self.assertTrue(minimax["id"].endswith(":free"))
        self.assertEqual(minimax["pricing"], {"prompt": 0.0, "completion": 0.0, "cache_read": 0.0, "cache_write": 0.0})

    def test_free_strip_contains_day_and_week_entries(self):
        doc = self.doc()
        scopes = {(e["scope"], e["id"]) for e in doc["free_stealth"]}
        self.assertIn(("day", "nvidia/nemotron-3-ultra-550b-a55b:free"), scopes)
        self.assertIn(("week", "minimax/minimax-m3.1-flash:free"), scopes)
        self.assertTrue(all(e["free"] for e in doc["free_stealth"]))

    def test_stealth_classification(self):
        zero = {"prompt": "0", "completion": "0"}
        self.assertEqual(T.classify(zero, "acme/oxalpha", "standard", "Acme: OX Alpha"), (True, True))
        self.assertEqual(T.classify(zero, "acme/space-bunny", "standard", "Acme: Space Bunny"), (True, True))
        self.assertEqual(T.classify(zero, "acme/x:free", "free", "Acme: X (free)"), (True, False))
        self.assertEqual(T.classify(zero, "openrouter/free", "standard", "Free Models Router"), (True, False))
        self.assertEqual(T.classify({"prompt": "0.000002", "completion": "0"}, "acme/y", "standard", "Acme: Y"), (False, False))

    def test_price_scaling_and_nulls(self):
        self.assertEqual(T.price_per_million("0.000002"), 2.0)
        self.assertEqual(T.price_per_million("0.00000015"), 0.15)
        self.assertIsNone(T.price_per_million(None))
        self.assertIsNone(T.price_per_million("n/a"))

    def test_diff_new_and_gone(self):
        first = self.doc()
        state = T._state_from_doc(first, self.now)
        second_day = [dict(e) for e in first["day"]]
        second_day[9] = dict(second_day[9], key="new/model", name="New: Model", id="new/model")
        week = first["week"]
        doc = dict(first, day=second_day, week=week)
        doc["diff"] = {
            "day": T.compute_diff(second_day, state["day"]),
            "week": T.compute_diff(week, state["week"]),
        }
        self.assertEqual([e["key"] for e in doc["diff"]["day"]["new"]], ["new/model"])
        self.assertEqual(len(doc["diff"]["day"]["gone"]), 1)
        self.assertEqual(doc["diff"]["week"]["new"], [])
        self.assertEqual(doc["diff"]["week"]["gone"], [])

    def test_atomic_write_state_roundtrip(self):
        doc = self.doc()
        d = Path(tempfile.mkdtemp())
        out = d / "models_trending.json"
        state = d / "state.json"
        T.atomic_write_json(out, doc)
        T.atomic_write_json(state, T._state_from_doc(doc, self.now))
        self.assertEqual(json.loads(out.read_text(encoding="utf-8"))["day"][0]["rank"], 1)
        loaded = T.load_state(state)
        self.assertEqual([e["key"] for e in loaded["day"]], [e["key"] for e in doc["day"]])
        self.assertFalse(list(d.glob("*.tmp")))

    def test_generated_at_is_utc_z(self):
        self.assertEqual(self.doc()["generated_at"], "2026-10-25T12:00:00Z")

    def test_fail_safe_keeps_old_file(self):
        d = Path(tempfile.mkdtemp())
        out = d / "models_trending.json"
        out.write_text('{"old": true}\n', encoding="utf-8")
        rc = T.main(["--out", str(out), "--state", str(d / "state.json"),
                     "--day-url", (d / "missing.json").as_uri(),
                     "--week-url", (d / "missing.json").as_uri(),
                     "--catalog-url", (d / "missing.json").as_uri(), "--timeout", "2"])
        self.assertEqual(rc, 2)
        self.assertEqual(out.read_text(encoding="utf-8"), '{"old": true}\n')
        self.assertFalse((d / "state.json").exists())


if __name__ == "__main__":
    unittest.main()


class FreeModels(unittest.TestCase):
    NOW = 1_800_000_000.0

    def catalog(self):
        zero = {"prompt": "0", "completion": "0"}
        day = 86400
        return {"data": [
            {"id": "old/paid", "name": "Old: Paid", "pricing": {"prompt": "0.000001", "completion": "0.000002"}, "created": self.NOW - day},
            {"id": "lab/labelled:free", "name": "Lab: Labelled (free)", "pricing": zero, "created": self.NOW - 40 * day, "context_length": 200000, "supported_parameters": ["tools"]},
            {"id": "oxalpha/mystery", "name": "Oxalpha: Mystery", "pricing": zero, "created": self.NOW - 2 * day, "context_length": 1000000, "supported_parameters": ["tools", "reasoning"]},
            {"id": "ancient/unlabelled", "name": "Ancient: Unlabelled", "pricing": zero, "created": self.NOW - 300 * day},
            {"id": "fresh/labelled:free", "name": "Fresh: Labelled (free)", "pricing": zero, "created": self.NOW - 3 * day},
            {"id": "openrouter/free", "name": "Free Models Router", "pricing": zero, "created": self.NOW - 100 * day},
        ]}

    def test_only_zero_cost_entries_and_alert_is_stealth_and_new(self):
        out = T.build_free_models(self.catalog(), self.NOW)
        ids = [m["id"] for m in out]
        self.assertNotIn("old/paid", ids)
        self.assertEqual(ids[0], "oxalpha/mystery")  # stealth + new = alert, listed first
        alert = out[0]
        self.assertTrue(alert["alert"] and alert["stealth"] and alert["new"] and alert["tools"])
        by_id = {m["id"]: m for m in out}
        self.assertFalse(by_id["lab/labelled:free"]["stealth"])
        self.assertTrue(by_id["fresh/labelled:free"]["new"] and not by_id["fresh/labelled:free"]["alert"])
        self.assertTrue(by_id["ancient/unlabelled"]["stealth"] and not by_id["ancient/unlabelled"]["alert"])
        self.assertFalse(by_id["openrouter/free"]["stealth"])  # the router itself is not a stealth model

    def test_limit_and_doc_flags(self):
        cat = self.catalog()
        self.assertEqual(len(T.build_free_models(cat, self.NOW, limit=2)), 2)

    def test_baseline_flag_marks_the_first_run(self):
        day, week, catalog = load("openrouter_rankings_day.json"), load("openrouter_rankings_week.json"), load("openrouter_models.json")
        now = datetime(2026, 10, 25, 12, 0, tzinfo=timezone.utc)
        first = T.build_doc(day, week, catalog, state=None, now=now)
        again = T.build_doc(day, week, catalog, state=T._state_from_doc(first, now), now=now)
        self.assertTrue(first["baseline"])
        self.assertFalse(again["baseline"])
        self.assertIn("free_models", first)


class StealthNamespace(unittest.TestCase):
    def test_cloaked_preview_under_openrouter_namespace_is_stealth_but_routers_are_not(self):
        zero = {"prompt": "0", "completion": "0"}
        self.assertEqual(T.classify(zero, "openrouter/horizon-codename", "standard", "OpenRouter: Horizon"), (True, True))
        self.assertEqual(T.classify(zero, "openrouter/free", "standard", "Free Models Router"), (True, False))
        self.assertEqual(T.classify(zero, "openrouter/auto", "standard", "Auto"), (True, False))
        self.assertEqual(T.classify(zero, "openrouter/whatever", "standard", "OpenRouter Smart Router"), (True, False))
