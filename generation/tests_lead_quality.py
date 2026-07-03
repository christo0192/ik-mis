#!/usr/bin/env python3
"""
Tests for the lead-quality signal (CPQL) job -- PLAN.md Phase 1 Step 7.

Run: `python3 generation/tests_lead_quality.py` (from the MIS repo root, or
anywhere -- imports resolve relative to this file's directory). Must pass
with DRY_RUN=true (the default) and without google-cloud-bigquery importable
for the fixture path -- lead_quality.py only imports it lazily inside
fetch_lead_outcomes()/get_read_client(), never at module load.

Grain note: lead_quality.py reads `pmis_daily_segment_facts`
(date, segment, spend, iql, junk), not `pmis_full_funnel` -- see the module
docstring for why (the campaign-grain join is confirmed broken upstream).
There is no campaign identity at this grain, so aggregate_and_derive() groups
by segment alone and stamps campaign_name = "segment:<Segment>" on every
output row (a schema-required field with an honest placeholder value, not a
fabricated per-campaign number).

Asserts:
  (a) aggregate_and_derive() on fixtures/lead_quality_sample.json produces
      hand-computed quality_ratio/cpl_inr/cpql_inr per segment, including a
      zero-quality-leads segment (cpql_inr -> None, not inf/error) and a
      zero-lead segment (quality_ratio/cpl_inr/cpql_inr all -> None).
  (b) a raw row with a missing/empty `segment` is dropped rather than
      crashing or polluting the output with a bogus group.
  (c) every derived row validates against schemas.LeadQualityScoreRow, and
      campaign_name is exactly "segment:<segment>" for that row.
  (d) a full `--from-fixture` CLI run (main()) succeeds and lands a DRY_RUN
      preview file with the same row count.
"""
import json
import sys
from datetime import date
from pathlib import Path

import config
import schemas
import lead_quality

FIXTURE_PATH = str(Path(__file__).resolve().parent / "fixtures" / "lead_quality_sample.json")
PERIOD_START = date(2026, 6, 1)
PERIOD_END = date(2026, 6, 30)
RUN_DATE = date(2026, 7, 1)


def check(cond, msg):
    if not cond:
        print(f"FAIL: {msg}")
        sys.exit(1)
    print(f"PASS: {msg}")


def _find(rows, segment):
    matches = [r for r in rows if r["segment"] == segment]
    check(len(matches) == 1, f"exactly one output row for segment {segment!r}")
    return matches[0]


