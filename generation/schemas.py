"""Pydantic validation contracts + BigQuery schemas for the Generation dept.

Pattern cloned from market_research/shared/schemas.py: each Pydantic model is
the write-time gate -- a row must validate here (via bq_writer.write_rows())
before it is written, in DRY_RUN or live. Each model has a parallel *_BQ
schema tuple list (name, bq_type, mode) that must stay in lockstep with the
model's fields -- add/rename a Pydantic field, update the matching *_BQ entry
in the same commit.

All 12 tables carry `campaign_name` + `segment` except `generation_runs`
(a per-run aggregate across campaigns/paths, not a per-campaign row) and
`value_ledger` (campaign_name/segment are nullable there -- some ledger
entries, e.g. an account-wide waste baseline, aren't tied to one campaign).
All tables partition on `run_date` except `campaign_registry` (a snapshot
table, partitions on `snapshot_date`) and `kw_search_term_perf` (partitions
on its own `date` column, per PLAN.md Phase 0 Step 1's field list).

Every generated-content table (keyword_candidates, keyword_rejects,
ad_copy_drafts, audience_angles, creative_briefs) requires `source_signal`
(grounding citation) -- PLAN.md: "every brain must require its output to
cite the source signal."
"""
from __future__ import annotations
from datetime import date, datetime, timezone
from typing import Optional, Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator


class _Row(BaseModel):
    """Shared base for every Generation-department row model: forbids any
    field not explicitly declared, so a typo'd or extra key (e.g. a renamed
    column the caller didn't update) raises pydantic.ValidationError instead
    of silently passing through model_dump() and getting written -- or
    silently dropped -- at the BQ layer."""
    model_config = ConfigDict(extra="forbid")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _non_empty(v: str, field_name: str) -> str:
    if not v or not v.strip():
        raise ValueError(f"{field_name} must not be empty (grounding is mandatory)")
    return v.strip()


# ---- 1. campaign_registry (snapshot table, partitions on snapshot_date) ----
class CampaignRegistryRow(_Row):
    campaign_name: str
    platform: Literal["google", "linkedin", "meta"]
    segment: str
    program: Optional[str] = None
    geo: Optional[str] = None
    funnel_stage: Optional[str] = None
    utm_campaign: Optional[str] = None
    utm_source: Optional[str] = None
    utm_medium: Optional[str] = None
    platform_campaign_id: Optional[str] = None
    status: Literal["active", "paused", "archived"] = "active"
    registered_at: datetime = Field(default_factory=_now)
    snapshot_date: date


CAMPAIGN_REGISTRY_BQ = [
    ("campaign_name", "STRING", "REQUIRED"), ("platform", "STRING", "REQUIRED"),
    ("segment", "STRING", "REQUIRED"), ("program", "STRING", "NULLABLE"),
    ("geo", "STRING", "NULLABLE"), ("funnel_stage", "STRING", "NULLABLE"),
    ("utm_campaign", "STRING", "NULLABLE"), ("utm_source", "STRING", "NULLABLE"),
    ("utm_medium", "STRING", "NULLABLE"), ("platform_campaign_id", "STRING", "NULLABLE"),
    ("status", "STRING", "NULLABLE"), ("registered_at", "TIMESTAMP", "REQUIRED"),
    ("snapshot_date", "DATE", "REQUIRED"),
]


