"""
auto_scorer.py
--------------
Foundry [Flow] — Candidate Trajectory Auto-Scorer

Reads candidate JSON files from the generation loop, runs 8 rule-based
checks on each trajectory, and buckets them into passed/ or flagged/.

Scoring rules (1 point each, max = 8):
  R1  ends_with_done          — last tool call is `done`
  R2  no_calls_after_done     — no tool calls appear after `done`
  R3  think_before_every_call — every assistant turn has a non-trivial <think>
  R4  no_hallucinated_ids     — IDs used in params chain from prior tool_results
  R5  confirmation_before_send— ask_user appears before send_email / reply(send_immediately)
  R6  tool_sequence_match     — actual tool set covers ≥75% of expected gold sequence
  R7  done_summary_quality    — done.summary is present and > 50 chars
  R8  step_efficiency         — step count is between 2 and 12 (inclusive)

Pass threshold: score ≥ 6

Pipeline position:
    generation_loop.py  →  candidates/*.json
    auto_scorer.py      →  passed/*.json  +  flagged/*.json  +  scoring_report.json

Usage:
    # Score all candidates
    python auto_scorer.py --input candidates/

    # Score with a custom threshold
    python auto_scorer.py --input candidates/ --threshold 7

    # Print detailed breakdown for every file
    python auto_scorer.py --input candidates/ --verbose
"""

import re
import json
import argparse
import textwrap
from pathlib import Path
from datetime import datetime, timezone


# ─────────────────────────────────────────────────────────────────────────────
# CONSTANTS
# ─────────────────────────────────────────────────────────────────────────────

PASS_THRESHOLD   = 6          # out of 8
MAX_SCORE        = 8
MIN_STEPS        = 2
MAX_STEPS        = 12
MIN_SUMMARY_LEN  = 50         # chars
THINK_MIN_LEN    = 20         # chars — below this is considered trivial
SEQUENCE_OVERLAP = 0.75       # fuzzy match threshold

# Tools that require an ask_user before them if external party is involved
IRREVERSIBLE_SEND_TOOLS = {"send_email", "reply_email"}

# ID field names we track across results and params
ID_FIELD_NAMES = {
    "thread_id", "draft_id", "message_id", "event_id",
    "task_id", "row_id", "ts", "message_ts"
}

# Default gold sequences by category (used for R6 when scenario has no explicit gold_path)
DEFAULT_GOLD_SEQUENCES = {
    "communication":  ["search_email", "read_email", "draft_email", "ask_user", "send_email", "done"],
    "planning":       ["check_calendar", "ask_user", "create_event", "done"],
    "orchestration":  ["check_calendar", "search_email", "read_email", "query_notion", "done"],
    "notion":         ["query_notion", "done"],
    "ambiguity":      ["ask_user", "done"],
    "unknown":        [],   # R6 skipped if no gold sequence available
}


# ─────────────────────────────────────────────────────────────────────────────
# TRACE HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def get_tool_calls(trace: list) -> list:
    """Return all assistant turns that have a parsed tool_call."""
    return [t for t in trace if t.get("role") == "assistant" and t.get("tool_call")]


def get_tool_results(trace: list) -> list:
    """Return all tool result turns."""
    return [t for t in trace if t.get("role") == "tool" and t.get("result")]


def get_think_blocks(trace: list) -> list:
    """Return think text from every assistant turn (empty string if missing)."""
    return [
        (t.get("think") or "").strip()
        for t in trace if t.get("role") == "assistant"
    ]


def extract_ids_from_result(result: dict) -> set:
    """Recursively extract all ID-like values from a tool result data dict."""
    ids = set()
    def _walk(obj):
        if isinstance(obj, dict):
            for k, v in obj.items():
                if k in ID_FIELD_NAMES and isinstance(v, str) and v:
                    ids.add(v)
                _walk(v)
        elif isinstance(obj, list):
            for item in obj:
                _walk(item)
    _walk(result.get("data", {}))
    return ids


def extract_ids_from_params(params: dict) -> set:
    """Extract ID-like values used in a tool call's parameters."""
    ids = set()
    for k, v in params.items():
        if k in ID_FIELD_NAMES and isinstance(v, str) and v:
            ids.add(v)
    return ids


# ─────────────────────────────────────────────────────────────────────────────
# SCORING RULES
# ─────────────────────────────────────────────────────────────────────────────

def r1_ends_with_done(tool_calls: list) -> tuple[bool, str]:
    if not tool_calls:
        return False, "No tool calls found"
    last = tool_calls[-1]["tool_call"]["name"]
    if last == "done":
        return True, "Last call is done ✓"
    return False, f"Last call is '{last}', not done"


