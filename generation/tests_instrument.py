#!/usr/bin/env python3
"""
Tests for the value-ledger + acceptance-rate instrumentation -- PLAN.md
Phase 1 Step 15.

Run: `.venv/bin/python generation/tests_instrument.py` (from the MIS repo
root, or anywhere -- imports resolve relative to this file's directory).

Same `check(cond, msg)` harness as tests_notify.py/tests_gate.py. All
numbers below are hand-computed in the test itself (see the inline
arithmetic in each assertion) -- this file is the primary hand-check that
instrument.py's ratios/rollups are actually correct, not just "doesn't
crash." Fixtures are held in memory (or written to the OS tmp dir for the
one CLI-level test) -- never generation/fixtures/.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

import instrument as I

HERE = Path(__file__).resolve().parent


def check(cond, msg):
    if not cond:
        print(f"FAIL: {msg}")
        sys.exit(1)
    print(f"PASS: {msg}")


# ---------------------------------------------------------------------------
# 1. _safe_ratio -- the null-safety primitive everything else depends on
# ---------------------------------------------------------------------------
def test_safe_ratio_edge_cases():
    check(I._safe_ratio(3, 4) == 0.75, "safe_ratio(3,4) == 0.75")
    check(I._safe_ratio(0, 0) is None, "safe_ratio(0,0) is None (zero-reviewed edge case)")
    check(I._safe_ratio(None, 10) is None, "safe_ratio(None, 10) is None (unmeasured numerator)")
    check(I._safe_ratio(5, None) is None, "safe_ratio(5, None) is None (no denominator)")
    check(I._safe_ratio(0, 5) == 0.0, "safe_ratio(0,5) == 0.0 (measured zero, not None)")


# ---------------------------------------------------------------------------
# 2. Filter pass/drop rates -- per-run and rolling
# ---------------------------------------------------------------------------
def _run_row(path, run_date, **overrides):
    row = {"run_date": run_date, "run_id": f"gen-{run_date}", "path": path,
           "candidates_generated": 100, "passed_filter": 60, "borderline": 30,
           "rejected": 10, "llm_calls": 5, "duration_seconds": 20.0,
           "status": "success", "notes": None}
    row.update(overrides)
    return row


def test_compute_filter_rates_hand_computed():
    anchor_rows = [_run_row("keyword", "2026-07-04", candidates_generated=60,
                             passed_filter=40, borderline=15, rejected=5)]
    window_rows = [
        _run_row("keyword", "2026-07-04", candidates_generated=60, passed_filter=40,
                 borderline=15, rejected=5),
        _run_row("keyword", "2026-07-03", candidates_generated=40, passed_filter=20,
                 borderline=15, rejected=5),
    ]
    result = I.compute_filter_rates(anchor_rows, window_rows)

    per_run = result["per_run"]["keyword"]
    check(per_run["pass_rate"] == round(40 / 60, 4),
          f"per-run pass_rate hand-computed, got {per_run['pass_rate']}")
    check(per_run["pass_rate"] == 0.6667, "per-run pass_rate rounds to 0.6667")

    rolling = result["rolling"]["keyword"]
    # 60+40=100 generated, 40+20=60 passed, 15+15=30 borderline, 5+5=10 rejected
    check(rolling["candidates_generated"] == 100, "rolling candidates_generated == 60+40")
    check(rolling["passed_filter"] == 60, "rolling passed_filter == 40+20")
    check(rolling["pass_rate"] == 0.6, f"rolling pass_rate == 60/100 == 0.6, got {rolling['pass_rate']}")
    check(rolling["borderline_rate"] == 0.3, "rolling borderline_rate == 30/100 == 0.3")
    check(rolling["drop_rate"] == 0.1, "rolling drop_rate == 10/100 == 0.1")


def test_compute_filter_rates_unmeasured_path_stays_none():
    """Paths that never populate passed_filter/borderline/rejected (e.g.
    "copy"/"angle") must report None, not a misleading 0.0, even summed
    across a multi-row window."""
    rows = [
        _run_row("copy", "2026-07-04", candidates_generated=20, passed_filter=None,
                 borderline=None, rejected=None),
        _run_row("copy", "2026-07-03", candidates_generated=15, passed_filter=None,
                 borderline=None, rejected=None),
    ]
    result = I.compute_filter_rates([rows[0]], rows)
    check(result["per_run"]["copy"]["pass_rate"] is None, "per-run copy pass_rate is None, not 0.0")
    check(result["rolling"]["copy"]["pass_rate"] is None, "rolling copy pass_rate is None across the window")
    check(result["rolling"]["copy"]["candidates_generated"] == 35,
          "rolling candidates_generated still sums correctly even when other fields are unmeasured")


# ---------------------------------------------------------------------------
# 3. Acceptance rate per asset type -- including the zero-reviewed edge case
# ---------------------------------------------------------------------------
def test_compute_acceptance_rates_hand_computed_and_zero_reviewed():
    rows_by_asset_type = {
        "keyword": [{"status": "approved"}, {"status": "approved"}, {"status": "rejected"},
                    {"status": "proposed"}, {"status": "needs_review"}],
        "copy": [{"status": "proposed"}, {"status": "needs_review"}],  # zero-reviewed edge case
        "angle": [],  # also zero-reviewed
    }
    result = I.compute_acceptance_rates(rows_by_asset_type)

    kw = result["keyword"]
    check(kw["approved"] == 2 and kw["rejected"] == 1, "keyword approved=2 rejected=1")
    check(kw["acceptance_rate"] == round(2 / 3, 4),
          f"keyword acceptance_rate == 2/3, got {kw['acceptance_rate']}")

    copy_ = result["copy"]
    check(copy_["approved"] == 0 and copy_["rejected"] == 0, "copy has zero approved/rejected")
    check(copy_["acceptance_rate"] is None,
          "copy acceptance_rate is None (zero-reviewed edge case), no ZeroDivisionError")

    angle = result["angle"]
    check(angle["acceptance_rate"] is None, "angle (empty rows) acceptance_rate is None")

    combined = result["_combined"]
    check(combined["approved"] == 2 and combined["rejected"] == 1,
          "combined totals sum across all three asset types")
    check(combined["acceptance_rate"] == round(2 / 3, 4), "combined acceptance_rate == 2/3")


def test_compute_acceptance_rates_all_zero_combined_is_none():
    rows_by_asset_type = {"keyword": [], "copy": [], "angle": []}
    result = I.compute_acceptance_rates(rows_by_asset_type)
    check(result["_combined"]["acceptance_rate"] is None,
          "fully zero-reviewed pipeline -> combined acceptance_rate is None, no crash")


# ---------------------------------------------------------------------------
# 4. value_ledger rollup + waste-eliminated headline figure
# ---------------------------------------------------------------------------
def _ledger_row(action_type, status, expected=None, realized=None, run_date="2026-07-04"):
    return {"run_date": run_date, "entry_id": f"vl-{action_type}-{status}-{expected}-{realized}",
            "action_type": action_type, "description": None, "campaign_name": None,
            "segment": None, "expected_impact_inr": expected, "realized_impact_inr": realized,
            "realized_at": None, "status": status}


def test_value_ledger_rollup_hand_computed():
    rows = [
        _ledger_row("negative_added", "projected", expected=650.76),
        _ledger_row("negative_added", "realized", expected=100.0, realized=90.0),
        _ledger_row("keyword_launched", "projected", expected=200.0),
    ]
    rollup = I.compute_value_ledger_rollup(rows)

    neg = rollup["negative_added"]
    check(neg["count"] == 2, "negative_added count == 2")
    check(neg["expected_impact_inr"] == 750.76, f"negative_added expected sum == 750.76, got {neg['expected_impact_inr']}")
    check(neg["realized_impact_inr"] == 90.0, "negative_added realized sum == 90.0 (only the realized row)")
    check(neg["status_counts"] == {"projected": 1, "realized": 1}, "negative_added status_counts correct")

    kl = rollup["keyword_launched"]
    check(kl["count"] == 1 and kl["realized_impact_inr"] == 0.0,
          "keyword_launched has no realized entries -> realized sum is 0.0, not skipped")


def test_waste_eliminated_prefers_realized_over_projected():
    rows_with_realized = [
        _ledger_row("negative_added", "projected", expected=650.76),
        _ledger_row("negative_added", "realized", expected=100.0, realized=90.0),
    ]
    waste = I.compute_waste_eliminated(rows_with_realized)
    check(waste["basis"] == "realized", "waste basis is 'realized' when any realized row exists")
    check(waste["amount_inr"] == 90.0, "waste amount uses only the realized row's realized_impact_inr")

    rows_only_projected = [_ledger_row("negative_added", "projected", expected=650.76)]
    waste2 = I.compute_waste_eliminated(rows_only_projected)
    check(waste2["basis"] == "projected", "falls back to 'projected' basis when nothing realized yet")
    check(waste2["amount_inr"] == 650.76, "projected fallback sums expected_impact_inr")

    waste_empty = I.compute_waste_eliminated([])
    check(waste_empty["amount_inr"] == 0.0 and waste_empty["basis"] == "projected",
          "no negative_added rows at all -> 0.0, projected basis, no crash")


# ---------------------------------------------------------------------------
# 5. CPQL trend
# ---------------------------------------------------------------------------
def test_cpql_trend_hand_computed():
    current = [{"cpql_inr": 300.0}, {"cpql_inr": 400.0}]   # avg 350.0
    prior = [{"cpql_inr": 500.0}]                           # avg 500.0
    trend = I.compute_cpql_trend(current, prior)
    check(trend["current_avg_cpql_inr"] == 350.0, "current avg CPQL == 350.0")
    check(trend["prior_avg_cpql_inr"] == 500.0, "prior avg CPQL == 500.0")
    # (350 - 500) / 500 * 100 = -30.0
    check(trend["pct_change"] == -30.0, f"pct_change == -30.0, got {trend['pct_change']}")


def test_cpql_trend_no_prior_baseline():
    trend = I.compute_cpql_trend([{"cpql_inr": 350.0}], [])
    check(trend["prior_avg_cpql_inr"] is None, "no prior rows -> prior_avg is None")
    check(trend["pct_change"] is None, "no prior baseline -> pct_change is None, not a crash")


def test_cpql_trend_all_null_values():
    trend = I.compute_cpql_trend([{"cpql_inr": None}], [{"cpql_inr": None}])
    check(trend["current_avg_cpql_inr"] is None, "all-null cpql_inr rows -> current avg is None")
    check(trend["pct_change"] is None, "all-null cpql_inr rows -> pct_change is None")


# ---------------------------------------------------------------------------
# 6. Headline block format
# ---------------------------------------------------------------------------
def test_render_headline_format():
    waste = {"amount_inr": 650.76, "basis": "projected"}
    cpql_trend = {"current_avg_cpql_inr": 350.0, "prior_avg_cpql_inr": 500.0, "pct_change": -30.0}
    combined = {"acceptance_rate": 0.6667}
    headline = I.render_headline(waste, cpql_trend, combined)

    check("waste eliminated" in headline, "headline mentions waste eliminated")
    check("CPQL trend" in headline, "headline mentions CPQL trend")
    check("acceptance" in headline, "headline mentions acceptance")
    check("₹650.76" in headline, "headline includes the waste ₹ figure")
    check("(projected)" in headline, "headline flags a projected (not yet realized) figure")
    check("-30.0%" in headline, "headline includes the CPQL pct change")
    check("66.7%" in headline, "headline includes the acceptance percentage")


def test_render_headline_zero_reviewed_and_no_baseline():
    waste = {"amount_inr": 0.0, "basis": "projected"}
    cpql_trend = {"current_avg_cpql_inr": None, "prior_avg_cpql_inr": None, "pct_change": None}
    combined = {"acceptance_rate": None}
    headline = I.render_headline(waste, cpql_trend, combined)
    check("insufficient data" in headline, "no CPQL data yet -> headline says so, not '0%' or a crash")
    check("no reviews yet" in headline, "zero-reviewed -> headline says so, not '0.0%'")


# ---------------------------------------------------------------------------
# 7. Milestones
# ---------------------------------------------------------------------------
def test_milestones():
    m = I.compute_milestones("2026-08-05", start_date="2026-07-01")  # 35 days elapsed
    check(m["elapsed_days"] == 35, f"elapsed_days == 35, got {m['elapsed_days']}")
    check(m["gates"]["30"]["reached"] is True, "30-day gate reached at 35 elapsed days")
    check(m["gates"]["60"]["reached"] is False, "60-day gate not reached at 35 elapsed days")
    check(m["gates"]["60"]["days_remaining"] == 25, "60-day gate has 25 days remaining")
    check(m["gates"]["90"]["reached"] is False, "90-day gate not reached at 35 elapsed days")


# ---------------------------------------------------------------------------
# 8. build_report end-to-end (fixture-driven, no BQ/network)
# ---------------------------------------------------------------------------
FULL_FIXTURE = {
    "generation_runs": [_run_row("keyword", "2026-07-04")],
    "keyword_candidates": [{"run_date": "2026-07-04", "status": "approved"},
                            {"run_date": "2026-07-04", "status": "rejected"}],
    "ad_copy_drafts": [{"run_date": "2026-07-04", "status": "proposed"}],
    "audience_angles": [],
    "value_ledger": [_ledger_row("negative_added", "projected", expected=650.76)],
    "lead_quality_scores": [{"run_date": "2026-07-04", "cpql_inr": 350.0}],
}


def test_build_report_end_to_end():
    report = I.build_report("2026-07-04", 7, FULL_FIXTURE)
    check(report["anchor_run_date"] == "2026-07-04", "report anchor_run_date matches input")
    check(report["window"]["days"] == 7, "report window days matches --window-days")
    check(report["acceptance"]["keyword"]["acceptance_rate"] == 0.5,
          "end-to-end: keyword acceptance_rate == 1 approved / (1 approved + 1 rejected) == 0.5")
    check(report["waste_eliminated"]["amount_inr"] == 650.76, "end-to-end waste figure matches ledger fixture")
    check("waste eliminated" in report["headline"], "end-to-end headline assembled correctly")

    text = I.render_report_text(report)
    check("Generation instrumentation report" in text, "rendered text has the report banner")
    check("30/60/90 milestones" in text, "rendered text includes the milestones section")


def test_report_never_writes_to_bq():
    import ast
    import inspect
    tree = ast.parse(inspect.getsource(I))
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)]
    write_calls = [
        c for c in calls
        if (isinstance(c.func, ast.Attribute) and c.func.attr == "write_rows")
        or (isinstance(c.func, ast.Name) and c.func.id == "write_rows")
    ]
    check(not write_calls,
          "instrument.py never calls *.write_rows(...) anywhere in its actual code "
          "(docstring mentions of the invariant don't count) -- read-only, report-only")
    check(not hasattr(I, "bq_writer"),
          "instrument.py doesn't even import bq_writer -- it only reuses notify_slack's "
          "read-only client, no write module in scope at all")


# ---------------------------------------------------------------------------
# 9. CLI end-to-end (subprocess), fixture-driven
# ---------------------------------------------------------------------------
def test_cli_end_to_end():
    with tempfile.TemporaryDirectory() as td:
        fixture_path = Path(td) / "instrument_fixture.json"
        fixture_path.write_text(json.dumps(FULL_FIXTURE), encoding="utf-8")

        result = subprocess.run(
            [sys.executable, str(HERE / "instrument.py"),
             "--from-fixture", str(fixture_path), "--run-date", "2026-07-04",
             "--window-days", "7"],
            capture_output=True, text=True, timeout=30,
        )
        check(result.returncode == 0, f"CLI exits 0, stderr={result.stderr[-500:]}")
        check("waste eliminated" in result.stdout, "CLI text output includes the headline")

        result_json = subprocess.run(
            [sys.executable, str(HERE / "instrument.py"),
             "--from-fixture", str(fixture_path), "--run-date", "2026-07-04", "--json"],
            capture_output=True, text=True, timeout=30,
        )
        check(result_json.returncode == 0, "CLI --json exits 0")
        parsed = json.loads(result_json.stdout)
        check(parsed["anchor_run_date"] == "2026-07-04", "CLI --json output parses and matches anchor")


def main():
    test_safe_ratio_edge_cases()
    test_compute_filter_rates_hand_computed()
    test_compute_filter_rates_unmeasured_path_stays_none()
    test_compute_acceptance_rates_hand_computed_and_zero_reviewed()
    test_compute_acceptance_rates_all_zero_combined_is_none()
    test_value_ledger_rollup_hand_computed()
    test_waste_eliminated_prefers_realized_over_projected()
    test_cpql_trend_hand_computed()
    test_cpql_trend_no_prior_baseline()
    test_cpql_trend_all_null_values()
    test_render_headline_format()
    test_render_headline_zero_reviewed_and_no_baseline()
    test_milestones()
    test_build_report_end_to_end()
    test_report_never_writes_to_bq()
    test_cli_end_to_end()
    print("\nAll instrument tests passed.")


if __name__ == "__main__":
    main()
