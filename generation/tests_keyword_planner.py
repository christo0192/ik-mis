#!/usr/bin/env python3
"""
Tests for the Keyword Planner demand-grounding path
(docs/keyword-planner-integration-plan.md, Step 6).

No live Google Ads call: the one function that touches the API
(`keyword_planner._generate_keyword_ideas_raw`) and the conductor's
`keyword_planner.fetch_ideas` seam are monkeypatched -- the same way
tests_conductor monkeypatches `llm.call`. Run: `python tests_keyword_planner.py`.
"""
import sys
from datetime import date

import keyword_planner as kp
import generate_assets as G
import schemas

RUN_DATE = date(2026, 7, 9)
RUN_ID = "kwp-test-run"


def check(cond, msg):
    if not cond:
        print(f"FAIL: {msg}")
        sys.exit(1)
    print(f"PASS: {msg}")


def _ai_row(keyword, segment, campaign):
    return schemas.KeywordCandidateRow(
        run_date=RUN_DATE, run_id=RUN_ID, campaign_name=campaign, segment=segment,
        keyword=keyword, match_type="phrase", kind="new",
        relevance_verdict="borderline", relevance_score=0.5,
        source_signal="job_market_demand: sample", status="proposed")


# --- module: normalize + dedupe/cap ----------------------------------------
def test_normalize_and_dedupe():
    check(kp.normalize("  Data   Structures ") == "data structures", "normalize lowercases+collapses")
    raw = [
        {"text": "Data Structures", "avg_monthly_searches": 100},
        {"text": "data  structures", "avg_monthly_searches": 300},   # dup, higher vol
        {"text": "system design", "avg_monthly_searches": 50},
    ]
    out = kp._dedupe_and_cap(raw, 10)
    check([r["avg_monthly_searches"] for r in out] == [300, 50],
          "dedupe keeps highest-volume variant, sorts desc")
    check(len(kp._dedupe_and_cap(raw, 1)) == 1, "cap limits result count")


# --- module: fail-soft + no-seeds ------------------------------------------
def test_fetch_ideas_failsoft(monkeypatch=None):
    orig = kp._generate_keyword_ideas_raw
    try:
        kp._generate_keyword_ideas_raw = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("api boom"))
        ideas, reason = kp.fetch_ideas(["system design interview"])
        check(ideas == [] and reason and "boom" in reason,
              "fetch_ideas returns ([], reason) on API error (never raises)")
    finally:
        kp._generate_keyword_ideas_raw = orig
    ideas, reason = kp.fetch_ideas([])
    check(ideas == [] and reason == "no seeds provided", "fetch_ideas rejects empty seeds")


def test_fetch_ideas_success():
    orig = kp._generate_keyword_ideas_raw
    try:
        kp._generate_keyword_ideas_raw = lambda seeds, geo, language, network: [
            {"text": "a", "avg_monthly_searches": 10, "competition": "LOW",
             "competition_index": 1, "top_of_page_bid_low_inr": 1.0, "top_of_page_bid_high_inr": 2.0},
            {"text": "b", "avg_monthly_searches": 99, "competition": "HIGH",
             "competition_index": 80, "top_of_page_bid_low_inr": 5.0, "top_of_page_bid_high_inr": 9.0},
        ]
        ideas, reason = kp.fetch_ideas(["seed"])
        check(reason is None and [i["text"] for i in ideas] == ["b", "a"],
              "fetch_ideas returns deduped, volume-sorted ideas on success")
    finally:
        kp._generate_keyword_ideas_raw = orig


