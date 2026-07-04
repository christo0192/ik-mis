import "server-only";

import { DATASET, PROJECT_ID, TABLES, query, safeQuery } from "@/lib/bq";

/**
 * SERVER-ONLY query layer.
 *
 * Every function returns the latest-snapshot rows (the partition with the
 * maximum `run_date`) unless otherwise noted. Aggregates use BigQuery UNNEST
 * for REPEATED columns. No user-supplied input is interpolated anywhere here,
 * so there is nothing to parameterize yet.
 *
 * NOTE on numerics: BigQuery returns INT64 columns as JS strings (and DATE as
 * `{ value: "YYYY-MM-DD" }` or a plain string depending on driver). Helpers
 * below normalize these so UI/chart components receive plain numbers/strings.
 */

// ---------------------------------------------------------------------------
// Normalization helpers
// ---------------------------------------------------------------------------

/** Coerce a BigQuery numeric (number | string | null) to number | null. */
function num(v: unknown): number | null {
  if (v === null || v === undefined || v === "") return null;
  const n = typeof v === "number" ? v : Number(v);
  return Number.isFinite(n) ? n : null;
}

/** BigQuery DATE/TIMESTAMP can arrive as `{ value }` or a string. Normalize to string|null. */
function dateStr(v: unknown): string | null {
  if (v === null || v === undefined) return null;
  if (typeof v === "string") return v;
  if (typeof v === "object" && v !== null && "value" in v) {
    const val = (v as { value: unknown }).value;
    return typeof val === "string" ? val : null;
  }
  return String(v);
}

/** Ensure REPEATED columns are always arrays of strings (never null). */
function arr(v: unknown): string[] {
  if (Array.isArray(v)) return v.map((x) => String(x)).filter((x) => x.length > 0);
  return [];
}

// ---------------------------------------------------------------------------
// Shared row types (post-normalization)
// ---------------------------------------------------------------------------

export interface ExecPriority {
  run_date: string | null;
  rank: number | null;
  direction: string | null;
  sections: string[];
  urgency: string | null;
  gap_vs_competitors: string | null;
  actionable_insight: string | null;
  effort_estimate: string | null;
  revenue_impact: string | null;
  supporting_data: string | null;
}

export interface JobRow {
  title: string | null;
  normalized_role: string | null;
  role_family: string | null;
  company: string | null;
  company_tier: string | null;
  location: string | null;
  seniority_level: string | null;
  employment_type: string | null;
  applicants_count: number | null;
  salary_min_inr: number | null;
  salary_max_inr: number | null;
  posted_date: string | null;
  is_emerging: boolean;
  skills: string[];
  lenses: string[];
  reference_url: string | null;
}

export interface SocialRow {
  source: string | null;
  topic: string | null;
  sentiment: number | null;
  engagement: string | null;
  engagement_value: number | null;
  category: string | null;
  reference_url: string | null;
}

export interface BlogRow {
  publication: string | null;
  title: string | null;
  recommended_skill: string | null;
  audience: string | null;
  urgency: string | null;
  curriculum_match: string | null;
  published_date: string | null;
  summary: string | null;
  reference_url: string | null;
}

export interface CompetitorRow {
  competitor: string | null;
  program: string | null;
  price_inr: number | null;
  duration_months: number | null;
  curriculum_overview: string | null;
  key_tools: string[];
  hands_on_projects: string | null;
  certifications: string | null;
  mentor_quality: string | null;
  curriculum_gaps_vs_ik: string[];
  ik_advantages: string[];
  ik_gaps: string[];
  ad_intensity: number | null;
  reference_url: string | null;
}

export interface CountRow {
  key: string;
  count: number;
}

export interface RoleAggRow {
  normalized_role: string;
  count: number;
  emerging_count: number;
}

export interface SocialSourceAggRow {
  source: string;
  count: number;
  avg_sentiment: number | null;
}