# ---- 2. keyword_candidates --------------------------------------------------
class KeywordCandidateRow(_Row):
    run_date: date
    run_id: str
    campaign_name: str
    segment: str
    keyword: str
    match_type: Optional[str] = None                      # broad | phrase | exact
    kind: Literal["new", "negative"] = "new"
    relevance_verdict: Literal["relevant", "borderline", "irrelevant"]
    relevance_score: float = Field(ge=0, le=1)
    filter_reason: Optional[str] = None
    source_signal: str                                    # grounding citation, required
    status: Literal[
        "proposed", "needs_review", "approved", "rejected", "launched"
    ] = "proposed"
    reviewed_by: Optional[str] = None
    reviewed_at: Optional[datetime] = None
    # --- Keyword Planner demand grounding (all NULLABLE; populated only when
    # CONDUCTOR_ENABLE_KEYWORD_PLANNER is on -- see generation/keyword_planner.py).
    # keyword_source: how this candidate arose -- "ai" (generator only),
    # "keyword_planner" (real-searched expansion term), or "both" (AI term that
    # Planner confirmed real demand for). demand_flag e.g. "no_planner_volume"
    # marks an AI term Planner reported at/under the volume floor (kept, not
    # dropped -- may be emerging demand ahead of search history).
    keyword_source: Optional[str] = None                  # ai | keyword_planner | both
    avg_monthly_searches: Optional[int] = Field(default=None, ge=0)
    competition: Optional[str] = None                     # LOW | MEDIUM | HIGH | UNKNOWN
    competition_index: Optional[int] = Field(default=None, ge=0, le=100)
    top_of_page_bid_low_inr: Optional[float] = Field(default=None, ge=0)
    top_of_page_bid_high_inr: Optional[float] = Field(default=None, ge=0)
    demand_flag: Optional[str] = None                     # e.g. no_planner_volume

    @field_validator("source_signal")
    @classmethod
    def _source_signal_required(cls, v):
        return _non_empty(v, "source_signal")

    @field_validator("keyword")
    @classmethod
    def _keyword_required(cls, v):
        return _non_empty(v, "keyword")


KEYWORD_CANDIDATES_BQ = [
    ("run_date", "DATE", "REQUIRED"), ("run_id", "STRING", "REQUIRED"),
    ("campaign_name", "STRING", "REQUIRED"), ("segment", "STRING", "REQUIRED"),
    ("keyword", "STRING", "REQUIRED"), ("match_type", "STRING", "NULLABLE"),
    ("kind", "STRING", "NULLABLE"), ("relevance_verdict", "STRING", "REQUIRED"),
    ("relevance_score", "FLOAT64", "REQUIRED"), ("filter_reason", "STRING", "NULLABLE"),
    ("source_signal", "STRING", "REQUIRED"), ("status", "STRING", "NULLABLE"),
    ("reviewed_by", "STRING", "NULLABLE"), ("reviewed_at", "TIMESTAMP", "NULLABLE"),
    ("keyword_source", "STRING", "NULLABLE"), ("avg_monthly_searches", "INTEGER", "NULLABLE"),
    ("competition", "STRING", "NULLABLE"), ("competition_index", "INTEGER", "NULLABLE"),
    ("top_of_page_bid_low_inr", "FLOAT64", "NULLABLE"),
    ("top_of_page_bid_high_inr", "FLOAT64", "NULLABLE"),
    ("demand_flag", "STRING", "NULLABLE"),
]


# ---- 3. keyword_rejects (negative-keyword candidates; first shipped export) -
class KeywordRejectRow(_Row):
    run_date: date
    run_id: str
    campaign_name: str
    segment: str
    keyword: str
    reject_reason: str
    relevance_score: Optional[float] = Field(default=None, ge=0, le=1)
    source_signal: str
    negative_candidate: bool = True

    @field_validator("keyword")
    @classmethod
    def _keyword_required(cls, v):
        return _non_empty(v, "keyword")

    @field_validator("reject_reason")
    @classmethod
    def _reason_required(cls, v):
        return _non_empty(v, "reject_reason")

    @field_validator("source_signal")
    @classmethod
    def _source_signal_required(cls, v):
        return _non_empty(v, "source_signal")


KEYWORD_REJECTS_BQ = [
    ("run_date", "DATE", "REQUIRED"), ("run_id", "STRING", "REQUIRED"),
    ("campaign_name", "STRING", "REQUIRED"), ("segment", "STRING", "REQUIRED"),
    ("keyword", "STRING", "REQUIRED"), ("reject_reason", "STRING", "REQUIRED"),
    ("relevance_score", "FLOAT64", "NULLABLE"), ("source_signal", "STRING", "REQUIRED"),
    ("negative_candidate", "BOOL", "NULLABLE"),
]