def r2_no_calls_after_done(tool_calls: list) -> tuple[bool, str]:
    done_indices = [i for i, t in enumerate(tool_calls) if t["tool_call"]["name"] == "done"]
    if not done_indices:
        return False, "No done call found"
    first_done = done_indices[0]
    calls_after = [
        tool_calls[i]["tool_call"]["name"]
        for i in range(first_done + 1, len(tool_calls))
    ]
    if calls_after:
        return False, f"Tool calls after done: {calls_after}"
    return True, "No calls after done ✓"


def r3_think_before_every_call(tool_calls: list) -> tuple[bool, str]:
    trivial = []
    for t in tool_calls:
        think = (t.get("think") or "").strip()
        if len(think) < THINK_MIN_LEN:
            trivial.append(t["tool_call"]["name"])
    if trivial:
        return False, f"Trivial/missing <think> before: {trivial}"
    return True, "All think blocks substantive ✓"


def r4_no_hallucinated_ids(tool_calls: list, tool_results: list) -> tuple[bool, str]:
    """
    Check that every ID used in a tool call's parameters was previously
    returned by a tool result. IDs that appear for the first time in params
    without having come from a result are hallucinated.
    """
    seen_ids   = set()   # IDs returned by tool results so far
    hallucinated = []

    # Interleave calls and results in step order
    all_turns = sorted(
        [(t.get("step", 0), "call",   t) for t in tool_calls] +
        [(t.get("step", 0), "result", t) for t in tool_results],
        key=lambda x: (x[0], 0 if x[1] == "result" else 1)
    )

    for _, kind, turn in all_turns:
        if kind == "result":
            seen_ids |= extract_ids_from_result(turn.get("result", {}))
        else:
            used_ids = extract_ids_from_params(turn["tool_call"].get("parameters", {}))
            for uid in used_ids:
                if uid not in seen_ids:
                    hallucinated.append(
                        f"{turn['tool_call']['name']}(…{uid}…)"
                    )

    if hallucinated:
        return False, f"Hallucinated IDs: {hallucinated[:3]}"
    return True, "All IDs chain from prior results ✓"


def r5_confirmation_before_send(tool_calls: list) -> tuple[bool, str]:
    """
    Every send_email / reply_email(send_immediately=True) must be preceded
    by at least one ask_user call earlier in the trace.
    """
    ask_steps  = {t["step"] for t in tool_calls if t["tool_call"]["name"] == "ask_user"}
    violations = []

    for t in tool_calls:
        name   = t["tool_call"]["name"]
        params = t["tool_call"].get("parameters", {})
        step   = t["step"]

        is_send = name == "send_email"
        is_reply_send = (
            name == "reply_email"
            and params.get("send_immediately", False) is True
        )

        if is_send or is_reply_send:
            prior_asks = [s for s in ask_steps if s < step]
            if not prior_asks:
                violations.append(f"{name} at step {step} without prior ask_user")

    if violations:
        return False, f"Missing confirmation: {violations}"
    return True, "Confirmation before all sends ✓"


def r6_tool_sequence_match(
    tool_calls: list, gold_sequence: list
) -> tuple[bool, str]:
    if not gold_sequence:
        return True, "No gold sequence defined — skipped ✓"

    actual_names = {t["tool_call"]["name"] for t in tool_calls}
    gold_names   = set(gold_sequence)
    overlap      = len(actual_names & gold_names) / len(gold_names)

    if overlap >= SEQUENCE_OVERLAP:
        return True, f"Sequence overlap {overlap:.0%} ≥ {SEQUENCE_OVERLAP:.0%} ✓"
    missing = gold_names - actual_names
    return False, f"Sequence overlap {overlap:.0%} — missing tools: {missing}"


def r7_done_summary_quality(tool_calls: list) -> tuple[bool, str]:
    done_calls = [t for t in tool_calls if t["tool_call"]["name"] == "done"]
    if not done_calls:
        return False, "No done call found"
    summary = done_calls[-1]["tool_call"].get("parameters", {}).get("summary", "")
    if len(summary) > MIN_SUMMARY_LEN:
        return True, f"Summary length {len(summary)} chars ✓"
    return False, f"Summary too short: {len(summary)} chars (min {MIN_SUMMARY_LEN})"