export interface JobStats {
  total: number;
  emerging: number;
  in_window: number;
  role_count: number;
}

export interface TableStat {
  table_id: string;
  label: string;
  row_count: number;
  latest_run_date: string | null;
}

// ---------------------------------------------------------------------------
// Executive summary
// ---------------------------------------------------------------------------

export async function getExecPriorities(): Promise<ExecPriority[]> {
  const rows = await query<Record<string, unknown>>(
    `SELECT run_date, rank, direction, sections, urgency, gap_vs_competitors,
            actionable_insight, effort_estimate, revenue_impact, supporting_data
       FROM \`${TABLES.exec}\`
      WHERE run_date = (SELECT MAX(run_date) FROM \`${TABLES.exec}\`)
      ORDER BY rank`,
  );
  return rows.map((r) => ({
    run_date: dateStr(r.run_date),
    rank: num(r.rank),
    direction: (r.direction as string) ?? null,
    sections: arr(r.sections),
    urgency: (r.urgency as string) ?? null,
    gap_vs_competitors: (r.gap_vs_competitors as string) ?? null,
    actionable_insight: (r.actionable_insight as string) ?? null,
    effort_estimate: (r.effort_estimate as string) ?? null,
    revenue_impact: (r.revenue_impact as string) ?? null,
    supporting_data: (r.supporting_data as string) ?? null,
  }));
}

// ---------------------------------------------------------------------------
// Jobs
// ---------------------------------------------------------------------------

export async function getJobs(): Promise<JobRow[]> {
  const rows = await query<Record<string, unknown>>(
    `SELECT title, normalized_role, role_family, company, company_tier, location,
            seniority_level, employment_type, applicants_count, salary_min_inr,
            salary_max_inr, posted_date, is_emerging, skills, lenses, reference_url
       FROM \`${TABLES.jobs}\`
      WHERE run_date = (SELECT MAX(run_date) FROM \`${TABLES.jobs}\`)
      ORDER BY applicants_count DESC NULLS LAST, posted_date DESC`,
  );
  return rows.map((r) => ({
    title: (r.title as string) ?? null,
    normalized_role: (r.normalized_role as string) ?? null,
    role_family: (r.role_family as string) ?? null,
    company: (r.company as string) ?? null,
    company_tier: (r.company_tier as string) ?? null,
    location: (r.location as string) ?? null,
    seniority_level: (r.seniority_level as string) ?? null,
    employment_type: (r.employment_type as string) ?? null,
    applicants_count: num(r.applicants_count),
    salary_min_inr: num(r.salary_min_inr),
    salary_max_inr: num(r.salary_max_inr),
    posted_date: dateStr(r.posted_date),
    is_emerging: Boolean(r.is_emerging),
    skills: arr(r.skills),
    lenses: arr(r.lenses),
    reference_url: (r.reference_url as string) ?? null,
  }));
}

/** normalized_role -> total count + emerging count, descending by count. */
export async function getJobsByRole(): Promise<RoleAggRow[]> {
  const rows = await query<Record<string, unknown>>(
    `SELECT
        COALESCE(normalized_role, 'Unspecified') AS normalized_role,
        COUNT(*) AS count,
        COUNTIF(is_emerging) AS emerging_count
       FROM \`${TABLES.jobs}\`
      WHERE run_date = (SELECT MAX(run_date) FROM \`${TABLES.jobs}\`)
      GROUP BY normalized_role
      ORDER BY count DESC`,
  );
  return rows.map((r) => ({
    normalized_role: String(r.normalized_role),
    count: num(r.count) ?? 0,
    emerging_count: num(r.emerging_count) ?? 0,
  }));
}

