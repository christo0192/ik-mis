#!/usr/bin/env python3
"""
Negative-keyword export + ₹ waste-baseline job -- PLAN.md Phase 1 Step 11
("Negative-keyword export -- the first shipped artifact").

Two things this job does, both read-only against ad platforms (Step 11's
own risk note: "read + CSV; nothing writes to platforms"):

1. **Waste-baseline analysis.** Loads `kw_search_term_perf` rows (interim
   source: the DRY_RUN preview file `staging/bq_preview_kw_search_term_perf
   .json` that `collect_kw_perf.py` writes -- see "--terms-source" below),
   runs every row's search term through `taxonomy.pre_filter()` (the same
   deterministic, ads-domain pre-filter Step 10's gate uses -- never an LLM
   here either), and sums the `cost_inr` of the terms it verdicts `"drop"`.
   That sum is the recoverable-waste baseline: a conservative *lower bound*
   (only unambiguously off-topic terms are counted; `"ambiguous"` terms --
   including anything that also matches a domain/interview-intent signal --
   are left to Step 10's real relevance gate or a human, never auto-counted
   as waste here). One `value_ledger` row (`action_type="waste_baseline"`)
   is written per run via the guarded writer (`bq_writer.write_rows`,
   DRY_RUN by default).

2. **Negative-keyword CSV export.** Merges three sources of negative-keyword
   candidates -- (a) `keyword_rejects` rows flagged `negative_candidate`,
   (b) the high-spend fast-dropped *search terms* found in the baseline
   analysis above (never a `term_type="keyword"` row -- you don't add your
   own targeted keyword as a negative), (c) strategist-proposed
   `kind="negative"` rows from `staging/raw_keyword_candidates_*.json` --
   dedupes on (campaign, keyword), and writes a Google Ads Editor-compatible
   CSV to `generation/exports/negatives_<date>.csv`. See "CSV format" below
   for the column choice + citation.

------------------------------------------------------------------------
TAXONOMY DEPENDENCY -- WHY THE INLINE PLACEHOLDER WAS REPLACED
------------------------------------------------------------------------
This job now imports `generation/taxonomy.py` (`pre_filter()`) directly --
a hard dependency, same directory, no import risk -- instead of the inline
`FAST_DROP_PATTERNS`/`fast_drop_reason()` placeholder this file originally
shipped with (Step 10's relevance gate, `gate_keywords.py`, had not landed
yet when this step first ran; it has since, and owns the authoritative
deterministic pre-filter + LLM-graded relevance judge).

**Why the placeholder had to go, not just get superseded eventually:** its
bare `\bcrack(ed)?\b` "piracy" pattern produced real false positives on
core India-market buyer intent -- it flagged `"crack the coding interview"`
and `"how to crack an interview for experienced candidates"` as piracy,
when "crack the interview" is standard Indian English for "succeed at the
interview," exactly IK's target search intent. Left running, this would
have silently exported genuinely converting queries as negative keywords.
`taxonomy.pre_filter()` fixes this by construction: `"crack the coding
interview"` matches its domain+interview-intent `FAST_PASS` vocabulary and
verdicts `"pass"`; `"how to crack an interview for experienced candidates"`
matches neither `FAST_PASS` nor `FAST_DROP` and verdicts `"ambiguous"` (no
domain-specific vocabulary present) -- neither case is ever treated as
waste. See `tests_export.py` for the regression assertions and the
module's own docstring for the full conservative-by-construction design
(a term matching *both* `FAST_PASS` and `FAST_DROP` -- e.g. "free system
design interview course pdf", domain+interview-relevant *and*
freebie-seeking -- also resolves to `"ambiguous"`, not a coin-flip drop).

Only `pre_filter()`'s `"drop"` verdict is ever treated as waste/counted
toward the ₹ baseline or turned into a negative-keyword CSV row from the
search-term source below. `"ambiguous"` verdicts are never auto-negatived
here -- they are exactly the pool Step 10's `claude -p` relevance judge
(`gate_keywords.py` / `brains/keyword_relevance_filter.md`) exists to weigh
with more context than a regex can bring; this job does not call that
judge itself (would be cost/latency-prohibitive at ~10k+ rows/day for a
scheduled baseline job), it only ever consumes the deterministic layer.

------------------------------------------------------------------------
CSV FORMAT -- Google Ads Editor campaign-level negative keywords
------------------------------------------------------------------------
Researched against Google's own Google Ads Editor Help docs (2026-07):
  - "Prepare a CSV file" (support.google.com/google-ads/editor/answer/56368):
    UTF-8/Unicode-safe CSV, one row per criterion, standard column headers
    recommended.
  - "CSV file columns" (support.google.com/google-ads/editor/answer/57747):
    the `Type`/`Criterion Type` column takes literal value `Negative` for an
    ad-group-level negative, and **`Campaign negative`** for a **campaign-
    level** negative (keyword, placement, gender, age, or audience row) --
    quoting the doc: "Enter `Campaign negative` to specify a campaign-level
    negative."
  - "Add negative keywords" (support.google.com/google-ads/editor/answer/
    30553): confirms `Match Type` for negatives uses the same three values
    as regular keywords -- `Broad`, `Phrase`, `Exact` -- not a separate
    "Negative Exact"/"Negative Phrase" vocabulary (a claim repeated by some
    third-party blogs but not present in Google's own column reference).
This job's columns: `Campaign, Ad Group, Keyword, Match Type, Criterion
Type` -- `Ad Group` is always blank (campaign-level negative, per PLAN.md
Step 11's "campaign targeting column from the row's campaign_name"),
`Criterion Type` is always the literal `Campaign negative`, `Match Type`
defaults to `Exact` (the conservative default: an exact-match negative
blocks only that literal query, never accidentally blocking a legitimate
broader query the way a broad-match negative could).

Usage:
    python3 export_negatives.py
    python3 export_negatives.py --terms-source staging --rejects-source fixture
    python3 export_negatives.py --min-spend-inr 500
    python3 export_negatives.py --terms-source bq --rejects-source bq --bq-days 90

DRY_RUN (default true, see config.py) governs the `value_ledger` write only
-- the CSV export and baseline analysis are pure reads, always performed
regardless of DRY_RUN (there is no "live" mode for a file export).
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
import re
import sys
from collections import defaultdict
from pathlib import Path

import bq_writer
import config
import taxonomy

log = logging.getLogger("generation.export_negatives")

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"
EXPORTS_DIR = Path(__file__).resolve().parent / "exports"
DEFAULT_REJECTS_FIXTURE = FIXTURES_DIR / "export_keyword_rejects_sample.json"
DEFAULT_TERMS_STAGING_FILE = config.STAGING_DIR / "bq_preview_kw_search_term_perf.json"


# ---------------------------------------------------------------------------
# Loaders
# ---------------------------------------------------------------------------
def _rows_from_json(data) -> list[dict]:
    """Accept either a bare list of row dicts or a bq_writer DRY_RUN preview
    envelope (`{"rows": [...], ...}`) -- both shapes appear in this repo's
    staging files."""
    if isinstance(data, dict):
        return data.get("rows", [])
    if isinstance(data, list):
        return data
    return []


def load_terms_staging(path: Path | None = None) -> list[dict]:
    """Load `kw_search_term_perf` rows from the DRY_RUN preview file
    `collect_kw_perf.py` writes. This is the interim data source PLAN.md
    Phase 0 Step 3 established, until the table is queryable live in BQ.

    NOTE (found while building this step): `bq_writer.py`'s DRY_RUN preview
    is ONE shared file per table (`staging/bq_preview_<table>.json`) --
    every caller that writes `kw_search_term_perf` (a real collector run, a
    smoke test, another job's test suite) overwrites the same file. A real
    10,855-row collection for 2026-07-02 was reportedly landed here earlier
    in this build, but by the time this step ran, the file held only the
    1-row sample `smoke_test.py` writes for every table it exercises (see
    README.md's "Step 11" section for the full writeup and the numbers this
    job actually produced against what was present). This loader reads
    whatever is there today -- it is not able to recover data a later
    writer already overwrote.
    """
    path = path or DEFAULT_TERMS_STAGING_FILE
    if not path.exists():
        log.warning("terms staging file not found at %s -- 0 rows", path)
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    return _rows_from_json(data)


def load_terms_bq(days: int) -> list[dict]:
    """Live read of `kw_search_term_perf`, trailing `days` days. Lazy-
    imports `google.cloud.bigquery` (never at module load, matching
    `bq_writer.py`'s own convention) so `--terms-source staging` (the
    default) works without the package installed. NOT exercised in this
    environment -- no BQ read credentials/table were available to verify
    this path end-to-end; it is written against `bq_writer.get_client()`
    and `schemas.KW_SEARCH_TERM_PERF_BQ`'s column list and should be spot-
    checked against a live query before being scheduled."""
    client = bq_writer.get_client()
    table_id = bq_writer.validate_target("kw_search_term_perf")
    query = f"""
        SELECT date, campaign_name, segment, keyword_or_term, term_type,
               match_type, impressions, clicks, ctr, avg_cpc_inr, cost_inr,
               quality_score
        FROM `{table_id}`
        WHERE date >= DATE_SUB(CURRENT_DATE(), INTERVAL {int(days)} DAY)
    """
    return [dict(row.items()) for row in client.query(query).result()]


def load_rejects_fixture(path: Path | None = None) -> list[dict]:
    """`keyword_rejects` has 0 real rows as of this step (Step 10's gate
    just landed, per this task's own briefing) -- read a representative
    fixture instead, shaped exactly like `schemas.KeywordRejectRow`."""
    path = path or DEFAULT_REJECTS_FIXTURE
    if not path.exists():
        log.warning("rejects fixture not found at %s -- 0 rows", path)
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    return _rows_from_json(data)


def load_rejects_bq(run_date: str | None = None) -> list[dict]:
    """Live read of `keyword_rejects` -- the real integration path for once
    Step 10's gate has written real rows. Reads the latest `run_date`'s
    rows when `run_date` is not given. NOT exercised in this environment
    (see `load_terms_bq`'s note -- same reason)."""
    client = bq_writer.get_client()
    table_id = bq_writer.validate_target("keyword_rejects")
    where = f"WHERE run_date = '{run_date}'" if run_date else ""
    query = f"""
        SELECT run_date, run_id, campaign_name, segment, keyword,
               reject_reason, relevance_score, source_signal,
               negative_candidate
        FROM `{table_id}`
        {where}
        ORDER BY run_date DESC
    """
    return [dict(row.items()) for row in client.query(query).result()]


def load_strategist_negatives(staging_dir: Path | None = None) -> list[dict]:
    """Read `kind="negative"` rows from every
    `staging/raw_keyword_candidates_<run_date>.json` file -- source (c) of
    PLAN.md Step 11's negative-CSV bullet: strategist-proposed negative
    candidates from the real Step 9 conductor run (see README.md's "Real
    (non-mocked) end-to-end proof" section) -- not a fixture; these are
    real rows already on disk today."""
    staging_dir = staging_dir or config.STAGING_DIR
    out = []
    if not staging_dir.exists():
        return out
    for fp in sorted(staging_dir.glob("raw_keyword_candidates_*.json")):
        try:
            data = json.loads(fp.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            log.warning("skipping unreadable %s: %s", fp, e)
            continue
        for r in _rows_from_json(data):
            if r.get("kind") == "negative":
                out.append(r)
    return out


# ---------------------------------------------------------------------------
# Waste-baseline analysis
# ---------------------------------------------------------------------------
def analyze_waste_baseline(term_rows: list[dict], min_spend_inr: float = 0.0) -> dict:
    """Run every row's `keyword_or_term` through `taxonomy.pre_filter()`; a
    row is waste-flagged **iff its verdict is `"drop"`** -- `"pass"` rows
    are obviously relevant and `"ambiguous"` rows (including anything that
    conflicts a domain/interview-intent signal against a drop signal, e.g.
    "free system design interview course pdf") are deliberately left alone
    here, not auto-counted as waste (see module docstring). Sum `cost_inr`
    of the `"drop"`-verdict rows, grouped by segment, plus a grand total and
    every flagged row (`all_offenders`, sorted by spend descending --
    `top_offenders` is the first 20). `min_spend_inr` filters which flagged
    rows count toward the totals/offender lists (a row below it is treated
    as not worth a negative-keyword action, not as relevant) -- CLI default
    0.0 (no filter, matching PLAN.md Step 11's own `--min-spend-inr N`
    default 0)."""
    per_segment: dict[str, float] = defaultdict(float)
    per_segment_count: dict[str, int] = defaultdict(int)
    offenders = []
    total = 0.0
    rows_scanned = 0
    rows_flagged = 0

    for row in term_rows:
        term = row.get("keyword_or_term") or ""
        rows_scanned += 1
        verdict, reason = taxonomy.pre_filter(term)
        if verdict != "drop":
            continue
        cost = float(row.get("cost_inr") or 0.0)
        if cost < min_spend_inr:
            continue
        rows_flagged += 1
        segment = row.get("segment") or "Unknown"
        per_segment[segment] += cost
        per_segment_count[segment] += 1
        total += cost
        offenders.append({
            "keyword_or_term": term,
            "campaign_name": row.get("campaign_name"),
            "segment": segment,
            "term_type": row.get("term_type"),
            "cost_inr": round(cost, 2),
            "reject_reason": reason,
            "date": row.get("date"),
        })

    offenders.sort(key=lambda r: r["cost_inr"], reverse=True)
    return {
        "rows_scanned": rows_scanned,
        "rows_flagged": rows_flagged,
        "total_recoverable_inr": round(total, 2),
        "per_segment_inr": {
            k: round(v, 2) for k, v in sorted(per_segment.items(), key=lambda kv: -kv[1])
        },
        "per_segment_count": dict(per_segment_count),
        "top_offenders": offenders[:20],
        "all_offenders": offenders,
    }


def _period_label(term_rows: list[dict]) -> str:
    dates_seen = sorted({str(r["date"]) for r in term_rows if r.get("date")})
    if not dates_seen:
        return "no dated rows in source"
    if dates_seen[0] == dates_seen[-1]:
        return dates_seen[0]
    return f"{dates_seen[0]} to {dates_seen[-1]}"


def build_value_ledger_row(baseline: dict, run_date: str, period_label: str) -> dict:
    """One `value_ledger` row, `action_type="waste_baseline"`,
    `expected_impact_inr` = the total recoverable ₹ found (`status`
    stays "projected" -- this is an estimate until negatives are actually
    applied and spend is re-measured, per `schemas.ValueLedgerRow`)."""
    seg_summary = "; ".join(
        f"{seg}: Rs {amt:,.2f}" for seg, amt in baseline["per_segment_inr"].items()
    ) or "none"
    description = (
        f"Recoverable-waste baseline for {period_label}: sum(cost_inr) of "
        f"search terms/keywords `taxonomy.pre_filter()` verdicts \"drop\" "
        f"(conservative lower bound -- deterministic pattern match only, no "
        f"LLM; \"ambiguous\" verdicts are never counted as waste here -- see "
        f"generation/taxonomy.py). "
        f"{baseline['rows_flagged']}/{baseline['rows_scanned']} rows flagged. "
        f"By segment: {seg_summary}."
    )
    return {
        "run_date": run_date,
        "entry_id": f"vl-waste-baseline-{run_date}",
        "action_type": "waste_baseline",
        "description": description,
        "campaign_name": None,
        "segment": None,
        "expected_impact_inr": baseline["total_recoverable_inr"],
        "realized_impact_inr": None,
        "realized_at": None,
        "status": "projected",
    }


# ---------------------------------------------------------------------------
# Negative-keyword CSV export
# ---------------------------------------------------------------------------
def _sanitize_keyword_for_csv(kw: str) -> str:
    """Collapse internal whitespace/newlines to single spaces and strip
    leading/trailing whitespace. Defense-in-depth normalize -- `csv.writer`
    already correctly quotes any field containing a comma, this just keeps
    the *content* clean (a keyword should never legitimately contain a
    newline or run of whitespace)."""
    return re.sub(r"\s+", " ", kw or "").strip()


def build_negative_rows(
    rejects_rows: list[dict],
    strategist_rows: list[dict],
    offender_rows: list[dict],
    default_match_type: str = "Exact",
) -> list[dict]:
    """Merge the three negative-keyword sources, dedupe on
    (campaign_name, keyword.lower()), and return Editor-ready dicts (keys:
    campaign_name, keyword, match_type, source, reason). A `keyword_rejects`
    row with `negative_candidate=False` is excluded -- it was kept as a
    keyword candidate, not proposed as a negative. An `offender_rows` entry
    is only used if `term_type == "search_term"` -- you never add your own
    targeted keyword (`term_type == "keyword"`) as a negative."""
    merged = []
    for r in rejects_rows:
        if not r.get("negative_candidate", True):
            continue
        merged.append({
            "campaign_name": r.get("campaign_name"),
            "keyword": r.get("keyword"),
            "match_type": default_match_type,
            "source": "keyword_rejects",
            "reason": r.get("reject_reason"),
        })
    for r in strategist_rows:
        merged.append({
            "campaign_name": r.get("campaign_name"),
            "keyword": r.get("keyword"),
            "match_type": default_match_type,
            "source": "strategist_candidate",
            "reason": r.get("source_signal"),
        })
    for r in offender_rows:
        if r.get("term_type") != "search_term":
            continue
        merged.append({
            "campaign_name": r.get("campaign_name"),
            "keyword": r.get("keyword_or_term"),
            "match_type": default_match_type,
            "source": "waste_baseline_fastdrop",
            "reason": r.get("reject_reason"),
        })

    seen = set()
    deduped = []
    for r in merged:
        kw = _sanitize_keyword_for_csv(r.get("keyword") or "")
        campaign = r.get("campaign_name") or ""
        if not kw or not campaign:
            continue
        key = (campaign, kw.lower())
        if key in seen:
            continue
        seen.add(key)
        r["keyword"] = kw
        deduped.append(r)
    return deduped


CSV_COLUMNS = ["Campaign", "Ad Group", "Keyword", "Match Type", "Criterion Type"]


def write_negatives_csv(rows: list[dict], out_path: Path) -> int:
    """Write a Google Ads Editor-compatible campaign-level negative-keyword
    CSV. See module docstring "CSV format" section for the column choice +
    citation. `Ad Group` is always blank (campaign-level negative);
    `Criterion Type` is always the literal `Campaign negative`."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(CSV_COLUMNS)
        for r in rows:
            writer.writerow([
                r.get("campaign_name") or "",
                "",
                r["keyword"],
                r.get("match_type") or "Exact",
                "Campaign negative",
            ])
    return len(rows)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Negative-keyword export + waste-baseline (PLAN.md Phase 1 Step 11)")
    parser.add_argument("--rejects-source", choices=["fixture", "bq"], default="fixture")
    parser.add_argument("--terms-source", choices=["staging", "bq"], default="staging")
    parser.add_argument("--min-spend-inr", type=float, default=0.0)
    parser.add_argument("--bq-days", type=int, default=90,
                         help="trailing-day window for --terms-source/--rejects-source bq")
    parser.add_argument("--rejects-fixture", default=str(DEFAULT_REJECTS_FIXTURE))
    parser.add_argument("--terms-staging-file", default=None)
    parser.add_argument("--strategist-dir", default=None,
                         help="dir to scan for raw_keyword_candidates_*.json (default: staging/)")
    parser.add_argument("--out-dir", default=None,
                         help="CSV output directory (default: generation/exports/)")
    parser.add_argument("--run-date", default=None,
                         help="value_ledger run_date + CSV filename date (default: latest date in the terms source)")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO,
                         format="%(asctime)s [%(levelname)s] %(message)s")

    # ---- terms + baseline ----
    if args.terms_source == "staging":
        terms_path = Path(args.terms_staging_file) if args.terms_staging_file else None
        term_rows = load_terms_staging(terms_path)
    else:
        term_rows = load_terms_bq(args.bq_days)
    log.info("loaded %d kw_search_term_perf row(s) (source=%s)", len(term_rows), args.terms_source)

    baseline = analyze_waste_baseline(term_rows, min_spend_inr=args.min_spend_inr)
    run_date = args.run_date or (
        sorted({str(r["date"]) for r in term_rows if r.get("date")})[-1:]
        or [bq_writer.run_date_today_ist()]
    )[-1]
    period_label = _period_label(term_rows)

    ledger_row = build_value_ledger_row(baseline, run_date, period_label)
    bq_writer.write_rows("value_ledger", [ledger_row])
    log.info("value_ledger: wrote 1 row (entry_id=%s, expected_impact_inr=%s, DRY_RUN=%s)",
              ledger_row["entry_id"], ledger_row["expected_impact_inr"], config.DRY_RUN)

    # ---- rejects + strategist + CSV ----
    if args.rejects_source == "fixture":
        rejects_rows = load_rejects_fixture(Path(args.rejects_fixture))
    else:
        rejects_rows = load_rejects_bq()
    strategist_dir = Path(args.strategist_dir) if args.strategist_dir else None
    strategist_rows = load_strategist_negatives(strategist_dir)
    log.info("loaded %d keyword_rejects row(s) (source=%s), %d strategist negative row(s)",
              len(rejects_rows), args.rejects_source, len(strategist_rows))

    negatives = build_negative_rows(rejects_rows, strategist_rows, baseline["all_offenders"])
    out_dir = Path(args.out_dir) if args.out_dir else EXPORTS_DIR
    out_path = out_dir / f"negatives_{run_date}.csv"
    n = write_negatives_csv(negatives, out_path)
    log.info("CSV: wrote %d negative-keyword row(s) -> %s", n, out_path)

    report = {
        "period": period_label,
        "rows_scanned": baseline["rows_scanned"],
        "rows_flagged": baseline["rows_flagged"],
        "total_recoverable_inr": baseline["total_recoverable_inr"],
        "per_segment_inr": baseline["per_segment_inr"],
        "top_offenders": baseline["top_offenders"],
        "value_ledger_entry_id": ledger_row["entry_id"],
        "negatives_csv": str(out_path),
        "negatives_count": n,
    }
    print(json.dumps(report, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
