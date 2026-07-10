#!/usr/bin/env python3
"""
Google Keyword Planner demand grounding for the Generation dept's keyword path
(PLAN.md sub-plan `docs/keyword-planner-integration-plan.md`, Step 1).

Wraps `KeywordPlanIdeaService.GenerateKeywordIdeas` to return real
search-volume / competition / top-of-page-bid data for a set of seed keywords.
The keyword path uses this for BOTH:

  * enrich -- attach real INR volume/competition/bid to AI-generated candidates;
  * expand -- surface real-searched terms the AI did not propose.

Reuse, not rebuild: the live call goes through the SAME `GoogleAdsClient` +
`GOOGLE_ADS_*` credentials `collect_kw_perf.py` already builds (the "direct
Google Ads API, not an MCP tool" pattern the collector uses). One new service.

Design guarantees this module holds up:
  1. Fail-soft -- every public call returns `(rows, reason)`; on ANY Google Ads
     error it returns `([], reason)` so the conductor degrades to AI-only and
     never crashes the scheduled chain (mirrors generate_assets' `degraded`
     handling). Credential fragments are redacted via collect_kw_perf's shared
     sanitizer.
  2. Testable -- the one function that touches the live API
     (`_generate_keyword_ideas_raw`) is isolated so tests monkeypatch it (the
     same way tests_conductor monkeypatches `llm.call`) and never hit Google.
  3. India/INR by default -- geo/language/network are env-driven with
     India + English + Google Search defaults; bid micros are converted to INR
     (the account's currency).

Every knob is its own env var with a sane default, matching config.py's style.
"""
from __future__ import annotations

import argparse
import logging
import os
import re
from typing import Optional

import collect_kw_perf

log = logging.getLogger("generation.keyword_planner")

# Well-known Google Ads targeting constants (India geo, English language).
KWP_GEO_TARGET = os.environ.get("KWP_GEO_TARGET", "geoTargetConstants/2356")   # India
KWP_LANGUAGE = os.environ.get("KWP_LANGUAGE", "languageConstants/1000")        # English
KWP_NETWORK = os.environ.get("KWP_NETWORK", "GOOGLE_SEARCH")                   # exclude search partners
# Cap on ideas kept per call (after dedup, sorted by volume desc) -- keeps the
# expansion set bounded before it flows into the gate.
KWP_MAX_IDEAS = int(os.environ.get("KWP_MAX_IDEAS", "200"))
# At/under this monthly-search volume an AI candidate is tagged
# `no_planner_volume` (kept, never dropped -- see the integration plan).
KWP_LOW_VOLUME_FLOOR = int(os.environ.get("KWP_LOW_VOLUME_FLOOR", "10"))

_MICROS_PER_UNIT = 1_000_000  # bid micros -> account currency (INR)

_sanitize = collect_kw_perf._sanitize_error  # shared credential-fragment redaction
_WS = re.compile(r"\s+")


def normalize(text: str) -> str:
    """Normalized dedup/match key: lowercased, whitespace-collapsed, trimmed.
    Used both to collapse Planner's own close variants and to match Planner
    ideas back to AI candidates."""
    return _WS.sub(" ", (text or "").strip().lower())


def _generate_keyword_ideas_raw(seeds: list[str], geo: str, language: str,
                                network: str) -> list[dict]:
    """The single live Google Ads call. Isolated so tests monkeypatch THIS
    function and never touch the API (parallel to how the conductor's tests
    monkeypatch `llm.call`). Returns one dict per idea, unsorted/undeduped."""
    client, customer_id = collect_kw_perf._client_and_customer_id()
    svc = client.get_service("KeywordPlanIdeaService")
    req = client.get_type("GenerateKeywordIdeasRequest")
    req.customer_id = customer_id
    req.language = language
    req.geo_target_constants.append(geo)
    req.include_adult_keywords = False
    req.keyword_plan_network = getattr(client.enums.KeywordPlanNetworkEnum, network)
    req.keyword_seed.keywords.extend(seeds)

    out = []
    for idea in svc.generate_keyword_ideas(request=req):
        m = idea.keyword_idea_metrics
        out.append({
            "text": idea.text,
            "avg_monthly_searches": int(m.avg_monthly_searches or 0),
            "competition": getattr(m.competition, "name", str(m.competition)),
            "competition_index": int(m.competition_index or 0),
            "top_of_page_bid_low_inr": round((m.low_top_of_page_bid_micros or 0) / _MICROS_PER_UNIT, 2),
            "top_of_page_bid_high_inr": round((m.high_top_of_page_bid_micros or 0) / _MICROS_PER_UNIT, 2),
        })
    return out