# ---- 4. ad_copy_drafts -------------------------------------------------------
class AdCopyDraftRow(_Row):
    run_date: date
    run_id: str
    campaign_name: str
    segment: str
    platform: Literal["google", "linkedin", "meta"]
    asset_id: str
    headline: str
    primary_text: Optional[str] = None
    description: Optional[str] = None
    cta: Optional[str] = None
    source_signal: str
    status: Literal["proposed", "approved", "rejected", "launched"] = "proposed"
    reviewed_by: Optional[str] = None
    reviewed_at: Optional[datetime] = None
    edit_distance: Optional[int] = Field(default=None, ge=0)

    @field_validator("headline", "asset_id", "source_signal")
    @classmethod
    def _required_str(cls, v, info):
        return _non_empty(v, info.field_name)


AD_COPY_DRAFTS_BQ = [
    ("run_date", "DATE", "REQUIRED"), ("run_id", "STRING", "REQUIRED"),
    ("campaign_name", "STRING", "REQUIRED"), ("segment", "STRING", "REQUIRED"),
    ("platform", "STRING", "REQUIRED"), ("asset_id", "STRING", "REQUIRED"),
    ("headline", "STRING", "REQUIRED"), ("primary_text", "STRING", "NULLABLE"),
    ("description", "STRING", "NULLABLE"), ("cta", "STRING", "NULLABLE"),
    ("source_signal", "STRING", "REQUIRED"), ("status", "STRING", "NULLABLE"),
    ("reviewed_by", "STRING", "NULLABLE"), ("reviewed_at", "TIMESTAMP", "NULLABLE"),
    ("edit_distance", "INTEGER", "NULLABLE"),
]


# ---- 5. audience_angles ------------------------------------------------------
class AudienceAngleRow(_Row):
    run_date: date
    run_id: str
    campaign_name: str
    segment: str
    platform: Literal["google", "linkedin", "meta"]
    asset_id: str
    angle_name: str
    angle_description: Optional[str] = None
    persona: Optional[str] = None
    source_signal: str
    status: Literal["proposed", "approved", "rejected", "launched"] = "proposed"
    reviewed_by: Optional[str] = None
    reviewed_at: Optional[datetime] = None

    @field_validator("angle_name", "asset_id", "source_signal")
    @classmethod
    def _required_str(cls, v, info):
        return _non_empty(v, info.field_name)


AUDIENCE_ANGLES_BQ = [
    ("run_date", "DATE", "REQUIRED"), ("run_id", "STRING", "REQUIRED"),
    ("campaign_name", "STRING", "REQUIRED"), ("segment", "STRING", "REQUIRED"),
    ("platform", "STRING", "REQUIRED"), ("asset_id", "STRING", "REQUIRED"),
    ("angle_name", "STRING", "REQUIRED"), ("angle_description", "STRING", "NULLABLE"),
    ("persona", "STRING", "NULLABLE"), ("source_signal", "STRING", "REQUIRED"),
    ("status", "STRING", "NULLABLE"), ("reviewed_by", "STRING", "NULLABLE"),
    ("reviewed_at", "TIMESTAMP", "NULLABLE"),
]


# ---- 6. creative_briefs ------------------------------------------------------
class CreativeBriefRow(_Row):
    run_date: date
    run_id: str
    campaign_name: str
    segment: str
    asset_id: str
    brief_title: str
    brief_body: Optional[str] = None
    visual_direction: Optional[str] = None
    source_signal: str
    status: Literal["proposed", "approved", "rejected", "launched"] = "proposed"

    @field_validator("brief_title", "asset_id", "source_signal")
    @classmethod
    def _required_str(cls, v, info):
        return _non_empty(v, info.field_name)


CREATIVE_BRIEFS_BQ = [
    ("run_date", "DATE", "REQUIRED"), ("run_id", "STRING", "REQUIRED"),
    ("campaign_name", "STRING", "REQUIRED"), ("segment", "STRING", "REQUIRED"),
    ("asset_id", "STRING", "REQUIRED"), ("brief_title", "STRING", "REQUIRED"),
    ("brief_body", "STRING", "NULLABLE"), ("visual_direction", "STRING", "NULLABLE"),
    ("source_signal", "STRING", "REQUIRED"), ("status", "STRING", "NULLABLE"),
]


