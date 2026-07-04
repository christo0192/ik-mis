#!/usr/bin/env python3
"""
Value-ledger + acceptance-rate instrumentation -- PLAN.md Phase 1 Step 15
("Instrument the value ledger + acceptance tracking, the auto-exec gate
metric").

**Read-only, report-only.** This module writes nothing to BigQuery (no
`bq_writer.write_rows()` call anywhere in this file) -- every number it
prints is a derived aggregation over data other jobs already logged
(`generate_assets.py` -> `generation_runs`; `gate_keywords.py` ->
`keyword_candidates`/`keyword_rejects`; the dashboard's approve/reject
writes -> `status` columns; `export_negatives.py` -> `value_ledger`;
`lead_quality.py` -> `lead_quality_scores`). Step 15's own risk note calls
this "logging/derived metrics only" -- this file is the "derived metrics"
half; the "logging" half already happens inside those other jobs.

Computes, over a trailing `--window-days` window ending on the anchor run
date:

1. **Filter pass/drop rates** -- per the single latest run (`generation_runs`
   grouped by `path`) and rolling (summed across the window).
2. **Acceptance rate per asset type** -- `approved / (approved + rejected)`,
   null-safe (None, not a ZeroDivisionError, when neither status has been
   set yet -- the "zero-reviewed" edge case), for `keyword_candidates`
   ("keyword"), `ad_copy_drafts` ("copy"), `audience_angles` ("angle").
3. **Weekly `value_ledger` rollup** -- expected vs. realized ₹ impact,
   grouped by `action_type`, over the window.
4. **CPQL trend** -- average `cpql_inr` from `lead_quality_scores` this
   window vs. the prior window of equal length.
5. **The headline block**: "waste eliminated ₹ · CPQL trend · acceptance %".
6. **30/60/90 milestone tracking** against `GENERATION_PROJECT_START_DATE`.

Read strategy mirrors notify_slack.py exactly (fixture > live BQ > local
DRY_RUN staging preview > empty) -- this module reuses notify_slack's lazy
read client and table-name resolution rather than duplicating them.

Usage:
    python3 instrument.py                                   # 7-day window, prints report
    python3 instrument.py --window-days 30
    python3 instrument.py --run-date 2026-07-04 --window-days 7
    python3 instrument.py --from-fixture /tmp/instrument_fixture.json
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from collections import Counter, defaultdict
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

import notify_slack as ns  # reuse: get_read_client, _table_fqn, resolve_run_date, staging fallback

logging.basicConfig(level=logging.INFO,
                     format="%(asctime)s [%(levelname)s] %(message)s",
                     datefmt="%Y-%m-%d %H:%M:%S")
log = logging.getLogger("generation.instrument")

# Approximate Phase-0 build start (PLAN.md status log, early July 2026) --
# override via env once a firmer "Phase 1 launch" date is agreed.
PROJECT_START_DATE = os.environ.get("GENERATION_PROJECT_START_DATE", "2026-07-01")

MILESTONES = (30, 60, 90)

ASSET_TYPE_TABLES = {
    "keyword": "keyword_candidates",
    "copy": "ad_copy_drafts",
    "angle": "audience_angles",
}


# ---------------------------------------------------------------------------
# Range reads (window-scoped, unlike notify_slack's single-day fetch) --
# same fixture > live BQ > staging-preview fallback chain.
# ---------------------------------------------------------------------------
def fetch_range_rows_bq(logical_table: str, start: str, end: str,
                         date_col: str = "run_date") -> list[dict]:
    from google.cloud import bigquery
    client = ns.get_read_client()
    table_id = ns._table_fqn(logical_table)
    query = f"SELECT * FROM `{table_id}` WHERE {date_col} BETWEEN @start AND @end"
    job_config = bigquery.QueryJobConfig(query_parameters=[
        bigquery.ScalarQueryParameter("start", "DATE", start),
        bigquery.ScalarQueryParameter("end", "DATE", end),
    ])
    return [dict(r.items()) for r in client.query(query, job_config=job_config).result(timeout=120)]


def fetch_range_rows(logical_table: str, start: str, end: str,
                      fixture: Optional[dict], date_col: str = "run_date") -> tuple[list[dict], str]:
    """Returns (rows, source_label). source_label in
    "fixture" / "bq" / "staging-preview" / "none"."""
    if fixture is not None:
        rows = [r for r in fixture.get(logical_table, [])
                 if r.get(date_col) and start <= str(r[date_col]) <= end]
        return rows, "fixture"
    try:
        rows = fetch_range_rows_bq(logical_table, start, end, date_col)
        if rows:
            return rows, "bq"
    except Exception as e:
        log.debug("fetch_range_rows_bq(%s): %s", logical_table, e)
    staging_rows = [r for r in ns.fetch_table_rows_staging(logical_table)
                     if r.get(date_col) and start <= str(r[date_col]) <= end]
    if staging_rows:
        return staging_rows, "staging-preview"
    return [], "none"


def _window_bounds(anchor: str, window_days: int) -> tuple[str, str]:
    end_d = date.fromisoformat(anchor)
    start_d = end_d - timedelta(days=window_days - 1)
    return str(start_d), str(end_d)


def _prior_window_bounds(current_start: str, window_days: int) -> tuple[str, str]:
    start_d = date.fromisoformat(current_start)
    prior_end = start_d - timedelta(days=1)
    prior_start = prior_end - timedelta(days=window_days - 1)
    return str(prior_start), str(prior_end)


def _safe_ratio(numerator: Optional[float], denominator: Optional[float]) -> Optional[float]:
    """None (not ZeroDivisionError/NaN, and not silently coerced to a
    misleading 0.0) when the denominator is 0/None/missing, OR when the
    numerator itself was never measured (None) -- e.g. the "copy"/"angle"
    generation_runs rows don't track passed_filter/borderline/rejected at
    all (those are keyword-path-only filter concepts), so a None numerator
    there means "not applicable," not "zero passed." This is also the
    "zero-reviewed" edge case this whole module must survive without
    crashing a scheduled run."""
    if not denominator or numerator is None:
        return None
    return round(numerator / denominator, 4)


# ---------------------------------------------------------------------------
# 1. Filter pass/drop rates
# ---------------------------------------------------------------------------
def compute_filter_rates(anchor_rows: list[dict], window_rows: list[dict]) -> dict:
    """`anchor_rows` = generation_runs rows for the single latest run_date
    (grouped by path); `window_rows` = generation_runs rows across the
    whole rolling window (summed, then rated)."""
    per_run = {}
    for r in anchor_rows:
        path = r.get("path") or "unknown"
        gen = r.get("candidates_generated")
        per_run[path] = {
            "candidates_generated": gen,
            "passed_filter": r.get("passed_filter"),
            "borderline": r.get("borderline"),
            "rejected": r.get("rejected"),
            "pass_rate": _safe_ratio(r.get("passed_filter"), gen),
            "borderline_rate": _safe_ratio(r.get("borderline"), gen),
            "drop_rate": _safe_ratio(r.get("rejected"), gen),
        }

    # `_measured` tracks, per path, whether ANY row in the window actually
    # reported a given field -- paths like "copy"/"angle" never populate
    # passed_filter/borderline/rejected (filter-pass concepts are keyword-
    # path-only), so a summed 0 there must stay None ("not applicable"),
    # not read as "zero passed" (same distinction _safe_ratio enforces for
    # a single row; here it must survive a sum across many rows).
    sums = defaultdict(lambda: {"candidates_generated": 0, "passed_filter": 0,
                                 "borderline": 0, "rejected": 0,
                                 "_measured": defaultdict(bool)})
    for r in window_rows:
        path = r.get("path") or "unknown"
        s = sums[path]
        s["candidates_generated"] += r.get("candidates_generated") or 0
        for k in ("passed_filter", "borderline", "rejected"):
            v = r.get(k)
            if v is not None:
                s[k] += v
                s["_measured"][k] = True

    rolling = {}
    for path, s in sums.items():
        gen = s["candidates_generated"]
        measured = s["_measured"]
        passed = s["passed_filter"] if measured["passed_filter"] else None
        borderline = s["borderline"] if measured["borderline"] else None
        rejected = s["rejected"] if measured["rejected"] else None
        rolling[path] = {
            "candidates_generated": gen,
            "passed_filter": passed, "borderline": borderline, "rejected": rejected,
            "pass_rate": _safe_ratio(passed, gen),
            "borderline_rate": _safe_ratio(borderline, gen),
            "drop_rate": _safe_ratio(rejected, gen),
        }

    return {"per_run": per_run, "rolling": rolling}


# ---------------------------------------------------------------------------
# 2. Acceptance rate per asset type
# ---------------------------------------------------------------------------
def compute_acceptance_rates(rows_by_asset_type: dict[str, list[dict]]) -> dict:
    """`approved / (approved + rejected)`, null-safe, per asset type, plus a
    combined total across all asset types."""
    result = {}
    total_approved = 0
    total_rejected = 0
    for asset_type, rows in rows_by_asset_type.items():
        counts = Counter(r.get("status") for r in rows)
        approved = counts.get("approved", 0)
        rejected = counts.get("rejected", 0)
        total_approved += approved
        total_rejected += rejected
        result[asset_type] = {
            "approved": approved, "rejected": rejected,
            "total_reviewed": approved + rejected,
            "acceptance_rate": _safe_ratio(approved, approved + rejected),
        }
    result["_combined"] = {
        "approved": total_approved, "rejected": total_rejected,
        "total_reviewed": total_approved + total_rejected,
        "acceptance_rate": _safe_ratio(total_approved, total_approved + total_rejected),
    }
    return result


# ---------------------------------------------------------------------------
# 3. value_ledger weekly rollup
# ---------------------------------------------------------------------------
def compute_value_ledger_rollup(rows: list[dict]) -> dict:
    by_action = defaultdict(lambda: {"count": 0, "expected_impact_inr": 0.0,
                                      "realized_impact_inr": 0.0, "status_counts": Counter()})
    for r in rows:
        action = r.get("action_type") or "other"
        b = by_action[action]
        b["count"] += 1
        b["expected_impact_inr"] += r.get("expected_impact_inr") or 0
        if r.get("status") == "realized":
            b["realized_impact_inr"] += r.get("realized_impact_inr") or 0
        b["status_counts"][r.get("status") or "unknown"] += 1

    return {
        action: {
            "count": b["count"],
            "expected_impact_inr": round(b["expected_impact_inr"], 2),
            "realized_impact_inr": round(b["realized_impact_inr"], 2),
            "status_counts": dict(b["status_counts"]),
        }
        for action, b in by_action.items()
    }


def compute_waste_eliminated(ledger_rows: list[dict]) -> dict:
    """Realized ₹ waste eliminated (negative_added, status=realized) if any
    exists; else falls back to the *projected* figure so the headline is
    never blank while a young pipeline has nothing realized yet."""
    negatives = [r for r in ledger_rows if r.get("action_type") == "negative_added"]
    realized = sum(r.get("realized_impact_inr") or 0 for r in negatives
                    if r.get("status") == "realized")
    if realized:
        return {"amount_inr": round(realized, 2), "basis": "realized"}
    projected = sum(r.get("expected_impact_inr") or 0 for r in negatives
                     if r.get("status") in ("projected", None))
    return {"amount_inr": round(projected, 2), "basis": "projected"}


# ---------------------------------------------------------------------------
# 4. CPQL trend
# ---------------------------------------------------------------------------
def compute_cpql_trend(current_rows: list[dict], prior_rows: list[dict]) -> dict:
    def _avg(rows):
        vals = [r.get("cpql_inr") for r in rows if r.get("cpql_inr") is not None]
        return round(sum(vals) / len(vals), 2) if vals else None

    current_avg = _avg(current_rows)
    prior_avg = _avg(prior_rows)
    pct_change = None
    if current_avg is not None and prior_avg:
        pct_change = round((current_avg - prior_avg) / prior_avg * 100, 2)
    return {"current_avg_cpql_inr": current_avg, "prior_avg_cpql_inr": prior_avg,
            "pct_change": pct_change}


# ---------------------------------------------------------------------------
# 6. 30/60/90 milestone tracking
# ---------------------------------------------------------------------------
def compute_milestones(anchor: str, start_date: str = PROJECT_START_DATE) -> dict:
    elapsed = (date.fromisoformat(anchor) - date.fromisoformat(start_date)).days
    return {
        "start_date": start_date,
        "elapsed_days": elapsed,
        "gates": {
            str(m): {"reached": elapsed >= m, "days_remaining": max(m - elapsed, 0)}
            for m in MILESTONES
        },
    }


# ---------------------------------------------------------------------------
# Headline block
# ---------------------------------------------------------------------------
def render_headline(waste: dict, cpql_trend: dict, acceptance_combined: dict) -> str:
    waste_str = f"₹{waste['amount_inr']:,.2f}" + (
        " (projected)" if waste["basis"] == "projected" else "")

    if cpql_trend["current_avg_cpql_inr"] is None:
        cpql_str = "insufficient data"
    elif cpql_trend["pct_change"] is None:
        cpql_str = f"₹{cpql_trend['current_avg_cpql_inr']:,.2f} (no prior-window baseline)"
    else:
        arrow = "down" if cpql_trend["pct_change"] < 0 else "up"
        cpql_str = (f"₹{cpql_trend['prior_avg_cpql_inr']:,.2f} -> "
                    f"₹{cpql_trend['current_avg_cpql_inr']:,.2f} "
                    f"({cpql_trend['pct_change']:+.1f}%, {arrow})")

    accept_rate = acceptance_combined["acceptance_rate"]
    accept_str = f"{accept_rate * 100:.1f}%" if accept_rate is not None else "no reviews yet"

    return f"waste eliminated {waste_str} · CPQL trend: {cpql_str} · acceptance {accept_str}"


# ---------------------------------------------------------------------------
# Full report
# ---------------------------------------------------------------------------
def build_report(anchor: str, window_days: int, fixture: Optional[dict] = None) -> dict:
    window_start, window_end = _window_bounds(anchor, window_days)
    prior_start, prior_end = _prior_window_bounds(window_start, window_days)

    run_anchor_rows, run_anchor_source = fetch_range_rows("generation_runs", anchor, anchor, fixture)
    run_window_rows, run_window_source = fetch_range_rows("generation_runs", window_start, window_end, fixture)
    filter_rates = compute_filter_rates(run_anchor_rows, run_window_rows)

    rows_by_asset_type = {}
    asset_sources = {}
    for asset_type, table in ASSET_TYPE_TABLES.items():
        rows, source = fetch_range_rows(table, window_start, window_end, fixture)
        rows_by_asset_type[asset_type] = rows
        asset_sources[asset_type] = source
    acceptance = compute_acceptance_rates(rows_by_asset_type)

    ledger_rows, ledger_source = fetch_range_rows("value_ledger", window_start, window_end, fixture)
    ledger_rollup = compute_value_ledger_rollup(ledger_rows)
    waste = compute_waste_eliminated(ledger_rows)

    cpql_current_rows, cpql_current_source = fetch_range_rows(
        "lead_quality_scores", window_start, window_end, fixture)
    cpql_prior_rows, _ = fetch_range_rows("lead_quality_scores", prior_start, prior_end, fixture)
    cpql_trend = compute_cpql_trend(cpql_current_rows, cpql_prior_rows)

    milestones = compute_milestones(anchor)

    headline = render_headline(waste, cpql_trend, acceptance["_combined"])

    return {
        "anchor_run_date": anchor,
        "window": {"start": window_start, "end": window_end, "days": window_days},
        "prior_window": {"start": prior_start, "end": prior_end},
        "sources": {
            "generation_runs_anchor": run_anchor_source, "generation_runs_window": run_window_source,
            **{f"acceptance_{k}": v for k, v in asset_sources.items()},
            "value_ledger": ledger_source, "lead_quality_scores": cpql_current_source,
        },
        "filter_rates": filter_rates,
        "acceptance": acceptance,
        "value_ledger_rollup": ledger_rollup,
        "waste_eliminated": waste,
        "cpql_trend": cpql_trend,
        "milestones": milestones,
        "headline": headline,
    }


def render_report_text(report: dict) -> str:
    lines = [
        f"=== Generation instrumentation report -- anchor {report['anchor_run_date']} "
        f"(window {report['window']['start']}..{report['window']['end']}) ===",
        "",
        report["headline"],
        "",
        "-- Filter pass/drop rates (latest run, by path) --",
    ]
    for path, s in sorted(report["filter_rates"]["per_run"].items()):
        lines.append(f"  {path}: generated={s['candidates_generated']} "
                      f"pass_rate={s['pass_rate']} borderline_rate={s['borderline_rate']} "
                      f"drop_rate={s['drop_rate']}")
    lines.append(f"-- Filter pass/drop rates (rolling {report['window']['days']}-day) --")
    for path, s in sorted(report["filter_rates"]["rolling"].items()):
        lines.append(f"  {path}: generated={s['candidates_generated']} "
                      f"pass_rate={s['pass_rate']} borderline_rate={s['borderline_rate']} "
                      f"drop_rate={s['drop_rate']}")

    lines.append("")
    lines.append("-- Acceptance rate by asset type --")
    for asset_type, s in report["acceptance"].items():
        if asset_type == "_combined":
            continue
        lines.append(f"  {asset_type}: approved={s['approved']} rejected={s['rejected']} "
                      f"acceptance_rate={s['acceptance_rate']}")
    combined = report["acceptance"]["_combined"]
    lines.append(f"  combined: approved={combined['approved']} rejected={combined['rejected']} "
                  f"acceptance_rate={combined['acceptance_rate']}")

    lines.append("")
    lines.append("-- value_ledger rollup (window, by action_type) --")
    for action, s in report["value_ledger_rollup"].items():
        lines.append(f"  {action}: count={s['count']} expected_inr={s['expected_impact_inr']} "
                      f"realized_inr={s['realized_impact_inr']} status={s['status_counts']}")

    lines.append("")
    lines.append("-- CPQL trend --")
    lines.append(f"  {report['cpql_trend']}")

    lines.append("")
    lines.append("-- 30/60/90 milestones --")
    m = report["milestones"]
    lines.append(f"  start_date={m['start_date']} elapsed_days={m['elapsed_days']}")
    for gate, g in m["gates"].items():
        lines.append(f"  {gate}-day gate: reached={g['reached']} days_remaining={g['days_remaining']}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def parse_args(argv=None):
    p = argparse.ArgumentParser(
        description="Value-ledger + acceptance-rate instrumentation -- "
                     "PLAN.md Phase 1 Step 15 (read-only, report-only).")
    p.add_argument("--window-days", type=int, default=7,
                    help="Rolling window size in days, ending on the anchor run date (default 7).")
    p.add_argument("--run-date", type=str, default=None,
                    help="Anchor run date YYYY-MM-DD (default: latest available across "
                         "fixture/live BQ/staging preview).")
    p.add_argument("--from-fixture", type=str, default=None,
                    help="Path to a JSON fixture with generation_runs/keyword_candidates/"
                         "ad_copy_drafts/audience_angles/value_ledger/lead_quality_scores "
                         "keys -- skips every BigQuery read entirely.")
    p.add_argument("--json", action="store_true",
                    help="Print the report as JSON instead of formatted text.")
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)

    fixture = None
    if args.from_fixture:
        fixture = json.loads(Path(args.from_fixture).read_text(encoding="utf-8"))

    anchor = ns.resolve_run_date(fixture, args.run_date)
    log.info("instrument: anchor=%s window_days=%d from_fixture=%s",
             anchor, args.window_days, args.from_fixture)

    report = build_report(anchor, args.window_days, fixture)
    if args.json:
        print(json.dumps(report, indent=2, default=str))
    else:
        print(render_report_text(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