def _dedupe_and_cap(rows: list[dict], max_ideas: int) -> list[dict]:
    """Collapse close variants on the normalized text (keep the highest-volume
    representative), sort by volume descending, cap to `max_ideas`."""
    best: dict[str, dict] = {}
    for r in rows:
        key = normalize(r.get("text", ""))
        if not key:
            continue
        cur = best.get(key)
        if cur is None or r.get("avg_monthly_searches", 0) > cur.get("avg_monthly_searches", 0):
            best[key] = r
    ordered = sorted(best.values(), key=lambda r: r.get("avg_monthly_searches", 0), reverse=True)
    return ordered[:max_ideas] if max_ideas and max_ideas > 0 else ordered


def fetch_ideas(seeds: list[str], *, geo: Optional[str] = None,
                language: Optional[str] = None, network: Optional[str] = None,
                max_ideas: Optional[int] = None) -> tuple[list[dict], Optional[str]]:
    """Fetch Keyword Planner ideas for `seeds`. Returns `(ideas, reason)`:
    `reason` is None on success, or a short sanitized string when the call
    could not run (no seeds, or a Google Ads error) -- in which case `ideas`
    is `[]` and the caller degrades to AI-only. Never raises."""
    seeds = [s.strip() for s in (seeds or []) if s and s.strip()]
    if not seeds:
        return [], "no seeds provided"
    try:
        raw = _generate_keyword_ideas_raw(
            seeds, geo or KWP_GEO_TARGET, language or KWP_LANGUAGE, network or KWP_NETWORK)
    except Exception as e:  # fail-soft: degrade, never crash the chain
        reason = f"keyword_planner fetch failed: {_sanitize(str(e))[:280]}"
        log.warning(reason)
        return [], reason
    ideas = _dedupe_and_cap(raw, max_ideas if max_ideas is not None else KWP_MAX_IDEAS)
    log.info("keyword_planner: %d seeds -> %d ideas (deduped, capped %d)",
             len(seeds), len(ideas), max_ideas if max_ideas is not None else KWP_MAX_IDEAS)
    return ideas, None


def _fetch_active_keywords_raw() -> list[str]:
    """Live pull of the account's ENABLED positive keyword texts via
    `keyword_view`. Isolated (like `_generate_keyword_ideas_raw`) so tests
    monkeypatch it and never touch the API."""
    client, customer_id = collect_kw_perf._client_and_customer_id()
    ga = client.get_service("GoogleAdsService")
    query = (
        "SELECT ad_group_criterion.keyword.text "
        "FROM keyword_view "
        "WHERE ad_group_criterion.status = 'ENABLED' "
        "AND campaign.status = 'ENABLED' AND ad_group.status = 'ENABLED'"
    )
    return [row.ad_group_criterion.keyword.text for row in ga.search(
        customer_id=customer_id, query=query)]


def fetch_active_keywords() -> tuple[set, Optional[str]]:
    """Normalized set of the account's currently-running (ENABLED) keywords,
    for the already-running exclusion gate. Fail-soft: on error returns
    (empty set, reason) so the gate simply excludes nothing (safe degradation --
    never drops candidates because the pull failed) and never raises."""
    try:
        texts = _fetch_active_keywords_raw()
    except Exception as e:
        reason = f"active-keyword fetch failed: {_sanitize(str(e))[:280]}"
        log.warning(reason)
        return set(), reason
    active = {normalize(t) for t in texts if t}
    log.info("keyword_planner: %d active account keywords loaded (exclusion gate)", len(active))
    return active, None


def _cli(argv=None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    p = argparse.ArgumentParser(description="Keyword Planner idea fetch (throwaway/manual check).")
    p.add_argument("--seeds", nargs="+", required=True, help="Seed keywords.")
    p.add_argument("--max-ideas", type=int, default=None)
    args = p.parse_args(argv)
    ideas, reason = fetch_ideas(args.seeds, max_ideas=args.max_ideas)
    if reason:
        print(f"(no ideas) reason: {reason}")
        return 1
    print(f"{len(ideas)} ideas:")
    print(f"{'keyword':<44} {'avg/mo':>8} {'compet':>7} {'idx':>4} {'bid_lo':>7} {'bid_hi':>7}")
    for r in ideas[:40]:
        print(f"{r['text'][:43]:<44} {r['avg_monthly_searches']:>8} {r['competition']:>7} "
              f"{r['competition_index']:>4} {r['top_of_page_bid_low_inr']:>7.1f} "
              f"{r['top_of_page_bid_high_inr']:>7.1f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