# ---- 7. creative_assets (Phase 2, schema staged now) -------------------------
class CreativeAssetRow(_Row):
    run_date: date
    run_id: str
    campaign_name: str
    segment: str
    asset_id: str
    brief_asset_id: Optional[str] = None
    asset_type: Literal["image", "video"]
    storage_uri: Optional[str] = None
    format: Optional[str] = None
    width: Optional[int] = Field(default=None, ge=0)
    height: Optional[int] = Field(default=None, ge=0)
    brand_safe: bool = False
    format_valid: bool = False
    status: Literal["proposed", "approved", "rejected", "launched"] = "proposed"

    @field_validator("asset_id")
    @classmethod
    def _asset_id_required(cls, v):
        return _non_empty(v, "asset_id")


CREATIVE_ASSETS_BQ = [
    ("run_date", "DATE", "REQUIRED"), ("run_id", "STRING", "REQUIRED"),
    ("campaign_name", "STRING", "REQUIRED"), ("segment", "STRING", "REQUIRED"),
    ("asset_id", "STRING", "REQUIRED"), ("brief_asset_id", "STRING", "NULLABLE"),
    ("asset_type", "STRING", "REQUIRED"), ("storage_uri", "STRING", "NULLABLE"),
    ("format", "STRING", "NULLABLE"), ("width", "INTEGER", "NULLABLE"),
    ("height", "INTEGER", "NULLABLE"), ("brand_safe", "BOOL", "NULLABLE"),
    ("format_valid", "BOOL", "NULLABLE"), ("status", "STRING", "NULLABLE"),
]


# ---- 8. asset_performance_feedback (Phase 3 feedback loop, schema staged now)
class AssetPerformanceFeedbackRow(_Row):
    run_date: date
    campaign_name: str
    segment: str
    asset_id: str
    utm_content: Optional[str] = None
    platform_ad_id: Optional[str] = None
    impressions: Optional[int] = Field(default=None, ge=0)
    clicks: Optional[int] = Field(default=None, ge=0)
    ctr: Optional[float] = Field(default=None, ge=0)
    spend_inr: Optional[float] = Field(default=None, ge=0)
    leads: Optional[int] = Field(default=None, ge=0)
    quality_leads: Optional[int] = Field(default=None, ge=0)
    cpql_inr: Optional[float] = Field(default=None, ge=0)
    verdict: Optional[Literal["winner", "neutral", "loser"]] = None
    period_start: Optional[date] = None
    period_end: Optional[date] = None

    @field_validator("asset_id")
    @classmethod
    def _asset_id_required(cls, v):
        return _non_empty(v, "asset_id")


ASSET_PERFORMANCE_FEEDBACK_BQ = [
    ("run_date", "DATE", "REQUIRED"), ("campaign_name", "STRING", "REQUIRED"),
    ("segment", "STRING", "REQUIRED"), ("asset_id", "STRING", "REQUIRED"),
    ("utm_content", "STRING", "NULLABLE"), ("platform_ad_id", "STRING", "NULLABLE"),
    ("impressions", "INTEGER", "NULLABLE"), ("clicks", "INTEGER", "NULLABLE"),
    ("ctr", "FLOAT64", "NULLABLE"), ("spend_inr", "FLOAT64", "NULLABLE"),
    ("leads", "INTEGER", "NULLABLE"), ("quality_leads", "INTEGER", "NULLABLE"),
    ("cpql_inr", "FLOAT64", "NULLABLE"), ("verdict", "STRING", "NULLABLE"),
    ("period_start", "DATE", "NULLABLE"), ("period_end", "DATE", "NULLABLE"),
]


