#!/usr/bin/env python3
"""
Tests for topic routing + already-running-keyword exclusion (keyword_routing.py).
Pure functions with an injected registry / running-set -- no live Google Ads.
Run: `python tests_keyword_routing.py`.
"""
import os
import sys
from datetime import date

import keyword_routing as kr
import keyword_planner as kp
import schemas
from registry import RegistryEntry

RUN_DATE = date(2026, 7, 10)
RUN_ID = "routing-test"

REG = {
    # A real SEARCH campaign (the valid keyword target) ...
    "IK-IN_Search_NB_EdgeUp_Generic_AI_35-54": RegistryEntry(
        campaign_name="IK-IN_Search_NB_EdgeUp_Generic_AI_35-54", platform="google",
        segment="NB", program="edgeup"),
    # ... and a Discovery/Demand-Gen one that must NEVER be picked for keywords.
    "IK-IN_Discovery_EdgeUp_CI_Comp_India": RegistryEntry(
        campaign_name="IK-IN_Discovery_EdgeUp_CI_Comp_India", platform="google",
        segment="Discovery", program="edgeup"),
    "IK-IN_Search_NB_LevelUp_Generic_Allsale_HI_INDIA": RegistryEntry(
        campaign_name="IK-IN_Search_NB_LevelUp_Generic_Allsale_HI_INDIA", platform="google",
        segment="DS", program="levelup"),
    # LevelUp PMax — must never be picked either.
    "IK-IN_PMax_LevelUp_INDIA": RegistryEntry(
        campaign_name="IK-IN_PMax_LevelUp_INDIA", platform="google",
        segment="Other", program="levelup"),
}


def check(cond, msg):
    if not cond:
        print(f"FAIL: {msg}")
        sys.exit(1)
    print(f"PASS: {msg}")


def _row(keyword, segment, campaign, kind="new"):
    return schemas.KeywordCandidateRow(
        run_date=RUN_DATE, run_id=RUN_ID, campaign_name=campaign, segment=segment,
        keyword=keyword, kind=kind, relevance_verdict="borderline", relevance_score=0.5,
        source_signal="test", status="proposed")


def test_is_ai_keyword():
    for kw in ["machine learning course", "genai bootcamp", "llm interview prep",
               "agentic ai roadmap", "gpt prompt engineering", "ai engineer salary"]:
        check(kr.is_ai_keyword(kw), f"AI: {kw!r}")
    for kw in ["system design interview", "data structures", "dsa course",
               "coding interview prep", "email marketing", "data science course"]:
        check(not kr.is_ai_keyword(kw), f"non-AI: {kw!r}")


def test_resolve_program_campaign():
    c, s = kr.resolve_program_campaign("edgeup", "NB", REG)
    check(c == "IK-IN_Search_NB_EdgeUp_Generic_AI_35-54" and s == "NB",
          "edgeup -> SEARCH campaign (never the Discovery one)")
    c, s = kr.resolve_program_campaign("levelup", "DS", REG)
    check(c == "IK-IN_Search_NB_LevelUp_Generic_Allsale_HI_INDIA" and s == "DS",
          "levelup -> SEARCH campaign (never PMax)")
    c, s = kr.resolve_program_campaign("edgeup", "CS", {})
    check(c is None and s == "CS", "no search campaign -> (None, original segment)")
    # A program with ONLY non-search campaigns must resolve to None, not the PMax/Discovery one.
    only_pmax = {"p": RegistryEntry(campaign_name="IK-IN_PMax_LevelUp_INDIA",
                                    platform="google", segment="Other", program="levelup")}
    c, s = kr.resolve_program_campaign("levelup", "DS", only_pmax)
    check(c is None, "program with only non-search campaigns -> None (never routes keywords to PMax)")
    os.environ["KWP_EDGEUP_CAMPAIGN"] = "IK-IN_Search_NB_EdgeUp_Pinned"
    try:
        c, s = kr.resolve_program_campaign("edgeup", "CS", REG)
        check(c == "IK-IN_Search_NB_EdgeUp_Pinned", "env pin overrides registry auto-pick")
    finally:
        del os.environ["KWP_EDGEUP_CAMPAIGN"]


