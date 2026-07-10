#!/usr/bin/env python3
"""
Smoke test for the Generation department's guarded BigQuery writer.

Run: `python3 generation/smoke_test.py` (from the MIS repo root, or anywhere
-- it resolves paths relative to this file). Must pass with DRY_RUN=true
(the default) and WITHOUT google-cloud-bigquery installed -- that's the
point of bq_writer.py's lazy import.

Asserts (PLAN.md Phase 0 Step 1 "Done when"):
  (a) one valid row per table, written through bq_writer.write_rows(),
      produces a correct generation/staging/bq_preview_<table>.json, and the
      preview's row content actually matches what was submitted (including a
      date field round-tripped through ISO-string serialization)
  (b) a Pydantic-invalid row is rejected with pydantic.ValidationError
  (c) a write targeting a non-allowlisted table (e.g. "India_Leads.leads" or
      "Market_Research.social_trends") raises bq_writer.WriteTargetError
  (d) a fully-qualified allowlisted table name (project.dataset.table) works
      end-to-end in DRY_RUN, not just the bare logical name
  (e) a row with an extra/typo'd field is rejected with
      pydantic.ValidationError (schemas.py's shared extra="forbid" base)
"""
import json
import sys

import config
import schemas
import bq_writer
from pydantic import ValidationError

RUN_DATE = "2026-07-03"
RUN_ID = "smoke-test-run-1"

# One valid row per table (logical name -> row dict). Every required field
# from schemas.py is populated; enum fields use a real allowed value.
SAMPLE_ROWS = {
    "campaign_registry": {
        "campaign_name": "google_search_fte_ncr_mofu_v1",
        "platform": "google",
        "segment": "fte",
        "program": "Full Stack",
        "geo": "NCR",
        "funnel_stage": "mofu",
        "utm_campaign": "google_search_fte_ncr_mofu_v1",
        "utm_source": "google",
        "utm_medium": "cpc",
        "platform_campaign_id": "123456789",
        "status": "active",
        "snapshot_date": RUN_DATE,
    },
    "keyword_candidates": {
        "run_date": RUN_DATE, "run_id": RUN_ID,
        "campaign_name": "google_search_fte_ncr_mofu_v1", "segment": "fte",
        "keyword": "system design interview course", "match_type": "phrase",
        "kind": "new", "relevance_verdict": "relevant", "relevance_score": 0.92,
        "filter_reason": None,
        "source_signal": "job_market_demand: 'system design' in 41% of postings (run 2026-06-28)",
        "status": "proposed",
        # Keyword Planner demand-grounding fields (round-trip coverage for the
        # 7 nullable additions).
        "keyword_source": "both", "avg_monthly_searches": 5400, "competition": "MEDIUM",
        "competition_index": 63, "top_of_page_bid_low_inr": 5.9,
        "top_of_page_bid_high_inr": 54.7, "demand_flag": None,
    },
    "keyword_rejects": {
        "run_date": RUN_DATE, "run_id": RUN_ID,
        "campaign_name": "google_search_fte_ncr_mofu_v1", "segment": "fte",
        "keyword": "free coding course pdf download",
        "reject_reason": "off-topic: freebie-seeker intent, no IK program match",
        "relevance_score": 0.06,
        "source_signal": "kw_search_term_perf: 0 quality leads / 340 clicks (30d)",
        "negative_candidate": True,
    },
    "ad_copy_drafts": {
        "run_date": RUN_DATE, "run_id": RUN_ID,
        "campaign_name": "google_search_fte_ncr_mofu_v1", "segment": "fte",
        "platform": "google", "asset_id": "acd-0001",
        "headline": "Master System Design in 12 Weeks",
        "primary_text": "Live cohort, FAANG mentors, mock interviews included.",
        "description": "Land your next role with structured interview prep.",
        "cta": "Apply Now", "source_signal": "executive_summary rank 1: system design demand spike",
        "status": "proposed", "edit_distance": 0,
    },
    "audience_angles": {
        "run_date": RUN_DATE, "run_id": RUN_ID,
        "campaign_name": "google_search_fte_ncr_mofu_v1", "segment": "fte",
        "platform": "linkedin", "asset_id": "aa-0001",
        "angle_name": "career-switcher-urgency",
        "angle_description": "Speaks to mid-career engineers feeling stuck pre-promo cycle.",
        "persona": "3-5 YOE backend engineer, non-FAANG",
        "source_signal": "social_trends: 'stuck at same level' thread, 12k upvotes",
        "status": "proposed",
    },
    "creative_briefs": {
        "run_date": RUN_DATE, "run_id": RUN_ID,
        "campaign_name": "google_search_fte_ncr_mofu_v1", "segment": "fte",
        "asset_id": "cb-0001", "brief_title": "System Design Confidence",
        "brief_body": "Visualize the whiteboard-to-offer journey in 3 panels.",
        "visual_direction": "Dark navy, single accent color, whiteboard motif",
        "source_signal": "competitor_intel: gap vs Scaler on system design depth",
        "status": "proposed",
    },
    "creative_assets": {
        "run_date": RUN_DATE, "run_id": RUN_ID,
        "campaign_name": "google_search_fte_ncr_mofu_v1", "segment": "fte",
        "asset_id": "ca-0001", "brief_asset_id": "cb-0001",
        "asset_type": "image", "storage_uri": "gs://ik-creative-assets/ca-0001.png",
        "format": "1080x1080", "width": 1080, "height": 1080,
        "brand_safe": True, "format_valid": True, "status": "proposed",
    },
    "asset_performance_feedback": {
        "run_date": RUN_DATE,
        "campaign_name": "google_search_fte_ncr_mofu_v1", "segment": "fte",
        "asset_id": "acd-0001", "utm_content": "acd-0001", "platform_ad_id": "987654321",
        "impressions": 12000, "clicks": 340, "ctr": 0.0283, "spend_inr": 45000.0,
        "leads": 22, "quality_leads": 9, "cpql_inr": 5000.0, "verdict": "winner",
        "period_start": "2026-06-01", "period_end": "2026-06-30",
    },
    "generation_runs": {
        "run_date": RUN_DATE, "run_id": RUN_ID, "path": "keyword",
        "candidates_generated": 120, "passed_filter": 78, "borderline": 15,
        "rejected": 27, "llm_calls": 6, "duration_seconds": 184.5,
        "status": "success", "notes": "smoke test run",
    },
    "kw_search_term_perf": {
        "date": RUN_DATE,
        "campaign_name": "google_search_fte_ncr_mofu_v1", "segment": "fte",
        "keyword_or_term": "system design interview course", "term_type": "keyword",
        "match_type": "phrase", "impressions": 5400, "clicks": 210, "ctr": 0.0389,
        "avg_cpc_inr": 38.5, "cost_inr": 8085.0, "quality_score": 8,
    },
    "lead_quality_scores": {
        "period_start": "2026-06-01", "period_end": "2026-06-30", "run_date": RUN_DATE,
        "campaign_name": "google_search_fte_ncr_mofu_v1", "segment": "fte",
        "leads": 22, "quality_leads": 9, "junk_leads": 13, "quality_ratio": 0.409,
        "spend_inr": 45000.0, "cpl_inr": 2045.5, "cpql_inr": 5000.0,
    },
    "value_ledger": {
        "run_date": RUN_DATE, "entry_id": "vl-0001", "action_type": "waste_baseline",
        "description": "30-day spend on search terms the relevance gate would reject",
        "campaign_name": "google_search_fte_ncr_mofu_v1", "segment": "fte",
        "expected_impact_inr": 62000.0, "realized_impact_inr": None,
        "realized_at": None, "status": "projected",
    },
}


