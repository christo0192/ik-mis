import "server-only";

import { CE_TABLES, safeQuery, type SafeResult } from "@/lib/bq";

/**
 * SERVER-ONLY query layer for the MIS Generation department's `ce_*` tables
 * (ik-marketing-data.SIP_pattern_rag.ce_<name>, generation/schemas.py's
 * 12-table REGISTRY). Every exported function goes through `safeQuery` (see
 * bq.ts) so a missing table / permission error / transient BQ outage
 * degrades to `{ rows: [], error }` — the caller renders an honest
 * "no data yet" state, never a 500 and never a faked zero
 * (docs/dashboard-vision.md §5).
 *
 * Read-only. The dashboard's only write path is
 * src/app/api/generation/review/route.ts.
 */

// ---------------------------------------------------------------------------
// Normalization helpers (BigQuery driver quirks: INT64 -> string, DATE ->
// {value} or string). Duplicated intentionally from queries.ts rather than
// shared, to keep the two data layers (Market Research vs Generation dept)
// independently movable/removable.
// ---------------------------------------------------------------------------

function num(v: unknown): number | null {
  if (v === null || v === undefined || v === "") return null;
  const n = typeof v === "number" ? v : Number(v);
  return Number.isFinite(n) ? n : null;
}

function dateStr(v: unknown): string | null {
  if (v === null || v === undefined) return null;
  if (typeof v === "string") return v;
  if (typeof v === "object" && v !== null && "value" in v) {
    const val = (v as { value: unknown }).value;
    return typeof val === "string" ? val : null;
  }
  return String(v);
}

function str(v: unknown): string | null {
  return v === null || v === undefined ? null : String(v);
}

function bool(v: unknown): boolean {
  return v === true || v === "true" || v === 1;
}

/** Clamp a caller-supplied LIMIT to a sane, bound integer range before binding as a query param. */
function clampLimit(value: number, max = 1000): number {
  const n = Math.trunc(value);
  if (!Number.isFinite(n) || n < 1) return 1;
  return Math.min(n, max);
}

// ---------------------------------------------------------------------------
// Row types (post-normalization)
// ---------------------------------------------------------------------------

export type ReviewStatus =
  | "proposed"
  | "needs_review"
  | "approved"
  | "rejected"
  | "launched";

export interface CampaignRegistryRow {
  campaign_name: string;
  platform: string | null;
  segment: string | null;
  program: string | null;
  geo: string | null;
  funnel_stage: string | null;
  utm_campaign: string | null;
  utm_source: string | null;
  utm_medium: string | null;
  platform_campaign_id: string | null;
  status: string | null;
  registered_at: string | null;
  snapshot_date: string | null;
  needs_attention: boolean;
}

export interface KeywordCandidateRow {
  run_date: string | null;
  run_id: string | null;
  campaign_name: string;
  segment: string | null;
  keyword: string;
  match_type: string | null;
  kind: string | null;
  relevance_verdict: string | null;
  relevance_score: number | null;
  filter_reason: string | null;
  source_signal: string | null;
  status: string | null;
  reviewed_by: string | null;
  reviewed_at: string | null;
}

export interface KeywordRejectRow {
  run_date: string | null;
  run_id: string | null;
  campaign_name: string;
  segment: string | null;
  keyword: string;
  reject_reason: string | null;
  relevance_score: number | null;
  source_signal: string | null;
  negative_candidate: boolean;
}

export interface AdCopyDraftRow {
  run_date: string | null;
  run_id: string | null;
  campaign_name: string;
  segment: string | null;
  platform: string | null;
  asset_id: string;
  headline: string;
  primary_text: string | null;
  description: string | null;
  cta: string | null;
  source_signal: string | null;
  status: string | null;
  reviewed_by: string | null;
  reviewed_at: string | null;
  edit_distance: number | null;
}

export interface AudienceAngleRow {
  run_date: string | null;
  run_id: string | null;
  campaign_name: string;
  segment: string | null;
  platform: string | null;
  asset_id: string;
  angle_name: string;
  angle_description: string | null;
  persona: string | null;
  source_signal: string | null;
  status: string | null;
  reviewed_by: string | null;
  reviewed_at: string | null;
}

export interface GenerationRunRow {
  run_date: string | null;
  run_id: string;
  path: string | null;
  candidates_generated: number | null;
  passed_filter: number | null;
  borderline: number | null;
  rejected: number | null;
  llm_calls: number | null;
  duration_seconds: number | null;
  status: string | null;
  notes: string | null;
}