def main():
    check(config.DRY_RUN, "DRY_RUN defaults true")

    raw_rows = lead_quality.load_fixture(FIXTURE_PATH)
    check(len(raw_rows) == 9, f"fixture has 9 raw rows (got {len(raw_rows)})")

    rows = lead_quality.aggregate_and_derive(raw_rows, PERIOD_START, PERIOD_END, RUN_DATE)
    check(len(rows) == 5,
          f"aggregation collapses 9 raw rows into 5 segment rows -- the 9th "
          f"row (empty segment) is dropped, not a 6th group (got {len(rows)})")

    # --- (a) hand-computed expectations ------------------------------------
    nb = _find(rows, "NB")
    check(nb["campaign_name"] == "segment:NB",
          f"NB row campaign_name == 'segment:NB' placeholder (got {nb['campaign_name']!r})")
    check(nb["leads"] == 11, f"NB leads == 5+6 == 11 (got {nb['leads']})")
    check(nb["junk_leads"] == 1, f"NB junk_leads == 1+0 == 1 (got {nb['junk_leads']})")
    check(nb["quality_leads"] == 10, f"NB quality_leads == 11-1 == 10 (got {nb['quality_leads']})")
    check(nb["quality_ratio"] == round(10 / 11, 4),
          f"NB quality_ratio == round(10/11,4) (got {nb['quality_ratio']})")
    check(nb["spend_inr"] == 31000.0, f"NB spend_inr == 15000+16000 == 31000.0 (got {nb['spend_inr']})")
    check(nb["cpl_inr"] == round(31000 / 11, 2), f"NB cpl_inr == round(31000/11,2) (got {nb['cpl_inr']})")
    check(nb["cpql_inr"] == round(31000 / 10, 2), f"NB cpql_inr == round(31000/10,2) (got {nb['cpql_inr']})")

    disc = _find(rows, "Discovery")
    check(disc["campaign_name"] == "segment:Discovery",
          f"Discovery row campaign_name == 'segment:Discovery' (got {disc['campaign_name']!r})")
    check(disc["leads"] == 7, f"Discovery leads == 4+3 == 7 (got {disc['leads']})")
    check(disc["junk_leads"] == 7, f"Discovery junk_leads == 4+3 == 7 (got {disc['junk_leads']})")
    check(disc["quality_leads"] == 0, f"Discovery quality_leads == 0 (all-junk segment) (got {disc['quality_leads']})")
    check(disc["quality_ratio"] == 0.0, f"Discovery quality_ratio == 0.0 (got {disc['quality_ratio']})")
    check(disc["cpl_inr"] == round(16500 / 7, 2), f"Discovery cpl_inr == round(16500/7,2) (got {disc['cpl_inr']})")
    check(disc["cpql_inr"] is None,
          f"Discovery cpql_inr is None, not inf/error, when quality_leads == 0 (got {disc['cpql_inr']!r})")

    brand = _find(rows, "Brand")
    check(brand["campaign_name"] == "segment:Brand",
          f"Brand row campaign_name == 'segment:Brand' (got {brand['campaign_name']!r})")
    check(brand["leads"] == 2 and brand["quality_leads"] == 2 and brand["junk_leads"] == 0,
          "Brand segment: single-date row passes through unaggregated (2/2/0)")
    check(brand["quality_ratio"] == 1.0, f"Brand quality_ratio == 1.0 (no junk) (got {brand['quality_ratio']})")
    check(brand["cpl_inr"] == 1000.0 and brand["cpql_inr"] == 1000.0,
          f"Brand cpl_inr == cpql_inr == 1000.0 when quality_leads == leads (got {brand['cpl_inr']}, {brand['cpql_inr']})")

    li = _find(rows, "LinkedIn")
    check(li["campaign_name"] == "segment:LinkedIn",
          f"LinkedIn row campaign_name == 'segment:LinkedIn' (got {li['campaign_name']!r})")
    check(li["leads"] == 11 and li["junk_leads"] == 1 and li["quality_leads"] == 10,
          f"LinkedIn leads/junk/quality == 11/1/10 (got {li['leads']}/{li['junk_leads']}/{li['quality_leads']})")
    check(li["spend_inr"] == 25000.0, f"LinkedIn spend_inr == 12000+13000 == 25000.0 (got {li['spend_inr']})")
    check(li["cpl_inr"] == round(25000 / 11, 2), f"LinkedIn cpl_inr == round(25000/11,2) (got {li['cpl_inr']})")
    check(li["cpql_inr"] == round(25000 / 10, 2), f"LinkedIn cpql_inr == round(25000/10,2) (got {li['cpql_inr']})")

    zero = _find(rows, "ZeroLeadTest")
    check(zero["campaign_name"] == "segment:ZeroLeadTest",
          f"zero-lead row campaign_name == 'segment:ZeroLeadTest' (got {zero['campaign_name']!r})")
    check(zero["leads"] == 0, f"zero-lead segment leads == 0 (got {zero['leads']})")
    check(zero["quality_ratio"] is None and zero["cpl_inr"] is None and zero["cpql_inr"] is None,
          "zero-lead segment: quality_ratio/cpl_inr/cpql_inr all None (div-by-zero guard, not error)")
    check(zero["spend_inr"] == 500.0, f"zero-lead segment still carries spend_inr == 500.0 (got {zero['spend_inr']})")

    # --- (b) missing-segment row is dropped, not a phantom 6th group --------
    segments_seen = {r["segment"] for r in rows}
    check("" not in segments_seen and None not in segments_seen,
          "no output row for the raw row with an empty segment (dropped by the missing-segment guard)")

    # --- (c) schema validation ----------------------------------------------
    model_cls, _bq_schema = schemas.REGISTRY["lead_quality_scores"]
    for row in rows:
        model_cls(**row)   # raises pydantic.ValidationError on any mismatch
        check(row["campaign_name"] == f"segment:{row['segment']}",
              f"row campaign_name == 'segment:{row['segment']}' placeholder convention holds")
    check(True, f"all {len(rows)} derived rows validate against schemas.LeadQualityScoreRow")

    # --- (d) full CLI run lands a DRY_RUN preview ----------------------------
    if config.STAGING_DIR.exists():
        preview_path = config.STAGING_DIR / "bq_preview_lead_quality_scores.json"
        if preview_path.exists():
            preview_path.unlink()

    rc = lead_quality.main([
        "--from-fixture", FIXTURE_PATH,
        "--run-date", str(RUN_DATE),
        "--period-days", "30",
    ])
    check(rc == 0, f"lead_quality.main() --from-fixture exits 0 (got {rc})")

    preview_path = config.STAGING_DIR / "bq_preview_lead_quality_scores.json"
    check(preview_path.exists(), f"DRY_RUN preview file created at {preview_path}")
    preview = json.loads(preview_path.read_text(encoding="utf-8"))
    expected_table_id = (
        f"{config.BQ_PROJECT}.{config.TARGET_DATASET}.{config.TABLE_PREFIX}lead_quality_scores")
    check(preview["table"] == expected_table_id, f"preview['table'] == {expected_table_id!r}")
    check(preview["row_count"] == 5, f"preview row_count == 5 (got {preview['row_count']})")
    check(preview["partition_field"] == "run_date", "preview partition_field == 'run_date'")

    print("\nAll lead_quality assertions passed.")


if __name__ == "__main__":
    main()
