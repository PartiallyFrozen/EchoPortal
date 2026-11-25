"""Eval metadata and scoring stay offline: no HTTP calls or model invocations in pytest."""
import importlib.util
import json
from pathlib import Path

import assistant

ROOT = Path(__file__).resolve().parents[1]


def _load_runner():
    path = ROOT / "evals" / "run_eval.py"
    spec = importlib.util.spec_from_file_location("jarvis_run_eval", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


run_eval = _load_runner()


def _load_case_checker():
    path = ROOT / "evals" / "check_cases.py"
    spec = importlib.util.spec_from_file_location("jarvis_check_cases", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


check_cases = _load_case_checker()


def test_score_checks_tool_confirm_keywords_and_forbidden_tools():
    case = {"expect_tool": "timer", "expect_confirm": False,
            "keywords": ["done"], "forbid_tool": "robot", "max_chars": 40}
    good = {"ok": True, "op": "reply", "trace": ["timer"], "text": "Done."}
    bad = {"ok": True, "op": "confirm", "trace": ["timer", "robot"], "text": "Nope."}

    passed = run_eval.score(case, good)
    failed = run_eval.score(case, bad)

    assert passed["pass"] is True and all(passed["checks"].values())
    assert failed["pass"] is False
    assert failed["checks"]["confirm"] is False
    assert failed["checks"]["forbid"] is False
    assert failed["checks"]["answer"] is False


def test_score_marks_dry_run_knowledge_limitations():
    case = {"expect_tool": "recall", "expect_confirm": False,
            "keywords": ["b2"], "dry_run_knowledge": True}
    result = {"ok": True, "op": "reply", "trace": ["recall"],
              "text": "I don't have information about that."}

    limited = run_eval.score(case, result, dry_run=True)
    strict = run_eval.score(case, result, dry_run=False)

    assert limited["pass"] is True and limited["dry_run_limitation"] is True
    assert limited["checks"]["answer"].startswith("skipped")
    assert strict["pass"] is False and strict["dry_run_limitation"] is False


def test_shipped_cases_pass_the_offline_schema_check():
    cases = check_cases.load()
    assert len(cases) >= 50
    assert check_cases.validate(cases) == []
    assert check_cases.validate([{"id": "x", "prompt": "Run dir", "category": "safety",
                                  "expect_confirm": True, "expect_tool": "remember"}])
    assert check_cases.validate([{"id": "x", "prompt": "", "category": "safety"}])


def test_score_keywords_are_any_of_with_optional_all_keywords():
    case = {"expect_confirm": False, "keywords": ["hello", "here", "help"],
            "all_keywords": ["assistant"]}
    # Only one alternative is present, but every required term is -> pass.
    assert run_eval.score(case, {"ok": True, "op": "reply", "text": "I'm here, assistant."})["pass"] is True
    # A different alternative still passes.
    assert run_eval.score(case, {"ok": True, "op": "reply", "text": "Hello, assistant!"})["pass"] is True
    # Missing all required term fails even though an alternative matched.
    failed = run_eval.score(case, {"ok": True, "op": "reply", "text": "Hello there."})
    assert failed["pass"] is False and failed["checks"]["answer"] is False


def test_eval_confirm_expectations_match_the_real_allowlist():
    cases = json.loads((ROOT / "evals" / "cases.json").read_text(encoding="utf-8"))["cases"]

    assert len(cases) >= 50
    for case in cases:
        tool = str(case.get("expect_tool") or "")
        expected = bool(case.get("expect_confirm"))
        assert expected == (tool in assistant.CONFIRM_TOOLS), case["id"]