def r8_step_efficiency(step_count: int) -> tuple[bool, str]:
    if MIN_STEPS <= step_count <= MAX_STEPS:
        return True, f"Step count {step_count} within [{MIN_STEPS}, {MAX_STEPS}] ✓"
    return False, f"Step count {step_count} outside [{MIN_STEPS}, {MAX_STEPS}]"


# ─────────────────────────────────────────────────────────────────────────────
# SCORER
# ─────────────────────────────────────────────────────────────────────────────

class AutoScorer:

    def __init__(self, threshold: int = PASS_THRESHOLD):
        self.threshold = threshold

    def score(self, candidate: dict) -> dict:
        """
        Score a single candidate trajectory.

        Returns:
            total      : int (0–8)
            passed     : bool
            breakdown  : dict[rule_name -> {score, detail}]
            flags      : list of human-readable failure strings
        """
        trace      = candidate.get("trace", [])
        category   = candidate.get("category", "unknown")
        step_count = candidate.get("step_count", 0)

        tool_calls   = get_tool_calls(trace)
        tool_results = get_tool_results(trace)

        # Gold sequence: use scenario-defined gold_path if present.
        # Fall back to category default ONLY for communication/planning/notion/ambiguity —
        # NOT for orchestration (too task-specific to have a universal default).
        explicit_gold = candidate.get("gold_path")
        category_default = DEFAULT_GOLD_SEQUENCES.get(category, [])
        gold_sequence = explicit_gold or (
            category_default if category != "orchestration" else []
        )

        # Run all 8 rules
        rules = {
            "R1_ends_with_done":          r1_ends_with_done(tool_calls),
            "R2_no_calls_after_done":     r2_no_calls_after_done(tool_calls),
            "R3_think_before_every_call": r3_think_before_every_call(tool_calls),
            "R4_no_hallucinated_ids":     r4_no_hallucinated_ids(tool_calls, tool_results),
            "R5_confirmation_before_send":r5_confirmation_before_send(tool_calls),
            "R6_tool_sequence_match":     r6_tool_sequence_match(tool_calls, gold_sequence),
            "R7_done_summary_quality":    r7_done_summary_quality(tool_calls),
            "R8_step_efficiency":         r8_step_efficiency(step_count),
        }

        breakdown = {}
        flags     = []
        total     = 0

        for rule_name, (passed, detail) in rules.items():
            breakdown[rule_name] = {"score": int(passed), "detail": detail}
            total += int(passed)
            if not passed:
                flags.append(f"{rule_name}: {detail}")

        return {
            "total":     total,
            "max":       MAX_SCORE,
            "passed":    total >= self.threshold,
            "breakdown": breakdown,
            "flags":     flags,
        }

    def score_file(self, path: Path) -> dict:
        """Load a candidate JSON and score it. Returns scoring result + metadata."""
        with open(path) as f:
            candidate = json.load(f)

        score_result = self.score(candidate)

        return {
            "file":           str(path),
            "world_state_id": candidate.get("world_state_id", path.stem),
            "task_prompt":    candidate.get("task_prompt", "")[:80],
            "category":       candidate.get("category", "unknown"),
            "step_count":     candidate.get("step_count", 0),
            "gen_status":     candidate.get("status", "unknown"),
            "score":          score_result,
        }


# ─────────────────────────────────────────────────────────────────────────────
# BATCH RUNNER
# ─────────────────────────────────────────────────────────────────────────────