export interface LeadQualityScoreRow {
  period_start: string | null;
  period_end: string | null;
  run_date: string | null;
  campaign_name: string;
  segment: string | null;
  leads: number | null;
  quality_leads: number | null;
  junk_leads: number | null;
  quality_ratio: number | null;
  spend_inr: number | null;
  cpl_inr: number | null;
  cpql_inr: number | null;
}

export interface ValueLedgerRow {
  run_date: string | null;
  entry_id: string;
  action_type: string | null;
  description: string | null;
  campaign_name: string | null;
  segment: string | null;
  expected_impact_inr: number | null;
  realized_impact_inr: number | null;
  realized_at: string | null;
  status: string | null;
}

export interface CampaignAssetSummary {
  campaign_name: string;
  segment: string | null;
  asset_count: number;
  pending_count: number;
}

export interface CeFreshnessRow {
  table: string;
  latest_date: string | null;
}

// ---------------------------------------------------------------------------
// Row mappers
// ---------------------------------------------------------------------------

function mapKeywordCandidate(r: Record<string, unknown>): KeywordCandidateRow {
  return {
    run_date: dateStr(r.run_date),
    run_id: str(r.run_id),
    campaign_name: String(r.campaign_name ?? ""),
    segment: str(r.segment),
    keyword: String(r.keyword ?? ""),
    match_type: str(r.match_type),
    kind: str(r.kind),
    relevance_verdict: str(r.relevance_verdict),
    relevance_score: num(r.relevance_score),
    filter_reason: str(r.filter_reason),
    source_signal: str(r.source_signal),
    status: str(r.status),
    reviewed_by: str(r.reviewed_by),
    reviewed_at: dateStr(r.reviewed_at),
  };
}

function mapKeywordReject(r: Record<string, unknown>): KeywordRejectRow {
  return {
    run_date: dateStr(r.run_date),
    run_id: str(r.run_id),
    campaign_name: String(r.campaign_name ?? ""),
    segment: str(r.segment),
    keyword: String(r.keyword ?? ""),
    reject_reason: str(r.reject_reason),
    relevance_score: num(r.relevance_score),
    source_signal: str(r.source_signal),
    negative_candidate: bool(r.negative_candidate),
  };
}

function mapAdCopyDraft(r: Record<string, unknown>): AdCopyDraftRow {
  return {
    run_date: dateStr(r.run_date),
    run_id: str(r.run_id),
    campaign_name: String(r.campaign_name ?? ""),
    segment: str(r.segment),
    platform: str(r.platform),
    asset_id: String(r.asset_id ?? ""),
    headline: String(r.headline ?? ""),
    primary_text: str(r.primary_text),
    description: str(r.description),
    cta: str(r.cta),
    source_signal: str(r.source_signal),
    status: str(r.status),
    reviewed_by: str(r.reviewed_by),
    reviewed_at: dateStr(r.reviewed_at),
    edit_distance: num(r.edit_distance),
  };
}

function mapAudienceAngle(r: Record<string, unknown>): AudienceAngleRow {
  return {
    run_date: dateStr(r.run_date),
    run_id: str(r.run_id),
    campaign_name: String(r.campaign_name ?? ""),
    segment: str(r.segment),
    platform: str(r.platform),
    asset_id: String(r.asset_id ?? ""),
    angle_name: String(r.angle_name ?? ""),
    angle_description: str(r.angle_description),
    persona: str(r.persona),
    source_signal: str(r.source_signal),
    status: str(r.status),
    reviewed_by: str(r.reviewed_by),
    reviewed_at: dateStr(r.reviewed_at),
  };
}

function mapGenerationRun(r: Record<string, unknown>): GenerationRunRow {
  return {
    run_date: dateStr(r.run_date),
    run_id: String(r.run_id ?? ""),
    path: str(r.path),
    candidates_generated: num(r.candidates_generated),
    passed_filter: num(r.passed_filter),
    borderline: num(r.borderline),
    rejected: num(r.rejected),
    llm_calls: num(r.llm_calls),
    duration_seconds: num(r.duration_seconds),
    status: str(r.status),
    notes: str(r.notes),
  };
}

