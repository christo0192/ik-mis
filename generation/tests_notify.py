#!/usr/bin/env python3
"""
Tests for the after-run Slack digest -- PLAN.md Phase 1 Step 13.

Run: `.venv/bin/python generation/tests_notify.py` (from the MIS repo root,
or anywhere -- imports resolve relative to this file's directory).

Same harness style as tests_gate.py/tests_export.py: a plain `check(cond,
msg)` printing PASS/FAIL and exiting 1 on the first failure -- no pytest
dependency. Everything here uses `--from-fixture`-style fixture dicts held
in memory (or written to the OS scratch/tmp dir for CLI-level tests) --
never generation/fixtures/ (owned by other in-flight work) and never a real
BigQuery read or a real Slack post.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

import notify_slack as ns

HERE = Path(__file__).resolve().parent


def check(cond, msg):
    if not cond:
        print(f"FAIL: {msg}")
        sys.exit(1)
    print(f"PASS: {msg}")


# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------
def _run_row(path, run_date="2026-07-04", **overrides):
    row = {"run_date": run_date, "run_id": "gen-1", "path": path,
           "candidates_generated": 60, "passed_filter": 40, "borderline": 15,
           "rejected": 5, "llm_calls": 8, "duration_seconds": 30.1,
           "status": "success", "notes": None}
    row.update(overrides)
    return row


def _kw_row(keyword, run_date="2026-07-04", **overrides):
    row = {"run_date": run_date, "run_id": "gen-1", "campaign_name": "IK-IN_NB_Test",
           "segment": "NB", "keyword": keyword, "match_type": "broad", "kind": "new",
           "relevance_verdict": "relevant", "relevance_score": 0.5,
           "filter_reason": None, "source_signal": "test fixture",
           "status": "proposed", "reviewed_by": None, "reviewed_at": None}
    row.update(overrides)
    return row


def _copy_row(headline, run_date="2026-07-04", **overrides):
    row = {"run_date": run_date, "run_id": "gen-1", "campaign_name": "IK-IN_NB_Test",
           "segment": "NB", "platform": "google", "asset_id": "cp-1",
           "headline": headline, "primary_text": None, "description": None,
           "cta": "Apply now", "source_signal": "test fixture", "status": "proposed",
           "reviewed_by": None, "reviewed_at": None, "edit_distance": None}
    row.update(overrides)
    return row


FULL_FIXTURE = {
    "generation_runs": [
        _run_row("keyword"),
        _run_row("copy", candidates_generated=20, passed_filter=None, borderline=None, rejected=None),
    ],
    "keyword_candidates": [
        _kw_row("agentic ai interview prep", relevance_verdict="relevant",
                relevance_score=0.92, status="approved"),
        _kw_row("free coding course pdf", relevance_verdict="irrelevant",
                relevance_score=0.1, status="rejected"),
        _kw_row("system design interview coaching", relevance_verdict="relevant",
                relevance_score=0.81, status="needs_review"),
        _kw_row("faang salary negotiation", relevance_verdict="borderline",
                relevance_score=0.55, status="proposed"),
        _kw_row("machine learning course free", relevance_verdict="irrelevant",
                relevance_score=0.05, status="rejected"),
        _kw_row("edgeup agentic ai program", relevance_verdict="relevant",
                relevance_score=0.88, status="approved"),
    ],
    "ad_copy_drafts": [
        _copy_row("Crack FAANG interviews", status="proposed"),
        _copy_row("Land your dream SDE role", status="approved"),
    ],
}

EMPTY_FIXTURE = {"generation_runs": [], "keyword_candidates": [], "ad_copy_drafts": []}


# ===========================================================================
# 1. build_digest -- structured counts
# ===========================================================================
def test_build_digest_counts_and_top():
    digest = ns.build_digest("2026-07-04", FULL_FIXTURE)

    check(digest["by_path"]["keyword"]["candidates_generated"] == 60,
          "by_path.keyword.candidates_generated == 60")
    check(digest["by_path"]["copy"]["passed_filter"] is None,
          "by_path.copy.passed_filter is None (copy path doesn't track filter stats)")

    kw = digest["keyword_candidates"]
    check(kw["total"] == 6, "keyword_candidates total == 6")
    check(kw["by_verdict"] == {"relevant": 3, "irrelevant": 2, "borderline": 1},
          f"keyword verdict counts correct, got {kw['by_verdict']}")
    check(kw["by_status"] == {"approved": 2, "rejected": 2, "needs_review": 1, "proposed": 1},
          f"keyword status counts correct, got {kw['by_status']}")
    check(len(kw["top"]) == 5, "top keyword list capped at TOP_N=5")
    check(kw["top"][0]["keyword"] == "agentic ai interview prep",
          "top keyword sorted by relevance_score desc (0.92 first)")
    scores = [item["relevance_score"] for item in kw["top"]]
    check(scores == sorted(scores, reverse=True), "top keyword scores strictly non-increasing")

    copy_ = digest["ad_copy_drafts"]
    check(copy_["total"] == 2, "ad_copy_drafts total == 2")
    check(copy_["by_status"] == {"proposed": 1, "approved": 1},
          f"copy status counts correct, got {copy_['by_status']}")

    check(digest["sources"]["generation_runs"] == "fixture", "source label is 'fixture'")
    check(digest["dashboard_url"] == ns.DASHBOARD_URL, "dashboard_url matches module default")


# ===========================================================================
# 2. Zero-data edge case
# ===========================================================================
def test_build_digest_empty():
    digest = ns.build_digest("2026-07-04", EMPTY_FIXTURE)
    check(digest["keyword_candidates"]["total"] == 0, "empty fixture -> 0 keyword candidates")
    check(digest["ad_copy_drafts"]["total"] == 0, "empty fixture -> 0 copy drafts")
    check(digest["by_path"] == {}, "empty fixture -> no by_path entries")
    check(digest["keyword_candidates"]["by_verdict"] == {}, "empty verdict counts, no crash")

    main_message, thread_detail = ns.render_digest_text(digest)
    check("No generation data found" in main_message,
          "empty digest renders an explicit 'no data' notice, not a fake 0-count claim")
    check(ns.DASHBOARD_URL in main_message, "dashboard link still present even with no data")


# ===========================================================================
# 3. render_digest_text -- notify-only, zero interactive elements
# ===========================================================================
def test_render_digest_text_content_and_shape():
    digest = ns.build_digest("2026-07-04", FULL_FIXTURE)
    main_message, thread_detail = ns.render_digest_text(digest)

    check(ns.DASHBOARD_URL in main_message, "dashboard deep link present in main message")
    check("2026-07-04" in main_message, "run_date present in main message")
    check("6 candidates" in main_message and "2 copy drafts" in main_message,
          "main message summarizes counts")

    for forbidden in ("\"blocks\"", "\"actions\"", "button", "callback_id"):
        check(forbidden not in main_message and forbidden not in thread_detail,
              f"no interactive-element artifact {forbidden!r} in rendered text (notify-only)")

    check("agentic ai interview prep" in thread_detail, "thread detail includes top keyword item")
    check("Crack FAANG interviews" in thread_detail or "Land your dream SDE role" in thread_detail,
          "thread detail includes a top copy item")


# ===========================================================================
# 4. resolve_run_date
# ===========================================================================
def test_resolve_run_date_explicit_override():
    got = ns.resolve_run_date(FULL_FIXTURE, "2026-01-01")
    check(got == "2026-01-01", "explicit --run-date always wins over fixture content")


def test_resolve_run_date_from_fixture_max():
    fixture = {
        "generation_runs": [_run_row("keyword", run_date="2026-07-01"),
                             _run_row("keyword", run_date="2026-07-03")],
        "keyword_candidates": [], "ad_copy_drafts": [],
    }
    got = ns.resolve_run_date(fixture, None)
    check(got == "2026-07-03", f"resolve_run_date picks the max run_date in the fixture, got {got}")


# ===========================================================================
# 5. post_digest -- dry-run never shells out
# ===========================================================================
def test_post_digest_dry_run_prints_and_returns_zero(capsys=None):
    import io
    import contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = ns.post_digest("MAIN MESSAGE", "THREAD DETAIL", dry_run=True)
    out = buf.getvalue()
    check(rc == 0, "post_digest dry_run returns 0")
    check("[DRY RUN]" in out, "dry-run output is clearly labeled")
    check("MAIN MESSAGE" in out and "THREAD DETAIL" in out,
          "dry-run prints both the main message and the thread detail")


# ===========================================================================
# 6. Notify-only invariant: no write path exists in this module
# ===========================================================================
def test_module_has_no_bq_write_calls():
    import ast
    import inspect
    tree = ast.parse(inspect.getsource(ns))
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)]
    write_calls = [
        c for c in calls
        if (isinstance(c.func, ast.Attribute) and c.func.attr == "write_rows")
        or (isinstance(c.func, ast.Name) and c.func.id == "write_rows")
    ]
    check(not write_calls,
          "notify_slack.py never calls *.write_rows(...) anywhere in its actual code "
          "-- notify-only, no BQ writes")


# ===========================================================================
# 7. CLI end-to-end (subprocess), dry-run only -- never posts for real
# ===========================================================================
def test_cli_end_to_end_dry_run():
    with tempfile.TemporaryDirectory() as td:
        fixture_path = Path(td) / "digest_fixture.json"
        fixture_path.write_text(json.dumps(FULL_FIXTURE), encoding="utf-8")

        result = subprocess.run(
            [sys.executable, str(HERE / "notify_slack.py"),
             "--from-fixture", str(fixture_path), "--run-date", "2026-07-04", "--dry-run"],
            capture_output=True, text=True, timeout=30,
        )
        check(result.returncode == 0, f"CLI exits 0, stderr={result.stderr[-500:]}")
        check("[DRY RUN]" in result.stdout, "CLI dry-run output is clearly labeled")
        check(ns.DASHBOARD_URL in result.stdout, "CLI output includes the dashboard link")
        check(":warning:" not in result.stdout,
              "CLI without --soft-failure renders no fail-soft warning")

        # run_daily.sh's exact passthrough: one --soft-failure per failed step
        result = subprocess.run(
            [sys.executable, str(HERE / "notify_slack.py"),
             "--from-fixture", str(fixture_path), "--run-date", "2026-07-04", "--dry-run",
             "--soft-failure", "registry_refresh(exit=4)"],
            capture_output=True, text=True, timeout=30, check=False,
        )
        main_part = result.stdout.split("--- thread reply ---")[0]
        check(result.returncode == 0
              and "Fail-soft step(s) failed this run: registry_refresh(exit=4)" in main_part,
              f"CLI --soft-failure flags the degraded step in the MAIN message "
              f"(rc={result.returncode}, stderr={result.stderr[-300:]})")


# ===========================================================================
# 8. fail-soft steps surfaced (run_daily.sh --soft-failure passthrough)
# ===========================================================================
def test_render_soft_failures():
    digest = ns.build_digest("2026-07-04", FULL_FIXTURE)
    main_message, _thread = ns.render_digest_text(digest)
    check(":warning:" not in main_message,
          "no soft_failures key -> no fail-soft warning line (unchanged digest)")
    digest["soft_failures"] = ["registry_refresh(exit=4)", "other_step(exit=1)"]
    main_message, _thread = ns.render_digest_text(digest)
    check(":warning: Fail-soft step(s) failed this run: registry_refresh(exit=4), "
          "other_step(exit=1)" in main_message and ns.DASHBOARD_URL in main_message,
          "soft_failures render as one warning line in the main message, link kept")


def main():
    test_build_digest_counts_and_top()
    test_build_digest_empty()
    test_render_digest_text_content_and_shape()
    test_resolve_run_date_explicit_override()
    test_resolve_run_date_from_fixture_max()
    test_post_digest_dry_run_prints_and_returns_zero()
    test_module_has_no_bq_write_calls()
    test_cli_end_to_end_dry_run()
    test_render_soft_failures()
    print("\nAll notify_slack tests passed.")


if __name__ == "__main__":
    main()