# ---- 9. generation_runs (per-run aggregate; no campaign_name/segment -- see
# module docstring deviation note) -------------------------------------------
class GenerationRunRow(_Row):
    run_date: date
    run_id: str
    path: Literal["keyword", "copy", "angle"]
    candidates_generated: Optional[int] = Field(default=None, ge=0)
    passed_filter: Optional[int] = Field(default=None, ge=0)
    borderline: Optional[int] = Field(default=None, ge=0)
    rejected: Optional[int] = Field(default=None, ge=0)
    llm_calls: Optional[int] = Field(default=None, ge=0)
    duration_seconds: Optional[float] = Field(default=None, ge=0)
    status: Literal["success", "partial", "failed"] = "success"
    notes: Optional[str] = None


GENERATION_RUNS_BQ = [
    ("run_date", "DATE", "REQUIRED"), ("run_id", "STRING", "REQUIRED"),
    ("path", "STRING", "REQUIRED"), ("candidates_generated", "INTEGER", "NULLABLE"),
    ("passed_filter", "INTEGER", "NULLABLE"), ("borderline", "INTEGER", "NULLABLE"),
    ("rejected", "INTEGER", "NULLABLE"), ("llm_calls", "INTEGER", "NULLABLE"),
    ("duration_seconds", "FLOAT64", "NULLABLE"), ("status", "STRING", "NULLABLE"),
    ("notes", "STRING", "NULLABLE"),
]


# ---- 10. kw_search_term_perf (partitions on its own `date` column) ----------
class KwSearchTermPerfRow(_Row):
    date: date
    campaign_name: str
    segment: str
    keyword_or_term: str
    term_type: Literal["keyword", "search_term"]
    match_type: Optional[str] = None
    impressions: Optional[int] = Field(default=None, ge=0)
    clicks: Optional[int] = Field(default=None, ge=0)
    ctr: Optional[float] = Field(default=None, ge=0)
    avg_cpc_inr: Optional[float] = Field(default=None, ge=0)
    cost_inr: Optional[float] = Field(default=None, ge=0)
    quality_score: Optional[int] = Field(default=None, ge=1, le=10)

    @field_validator("keyword_or_term")
    @classmethod
    def _term_required(cls, v):
        return _non_empty(v, "keyword_or_term")


KW_SEARCH_TERM_PERF_BQ = [
    ("date", "DATE", "REQUIRED"), ("campaign_name", "STRING", "REQUIRED"),
    ("segment", "STRING", "REQUIRED"), ("keyword_or_term", "STRING", "REQUIRED"),
    ("term_type", "STRING", "REQUIRED"), ("match_type", "STRING", "NULLABLE"),
    ("impressions", "INTEGER", "NULLABLE"), ("clicks", "INTEGER", "NULLABLE"),
    ("ctr", "FLOAT64", "NULLABLE"), ("avg_cpc_inr", "FLOAT64", "NULLABLE"),
    ("cost_inr", "FLOAT64", "NULLABLE"), ("quality_score", "INTEGER", "NULLABLE"),
]


# ---- 11. lead_quality_scores (CPQL signal, read-only pull-forward) ----------
class LeadQualityScoreRow(_Row):
    period_start: date
    period_end: date
    run_date: date
    campaign_name: str
    segment: str
    leads: Optional[int] = Field(default=None, ge=0)
    quality_leads: Optional[int] = Field(default=None, ge=0)
    junk_leads: Optional[int] = Field(default=None, ge=0)
    quality_ratio: Optional[float] = Field(default=None, ge=0, le=1)
    spend_inr: Optional[float] = Field(default=None, ge=0)
    cpl_inr: Optional[float] = Field(default=None, ge=0)
    cpql_inr: Optional[float] = Field(default=None, ge=0)


LEAD_QUALITY_SCORES_BQ = [
    ("period_start", "DATE", "REQUIRED"), ("period_end", "DATE", "REQUIRED"),
    ("run_date", "DATE", "REQUIRED"), ("campaign_name", "STRING", "REQUIRED"),
    ("segment", "STRING", "REQUIRED"), ("leads", "INTEGER", "NULLABLE"),
    ("quality_leads", "INTEGER", "NULLABLE"), ("junk_leads", "INTEGER", "NULLABLE"),
    ("quality_ratio", "FLOAT64", "NULLABLE"), ("spend_inr", "FLOAT64", "NULLABLE"),
    ("cpl_inr", "FLOAT64", "NULLABLE"), ("cpql_inr", "FLOAT64", "NULLABLE"),
]