function mapCampaignRegistry(r: Record<string, unknown>): CampaignRegistryRow {
  return {
    campaign_name: String(r.campaign_name ?? ""),
    platform: str(r.platform),
    segment: str(r.segment),
    program: str(r.program),
    geo: str(r.geo),
    funnel_stage: str(r.funnel_stage),
    utm_campaign: str(r.utm_campaign),
    utm_source: str(r.utm_source),
    utm_medium: str(r.utm_medium),
    platform_campaign_id: str(r.platform_campaign_id),
    status: str(r.status),
    registered_at: dateStr(r.registered_at),
    snapshot_date: dateStr(r.snapshot_date),
    needs_attention: bool(r.needs_attention),
  };
}

function mapLeadQualityScore(r: Record<string, unknown>): LeadQualityScoreRow {
  return {
    period_start: dateStr(r.period_start),
    period_end: dateStr(r.period_end),
    run_date: dateStr(r.run_date),
    campaign_name: String(r.campaign_name ?? ""),
    segment: str(r.segment),
    leads: num(r.leads),
    quality_leads: num(r.quality_leads),
    junk_leads: num(r.junk_leads),
    quality_ratio: num(r.quality_ratio),
    spend_inr: num(r.spend_inr),
    cpl_inr: num(r.cpl_inr),
    cpql_inr: num(r.cpql_inr),
  };
}

function mapValueLedger(r: Record<string, unknown>): ValueLedgerRow {
  return {
    run_date: dateStr(r.run_date),
    entry_id: String(r.entry_id ?? ""),
    action_type: str(r.action_type),
    description: str(r.description),
    campaign_name: str(r.campaign_name),
    segment: str(r.segment),
    expected_impact_inr: num(r.expected_impact_inr),
    realized_impact_inr: num(r.realized_impact_inr),
    realized_at: dateStr(r.realized_at),
    status: str(r.status),
  };
}

// ---------------------------------------------------------------------------
// Review queue: keyword_candidates / ad_copy_drafts / audience_angles
// ---------------------------------------------------------------------------

export interface ReviewQueueFilter {
  /** Restrict to one campaign; when set, "latest" means latest run for THAT campaign, not the whole table. */
  campaignName?: string;
  /** Restrict to one segment (used by the /generation/[segment] drill-down). */
  segment?: string;
  /** Restrict to one status value. */
  status?: string;
}

export async function getKeywordCandidates(
  filter: ReviewQueueFilter = {},
): Promise<SafeResult<KeywordCandidateRow>> {
  const { campaignName, segment, status } = filter;
  const clauses: string[] = [];
  const params: Record<string, unknown> = {};

  const latestSubquery = campaignName
    ? `(SELECT MAX(run_date) FROM \`${CE_TABLES.keywordCandidates}\` WHERE campaign_name = @campaignName)`
    : `(SELECT MAX(run_date) FROM \`${CE_TABLES.keywordCandidates}\`)`;
  clauses.push(`run_date = ${latestSubquery}`);

  if (campaignName) {
    clauses.push("campaign_name = @campaignName");
    params.campaignName = campaignName;
  }
  if (segment) {
    clauses.push("segment = @segment");
    params.segment = segment;
  }
  if (status) {
    clauses.push("status = @status");
    params.status = status;
  }

  const result = await safeQuery<Record<string, unknown>>(
    `SELECT run_date, run_id, campaign_name, segment, keyword, match_type, kind,
            relevance_verdict, relevance_score, filter_reason, source_signal,
            status, reviewed_by, reviewed_at
       FROM \`${CE_TABLES.keywordCandidates}\`
      WHERE ${clauses.join(" AND ")}
      ORDER BY relevance_score DESC NULLS LAST, keyword`,
    params,
  );
  return { rows: result.rows.map(mapKeywordCandidate), error: result.error };
}

