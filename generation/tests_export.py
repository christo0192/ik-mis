#!/usr/bin/env python3
"""
Tests for the negative-keyword export + waste-baseline job --
PLAN.md Phase 1 Step 11.

Run: `python3 generation/tests_export.py` (from the MIS repo root, or
anywhere -- imports resolve relative to this file's directory). Must pass
with DRY_RUN=true (the default).

Deliberately self-contained: every fixture used here is either a file in
`generation/fixtures/export_*.json` (created for this step) or a synthetic
`tmp_path`-style temp directory this test builds and tears down itself --
NOT the live `generation/staging/` directory, which other concurrently-
running jobs/tests in this repo write to. Reading that shared, mutable
directory from a test would make the test flaky by construction (see
`export_negatives.load_terms_staging`'s own docstring note about the
shared-preview-file clobbering this step found in practice).

Asserts:
  (a) `taxonomy.pre_filter()` regression coverage for the false-positive
      incident that forced this job off its old inline `FAST_DROP_PATTERNS`
      placeholder: "crack the coding interview" and "how to crack an
      interview for experienced candidates" (core India-market buyer
      intent -- "crack the interview" == "succeed at the interview") must
      never verdict `"drop"`; a term that conflicts domain/interview
      relevance against a freebie-seeking signal ("free system design
      interview course pdf") must resolve `"ambiguous"`, never a
      deterministic `"drop"`.
  (b) `analyze_waste_baseline()` on `fixtures/export_kw_perf_sample.json` --
      hand-computed per-segment + total ₹ recoverable, rows_scanned/flagged,
      verified against each fixture term's real `taxonomy.pre_filter()`
      verdict (only `"drop"`-verdict rows are flagged; `"pass"`/`"ambiguous"`
      rows are asserted to NEVER appear in `all_offenders`).
  (c) `--min-spend-inr` filtering changes the total/flagged count as
      hand-computed.
  (d) `build_value_ledger_row()` output validates against
      `schemas.ValueLedgerRow` and carries the right expected_impact_inr.
  (e) `load_rejects_fixture()` reads the right row count; `negative_candidate
      =False` rows are excluded by `build_negative_rows()`.
  (f) `load_strategist_negatives()` glob-reads multiple
      `raw_keyword_candidates_*.json` files and filters to `kind="negative"`.
  (g) `build_negative_rows()` dedupes across sources on (campaign, keyword
      lowercased) and excludes `term_type="keyword"` offender rows.
  (h) `write_negatives_csv()` -- correct columns, a comma-containing keyword
      round-trips as one field (not split across two), keyword sanitization
      collapses embedded whitespace/newlines.
  (i) a full `main()` run (fixture + a temp terms-staging file) exits 0,
      writes a value_ledger DRY_RUN preview with 1 row, and writes a CSV.
"""
import csv
import json
import shutil
import sys
import tempfile
from pathlib import Path

import config
import schemas
import taxonomy
import export_negatives as en

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"
KW_PERF_FIXTURE = FIXTURES_DIR / "export_kw_perf_sample.json"
REJECTS_FIXTURE = FIXTURES_DIR / "export_keyword_rejects_sample.json"


def check(cond, msg):
    if not cond:
        print(f"FAIL: {msg}")
        sys.exit(1)
    print(f"PASS: {msg}")