def check(cond, msg):
    if not cond:
        print(f"FAIL: {msg}")
        sys.exit(1)
    print(f"PASS: {msg}")


def main():
    check(config.DRY_RUN, "DRY_RUN defaults true")
    check(set(SAMPLE_ROWS) == set(schemas.REGISTRY),
          "sample rows cover exactly the 12 REGISTRY tables")

    # Clean any stale previews so this run's assertions are unambiguous.
    if config.STAGING_DIR.exists():
        for f in config.STAGING_DIR.glob("bq_preview_*.json"):
            f.unlink()

    # (a) one valid row per table -> correct DRY_RUN preview
    for table_name, row in SAMPLE_ROWS.items():
        written = bq_writer.write_rows(table_name, [row])
        check(written == 0, f"{table_name}: DRY_RUN write_rows returns 0 (no live rows)")
        preview_path = config.STAGING_DIR / f"bq_preview_{table_name}.json"
        check(preview_path.exists(), f"{table_name}: preview file created at {preview_path}")
        preview = json.loads(preview_path.read_text(encoding="utf-8"))
        expected_table_id = (
            f"{config.BQ_PROJECT}.{config.TARGET_DATASET}.{config.TABLE_PREFIX}{table_name}")
        check(preview["table"] == expected_table_id,
              f"{table_name}: preview['table'] == {expected_table_id!r}")
        check(preview["row_count"] == 1, f"{table_name}: preview row_count == 1")
        check(preview["partition_field"] == schemas.PARTITION_FIELDS[table_name],
              f"{table_name}: preview partition_field matches PARTITION_FIELDS")

        # Content assertion: every field the test submitted must round-trip
        # into preview["rows"][0] unchanged -- this necessarily includes the
        # table's partition-field date (e.g. run_date/snapshot_date/date),
        # which _model_to_bq serializes via .isoformat(), so this also
        # verifies date fields serialize back to the same ISO string that
        # was submitted (rather than merely checking row_count).
        actual_row = preview["rows"][0]
        for field_name, expected_value in row.items():
            check(actual_row.get(field_name) == expected_value,
                  f"{table_name}: preview row[0][{field_name!r}] == input ({expected_value!r})")

    # (b) Pydantic-invalid row -> ValidationError, nothing written
    invalid_row = dict(SAMPLE_ROWS["keyword_candidates"])
    invalid_row["relevance_score"] = 4.2          # out of [0,1] range
    invalid_row["source_signal"] = ""             # empty grounding citation
    try:
        bq_writer.write_rows("keyword_candidates", [invalid_row])
        check(False, "invalid keyword_candidates row raises ValidationError")
    except ValidationError as e:
        check(True, f"invalid keyword_candidates row raises ValidationError ({len(e.errors())} error(s))")

    # (c) non-allowlisted target -> WriteTargetError
    for bad_target in ("India_Leads.leads", "Market_Research.social_trends"):
        try:
            bq_writer.write_rows(bad_target, [{"anything": "goes"}])
            check(False, f"write to {bad_target!r} raises WriteTargetError")
        except bq_writer.WriteTargetError as e:
            check(True, f"write to {bad_target!r} raises WriteTargetError ({e})")

    # (d) fully-qualified allowlisted name works end-to-end in DRY_RUN --
    # regression test for the write_rows()/REGISTRY KeyError gap (fix 9):
    # validate_target() always accepted "project.dataset.table", but
    # write_rows() used to index schemas.REGISTRY with the raw (possibly
    # fully-qualified) argument instead of the derived logical name.
    fq_name = f"{config.BQ_PROJECT}.{config.TARGET_DATASET}.{config.TABLE_PREFIX}keyword_candidates"
    fq_written = bq_writer.write_rows(fq_name, [SAMPLE_ROWS["keyword_candidates"]])
    check(fq_written == 0, f"fully-qualified name {fq_name!r}: DRY_RUN write_rows returns 0")
    fq_preview_path = config.STAGING_DIR / "bq_preview_keyword_candidates.json"
    check(fq_preview_path.exists(), "fully-qualified name write produced the expected preview file")

    # (e) extra/typo'd field -> ValidationError (schemas.py's shared _Row base,
    # model_config = ConfigDict(extra="forbid"), fix 8)
    extra_field_row = dict(SAMPLE_ROWS["keyword_candidates"])
    extra_field_row["totally_unexpected_field"] = "should not be allowed"
    try:
        bq_writer.write_rows("keyword_candidates", [extra_field_row])
        check(False, "row with an extra/typo'd field raises ValidationError (extra='forbid')")
    except ValidationError as e:
        check(True, f"row with an extra/typo'd field raises ValidationError ({len(e.errors())} error(s))")

    # (f) LinkedIn InMail / Conversation ad rows: platform-conditional validation
    # (docs/linkedin-inmail-copy-plan.md). Message needs subject+body; conversation
    # needs body; neither needs a headline. A message row missing subject fails.
    li_common = {"run_date": RUN_DATE, "run_id": RUN_ID,
                 "campaign_name": "IK-IN_LinkedIn_EdgeUp_Conversation_EM_JF",
                 "segment": "LinkedIn", "platform": "linkedin",
                 "source_signal": "pmis_recommendations: EM segment InMail angle", "status": "proposed"}
    li_message = {**li_common, "asset_id": "acd-li-msg-1", "linkedin_format": "message",
                  "subject": "Your team is upleveling in Agentic AI. Are you ready to lead them?",
                  "body": "Hi [Name],\n\nAcross India, tech teams are rapidly experimenting with LLMs...\n"
                          "• 60+ live hours with MAANG+ & PwC experts\n[Join our webinar →]\nBest,\n[Your Name]",
                  "cta": "Register"}
    li_convo = {**li_common, "asset_id": "acd-li-convo-1", "linkedin_format": "conversation",
                "body": "Step 1 (Initial):\nHi [First Name], I noticed you explored Interview Kickstart...\n"
                        "* Yes, tell me more\n* No, I don't want to upskill right now\n\nStep 2 (if Yes): ...",
                "cta": "Learn More"}
    for name, row in (("message", li_message), ("conversation", li_convo)):
        written = bq_writer.write_rows("ad_copy_drafts", [row])
        check(written == 0, f"linkedin {name} ad_copy row: DRY_RUN write returns 0")
        prev = json.loads((config.STAGING_DIR / "bq_preview_ad_copy_drafts.json").read_text(encoding="utf-8"))
        r0 = prev["rows"][0]
        check(r0.get("linkedin_format") == row["linkedin_format"] and r0.get("body") == row["body"]
              and r0.get("headline") is None,
              f"linkedin {name} round-trips (format+body preserved, headline null)")
    bad_message = dict(li_message)
    bad_message["subject"] = ""      # message ad must have a subject
    try:
        bq_writer.write_rows("ad_copy_drafts", [bad_message])
        check(False, "linkedin message row missing subject raises ValidationError")
    except ValidationError as e:
        check(True, f"linkedin message row missing subject raises ValidationError ({len(e.errors())} error(s))")

    print("\nAll smoke-test assertions passed.")


if __name__ == "__main__":
    main()
