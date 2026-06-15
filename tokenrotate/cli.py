"""Command-line interface for TOKENROTATE.

Subcommands:
  plan    -- print the ordered rotation plan
  report  -- print a roll-up summary
  check   -- exit non-zero if any secret is overdue/unknown (CI gate)

Common flags:
  --format {table,json}   output format (default: table)
  --version               print tool version and exit
"""
from __future__ import annotations

import argparse
import json
import sys
from typing import List, Optional

from . import TOOL_NAME, TOOL_VERSION
from .core import build_plan, load_inventory, summarize, RotationPlan

_STATUS_ORDER = {"overdue": 0, "unknown": 1, "due_soon": 2, "ok": 3}


def _emit_json(obj) -> None:
    print(json.dumps(obj, indent=2, sort_keys=False))


def _fmt_plan_table(plan: RotationPlan) -> str:
    rows = [
        ("STATUS", "PRIORITY", "PROVIDER", "SEVERITY", "AGE", "DUE_IN", "NAME"),
    ]
    for i in plan.items:
        age = "-" if i.age_days is None else str(i.age_days) + "d"
        due = "-" if i.days_until_due is None else str(i.days_until_due) + "d"
        rows.append(
            (
                i.status.upper(),
                ("%.1f" % i.priority),
                i.provider,
                i.severity,
                age,
                due,
                i.name,
            )
        )
    widths = [max(len(r[c]) for r in rows) for c in range(len(rows[0]))]
    lines = []
    for ridx, r in enumerate(rows):
        lines.append("  ".join(c.ljust(widths[idx]) for idx, c in enumerate(r)))
        if ridx == 0:
            lines.append("  ".join("-" * widths[idx] for idx in range(len(r))))
    lines.append("")
    lines.append("generated_on: %s   total: %d" % (plan.generated_on, len(plan.items)))
    return "\n".join(lines)


def _fmt_report_table(summary: dict) -> str:
    c = summary["counts"]
    lines = [
        "TOKENROTATE report  (generated_on %s)" % summary["generated_on"],
        "-" * 44,
        "  total secrets : %d" % summary["total"],
        "  ok            : %d" % c["ok"],
        "  due_soon      : %d" % c["due_soon"],
        "  overdue       : %d" % c["overdue"],
        "  unknown       : %d" % c["unknown"],
        "  actionable    : %d" % summary["actionable"],
    ]
    if summary["overdue_by_provider"]:
        lines.append("")
        lines.append("  actionable by provider:")
        for prov, n in sorted(
            summary["overdue_by_provider"].items(), key=lambda kv: (-kv[1], kv[0])
        ):
            lines.append("    %-12s %d" % (prov, n))
    return "\n".join(lines)


def _load_plan(args) -> RotationPlan:
    inv = load_inventory(args.inventory)
    return build_plan(inv)


def _cmd_plan(args) -> int:
    plan = _load_plan(args)
    if args.format == "json":
        _emit_json(plan.to_dict())
    else:
        print(_fmt_plan_table(plan))
    # Findings present => non-zero so plan is usable as a gate too.
    summary = summarize(plan)
    return 1 if summary["actionable"] > 0 else 0


def _cmd_report(args) -> int:
    plan = _load_plan(args)
    summary = summarize(plan)
    if args.format == "json":
        _emit_json(summary)
    else:
        print(_fmt_report_table(summary))
    return 1 if summary["actionable"] > 0 else 0


def _cmd_check(args) -> int:
    plan = _load_plan(args)
    summary = summarize(plan)
    findings = [i for i in plan.items if i.status in ("overdue", "unknown")]
    findings.sort(key=lambda i: (_STATUS_ORDER[i.status], -i.priority))
    if args.format == "json":
        _emit_json(
            {
                "generated_on": plan.generated_on,
                "actionable": summary["actionable"],
                "findings": [i.to_dict() for i in findings],
            }
        )
    else:
        if not findings:
            print("OK: no secrets overdue or unknown (%d checked)" % summary["total"])
        else:
            print("FINDINGS: %d secret(s) need rotation" % len(findings))
            for i in findings:
                detail = (
                    "overdue by %dd" % (-i.days_until_due)
                    if i.days_until_due is not None
                    else "never rotated / unknown date"
                )
                print("  [%s] %s (%s) -- %s" % (i.status.upper(), i.name, i.provider, detail))
    return 1 if findings else 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog=TOOL_NAME,
        description="Plan and track secret rotation across providers from an inventory.",
    )
    p.add_argument(
        "--version", action="version", version="%s %s" % (TOOL_NAME, TOOL_VERSION)
    )
    sub = p.add_subparsers(dest="command")

    def _add_common(sp):
        sp.add_argument("inventory", help="path to inventory JSON file")
        sp.add_argument(
            "--format",
            choices=("table", "json"),
            default="table",
            help="output format (default: table)",
        )

    sp_plan = sub.add_parser("plan", help="print ordered rotation plan")
    _add_common(sp_plan)
    sp_plan.set_defaults(func=_cmd_plan)

    sp_report = sub.add_parser("report", help="print roll-up summary")
    _add_common(sp_report)
    sp_report.set_defaults(func=_cmd_report)

    sp_check = sub.add_parser(
        "check", help="exit non-zero if any secret is overdue/unknown (CI gate)"
    )
    _add_common(sp_check)
    sp_check.set_defaults(func=_cmd_check)

    return p


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        parser.print_help()
        return 2
    try:
        return args.func(args)
    except FileNotFoundError as exc:
        print("error: inventory file not found: %s" % exc.filename, file=sys.stderr)
        return 2
    except IsADirectoryError as exc:
        print("error: inventory path is a directory, not a file: %s" % exc.filename, file=sys.stderr)
        return 2
    except PermissionError as exc:
        print("error: permission denied reading inventory: %s" % exc.filename, file=sys.stderr)
        return 2
    except UnicodeDecodeError as exc:
        print("error: inventory file is not valid UTF-8: %s" % exc, file=sys.stderr)
        return 2
    except (ValueError, json.JSONDecodeError) as exc:
        print("error: invalid inventory: %s" % exc, file=sys.stderr)
        return 2
    except OSError as exc:
        print("error: could not read inventory: %s" % exc, file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