export async function getAdCopyDrafts(
  filter: ReviewQueueFilter = {},
): Promise<SafeResult<AdCopyDraftRow>> {
  const { campaignName, segment, status } = filter;
  const clauses: string[] = [];
  const params: Record<string, unknown> = {};

  const latestSubquery = campaignName
    ? `(SELECT MAX(run_date) FROM \`${CE_TABLES.adCopyDrafts}\` WHERE campaign_name = @campaignName)`
    : `(SELECT MAX(run_date) FROM \`${CE_TABLES.adCopyDrafts}\`)`;
  clauses.push(`run_date = ${latestSubquery}`);

  if (campaignName) {
    clauses.push("campaign_name = @campaignName");
    params.campaignName = campaignName;
  }
  if (segment) {
    clauses.push("segment = @segment");
    params.segment = segment;
  }
  if (status) {
    clauses.push("status = @status");
    params.status = status;
  }

  const result = await safeQuery<Record<string, unknown>>(
    `SELECT run_date, run_id, campaign_name, segment, platform, asset_id,
            headline, primary_text, description, cta, source_signal,
            status, reviewed_by, reviewed_at, edit_distance
       FROM \`${CE_TABLES.adCopyDrafts}\`
      WHERE ${clauses.join(" AND ")}
      ORDER BY asset_id`,
    params,
  );
  return { rows: result.rows.map(mapAdCopyDraft), error: result.error };
}

export async function getAudienceAngles(
  filter: ReviewQueueFilter = {},
): Promise<SafeResult<AudienceAngleRow>> {
  const { campaignName, segment, status } = filter;
  const clauses: string[] = [];
  const params: Record<string, unknown> = {};

  const latestSubquery = campaignName
    ? `(SELECT MAX(run_date) FROM \`${CE_TABLES.audienceAngles}\` WHERE campaign_name = @campaignName)`
    : `(SELECT MAX(run_date) FROM \`${CE_TABLES.audienceAngles}\`)`;
  clauses.push(`run_date = ${latestSubquery}`);

  if (campaignName) {
    clauses.push("campaign_name = @campaignName");
    params.campaignName = campaignName;
  }
  if (segment) {
    clauses.push("segment = @segment");
    params.segment = segment;
  }
  if (status) {
    clauses.push("status = @status");
    params.status = status;
  }

  const result = await safeQuery<Record<string, unknown>>(
    `SELECT run_date, run_id, campaign_name, segment, platform, asset_id,
            angle_name, angle_description, persona, source_signal,
            status, reviewed_by, reviewed_at
       FROM \`${CE_TABLES.audienceAngles}\`
      WHERE ${clauses.join(" AND ")}
      ORDER BY asset_id`,
    params,
  );
  return { rows: result.rows.map(mapAudienceAngle), error: result.error };
}

/** Single ad_copy_draft or audience_angle asset by asset_id (latest run carrying that id), for the asset-detail drill-down page. */
export async function getAdCopyDraftByAssetId(
  assetId: string,
): Promise<SafeResult<AdCopyDraftRow>> {
  const result = await safeQuery<Record<string, unknown>>(
    `SELECT run_date, run_id, campaign_name, segment, platform, asset_id,
            headline, primary_text, description, cta, source_signal,
            status, reviewed_by, reviewed_at, edit_distance
       FROM \`${CE_TABLES.adCopyDrafts}\`
      WHERE asset_id = @assetId
      ORDER BY run_date DESC
      LIMIT 1`,
    { assetId },
  );
  return { rows: result.rows.map(mapAdCopyDraft), error: result.error };
}

export async function getAudienceAngleByAssetId(
  assetId: string,
): Promise<SafeResult<AudienceAngleRow>> {
  const result = await safeQuery<Record<string, unknown>>(
    `SELECT run_date, run_id, campaign_name, segment, platform, asset_id,
            angle_name, angle_description, persona, source_signal,
            status, reviewed_by, reviewed_at
       FROM \`${CE_TABLES.audienceAngles}\`
      WHERE asset_id = @assetId
      ORDER BY run_date DESC
      LIMIT 1`,
    { assetId },
  );
  return { rows: result.rows.map(mapAudienceAngle), error: result.error };
}

// ---------------------------------------------------------------------------
// Rejects / negatives
// ---------------------------------------------------------------------------

export async function getKeywordRejects(
  filter: Pick<ReviewQueueFilter, "campaignName" | "segment"> = {},
): Promise<SafeResult<KeywordRejectRow>> {
  const { campaignName, segment } = filter;
  const clauses: string[] = [];
  const params: Record<string, unknown> = {};

  const latestSubquery = campaignName
    ? `(SELECT MAX(run_date) FROM \`${CE_TABLES.keywordRejects}\` WHERE campaign_name = @campaignName)`
    : `(SELECT MAX(run_date) FROM \`${CE_TABLES.keywordRejects}\`)`;
  clauses.push(`run_date = ${latestSubquery}`);

  if (campaignName) {
    clauses.push("campaign_name = @campaignName");
    params.campaignName = campaignName;
  }
  if (segment) {
    clauses.push("segment = @segment");
    params.segment = segment;
  }

  const result = await safeQuery<Record<string, unknown>>(
    `SELECT run_date, run_id, campaign_name, segment, keyword, reject_reason,
            relevance_score, source_signal, negative_candidate
       FROM \`${CE_TABLES.keywordRejects}\`
      WHERE ${clauses.join(" AND ")}
      ORDER BY campaign_name, keyword`,
    params,
  );
  return { rows: result.rows.map(mapKeywordReject), error: result.error };
}