# ---- 12. value_ledger (the audited ₹ P&L line; campaign_name/segment nullable
# -- see module docstring deviation note) ------------------------------------
class ValueLedgerRow(_Row):
    run_date: date
    entry_id: str
    action_type: Literal[
        "negative_added", "keyword_launched", "copy_refreshed",
        "waste_baseline", "other",
    ]
    description: Optional[str] = None
    campaign_name: Optional[str] = None
    segment: Optional[str] = None
    expected_impact_inr: Optional[float] = None
    realized_impact_inr: Optional[float] = None
    realized_at: Optional[datetime] = None
    status: Literal["projected", "realized", "invalidated"] = "projected"

    @field_validator("entry_id")
    @classmethod
    def _entry_id_required(cls, v):
        return _non_empty(v, "entry_id")


VALUE_LEDGER_BQ = [
    ("run_date", "DATE", "REQUIRED"), ("entry_id", "STRING", "REQUIRED"),
    ("action_type", "STRING", "REQUIRED"), ("description", "STRING", "NULLABLE"),
    ("campaign_name", "STRING", "NULLABLE"), ("segment", "STRING", "NULLABLE"),
    ("expected_impact_inr", "FLOAT64", "NULLABLE"),
    ("realized_impact_inr", "FLOAT64", "NULLABLE"),
    ("realized_at", "TIMESTAMP", "NULLABLE"), ("status", "STRING", "NULLABLE"),
]


# ---------------------------------------------------------------------------
# logical table name -> (Pydantic model, BQ schema tuple list)
# bq_writer.py's allowlist is exactly this dict's keys -- nothing else is
# writable, and TABLE_PREFIX (config.py) is applied on top of these names.
# ---------------------------------------------------------------------------
REGISTRY = {
    "campaign_registry":            (CampaignRegistryRow,           CAMPAIGN_REGISTRY_BQ),
    "keyword_candidates":           (KeywordCandidateRow,           KEYWORD_CANDIDATES_BQ),
    "keyword_rejects":              (KeywordRejectRow,              KEYWORD_REJECTS_BQ),
    "ad_copy_drafts":               (AdCopyDraftRow,                AD_COPY_DRAFTS_BQ),
    "audience_angles":              (AudienceAngleRow,              AUDIENCE_ANGLES_BQ),
    "creative_briefs":              (CreativeBriefRow,              CREATIVE_BRIEFS_BQ),
    "creative_assets":              (CreativeAssetRow,              CREATIVE_ASSETS_BQ),
    "asset_performance_feedback":   (AssetPerformanceFeedbackRow,   ASSET_PERFORMANCE_FEEDBACK_BQ),
    "generation_runs":              (GenerationRunRow,              GENERATION_RUNS_BQ),
    "kw_search_term_perf":          (KwSearchTermPerfRow,           KW_SEARCH_TERM_PERF_BQ),
    "lead_quality_scores":          (LeadQualityScoreRow,           LEAD_QUALITY_SCORES_BQ),
    "value_ledger":                 (ValueLedgerRow,                VALUE_LEDGER_BQ),
}

# logical table name -> partition column (the idempotency key for
# bq_writer.write_partitions' table$YYYYMMDD atomic replace).
PARTITION_FIELDS = {
    "campaign_registry": "snapshot_date",
    "keyword_candidates": "run_date",
    "keyword_rejects": "run_date",
    "ad_copy_drafts": "run_date",
    "audience_angles": "run_date",
    "creative_briefs": "run_date",
    "creative_assets": "run_date",
    "asset_performance_feedback": "run_date",
    "generation_runs": "run_date",
    "kw_search_term_perf": "date",
    "lead_quality_scores": "run_date",
    "value_ledger": "run_date",
}

assert set(REGISTRY) == set(PARTITION_FIELDS), "REGISTRY/PARTITION_FIELDS drifted"
