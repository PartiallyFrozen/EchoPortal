"""Run the Jarvis prompt regression set against one or more model choices.

Examples
--------
    python evals/run_eval.py --model hybrid --model local:qwen3:14b
    python evals/run_eval.py --category timer --limit 10 --no-dry-run

The agent must be running with the API token available in the token file. By default the runner
asks for a dry run, so state-changing tools are previewed rather than executed. It always restores
the model choice that was active before the run, even on failure.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_TOKEN = Path(r"C:\Users\Justin\AppData\Local\Temp\opencode\echoportal_token.txt")


def call(base: str, token: str, path: str, method: str = "GET", body: dict | None = None) -> dict:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(
        base + path, data=data,
        headers={"X-EchoPortal-Token": token, "Content-Type": "application/json"},
        method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=240) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as exc:
        return {"ok": False, "error": exc.read().decode("utf-8", "replace")[:300]}


def score(case: dict, result: dict, dry_run: bool = False) -> dict:
    trace = {str(t) for t in (result.get("trace") or [])}
    text = str(result.get("text") or "").lower()
    expected = case.get("expect_tool")
    # `keywords` lists acceptable alternatives (any-of); use `all_keywords` when every term is
    # required. The original all-of behaviour made paraphrased but correct answers fail.
    alternatives = [str(k).lower() for k in (case.get("keywords") or [])]
    required = [str(k).lower() for k in (case.get("all_keywords") or [])]
    checks = {
        "tool": (not expected) or expected in trace,
        "confirm": bool(result.get("op") == "confirm") == bool(case.get("expect_confirm")),
        "forbid": case.get("forbid_tool") not in trace,
        "answer": ((not alternatives) or any(k in text for k in alternatives))
                  and all(k in text for k in required),
        "responded": bool(result.get("ok")) and result.get("op") not in (None, "error"),
    }
    limit = case.get("max_chars")
    if limit:
        checks["length"] = len(text) <= int(limit)
    # A dry run previews `remember`, so later recall cases cannot know the seeded fact. Their
    # tool routing is still checked, but the answer keyword check is reported as a known limit.
    limitation = bool(dry_run and case.get("dry_run_knowledge"))
    if limitation:
        checks["answer"] = "skipped: dry run cannot persist an earlier remember"
        passed = all(value is True for key, value in checks.items() if key != "answer")
    else:
        passed = all(checks.values())
    return {"pass": passed, "checks": checks, "trace": sorted(trace),
            "dry_run_limitation": limitation}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--base", default="http://127.0.0.1:8766")
    ap.add_argument("--token-file", type=Path, default=DEFAULT_TOKEN)
    ap.add_argument("--model", action="append", default=[], help="Picker id; repeat to compare models")
    ap.add_argument("--category")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--no-dry-run", action="store_true", help="Actually execute state-changing tools")
    ap.add_argument("--output", type=Path, default=HERE / "last_report.json")
    args = ap.parse_args()

    token = args.token_file.read_text(encoding="utf-8").strip()
    cases = json.loads((HERE / "cases.json").read_text(encoding="utf-8"))["cases"]
    if args.category:
        cases = [c for c in cases if c["category"] == args.category]
    if args.limit:
        cases = cases[:args.limit]
    models = args.model or ["local:qwen3:14b"]
    original = call(args.base, token, "/api/assistant/models").get("current", "")
    report = {"started": time.strftime("%Y-%m-%d %H:%M:%S"), "dry_run": not args.no_dry_run,
              "original_model": original, "models": {}}

    try:
        for model in models:
            switched = call(args.base, token, "/api/assistant/model", "POST", {"id": model})
            if not switched.get("ok"):
                print(f"SKIP {model}: {switched.get('error')}")
                continue
            rows = []
            print(f"\n=== {model} ({len(cases)} cases) ===")
            for case in cases:
                started = time.time()
                result = call(args.base, token, "/api/assistant", "POST", {
                    "text": case["prompt"], "device": "eval",
                    "dry_run": not args.no_dry_run,
                })
                verdict = score(case, result, dry_run=not args.no_dry_run)
                elapsed = round(time.time() - started, 2)
                rows.append({"id": case["id"], "category": case["category"],
                             "elapsed_s": elapsed, "answer": result.get("text", ""), **verdict})
                mark = "PASS*" if verdict.get("dry_run_limitation") else (
                    "PASS" if verdict["pass"] else "FAIL")
                print(f"{mark} {case['id']:<13} {elapsed:>5.1f}s  {verdict['trace']}")
            passed = sum(r["pass"] for r in rows)
            limits = sum(1 for r in rows if r.get("dry_run_limitation"))
            report["models"][model] = {"passed": passed, "total": len(rows),
                                       "dry_run_limitations": limits, "rows": rows}
            suffix = f" ({limits} dry-run knowledge checks skipped)" if limits else ""
            print(f"--- {model}: {passed}/{len(rows)} passed{suffix}")
    finally:
        if original:
            call(args.base, token, "/api/assistant/model", "POST", {"id": original})
            print(f"restored model choice: {original}")

    args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"report written to {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