// ---------------------------------------------------------------------------
// Generation runs
// ---------------------------------------------------------------------------

export async function getGenerationRuns(
  limit = 100,
): Promise<SafeResult<GenerationRunRow>> {
  const result = await safeQuery<Record<string, unknown>>(
    `SELECT run_date, run_id, path, candidates_generated, passed_filter,
            borderline, rejected, llm_calls, duration_seconds, status, notes
       FROM \`${CE_TABLES.generationRuns}\`
      ORDER BY run_date DESC, run_id DESC, path
      LIMIT @limit`,
    { limit: clampLimit(limit) },
  );
  return { rows: result.rows.map(mapGenerationRun), error: result.error };
}

// ---------------------------------------------------------------------------
// Campaign registry
// ---------------------------------------------------------------------------

export interface CampaignRegistryFilter {
  needsAttentionOnly?: boolean;
  program?: string;
  segment?: string;
}

const NEEDS_ATTENTION_EXPR =
  "(program IS NULL OR LOWER(program) = 'other' OR segment IS NULL OR LOWER(segment) = 'other')";

export async function getCampaignRegistry(
  filter: CampaignRegistryFilter = {},
): Promise<SafeResult<CampaignRegistryRow>> {
  const { needsAttentionOnly, program, segment } = filter;
  const clauses = [
    `snapshot_date = (SELECT MAX(snapshot_date) FROM \`${CE_TABLES.campaignRegistry}\`)`,
  ];
  const params: Record<string, unknown> = {};

  if (needsAttentionOnly) clauses.push(NEEDS_ATTENTION_EXPR);
  if (program) {
    clauses.push("LOWER(COALESCE(program, '')) = LOWER(@program)");
    params.program = program;
  }
  if (segment) {
    clauses.push("segment = @segment");
    params.segment = segment;
  }

  const result = await safeQuery<Record<string, unknown>>(
    `SELECT campaign_name, platform, segment, program, geo, funnel_stage,
            utm_campaign, utm_source, utm_medium, platform_campaign_id,
            status, registered_at, snapshot_date,
            ${NEEDS_ATTENTION_EXPR} AS needs_attention
       FROM \`${CE_TABLES.campaignRegistry}\`
      WHERE ${clauses.join(" AND ")}
      ORDER BY needs_attention DESC, campaign_name`,
    params,
  );
  return { rows: result.rows.map(mapCampaignRegistry), error: result.error };
}

export async function getCampaignRegistryRow(
  campaignName: string,
): Promise<SafeResult<CampaignRegistryRow>> {
  const result = await safeQuery<Record<string, unknown>>(
    `SELECT campaign_name, platform, segment, program, geo, funnel_stage,
            utm_campaign, utm_source, utm_medium, platform_campaign_id,
            status, registered_at, snapshot_date,
            ${NEEDS_ATTENTION_EXPR} AS needs_attention
       FROM \`${CE_TABLES.campaignRegistry}\`
      WHERE campaign_name = @campaignName
      ORDER BY snapshot_date DESC
      LIMIT 1`,
    { campaignName },
  );
  return { rows: result.rows.map(mapCampaignRegistry), error: result.error };
}

/**
 * Campaigns referenced by the latest generated content (keywords/copy/angles)
 * that have no row in the latest campaign_registry snapshot at all — the
 * warn-first "unregistered campaigns" signal
 * (docs/campaign-naming-convention.md §5, answer 5).
 */
export async function getUnregisteredCampaigns(): Promise<
  SafeResult<{ campaign_name: string; segment: string | null }>
