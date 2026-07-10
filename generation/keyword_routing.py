#!/usr/bin/env python3
"""
Topic-based campaign routing + already-running-keyword exclusion for the
Generation dept's keyword path (docs/keyword-planner-integration-plan.md,
follow-up changes 2026-07-10).

Two independent, flag-gated gates applied AFTER candidate generation
(+ Keyword Planner enrich/expand), BEFORE the raw-keyword staging write:

1. route_by_topic() -- AI-topic keywords route to the EdgeUp program's SEARCH
   campaign; everything else (DSA, system design, coding interview, algorithms,
   SWE, ...) routes to LevelUp's. "AI" is a curated vocabulary (ML / deep
   learning + GenAI / LLM / agentic) matched on word boundaries. The specific
   campaign is auto-resolved from the campaign_registry (program + a `Search`
   name token, non-search channels like Demand Gen / PMax excluded), but the
   account has many search campaigns per program, so the recommended production
   setup is to PIN the exact campaign per program via env
   (KWP_EDGEUP_CAMPAIGN / KWP_LEVELUP_CAMPAIGN). campaign_name AND segment are
   set together to keep attribution registry-consistent.

2. exclude_running() -- positive ("new") candidates whose keyword is already an
   ENABLED keyword in the account are excluded (we already bid on them). The
   running set comes from keyword_planner.fetch_active_keywords(). Negatives
   (kind="negative") are never touched here. Excluded terms are NOT written to
   keyword_rejects -- an already-running keyword must never become a negative.

Both gates are pure functions over the candidate list (the live keyword pull
is injected), so tests drive them with a fixture registry / running-set and
never hit Google Ads.
"""
from __future__ import annotations

import os
import re

import registry as _registry
import schemas

# AI vocabulary (user selection 2026-07-10: ML/deep-learning + GenAI/LLM/agentic;
# NLP/CV and data-science deliberately EXCLUDED -> they fall to LevelUp). Matched
# on word boundaries so "ai" doesn't fire inside "email" nor "ml" inside "html".
_AI_PATTERN = re.compile(
    r"\b("
    r"ai|artificial intelligence|"
    r"machine learning|ml|ml engineer|mlops|"
    r"deep learning|neural networks?|"
    r"generative ai|gen ai|genai|"
    r"llms?|large language models?|gpt|chatgpt|prompt engineering|"
    r"agentic(?: ai)?|rag|ai engineer"
    r")\b"
)
_WS = re.compile(r"\s+")

# Per-program env pin: set to a live campaign_name to force all of that topic's
# keywords into it (overrides the registry auto-pick). Verifying + pinning the
# exact SEARCH campaign per program is the recommended production setup -- the
# account has many search campaigns per program, so auto-pick is a fallback.
_ENV_PIN = {"edgeup": "KWP_EDGEUP_CAMPAIGN", "levelup": "KWP_LEVELUP_CAMPAIGN"}

# Non-search channels keywords must NEVER route to: Demand Gen / Discovery and
# Performance Max target audiences, not manual keywords. This account names the
# channel in the campaign_name (IK-IN_Discovery_..., IK-IN_PMax_...), and marks
# real search campaigns with a `Search` token (IK-IN_Search_NB_EdgeUp_...).
_NON_SEARCH_TOKENS = {"discovery", "demandgen", "pmax", "display", "video", "shopping"}


def normalize(text: str) -> str:
    return _WS.sub(" ", (text or "").strip().lower())


def is_ai_keyword(text: str) -> bool:
    """True if the keyword is AI-topic (-> EdgeUp); False otherwise (-> LevelUp)."""
    return bool(_AI_PATTERN.search(normalize(text)))


def _is_search_campaign(campaign_name: str) -> bool:
    """A campaign that can hold manual keywords: carries a `Search` token and no
    non-search channel token. Deliberately strict -- an ambiguous campaign is
    treated as non-search (we'd rather leave a keyword unrouted than drop it
    into a Demand Gen / PMax campaign that silently ignores keywords)."""
    tokens = set(re.split(r"[\s_\-]+", normalize(campaign_name)))
    if tokens & _NON_SEARCH_TOKENS:
        return False
    return "search" in tokens


def resolve_program_campaign(program: str, segment: str, reg=None) -> tuple[str | None, str]:
    """Resolve the target (campaign_name, segment) for a program.

    Priority: env pin -> a registry SEARCH campaign for the program (prefer one
    matching `segment`, then a 'generic' one, then name order) -> (None, segment)
    when the program has NO search campaign (caller keeps the candidate's
    original campaign_name; keywords are never auto-routed to a non-search
    channel). Returns the resolved campaign's own segment to keep
    campaign<->segment consistent for attribution.
    """
    pin = os.environ.get(_ENV_PIN.get(program, ""), "").strip()
    if pin:
        return pin, segment
    reg = reg if reg is not None else _registry.load_registry()
    search = [
        e for e in reg.values()
        if (e.program or "").lower() == program
        and (e.platform or "google").lower() in ("google", "none", "")
        and _is_search_campaign(e.campaign_name)
    ]
    if not search:
        return None, segment

    def rank(e):
        n = normalize(e.campaign_name)
        return (
            0 if (e.segment or "") == segment else 1,
            0 if "generic" in n else 1,
            e.campaign_name,
        )

    best = sorted(search, key=rank)[0]
    return best.campaign_name, (best.segment or segment)


def route_by_topic(rows: list, reg=None) -> tuple[list, dict]:
    """Override each POSITIVE ("new") candidate's campaign_name+segment by topic
    (AI -> EdgeUp CI campaign, else -> LevelUp CI campaign). Negatives are left
    as-is. Unresolvable programs leave the candidate unchanged (counted)."""
    reg = reg if reg is not None else _registry.load_registry()
    out, ai_n, nonai_n, unresolved = [], 0, 0, 0
    for r in rows:
        if r.kind == "negative":
            out.append(r)
            continue
        program = "edgeup" if is_ai_keyword(r.keyword) else "levelup"
        campaign, segment = resolve_program_campaign(program, r.segment, reg)
        if campaign is None:
            unresolved += 1
            out.append(r)
            continue
        if program == "edgeup":
            ai_n += 1
        else:
            nonai_n += 1
        out.append(schemas.KeywordCandidateRow(
            **{**r.model_dump(), "campaign_name": campaign, "segment": segment}))
    return out, {"routed_ai_edgeup": ai_n, "routed_nonai_levelup": nonai_n,
                 "route_unresolved": unresolved}


def exclude_running(rows: list, running: set) -> tuple[list, list, int]:
    """Drop positive ("new") candidates already ENABLED in the account.
    Returns (kept, excluded, excluded_count). No-op if `running` is empty."""
    if not running:
        return rows, [], 0
    kept, excluded = [], []
    for r in rows:
        if r.kind == "new" and normalize(r.keyword) in running:
            excluded.append(r)
        else:
            kept.append(r)
    return kept, excluded, len(excluded)