/** role_family -> count. */
export async function getJobsByFamily(): Promise<CountRow[]> {
  const rows = await query<Record<string, unknown>>(
    `SELECT COALESCE(role_family, 'Unspecified') AS key, COUNT(*) AS count
       FROM \`${TABLES.jobs}\`
      WHERE run_date = (SELECT MAX(run_date) FROM \`${TABLES.jobs}\`)
      GROUP BY key
      ORDER BY count DESC`,
  );
  return rows.map((r) => ({ key: String(r.key), count: num(r.count) ?? 0 }));
}

/** company_tier -> count. */
export async function getJobsByTier(): Promise<CountRow[]> {
  const rows = await query<Record<string, unknown>>(
    `SELECT COALESCE(company_tier, 'Unspecified') AS key, COUNT(*) AS count
       FROM \`${TABLES.jobs}\`
      WHERE run_date = (SELECT MAX(run_date) FROM \`${TABLES.jobs}\`)
      GROUP BY key
      ORDER BY count DESC`,
  );
  return rows.map((r) => ({ key: String(r.key), count: num(r.count) ?? 0 }));
}

/**
 * Headline job-market KPIs for the latest snapshot: total postings, emerging
 * count, in-window count, and distinct normalized roles. One cheap aggregate.
 */
export async function getJobStats(): Promise<JobStats> {
  const rows = await query<Record<string, unknown>>(
    `SELECT
        COUNT(*) AS total,
        COUNTIF(is_emerging) AS emerging,
        COUNTIF(in_window) AS in_window,
        COUNT(DISTINCT normalized_role) AS role_count
       FROM \`${TABLES.jobs}\`
      WHERE run_date = (SELECT MAX(run_date) FROM \`${TABLES.jobs}\`)`,
  );
  const r = rows[0] ?? {};
  return {
    total: num(r.total) ?? 0,
    emerging: num(r.emerging) ?? 0,
    in_window: num(r.in_window) ?? 0,
    role_count: num(r.role_count) ?? 0,
  };
}

/** Top skills via UNNEST(skills) -> count, limited. */
export async function getTopSkills(limit = 15): Promise<CountRow[]> {
  const rows = await query<Record<string, unknown>>(
    `SELECT skill AS key, COUNT(*) AS count
       FROM \`${TABLES.jobs}\`, UNNEST(skills) AS skill
      WHERE run_date = (SELECT MAX(run_date) FROM \`${TABLES.jobs}\`)
        AND skill IS NOT NULL AND skill != ''
      GROUP BY key
      ORDER BY count DESC
      LIMIT ${Number(limit)}`,
  );
  return rows.map((r) => ({ key: String(r.key), count: num(r.count) ?? 0 }));
}

// ---------------------------------------------------------------------------
// Social
// ---------------------------------------------------------------------------

export async function getSocial(): Promise<SocialRow[]> {
  const rows = await query<Record<string, unknown>>(
    `SELECT source, topic, sentiment, engagement, engagement_value, category, reference_url
       FROM \`${TABLES.social}\`
      WHERE run_date = (SELECT MAX(run_date) FROM \`${TABLES.social}\`)
      ORDER BY engagement_value DESC NULLS LAST`,
  );
  return rows.map((r) => ({
    source: (r.source as string) ?? null,
    topic: (r.topic as string) ?? null,
    sentiment: num(r.sentiment),
    engagement: (r.engagement as string) ?? null,
    engagement_value: num(r.engagement_value),
    category: (r.category as string) ?? null,
    reference_url: (r.reference_url as string) ?? null,
  }));
}

/** source -> count + avg sentiment. */
export async function getSocialBySource(): Promise<SocialSourceAggRow[]> {
  const rows = await query<Record<string, unknown>>(
    `SELECT COALESCE(source, 'Unknown') AS source,
            COUNT(*) AS count,
            ROUND(AVG(sentiment), 2) AS avg_sentiment
       FROM \`${TABLES.social}\`
      WHERE run_date = (SELECT MAX(run_date) FROM \`${TABLES.social}\`)
      GROUP BY source
      ORDER BY count DESC`,
  );
  return rows.map((r) => ({
    source: String(r.source),
    count: num(r.count) ?? 0,
    avg_sentiment: num(r.avg_sentiment),
  }));
}