> {
  const result = await safeQuery<Record<string, unknown>>(
    `WITH content AS (
       SELECT DISTINCT campaign_name, segment FROM \`${CE_TABLES.keywordCandidates}\`
        WHERE run_date = (SELECT MAX(run_date) FROM \`${CE_TABLES.keywordCandidates}\`)
       UNION DISTINCT
       SELECT DISTINCT campaign_name, segment FROM \`${CE_TABLES.adCopyDrafts}\`
        WHERE run_date = (SELECT MAX(run_date) FROM \`${CE_TABLES.adCopyDrafts}\`)
       UNION DISTINCT
       SELECT DISTINCT campaign_name, segment FROM \`${CE_TABLES.audienceAngles}\`
        WHERE run_date = (SELECT MAX(run_date) FROM \`${CE_TABLES.audienceAngles}\`)
     ),
     registry AS (
       SELECT DISTINCT campaign_name FROM \`${CE_TABLES.campaignRegistry}\`
        WHERE snapshot_date = (SELECT MAX(snapshot_date) FROM \`${CE_TABLES.campaignRegistry}\`)
     )
     SELECT content.campaign_name AS campaign_name, content.segment AS segment
       FROM content
       LEFT JOIN registry ON content.campaign_name = registry.campaign_name
      WHERE registry.campaign_name IS NULL
      ORDER BY content.segment, content.campaign_name`,
  );
  return {
    rows: result.rows.map((r) => ({
      campaign_name: String(r.campaign_name ?? ""),
      segment: str(r.segment),
    })),
    error: result.error,
  };
}

// ---------------------------------------------------------------------------
// Segment / campaign roll-ups (drill-down: segment -> campaigns -> assets)
// ---------------------------------------------------------------------------

const COMBINED_ASSETS_CTE = `
  WITH combined AS (
    SELECT campaign_name, segment, status FROM \`${CE_TABLES.keywordCandidates}\`
     WHERE run_date = (SELECT MAX(run_date) FROM \`${CE_TABLES.keywordCandidates}\`)
    UNION ALL
    SELECT campaign_name, segment, status FROM \`${CE_TABLES.adCopyDrafts}\`
     WHERE run_date = (SELECT MAX(run_date) FROM \`${CE_TABLES.adCopyDrafts}\`)
    UNION ALL
    SELECT campaign_name, segment, status FROM \`${CE_TABLES.audienceAngles}\`
     WHERE run_date = (SELECT MAX(run_date) FROM \`${CE_TABLES.audienceAngles}\`)
  )
`;

export interface SegmentSummary {
  segment: string;
  asset_count: number;
  pending_count: number;
  campaign_count: number;
}

/** Distinct segments across the latest generated content, for the Generation index's quick-nav. */
export async function getSegmentsSummary(): Promise<SafeResult<SegmentSummary>> {
  const result = await safeQuery<Record<string, unknown>>(
    `${COMBINED_ASSETS_CTE}
     SELECT COALESCE(segment, 'Unknown') AS segment,
            COUNT(*) AS asset_count,
            COUNTIF(status IN ('proposed', 'needs_review')) AS pending_count,
            COUNT(DISTINCT campaign_name) AS campaign_count
       FROM combined
      GROUP BY segment
      ORDER BY asset_count DESC`,
  );
  return {
    rows: result.rows.map((r) => ({
      segment: String(r.segment),
      asset_count: num(r.asset_count) ?? 0,
      pending_count: num(r.pending_count) ?? 0,
      campaign_count: num(r.campaign_count) ?? 0,
    })),
    error: result.error,
  };
}

/** Campaigns within one segment, for /generation/[segment]. */
export async function getCampaignsInSegment(
  segment: string,
): Promise<SafeResult<CampaignAssetSummary>> {
  const result = await safeQuery<Record<string, unknown>>(
    `${COMBINED_ASSETS_CTE}
     SELECT campaign_name, ANY_VALUE(segment) AS segment,
            COUNT(*) AS asset_count,
            COUNTIF(status IN ('proposed', 'needs_review')) AS pending_count
       FROM combined
      WHERE COALESCE(segment, 'Unknown') = @segment
      GROUP BY campaign_name
      ORDER BY campaign_name`,
    { segment },
  );
  return {
    rows: result.rows.map((r) => ({
      campaign_name: String(r.campaign_name ?? ""),
      segment: str(r.segment),
      asset_count: num(r.asset_count) ?? 0,
      pending_count: num(r.pending_count) ?? 0,
    })),
    error: result.error,
  };
}