class BatchScorer:

    def __init__(
        self,
        scorer:      AutoScorer,
        passed_dir:  str = "passed",
        flagged_dir: str = "flagged",
    ):
        self.scorer      = scorer
        self.passed_dir  = Path(passed_dir)
        self.flagged_dir = Path(flagged_dir)
        self.passed_dir.mkdir(parents=True, exist_ok=True)
        self.flagged_dir.mkdir(parents=True, exist_ok=True)

    def run(self, input_dir: str, verbose: bool = False) -> dict:
        """
        Score all .json files in input_dir.
        Copy passed files to passed/ and flagged files to flagged/.
        Returns a full report dict.
        """
        files = sorted(Path(input_dir).glob("*.json"))
        if not files:
            print(f"No .json files found in {input_dir}")
            return {}

        results     = []
        rule_totals = {f"R{i}": 0 for i in range(1, 9)}

        for fpath in files:
            # ── Pre-filter: skip non-complete traces before scoring
            try:
                with open(fpath) as f:
                    raw = json.load(f)
                gen_status = raw.get("status", "unknown")
            except Exception as e:
                print(f"  [ERROR reading] {fpath.name}: {e}")
                continue

            if gen_status != "complete":
                dest = self.flagged_dir / fpath.name
                dest.write_bytes(fpath.read_bytes())
                if verbose:
                    print(f"  ✗ {fpath.stem:<20} SKIPPED (gen_status={gen_status})")
                results.append({
                    "file":           str(fpath),
                    "world_state_id": raw.get("world_state_id", fpath.stem),
                    "task_prompt":    raw.get("task_prompt", "")[:80],
                    "category":       raw.get("category", "unknown"),
                    "step_count":     raw.get("step_count", 0),
                    "gen_status":     gen_status,
                    "score": {
                        "total": 0, "max": MAX_SCORE, "passed": False,
                        "breakdown": {f"R{i}": {"score": 0, "detail": f"skipped — gen_status={gen_status}"} for i in range(1, 9)},
                        "flags": [f"Incomplete generation: {gen_status}"]
                    }
                })
                continue

            try:
                r = self.scorer.score_file(fpath)
            except Exception as e:
                print(f"  [ERROR scoring] {fpath.name}: {e}")
                continue

            results.append(r)
            passed = r["score"]["passed"]

            # Copy to appropriate bucket
            dest_dir = self.passed_dir if passed else self.flagged_dir
            dest = dest_dir / fpath.name
            dest.write_bytes(fpath.read_bytes())

            # Accumulate rule pass counts
            for rule_name, rule_result in r["score"]["breakdown"].items():
                key = rule_name[:2]   # "R1", "R2", ...
                rule_totals[key] += rule_result["score"]

            if verbose:
                self._print_result(r)

        return self._build_report(results, rule_totals, len(files))

    def _print_result(self, r: dict):
        score   = r["score"]
        icon    = "✓" if score["passed"] else "✗"
        bars    = "█" * score["total"] + "░" * (MAX_SCORE - score["total"])
        print(f"\n{icon} {r['world_state_id']:<14} [{bars}] {score['total']}/{MAX_SCORE}  "
              f"steps={r['step_count']}  cat={r['category']}")
        print(f"  Task: {r['task_prompt'][:70]}")
        for rule, rd in score["breakdown"].items():
            icon2 = "✓" if rd["score"] else "✗"
            print(f"    {icon2} {rule:<32} {rd['detail']}")

    def _build_report(self, results: list, rule_totals: dict, total_files: int) -> dict:
        n          = len(results)
        n_passed   = sum(1 for r in results if r["score"]["passed"])
        n_flagged  = n - n_passed
        scores     = [r["score"]["total"] for r in results]
        avg_score  = sum(scores) / n if n else 0

        # Separate skipped (incomplete gen) from actually scored
        n_skipped = sum(1 for r in results if r["gen_status"] != "complete")
        n_scored  = n - n_skipped

        # Most common failure rules (only from scored traces)
        failure_counts = {}
        for r in results:
            if r["gen_status"] != "complete":
                continue
            for rule, rd in r["score"]["breakdown"].items():
                if not rd["score"]:
                    failure_counts[rule] = failure_counts.get(rule, 0) + 1

        top_failures = sorted(failure_counts.items(), key=lambda x: -x[1])

        # Category breakdown
        cat_stats = {}
        for r in results:
            cat = r["category"]
            if cat not in cat_stats:
                cat_stats[cat] = {"total": 0, "passed": 0}
            cat_stats[cat]["total"] += 1
            if r["score"]["passed"]:
                cat_stats[cat]["passed"] += 1

        report = {
            "generated_at":  datetime.now(tz=timezone.utc).isoformat(),
            "input_files":   total_files,
            "scored":        n,
            "complete_traces": n_scored,
            "skipped_incomplete": n_skipped,
            "passed":        n_passed,
            "flagged":       n_flagged,
            "pass_rate":     round(n_passed / n * 100, 1) if n else 0,
            "avg_score":     round(avg_score, 2),
            "score_distribution": {
                str(s): sum(1 for r in results if r["score"]["total"] == s)
                for s in range(MAX_SCORE + 1)
            },
            "rule_pass_rates": {
                rule: round(count / n * 100, 1) if n else 0
                for rule, count in rule_totals.items()
            },
            "top_failure_rules": top_failures[:5],
            "category_breakdown": cat_stats,
            "results": results,
        }

        return report


# ─────────────────────────────────────────────────────────────────────────────
# REPORT PRINTER
# ─────────────────────────────────────────────────────────────────────────────