def test_resolve_prefers_active():
    """2026-10-27: with weekly refreshes the registry carries real statuses.
    An ACTIVE search campaign must outrank a paused/archived one even when
    the paused one wins every other tie-break (segment match + 'generic'),
    but a paused one is still chosen when it's the only search campaign."""
    paused_best = RegistryEntry(
        campaign_name="IK-IN_Search_NB_EdgeUp_Generic_AI_35-54", platform="google",
        segment="NB", program="edgeup", status="paused")
    active_other = RegistryEntry(
        campaign_name="IK-IN_Search_CS_EdgeUp_AgenticAI", platform="google",
        segment="CS", program="edgeup", status="active")
    archived = RegistryEntry(
        campaign_name="IK-IN_Search_NB_EdgeUp_Generic_Archived", platform="google",
        segment="NB", program="edgeup", status="archived")
    reg = {e.campaign_name: e for e in (paused_best, active_other, archived)}
    c, s = kr.resolve_program_campaign("edgeup", "NB", reg)
    check(c == "IK-IN_Search_CS_EdgeUp_AgenticAI" and s == "CS",
          f"active search campaign outranks a paused segment-matching 'generic' one (got {c}, {s})")
    only_inactive = {e.campaign_name: e for e in (paused_best, archived)}
    c, _s = kr.resolve_program_campaign("edgeup", "NB", only_inactive)
    check(c == "IK-IN_Search_NB_EdgeUp_Generic_AI_35-54",
          f"with no active option, paused/archived campaigns are still ranked, not excluded (got {c})")
    no_status = {e.campaign_name: e for e in (
        RegistryEntry(campaign_name="IK-IN_Search_NB_EdgeUp_Generic_X", platform="google",
                      segment="NB", program="edgeup"),
        active_other)}
    c, _s = kr.resolve_program_campaign("edgeup", "NB", no_status)
    check(c == "IK-IN_Search_NB_EdgeUp_Generic_X",
          f"a missing status counts as active (fixtures / pre-refresh rows) (got {c})")


def test_route_by_topic():
    rows = [
        _row("machine learning course", "CS", "orig_ai"),
        _row("system design interview", "NB", "orig_nonai"),
        _row("free ai course pdf", "NB", "orig_neg", kind="negative"),
    ]
    out, stats = kr.route_by_topic(rows, reg=REG)
    ai = [r for r in out if r.keyword == "machine learning course"][0]
    check(ai.campaign_name == "IK-IN_Search_NB_EdgeUp_Generic_AI_35-54" and ai.segment == "NB",
          "AI keyword routed to EdgeUp SEARCH campaign (+ aligned segment)")
    non = [r for r in out if r.keyword == "system design interview"][0]
    check(non.campaign_name == "IK-IN_Search_NB_LevelUp_Generic_Allsale_HI_INDIA" and non.segment == "DS",
          "non-AI keyword routed to LevelUp SEARCH campaign")
    neg = [r for r in out if r.kind == "negative"][0]
    check(neg.campaign_name == "orig_neg", "negative candidate left untouched by routing")
    check(stats == {"routed_ai_edgeup": 1, "routed_nonai_levelup": 1, "route_unresolved": 0},
          f"routing stats correct ({stats})")


def test_route_unresolved():
    out, stats = kr.route_by_topic([_row("ml course", "CS", "keep_me")], reg={})
    check(out[0].campaign_name == "keep_me" and stats["route_unresolved"] == 1,
          "unresolvable program keeps original campaign + counts unresolved")


def test_exclude_running():
    rows = [
        _row("machine learning course", "CS", "c1"),
        _row("system design interview", "NB", "c2"),
        _row("machine learning course", "CS", "c3", kind="negative"),  # negative, must survive
    ]
    running = {"machine learning course"}
    kept, excluded, n = kr.exclude_running(rows, running)
    check(n == 1 and excluded[0].kind == "new", "already-running positive keyword excluded")
    check(any(r.kind == "negative" for r in kept), "negative with same text is NOT excluded")
    check(any(r.keyword == "system design interview" for r in kept), "non-running keyword kept")
    kept2, _e, n2 = kr.exclude_running(rows, set())
    check(n2 == 0 and len(kept2) == 3, "empty running set is a no-op")


def test_fetch_active_keywords_failsoft():
    orig = kp._fetch_active_keywords_raw
    try:
        kp._fetch_active_keywords_raw = lambda: (_ for _ in ()).throw(RuntimeError("api down"))
        active, reason = kp.fetch_active_keywords()
        check(active == set() and reason and "api down" in reason,
              "fetch_active_keywords degrades to (empty set, reason), never raises")
    finally:
        kp._fetch_active_keywords_raw = orig


if __name__ == "__main__":
    test_is_ai_keyword()
    test_resolve_program_campaign()
    test_resolve_prefers_active()
    test_route_by_topic()
    test_route_unresolved()
    test_exclude_running()
    test_fetch_active_keywords_failsoft()
    print("\nAll tests_keyword_routing assertions passed.")