// ---------------------------------------------------------------------------
// Review queue pressure (Home vital-sign tile)
// ---------------------------------------------------------------------------

export interface ReviewPressure {
  total: number;
  keywordCandidates: number;
  adCopyDrafts: number;
  audienceAngles: number;
  /** Tables that could not be reached — surfaced so the tile can say "partial" instead of silently under-counting. */
  unavailable: string[];
}

const PENDING_STATUSES = "('proposed', 'needs_review')";

export async function getReviewQueuePressure(): Promise<ReviewPressure> {
  const [kw, cd, aa] = await Promise.all([
    safeQuery<{ n: number | string }>(
      `SELECT COUNT(*) AS n FROM \`${CE_TABLES.keywordCandidates}\`
        WHERE run_date = (SELECT MAX(run_date) FROM \`${CE_TABLES.keywordCandidates}\`)
          AND status IN ${PENDING_STATUSES}`,
    ),
    safeQuery<{ n: number | string }>(
      `SELECT COUNT(*) AS n FROM \`${CE_TABLES.adCopyDrafts}\`
        WHERE run_date = (SELECT MAX(run_date) FROM \`${CE_TABLES.adCopyDrafts}\`)
          AND status IN ${PENDING_STATUSES}`,
    ),
    safeQuery<{ n: number | string }>(
      `SELECT COUNT(*) AS n FROM \`${CE_TABLES.audienceAngles}\`
        WHERE run_date = (SELECT MAX(run_date) FROM \`${CE_TABLES.audienceAngles}\`)
          AND status IN ${PENDING_STATUSES}`,
    ),
  ]);

  const kwCount = num(kw.rows[0]?.n) ?? 0;
  const cdCount = num(cd.rows[0]?.n) ?? 0;
  const aaCount = num(aa.rows[0]?.n) ?? 0;
  const unavailable: string[] = [];
  if (kw.error) unavailable.push("keyword_candidates");
  if (cd.error) unavailable.push("ad_copy_drafts");
  if (aa.error) unavailable.push("audience_angles");

  return {
    total: kwCount + cdCount + aaCount,
    keywordCandidates: kwCount,
    adCopyDrafts: cdCount,
    audienceAngles: aaCount,
    unavailable,
  };
}

// ---------------------------------------------------------------------------
// Value ledger + lead quality (Home vital-signs)
// ---------------------------------------------------------------------------

export interface ValueLedgerHeadline {
  latestRunDate: string | null;
  flaggedInrLatestRun: number | null;
  realizedInrToDate: number | null;
}

export async function getValueLedgerHeadline(): Promise<
  SafeResult<ValueLedgerHeadline>
> {
  const result = await safeQuery<Record<string, unknown>>(
    `SELECT
        (SELECT MAX(run_date) FROM \`${CE_TABLES.valueLedger}\`) AS latest_run_date,
        (SELECT SUM(expected_impact_inr) FROM \`${CE_TABLES.valueLedger}\`
          WHERE run_date = (SELECT MAX(run_date) FROM \`${CE_TABLES.valueLedger}\`)) AS flagged_latest_run,
        (SELECT SUM(realized_impact_inr) FROM \`${CE_TABLES.valueLedger}\`
          WHERE status = 'realized') AS realized_to_date`,
  );
  if (result.error || result.rows.length === 0) {
    return { rows: [], error: result.error };
  }
  const r = result.rows[0];
  return {
    rows: [
      {
        latestRunDate: dateStr(r.latest_run_date),
        flaggedInrLatestRun: num(r.flagged_latest_run),
        realizedInrToDate: num(r.realized_to_date),
      },
    ],
    error: null,
  };
}

export async function getRecentValueLedgerEntries(
  limit = 20,
): Promise<SafeResult<ValueLedgerRow>> {
  const result = await safeQuery<Record<string, unknown>>(
    `SELECT run_date, entry_id, action_type, description, campaign_name,
            segment, expected_impact_inr, realized_impact_inr, realized_at, status
       FROM \`${CE_TABLES.valueLedger}\`
      ORDER BY run_date DESC, entry_id DESC
      LIMIT @limit`,
    { limit: clampLimit(limit) },
  );
  return { rows: result.rows.map(mapValueLedger), error: result.error };
}