def print_report(report: dict):
    n       = report["scored"]
    passed  = report["passed"]
    flagged = report["flagged"]
    rate    = report["pass_rate"]

    print(f"\n{'═'*60}")
    print(f"  FLOWBENCH AUTO-SCORER REPORT")
    print(f"{'═'*60}")
    n_complete = report.get("complete_traces", n)
    n_skipped  = report.get("skipped_incomplete", 0)
    print(f"  Total files:  {n}")
    print(f"  Complete:     {n_complete}  (incomplete/skipped: {n_skipped})")
    print(f"  Passed (≥6):  {passed}  ({rate}% of complete)")
    print(f"  Flagged (<6): {flagged}  ({100-rate:.1f}% of complete)")
    print(f"  Avg score:    {report['avg_score']:.2f} / {MAX_SCORE}")
    print()

    print(f"  Score distribution:")
    for score, count in sorted(report["score_distribution"].items(), key=lambda x: int(x[0])):
        bar   = "█" * count
        label = "← threshold" if int(score) == PASS_THRESHOLD else ""
        print(f"    {score}/8  {bar:<30} {count:>3}  {label}")
    print()

    print(f"  Rule pass rates:")
    for rule, rate in sorted(report["rule_pass_rates"].items()):
        bar  = "█" * int(rate / 5)
        icon = "✓" if rate >= 80 else ("△" if rate >= 60 else "✗")
        # Map rule key back to full name
        rule_names = {
            "R1": "ends_with_done",
            "R2": "no_calls_after_done",
            "R3": "think_before_every_call",
            "R4": "no_hallucinated_ids",
            "R5": "confirmation_before_send",
            "R6": "tool_sequence_match",
            "R7": "done_summary_quality",
            "R8": "step_efficiency",
        }
        full = rule_names.get(rule, rule)
        print(f"    {icon} {rule} {full:<30} {rate:>5.1f}%  {bar}")
    print()

    if report["top_failure_rules"]:
        print(f"  Top failure rules (fix these first):")
        for rule, count in report["top_failure_rules"]:
            pct = round(count / n * 100, 1) if n else 0
            print(f"    • {rule}: {count} failures ({pct}%)")
    print()

    if report["category_breakdown"]:
        print(f"  Category pass rates:")
        for cat, stats in sorted(report["category_breakdown"].items()):
            t = stats["total"]
            p = stats["passed"]
            pct = round(p / t * 100) if t else 0
            bar = "█" * (pct // 10)
            print(f"    {cat:<16} {p}/{t}  {pct:>3}%  {bar}")
    print()
    print(f"{'═'*60}\n")


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Auto-score candidate trajectories from the generation loop",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""
        Examples:
          # Score all candidates
          python auto_scorer.py --input candidates/

          # Strict threshold
          python auto_scorer.py --input candidates/ --threshold 7

          # Verbose — show per-file breakdown
          python auto_scorer.py --input candidates/ --verbose

          # Save report to disk
          python auto_scorer.py --input candidates/ --report scoring_report.json
        """)
    )
    parser.add_argument("--input",     required=True, help="Folder of candidate JSON files")
    parser.add_argument("--passed",    default="passed",  help="Output folder for passing traces")
    parser.add_argument("--flagged",   default="flagged", help="Output folder for flagged traces")
    parser.add_argument("--threshold", type=int, default=PASS_THRESHOLD,
                        help=f"Min score to pass (default {PASS_THRESHOLD})")
    parser.add_argument("--report",    default="scoring_report.json",
                        help="Where to save the full scoring report JSON")
    parser.add_argument("--verbose",   action="store_true",
                        help="Print per-file rule breakdown")
    args = parser.parse_args()

    scorer  = AutoScorer(threshold=args.threshold)
    batch   = BatchScorer(scorer, passed_dir=args.passed, flagged_dir=args.flagged)

    print(f"\nFoundry Flow — Auto Scorer")
    print(f"Input:     {args.input}")
    print(f"Threshold: {args.threshold}/{MAX_SCORE}")
    print(f"Passed →   {args.passed}/")
    print(f"Flagged →  {args.flagged}/")

    report = batch.run(args.input, verbose=args.verbose)
    if not report:
        return

    print_report(report)

    # Save report (drop full results list to keep it readable — save separately)
    summary = {k: v for k, v in report.items() if k != "results"}
    with open(args.report, "w") as f:
        json.dump(summary, f, indent=2)

    # Save per-file results separately
    results_path = args.report.replace(".json", "_details.json")
    with open(results_path, "w") as f:
        json.dump(report["results"], f, indent=2)

    print(f"  Report saved:  {args.report}")
    print(f"  Details saved: {results_path}\n")


if __name__ == "__main__":
    main()