# --- conductor merge: enrich + expand + flag -------------------------------
def _fake_fetch(seeds, **kw):
    norm = {kp.normalize(s) for s in seeds}
    if "system design interview" in norm:                        # NB segment
        return ([
            {"text": "System Design Interview", "avg_monthly_searches": 5400, "competition": "MEDIUM",
             "competition_index": 63, "top_of_page_bid_low_inr": 5.9, "top_of_page_bid_high_inr": 54.7},
            {"text": "grokking system design", "avg_monthly_searches": 1300, "competition": "MEDIUM",
             "competition_index": 44, "top_of_page_bid_low_inr": 16.6, "top_of_page_bid_high_inr": 106.8},
            {"text": "rare low vol term", "avg_monthly_searches": 5, "competition": "LOW",
             "competition_index": 1, "top_of_page_bid_low_inr": 0.0, "top_of_page_bid_high_inr": 0.0},
        ], None)
    if "machine learning course" in norm:                        # DS segment
        return ([
            {"text": "machine learning course", "avg_monthly_searches": 14800, "competition": "MEDIUM",
             "competition_index": 55, "top_of_page_bid_low_inr": 31.5, "top_of_page_bid_high_inr": 106.2},
        ], None)
    return ([], "unexpected seeds")


def _find(rows, keyword, segment=None):
    m = [r for r in rows if r.keyword == keyword and (segment is None or r.segment == segment)]
    return m[0] if len(m) == 1 else None


def test_enrich_and_expand():
    ai = [
        _ai_row("system design interview", "NB", "IK-IN_NB_C1"),
        _ai_row("mock interviews", "NB", "IK-IN_NB_C1"),          # not in ideas -> ai/flag
        _ai_row("machine learning course", "DS", "IK-IN_DS_C2"),
    ]
    orig = kp.fetch_ideas
    try:
        kp.fetch_ideas = _fake_fetch
        rows, stats, degraded = G.enrich_and_expand_with_planner(ai, RUN_DATE, RUN_ID)
    finally:
        kp.fetch_ideas = orig

    r = _find(rows, "system design interview", "NB")
    check(r and r.keyword_source == "both" and r.avg_monthly_searches == 5400,
          "matched AI term is enriched (source=both, volume attached)")
    r = _find(rows, "mock interviews", "NB")
    check(r and r.keyword_source == "ai" and r.demand_flag == "no_planner_volume",
          "AI term with no Planner volume kept + flagged (never dropped)")
    exp = _find(rows, "grokking system design", "NB")
    check(exp and exp.keyword_source == "keyword_planner" and exp.segment == "NB"
          and exp.campaign_name == "IK-IN_NB_C1" and exp.source_signal.startswith("keyword_planner:"),
          "expansion term added, inherits segment + modal campaign, grounded source_signal")
    check(_find(rows, "rare low vol term") is None,
          "low-volume (<= floor) idea is NOT expanded into the queue")
    r = _find(rows, "machine learning course", "DS")
    check(r and r.keyword_source == "both" and r.avg_monthly_searches == 14800,
          "second segment enriched independently")
    check(stats == {"enriched": 2, "expansion_added": 1, "segments_processed": 2, "segments_failed": 0},
          f"stats correct ({stats})")
    check(not degraded, "no degradation on the happy path")


def test_segment_failure_degrades():
    ai = [_ai_row("system design interview", "NB", "IK-IN_NB_C1")]
    orig = kp.fetch_ideas
    try:
        kp.fetch_ideas = lambda seeds, **kw: ([], "keyword_planner fetch failed: quota")
        rows, stats, degraded = G.enrich_and_expand_with_planner(ai, RUN_DATE, RUN_ID)
    finally:
        kp.fetch_ideas = orig
    check(len(rows) == 1 and rows[0].keyword_source is None,
          "on Planner failure the segment's AI rows pass through unchanged")
    check(stats["segments_failed"] == 1 and any("keyword_planner[NB]" in d for d in degraded),
          "segment failure is counted + recorded in degraded (run continues)")


if __name__ == "__main__":
    test_normalize_and_dedupe()
    test_fetch_ideas_failsoft()
    test_fetch_ideas_success()
    test_enrich_and_expand()
    test_segment_failure_degrades()
    print("\nAll tests_keyword_planner assertions passed.")