/** Per-campaign lead-quality rows for the latest scored period (CPQL signal). */
export async function getLeadQualityScores(
  limit = 200,
): Promise<SafeResult<LeadQualityScoreRow>> {
  const result = await safeQuery<Record<string, unknown>>(
    `SELECT period_start, period_end, run_date, campaign_name, segment, leads,
            quality_leads, junk_leads, quality_ratio, spend_inr, cpl_inr, cpql_inr
       FROM \`${CE_TABLES.leadQualityScores}\`
      WHERE period_end = (SELECT MAX(period_end) FROM \`${CE_TABLES.leadQualityScores}\`)
      ORDER BY spend_inr DESC NULLS LAST
      LIMIT @limit`,
    { limit: clampLimit(limit) },
  );
  return { rows: result.rows.map(mapLeadQualityScore), error: result.error };
}

export interface LeadQualityHeadline {
  periodEnd: string | null;
  totalLeads: number | null;
  totalQualityLeads: number | null;
  blendedCpqlInr: number | null;
}

export async function getLeadQualityHeadline(): Promise<
  SafeResult<LeadQualityHeadline>
> {
  const result = await safeQuery<Record<string, unknown>>(
    `SELECT
        MAX(period_end) AS period_end,
        SUM(leads) AS total_leads,
        SUM(quality_leads) AS total_quality_leads,
        SAFE_DIVIDE(SUM(spend_inr), NULLIF(SUM(quality_leads), 0)) AS blended_cpql
       FROM \`${CE_TABLES.leadQualityScores}\`
      WHERE period_end = (SELECT MAX(period_end) FROM \`${CE_TABLES.leadQualityScores}\`)`,
  );
  if (result.error || result.rows.length === 0) {
    return { rows: [], error: result.error };
  }
  const r = result.rows[0];
  return {
    rows: [
      {
        periodEnd: dateStr(r.period_end),
        totalLeads: num(r.total_leads),
        totalQualityLeads: num(r.total_quality_leads),
        blendedCpqlInr: num(r.blended_cpql),
      },
    ],
    error: null,
  };
}

// ---------------------------------------------------------------------------
// Pipeline freshness (Home vital-sign: N healthy / N stale / N failed)
// ---------------------------------------------------------------------------

/** table label -> (fully-qualified table, partition column). Mirrors generation/schemas.py PARTITION_FIELDS. */
const FRESHNESS_SOURCES: Array<[label: string, table: string, col: string]> = [
  ["campaign_registry", CE_TABLES.campaignRegistry, "snapshot_date"],
  ["keyword_candidates", CE_TABLES.keywordCandidates, "run_date"],
  ["keyword_rejects", CE_TABLES.keywordRejects, "run_date"],
  ["ad_copy_drafts", CE_TABLES.adCopyDrafts, "run_date"],
  ["audience_angles", CE_TABLES.audienceAngles, "run_date"],
  ["creative_briefs", CE_TABLES.creativeBriefs, "run_date"],
  ["generation_runs", CE_TABLES.generationRuns, "run_date"],
  ["kw_search_term_perf", CE_TABLES.kwSearchTermPerf, "date"],
  ["lead_quality_scores", CE_TABLES.leadQualityScores, "run_date"],
  ["value_ledger", CE_TABLES.valueLedger, "run_date"],
];

/**
 * MAX(partition column) per Generation-dept table. Each table is queried
 * independently (not one UNION ALL) so one missing table doesn't blank out
 * freshness for the other eleven.
 */
export async function getCeFreshness(): Promise<CeFreshnessRow[]> {
  const results = await Promise.all(
    FRESHNESS_SOURCES.map(async ([label, table, col]) => {
      const r = await safeQuery<{ latest: unknown }>(
        `SELECT MAX(${col}) AS latest FROM \`${table}\``,
      );
      return {
        table: label,
        latest_date: r.error ? null : dateStr(r.rows[0]?.latest),
      };
    }),
  );
  return results;
}

/** Latest generation_runs.status, used to fold a hard pipeline failure into the Home health tile. */
export async function getLatestGenerationRunStatus(): Promise<string | null> {
  const result = await safeQuery<{ status: unknown }>(
    `SELECT status FROM \`${CE_TABLES.generationRuns}\`
      WHERE run_date = (SELECT MAX(run_date) FROM \`${CE_TABLES.generationRuns}\`)
      ORDER BY run_id DESC
      LIMIT 1`,
  );
  if (result.error || result.rows.length === 0) return null;
  return str(result.rows[0].status);
}
