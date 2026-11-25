"""Offline validation of the prompt regression set (no agent, no model, no network).

Used by CI so a typo in a case or a broken category cannot reach a live eval run.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REQUIRED = ("id", "prompt", "category")
CONFIRM_TOOLS = {
    "type_text", "open_app", "run_command", "robot", "forget", "look_at_screen", "ha_call",
    "task_add", "calendar_add", "window_focus", "window_close", "media_control",
}


def load() -> list[dict]:
    data = json.loads((HERE / "cases.json").read_text(encoding="utf-8"))
    return list(data.get("cases", [])) if isinstance(data, dict) else list(data)


def validate(cases: list[dict]) -> list[str]:
    errors: list[str] = []
    seen: set[str] = set()
    for index, case in enumerate(cases):
        label = case.get("id", "case #%d" % index) if isinstance(case, dict) else "case #%d" % index
        if not isinstance(case, dict):
            errors.append("%s: not an object" % label)
            continue
        for key in REQUIRED:
            if not case.get(key):
                errors.append("%s: missing %s" % (label, key))
        if label in seen:
            errors.append("%s: duplicate id" % label)
        seen.add(label)
        if case.get("expect_confirm") and case.get("expect_tool") not in CONFIRM_TOOLS:
            errors.append("%s: expect_confirm needs a state-changing expect_tool" % label)
        for key in ("keywords", "all_keywords", "forbid_tool"):
            if key in case and not isinstance(case[key], (list, str, type(None))):
                errors.append("%s: %s must be a list or string" % (label, key))
    if not cases:
        errors.append("no cases found")
    return errors


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--category", help="Only validate one category")
    ap.add_argument("--list", action="store_true", help="Print category names and exit")
    args = ap.parse_args()
    cases = load()
    if args.list:
        for name in sorted({str(c.get("category", "")) for c in cases if isinstance(c, dict)} - {""}):
            print(name)
        return 0
    if args.category:
        cases = [c for c in cases if isinstance(c, dict) and c.get("category") == args.category]
        if not cases:
            print("no cases in category %s" % args.category)
            return 1
    errors = validate(cases)
    counts: dict[str, int] = {}
    for case in cases:
        if isinstance(case, dict):
            key = str(case.get("category", ""))
            counts[key] = counts.get(key, 0) + 1
    print("checked %d cases across %d categories: %s" % (len(cases), len(counts),
          ", ".join("%s=%d" % item for item in sorted(counts.items()))))
    for error in errors:
        print("ERROR " + error)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