/** category -> count. */
export async function getSocialByCategory(): Promise<CountRow[]> {
  const rows = await query<Record<string, unknown>>(
    `SELECT COALESCE(category, 'Uncategorized') AS key, COUNT(*) AS count
       FROM \`${TABLES.social}\`
      WHERE run_date = (SELECT MAX(run_date) FROM \`${TABLES.social}\`)
      GROUP BY key
      ORDER BY count DESC`,
  );
  return rows.map((r) => ({ key: String(r.key), count: num(r.count) ?? 0 }));
}

// ---------------------------------------------------------------------------
// Blog
// ---------------------------------------------------------------------------

export async function getBlog(): Promise<BlogRow[]> {
  const rows = await query<Record<string, unknown>>(
    `SELECT publication, title, recommended_skill, audience, urgency,
            curriculum_match, published_date, summary, reference_url
       FROM \`${TABLES.blog}\`
      WHERE run_date = (SELECT MAX(run_date) FROM \`${TABLES.blog}\`)
      ORDER BY published_date DESC NULLS LAST`,
  );
  return rows.map((r) => ({
    publication: (r.publication as string) ?? null,
    title: (r.title as string) ?? null,
    recommended_skill: (r.recommended_skill as string) ?? null,
    audience: (r.audience as string) ?? null,
    urgency: (r.urgency as string) ?? null,
    curriculum_match: (r.curriculum_match as string) ?? null,
    published_date: dateStr(r.published_date),
    summary: (r.summary as string) ?? null,
    reference_url: (r.reference_url as string) ?? null,
  }));
}

/** urgency -> count. */
export async function getBlogByUrgency(): Promise<CountRow[]> {
  const rows = await query<Record<string, unknown>>(
    `SELECT COALESCE(urgency, 'Unspecified') AS key, COUNT(*) AS count
       FROM \`${TABLES.blog}\`
      WHERE run_date = (SELECT MAX(run_date) FROM \`${TABLES.blog}\`)
      GROUP BY key
      ORDER BY count DESC`,
  );
  return rows.map((r) => ({ key: String(r.key), count: num(r.count) ?? 0 }));
}

/** curriculum_match -> count. */
export async function getBlogByCurriculumMatch(): Promise<CountRow[]> {
  const rows = await query<Record<string, unknown>>(
    `SELECT COALESCE(curriculum_match, 'Unspecified') AS key, COUNT(*) AS count
       FROM \`${TABLES.blog}\`
      WHERE run_date = (SELECT MAX(run_date) FROM \`${TABLES.blog}\`)
      GROUP BY key
      ORDER BY count DESC`,
  );
  return rows.map((r) => ({ key: String(r.key), count: num(r.count) ?? 0 }));
}

// ---------------------------------------------------------------------------
// Competitor
// ---------------------------------------------------------------------------

export async function getCompetitors(): Promise<CompetitorRow[]> {
  const rows = await query<Record<string, unknown>>(
    `SELECT competitor, program, price_inr, duration_months, curriculum_overview,
            key_tools, hands_on_projects, certifications, mentor_quality,
            curriculum_gaps_vs_ik, ik_advantages, ik_gaps, ad_intensity, reference_url
       FROM \`${TABLES.competitor}\`
      WHERE run_date = (SELECT MAX(run_date) FROM \`${TABLES.competitor}\`)
      ORDER BY ad_intensity DESC NULLS LAST, competitor`,
  );
  return rows.map((r) => ({
    competitor: (r.competitor as string) ?? null,
    program: (r.program as string) ?? null,
    price_inr: num(r.price_inr),
    duration_months: num(r.duration_months),
    curriculum_overview: (r.curriculum_overview as string) ?? null,
    key_tools: arr(r.key_tools),
    hands_on_projects: (r.hands_on_projects as string) ?? null,
    certifications: (r.certifications as string) ?? null,
    mentor_quality: (r.mentor_quality as string) ?? null,
    curriculum_gaps_vs_ik: arr(r.curriculum_gaps_vs_ik),
    ik_advantages: arr(r.ik_advantages),
    ik_gaps: arr(r.ik_gaps),
    ad_intensity: num(r.ad_intensity),
    reference_url: (r.reference_url as string) ?? null,
  }));
}