def main():
    check(config.DRY_RUN, "DRY_RUN defaults true")

    # --- (a) taxonomy.pre_filter() regression coverage -------------------
    # The false-positive incident that killed the old inline
    # FAST_DROP_PATTERNS placeholder: its bare \bcrack(ed)?\b "piracy"
    # pattern flagged core India-market buyer intent. Confirm the real
    # taxonomy never repeats that mistake.
    crack_verdict, _ = taxonomy.pre_filter("crack the coding interview")
    check(crack_verdict != "drop",
          f"'crack the coding interview' must never verdict drop (got {crack_verdict!r})")
    crack_verdict2, _ = taxonomy.pre_filter("how to crack an interview for experienced candidates")
    check(crack_verdict2 != "drop",
          f"'how to crack an interview for experienced candidates' must never verdict drop (got {crack_verdict2!r})")
    # A term that is BOTH domain/interview-relevant AND freebie-seeking is a
    # genuine conflict -- taxonomy resolves that to "ambiguous", never a
    # deterministic "drop" (see taxonomy.py's conservative-by-construction
    # design).
    conflict_verdict, _ = taxonomy.pre_filter("free system design interview course pdf")
    check(conflict_verdict == "ambiguous",
          f"'free system design interview course pdf' (domain+interview AND freebie-seeking) "
          f"-> ambiguous (got {conflict_verdict!r})")
    # A confidently relevant term still passes.
    pass_verdict, _ = taxonomy.pre_filter("system design interview course")
    check(pass_verdict == "pass",
          f"'system design interview course' -> pass (got {pass_verdict!r})")
    empty_verdict, _ = taxonomy.pre_filter("")
    check(empty_verdict == "ambiguous", "empty string -> ambiguous (routed to the LLM judge, never dropped)")

    # --- load the kw_perf fixture once, used by (b)/(c) ------------------
    term_rows = en.load_terms_staging(KW_PERF_FIXTURE)
    check(len(term_rows) == 8, f"kw_perf fixture has 8 rows (got {len(term_rows)})")

    # --- (b) hand-computed baseline math ---------------------------------
    # Every fixture term run through the real taxonomy.pre_filter() (verified
    # directly, not assumed):
    #   NB:         "free system design interview course pdf"        ambiguous (conflict)   -- NOT flagged
    #   NB:         "system design interview course"                 pass                   -- NOT flagged
    #   fte:        "sarkari naukri vacancy 2026"                     drop    1200.0         -- FLAGGED
    #   fte:        "coding interview prep bootcamp"                  pass                   -- NOT flagged
    #   Discovery:  "bollywood movie torrent download"                drop     950.0         -- FLAGGED
    #   Discovery:  "machine learning engineer interview"             pass                   -- NOT flagged
    #   NB:         "civil engineering interview questions"           drop     300.0         -- FLAGGED
    #   NB:         "aws certification cost"                         ambiguous (bare skill) -- NOT flagged
    # Total = 1200 + 950 + 300 = 2450.0; NB = 300.0 (only the civil-engineering
    # row -- the freebie-conflict and bare-skill NB rows are NOT flagged).
    baseline = en.analyze_waste_baseline(term_rows, min_spend_inr=0.0)
    check(baseline["rows_scanned"] == 8, f"rows_scanned == 8 (got {baseline['rows_scanned']})")
    check(baseline["rows_flagged"] == 3, f"rows_flagged == 3 (got {baseline['rows_flagged']})")
    check(baseline["total_recoverable_inr"] == 2450.0,
          f"total_recoverable_inr == 2450.0 (got {baseline['total_recoverable_inr']})")
    check(baseline["per_segment_inr"]["fte"] == 1200.0,
          f"per_segment_inr['fte'] == 1200.0 (got {baseline['per_segment_inr'].get('fte')})")
    check(baseline["per_segment_inr"]["Discovery"] == 950.0,
          f"per_segment_inr['Discovery'] == 950.0 (got {baseline['per_segment_inr'].get('Discovery')})")
    check(baseline["per_segment_inr"]["NB"] == 300.0,
          f"per_segment_inr['NB'] == 300.0 (got {baseline['per_segment_inr'].get('NB')})")
    check(baseline["per_segment_count"]["NB"] == 1,
          f"per_segment_count['NB'] == 1 (got {baseline['per_segment_count'].get('NB')})")
    check(len(baseline["top_offenders"]) == 3, "top_offenders has all 3 flagged rows (< 20)")
    check(baseline["top_offenders"][0]["keyword_or_term"] == "sarkari naukri vacancy 2026",
          "top offender by spend is the 1200 INR job-board-intent term")
    check(baseline["top_offenders"][0]["cost_inr"] == 1200.0,
          f"top offender cost_inr == 1200.0 (got {baseline['top_offenders'][0]['cost_inr']})")
    # pass-verdict AND ambiguous-verdict terms never appear in offenders --
    # this is the whole point of the taxonomy migration: interview-prep
    # buyer intent (bare or freebie-conflicted) is never auto-flagged as waste.
    offender_terms = {o["keyword_or_term"] for o in baseline["all_offenders"]}
    check("system design interview course" not in offender_terms,
          "the relevant (quality_score=8) keyword row is never flagged as an offender")
    check("coding interview prep bootcamp" not in offender_terms,
          "the relevant search-term row is never flagged as an offender")
    check("machine learning engineer interview" not in offender_terms,
          "the relevant Discovery search-term row is never flagged as an offender")
    check("free system design interview course pdf" not in offender_terms,
          "the domain+interview/freebie CONFLICT term is ambiguous, never flagged as an offender")
    check("aws certification cost" not in offender_terms,
          "the bare-skill-mention term is ambiguous, never flagged as an offender")

    # --- (c) min-spend filter ---------------------------------------------
    # min_spend_inr=500 excludes the 300 INR (NB) row -> only the 1200
    # (fte) and 950 (Discovery) rows survive.
    # Total = 1200 + 950 = 2150.0; rows_flagged == 2.
    filtered = en.analyze_waste_baseline(term_rows, min_spend_inr=500.0)
    check(filtered["rows_flagged"] == 2, f"min-spend=500: rows_flagged == 2 (got {filtered['rows_flagged']})")
    check(filtered["total_recoverable_inr"] == 2150.0,
          f"min-spend=500: total == 2150.0 (got {filtered['total_recoverable_inr']})")
    check(filtered["per_segment_inr"]["fte"] == 1200.0,
          f"min-spend=500: fte == 1200.0 (got {filtered['per_segment_inr'].get('fte')})")
    check(filtered["per_segment_inr"]["Discovery"] == 950.0,
          f"min-spend=500: Discovery == 950.0 (got {filtered['per_segment_inr'].get('Discovery')})")
    check("NB" not in filtered["per_segment_inr"],
          "min-spend=500: NB (300 INR, below the 500 floor) drops out entirely")

    # --- (d) value_ledger row validates -------------------------------------
    ledger_row = en.build_value_ledger_row(baseline, "2026-07-02", en._period_label(term_rows))
    model_cls, _bq = schemas.REGISTRY["value_ledger"]
    validated = model_cls(**ledger_row)   # raises pydantic.ValidationError on any mismatch
    check(validated.action_type == "waste_baseline", "value_ledger row action_type == 'waste_baseline'")
    check(validated.expected_impact_inr == 2450.0,
          f"value_ledger row expected_impact_inr == 2450.0 (got {validated.expected_impact_inr})")
    check(validated.status == "projected", "value_ledger row status == 'projected'")
    check(validated.entry_id == "vl-waste-baseline-2026-07-02",
          f"value_ledger entry_id == 'vl-waste-baseline-2026-07-02' (got {validated.entry_id})")
    check(validated.campaign_name is None and validated.segment is None,
          "value_ledger row campaign_name/segment are None (account-wide baseline, not one campaign)")

    # --- (e) rejects fixture + negative_candidate filter --------------------
    rejects_rows = en.load_rejects_fixture(REJECTS_FIXTURE)
    check(len(rejects_rows) == 3, f"rejects fixture has 3 rows (got {len(rejects_rows)})")
    n_false = sum(1 for r in rejects_rows if r["negative_candidate"] is False)
    check(n_false == 1, "exactly one rejects-fixture row has negative_candidate=False")

    # --- (f) strategist negatives glob-read (self-contained temp dir) -------
    tmpdir = Path(tempfile.mkdtemp(prefix="export_negatives_test_"))
    try:
        (tmpdir / "raw_keyword_candidates_2026-07-01.json").write_text(json.dumps({
            "run_date": "2026-07-01", "run_id": "r1", "row_count": 2,
            "rows": [
                {"campaign_name": "camp_a", "keyword": "free pdf notes", "kind": "negative",
                 "source_signal": "sig1"},
                {"campaign_name": "camp_a", "keyword": "system design basics", "kind": "new",
                 "source_signal": "sig2"},
            ],
        }), encoding="utf-8")
        (tmpdir / "raw_keyword_candidates_2026-07-02.json").write_text(json.dumps({
            "run_date": "2026-07-02", "run_id": "r2", "row_count": 1,
            "rows": [
                {"campaign_name": "camp_b", "keyword": "torrent site", "kind": "negative",
                 "source_signal": "sig3"},
            ],
        }), encoding="utf-8")
        # a non-matching file must be ignored
        (tmpdir / "unrelated_file.json").write_text("[]", encoding="utf-8")

        strategist_rows = en.load_strategist_negatives(tmpdir)
        check(len(strategist_rows) == 2,
              f"load_strategist_negatives reads across both files, kind='negative' only (got {len(strategist_rows)})")
        check({r["keyword"] for r in strategist_rows} == {"free pdf notes", "torrent site"},
              "load_strategist_negatives returns exactly the two negative-kind rows across files")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

    # --- (g) build_negative_rows: dedupe + term_type filter -----------------
    offender_rows = [
        {"campaign_name": "google_search_nb_ncr_v1", "keyword_or_term": "FREE System Design Interview Course PDF",
         "term_type": "search_term", "reject_reason": "freebie_seeker"},
        {"campaign_name": "google_search_nb_ncr_v1", "keyword_or_term": "system design interview course",
         "term_type": "keyword", "reject_reason": "freebie_seeker"},  # must be excluded (term_type=keyword)
        {"campaign_name": "google_search_fte_ncr_v1", "keyword_or_term": "sarkari naukri 2026",
         "term_type": "search_term", "reject_reason": "unrelated_job_search"},
    ]
    strategist_dedupe_rows = [
        {"campaign_name": "camp_a", "keyword": "brand new negative term", "source_signal": "s"},
    ]
    negatives = en.build_negative_rows(rejects_rows, strategist_dedupe_rows, offender_rows)
    # rejects_rows row 1 (google_search_nb_ncr_v1 / "free system design interview course pdf")
    # dedupes against offender_rows row 1 (same campaign, case-insensitive same keyword).
    # rejects_rows row 2 (google_search_fte_ncr_v1 / "sarkari naukri 2026") dedupes against
    # offender_rows row 3. rejects_rows row 3 is excluded (negative_candidate=False).
    # offender_rows row 2 is excluded (term_type="keyword").
    # strategist_dedupe_rows row 1 is unique, included.
    check(len(negatives) == 3,
          f"build_negative_rows: 2 deduped pairs + 1 unique strategist row == 3 total (got {len(negatives)})")
    keys = {(r["campaign_name"], r["keyword"].lower()) for r in negatives}
    check(("google_search_nb_ncr_v1", "free system design interview course pdf") in keys,
          "deduped row keeps the (campaign, keyword) pair once despite case differences across sources")
    check(("google_search_nb_ncr_v1", "system design interview course") not in keys,
          "term_type='keyword' offender row never becomes a negative-keyword candidate")
    check(("camp_a", "brand new negative term") in keys, "unique strategist row survives the merge")

    # --- (h) CSV structure, comma handling, sanitization ---------------------
    csv_rows = [
        {"campaign_name": "camp_x", "keyword": "interview, prep, course", "match_type": "Exact",
         "source": "s", "reason": "r"},
        {"campaign_name": "camp_x", "keyword": "free   pdf\ndownload", "match_type": "Broad",
         "source": "s", "reason": "r"},
    ]
    # apply the same sanitize step build_negative_rows applies (call it directly here
    # to isolate the CSV-writing assertions from the merge/dedupe logic already tested above)
    for r in csv_rows:
        r["keyword"] = en._sanitize_keyword_for_csv(r["keyword"])
    check(csv_rows[1]["keyword"] == "free pdf download",
          f"keyword sanitization collapses internal whitespace/newlines (got {csv_rows[1]['keyword']!r})")

    tmp_csv_dir = Path(tempfile.mkdtemp(prefix="export_negatives_csv_test_"))
    try:
        out_path = tmp_csv_dir / "negatives_test.csv"
        n = en.write_negatives_csv(csv_rows, out_path)
        check(n == 2, f"write_negatives_csv returns row count == 2 (got {n})")
        with out_path.open(newline="", encoding="utf-8") as f:
            reader = list(csv.reader(f))
        check(reader[0] == en.CSV_COLUMNS,
              f"CSV header row == {en.CSV_COLUMNS} (got {reader[0]})")
        check(len(reader) == 3, f"CSV has header + 2 data rows == 3 lines (got {len(reader)})")
        check(len(reader[1]) == 5, f"comma-containing keyword did not split the row: 5 fields (got {len(reader[1])})")
        check(reader[1][2] == "interview, prep, course",
              f"comma-containing keyword round-trips exactly (got {reader[1][2]!r})")
        check(reader[1][1] == "", "Ad Group column is blank (campaign-level negative)")
        check(reader[1][4] == "Campaign negative", "Criterion Type column == 'Campaign negative'")
        check(reader[2][2] == "free pdf download", "sanitized keyword round-trips in the CSV")
        check(reader[2][3] == "Broad", "Match Type column preserved per-row")
    finally:
        shutil.rmtree(tmp_csv_dir, ignore_errors=True)

    # --- (i) full main() run -------------------------------------------------
    run_tmp = Path(tempfile.mkdtemp(prefix="export_negatives_main_test_"))
    try:
        terms_staging_file = run_tmp / "terms.json"
        terms_staging_file.write_text(KW_PERF_FIXTURE.read_text(encoding="utf-8"), encoding="utf-8")
        strategist_dir = run_tmp / "strategist_empty"
        strategist_dir.mkdir()
        out_dir = run_tmp / "exports"

        preview_path = config.STAGING_DIR / "bq_preview_value_ledger.json"
        if preview_path.exists():
            preview_path.unlink()

        rc = en.main([
            "--terms-source", "staging", "--terms-staging-file", str(terms_staging_file),
            "--rejects-source", "fixture", "--rejects-fixture", str(REJECTS_FIXTURE),
            "--strategist-dir", str(strategist_dir),
            "--out-dir", str(out_dir),
            "--run-date", "2026-07-02",
        ])
        check(rc == 0, f"main() exits 0 (got {rc})")

        check(preview_path.exists(), "main() run wrote the value_ledger DRY_RUN preview")
        preview = json.loads(preview_path.read_text(encoding="utf-8"))
        check(preview["row_count"] == 1, f"value_ledger preview row_count == 1 (got {preview['row_count']})")
        check(preview["rows"][0]["expected_impact_inr"] == 2450.0,
              f"value_ledger preview expected_impact_inr == 2450.0 (got {preview['rows'][0]['expected_impact_inr']})")

        out_csv = out_dir / "negatives_2026-07-02.csv"
        check(out_csv.exists(), f"main() wrote the negatives CSV at {out_csv}")
        with out_csv.open(newline="", encoding="utf-8") as f:
            main_rows = list(csv.reader(f))
        check(main_rows[0] == en.CSV_COLUMNS, "main()-produced CSV has the expected header")
        check(len(main_rows) > 1, "main()-produced CSV has at least one negative-keyword data row")

        # The core regression guard for this migration: NO keyword written to
        # the negatives CSV may be a taxonomy "pass" verdict -- a pass-verdict
        # term is confidently relevant buyer intent and must never be
        # exported as a negative keyword, from any of the three merge sources.
        keyword_col = en.CSV_COLUMNS.index("Keyword")
        pass_verdict_rows = []
        for data_row in main_rows[1:]:
            kw = data_row[keyword_col]
            verdict, _ = taxonomy.pre_filter(kw)
            if verdict == "pass":
                pass_verdict_rows.append(kw)
        check(len(pass_verdict_rows) == 0,
              f"no CSV keyword may verdict 'pass' from taxonomy (got: {pass_verdict_rows})")
    finally:
        shutil.rmtree(run_tmp, ignore_errors=True)

    print("\nAll export_negatives assertions passed.")


if __name__ == "__main__":
    main()
