"""Smoke + behavior tests for TOKENROTATE. Standard library only, no network."""
import datetime as dt
import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tokenrotate import (  # noqa: E402
    TOOL_NAME,
    TOOL_VERSION,
    Inventory,
    Secret,
    build_plan,
    load_inventory,
    summarize,
)
from tokenrotate.cli import main  # noqa: E402

FIXED_TODAY = dt.date(2026, 6, 8)


def _inv(secrets):
    return Inventory(secrets=secrets)


class TestCore(unittest.TestCase):
    def test_exports(self):
        self.assertEqual(TOOL_NAME, "tokenrotate")
        self.assertTrue(TOOL_VERSION)

    def test_overdue_detected(self):
        s = Secret(name="k", provider="aws", last_rotated="2025-12-01",
                   severity="critical")  # 90d cadence, ~189d old
        plan = build_plan(_inv([s]), today=FIXED_TODAY)
        item = plan.items[0]
        self.assertEqual(item.status, "overdue")
        self.assertLess(item.days_until_due, 0)
        self.assertGreater(item.priority, 100.0)

    def test_ok_when_fresh(self):
        s = Secret(name="k", provider="aws", last_rotated=FIXED_TODAY.isoformat())
        plan = build_plan(_inv([s]), today=FIXED_TODAY)
        self.assertEqual(plan.items[0].status, "ok")
        self.assertEqual(plan.items[0].age_days, 0)

    def test_due_soon_window(self):
        # 90d cadence, rotated 80 days ago -> 10 days until due -> due_soon
        last = (FIXED_TODAY - dt.timedelta(days=80)).isoformat()
        s = Secret(name="k", provider="aws", last_rotated=last)
        plan = build_plan(_inv([s]), today=FIXED_TODAY)
        self.assertEqual(plan.items[0].status, "due_soon")

    def test_unknown_when_no_date(self):
        s = Secret(name="k", provider="generic", last_rotated=None)
        plan = build_plan(_inv([s]), today=FIXED_TODAY)
        self.assertEqual(plan.items[0].status, "unknown")
        self.assertIsNone(plan.items[0].age_days)

    def test_per_secret_override_beats_provider(self):
        s = Secret(name="k", provider="aws", rotation_days=30,
                   last_rotated=(FIXED_TODAY - dt.timedelta(days=40)).isoformat())
        plan = build_plan(_inv([s]), today=FIXED_TODAY)
        self.assertEqual(plan.items[0].interval_days, 30)
        self.assertEqual(plan.items[0].status, "overdue")

    def test_severity_weights_priority(self):
        old = (FIXED_TODAY - dt.timedelta(days=100)).isoformat()
        crit = Secret(name="c", provider="aws", last_rotated=old, severity="critical")
        low = Secret(name="l", provider="aws", last_rotated=old, severity="low")
        plan = build_plan(_inv([low, crit]), today=FIXED_TODAY)
        # Highest priority sorts first.
        self.assertEqual(plan.items[0].name, "c")
        self.assertGreater(plan.items[0].priority, plan.items[1].priority)

    def test_summary_counts(self):
        secrets = [
            Secret(name="ok", provider="aws", last_rotated=FIXED_TODAY.isoformat()),
            Secret(name="late", provider="aws", last_rotated="2025-01-01"),
            Secret(name="none", provider="generic"),
        ]
        summary = summarize(build_plan(_inv(secrets), today=FIXED_TODAY))
        self.assertEqual(summary["total"], 3)
        self.assertEqual(summary["counts"]["overdue"], 1)
        self.assertEqual(summary["counts"]["unknown"], 1)
        self.assertEqual(summary["counts"]["ok"], 1)
        self.assertEqual(summary["actionable"], 2)

    def test_invalid_secret_raises(self):
        with self.assertRaises(ValueError):
            Secret.from_dict({"provider": "aws"})  # missing name
        with self.assertRaises(ValueError):
            Secret.from_dict({"name": "k", "rotation_days": -5})


class TestInventoryLoading(unittest.TestCase):
    def _write(self, data):
        fd, path = tempfile.mkstemp(suffix=".json")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
        self.addCleanup(os.remove, path)
        return path

    def test_load_object_form(self):
        path = self._write({
            "provider_intervals": {"github": 30},
            "secrets": [{"name": "k", "provider": "github",
                         "last_rotated": "2026-06-01"}],
        })
        inv = load_inventory(path)
        self.assertEqual(inv.provider_intervals["github"], 30)
        self.assertEqual(len(inv.secrets), 1)

    def test_load_bare_list_form(self):
        path = self._write([{"name": "k", "provider": "aws"}])
        inv = load_inventory(path)
        self.assertEqual(len(inv.secrets), 1)


class TestCLI(unittest.TestCase):
    def setUp(self):
        self.demo = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "demos", "01-basic", "inventory.json",
        )

    def _run(self, argv):
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = main(argv)
        return code, buf.getvalue()

    def test_demo_inventory_exists(self):
        self.assertTrue(os.path.exists(self.demo))

    def test_plan_json_is_valid_and_findings_exit(self):
        code, out = self._run(["plan", self.demo, "--format", "json"])
        data = json.loads(out)
        self.assertIn("items", data)
        self.assertEqual(len(data["items"]), 6)
        self.assertEqual(code, 1)  # demo has actionable findings

    def test_report_json(self):
        code, out = self._run(["report", self.demo, "--format", "json"])
        data = json.loads(out)
        self.assertEqual(data["total"], 6)
        self.assertIn("counts", data)

    def test_check_json_has_findings(self):
        code, out = self._run(["check", self.demo, "--format", "json"])
        data = json.loads(out)
        self.assertGreaterEqual(data["actionable"], 1)
        self.assertEqual(code, 1)

    def test_no_command_returns_2(self):
        code, _ = self._run([])
        self.assertEqual(code, 2)

    def test_missing_file_returns_2(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = main(["plan", "does-not-exist-xyz.json"])
        self.assertEqual(code, 2)

    def test_version(self):
        with self.assertRaises(SystemExit) as ctx:
            main(["--version"])
        self.assertEqual(ctx.exception.code, 0)


if __name__ == "__main__":
    unittest.main()