// ---------------------------------------------------------------------------
// Overview stats
// ---------------------------------------------------------------------------

const TABLE_LABELS: Record<string, string> = {
  job_market_demand: "Job Market",
  social_trends: "Social Trends",
  blog_publications: "Blog Signals",
  competitor_intel: "Competitors",
  executive_summary: "Exec Summary",
};

/**
 * Per-table row count + latest run_date. Row counts come from `__TABLES__`
 * (cheap metadata); latest run_date is a small per-table MAX query.
 */
export async function getOverviewStats(): Promise<TableStat[]> {
  const order = [
    "job_market_demand",
    "social_trends",
    "blog_publications",
    "competitor_intel",
    "executive_summary",
  ];

  const counts = await query<{ table_id: string; row_count: number | string }>(
    `SELECT table_id, row_count
       FROM \`${PROJECT_ID}.${DATASET}.__TABLES__\``,
  );
  const countMap = new Map(counts.map((c) => [c.table_id, num(c.row_count) ?? 0]));

  // One UNION ALL query to fetch every table's latest run_date in a single round-trip.
  const dateRows = await query<{ table_id: string; latest: unknown }>(
    `SELECT 'job_market_demand' AS table_id, MAX(run_date) AS latest FROM \`${TABLES.jobs}\`
     UNION ALL SELECT 'social_trends', MAX(run_date) FROM \`${TABLES.social}\`
     UNION ALL SELECT 'blog_publications', MAX(run_date) FROM \`${TABLES.blog}\`
     UNION ALL SELECT 'competitor_intel', MAX(run_date) FROM \`${TABLES.competitor}\`
     UNION ALL SELECT 'executive_summary', MAX(run_date) FROM \`${TABLES.exec}\``,
  );
  const dateMap = new Map(dateRows.map((d) => [d.table_id, dateStr(d.latest)]));

  return order.map((id) => ({
    table_id: id,
    label: TABLE_LABELS[id] ?? id,
    row_count: countMap.get(id) ?? 0,
    latest_run_date: dateMap.get(id) ?? null,
  }));
}

/** The single most-recent run_date across the job table (used in the header). */
export async function getLatestRunDate(): Promise<string | null> {
  const rows = await query<{ latest: unknown }>(
    `SELECT MAX(run_date) AS latest FROM \`${TABLES.jobs}\``,
  );
  return rows.length ? dateStr(rows[0].latest) : null;
}

/**
 * MAX(run_date) for the two Market_Research tables the system graph's
 * Intelligence-department nodes need (social_trends, job_market_demand).
 * Goes through `safeQuery` (unlike the rest of this file) so a missing table
 * degrades to "unknown" health on the graph rather than crashing the Home
 * page render.
 */
export async function getMarketResearchFreshness(): Promise<
  Array<{ table: "social_trends" | "job_market_demand"; latest_date: string | null }>
> {
  const [social, jobs] = await Promise.all([
    safeQuery<{ latest: unknown }>(`SELECT MAX(run_date) AS latest FROM \`${TABLES.social}\``),
    safeQuery<{ latest: unknown }>(`SELECT MAX(run_date) AS latest FROM \`${TABLES.jobs}\``),
  ]);
  return [
    { table: "social_trends", latest_date: social.error ? null : dateStr(social.rows[0]?.latest) },
    { table: "job_market_demand", latest_date: jobs.error ? null : dateStr(jobs.rows[0]?.latest) },
  ];
}
