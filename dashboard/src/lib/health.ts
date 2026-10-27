/**
 * Shared health model — client-safe (no server-only import), used by the
 * Home page tiles, the system graph, and the caretaker bots so all three
 * agree on what "healthy / stale / failed / unknown" means from the same
 * MAX(date) freshness numbers (docs/dashboard-vision.md §2, §2b).
 *
 * Deliberately import-free and limited to erasable TypeScript (no enum /
 * namespace / parameter properties) so `npm test` can run health.test.ts
 * straight through Node's native type stripping — no test framework, no
 * `@/` path resolution.
 */

export type Health = "healthy" | "stale" | "failed" | "unknown";

/** How often a table is expected to be written. "on demand" = no scheduled writer; never judged stale. */
export type Cadence = "daily" | "weekly" | "monthly" | "on demand";

/**
 * Per-table freshness SLA: a table is "stale" once its MAX(date) is more than
 * `slaDays` old. Replaces the single global STALE_AFTER_DAYS = 8 (2026-10-27),
 * which judged daily, weekly and monthly tables alike — a daily table could
 * sit a week behind and still read "fresh", while every monthly Intelligence
 * table read "stale" for most of each month regardless of whether its job ran.
 *
 * Age is measured from 00:00 UTC of the stored DATE (= 05:30 IST) in
 * fractional days; the daily pipeline runs at 05:30 UTC
 * (cloudflare-trigger/wrangler.toml), so a healthy run_date table peaks at
 * ~1.23 days old just before the next run and a data-date table at ~2.23.
 *
 *   - Daily, run_date-stamped (written with the IST run day): 3. One missed
 *     run is tolerated; the alarm fires ~18h after a second consecutive miss.
 *     keyword_candidates / ad_copy_drafts / audience_angles are only written
 *     when non-empty (gate_keywords.py, generate_assets.py), so one quiet day
 *     also fits inside this; raise to 4 if that proves noisy.
 *   - Daily, data-date-stamped: 4 — the same tolerance plus the built-in
 *     1-day lag. kw_search_term_perf.date = IST yesterday (collect_kw_perf.py);
 *     value_ledger.run_date = MAX(kw_search_term_perf.date), not the run day
 *     (export_negatives.py); pmis_daily_segment_facts.date is the anomaly
 *     engine's data day.
 *   - keyword_rejects: 7 — written only when the gate actually rejects
 *     something (gate_keywords.py), so gaps of several days are legitimate.
 *   - campaign_registry: 9 (weekly + 2 days slippage) — rebuilt weekly on
 *     Monday IST, plus a catch-up whenever the latest snapshot is older than
 *     7 days.
 *   - social_trends / job_market_demand: 35 — monthly Intelligence job, up to
 *     31 days between runs plus slippage.
 *   - creative_briefs: null — no pipeline writer today (only schemas.py and
 *     the smoke test reference it), so it is never marked stale.
 */
export const FRESHNESS_SLA = {
  // Generation-dept ce_* tables (ce-queries.ts FRESHNESS_SOURCES)
  campaign_registry: { cadence: "weekly", slaDays: 9 },
  keyword_candidates: { cadence: "daily", slaDays: 3 },
  keyword_rejects: { cadence: "daily", slaDays: 7 },
  ad_copy_drafts: { cadence: "daily", slaDays: 3 },
  audience_angles: { cadence: "daily", slaDays: 3 },
  creative_briefs: { cadence: "on demand", slaDays: null },
  generation_runs: { cadence: "daily", slaDays: 3 },
  kw_search_term_perf: { cadence: "daily", slaDays: 4 },
  lead_quality_scores: { cadence: "daily", slaDays: 3 },
  value_ledger: { cadence: "daily", slaDays: 4 },
  // Anomaly-engine output (pmis-queries.ts getPmisFreshness)
  pmis_daily_segment_facts: { cadence: "daily", slaDays: 4 },
  // Market_Research Intelligence tables (queries.ts getMarketResearchFreshness)
  social_trends: { cadence: "monthly", slaDays: 35 },
  job_market_demand: { cadence: "monthly", slaDays: 35 },
} as const satisfies Record<string, { cadence: Cadence; slaDays: number | null }>;

/**
 * Every table the dashboard judges for freshness. Callers must name one —
 * there is no default SLA, so a new table can't silently inherit the wrong
 * cadence and a typo is a compile error.
 */
export type FreshnessTable = keyof typeof FRESHNESS_SLA;

const DAY_MS = 86_400_000;

/** The table's staleness threshold in days, or null when it has no SLA (never stale). */
export function slaFor(table: FreshnessTable): number | null {
  return FRESHNESS_SLA[table].slaDays;
}

/** Fractional days since `latestDate` (a YYYY-MM-DD DATE, read as 00:00 UTC). null when missing or unparseable. */
export function ageDays(latestDate: string | null, now: number = Date.now()): number | null {
  if (!latestDate) return null;
  const then = Date.parse(latestDate);
  if (Number.isNaN(then)) return null;
  return (now - then) / DAY_MS;
}

/**
 * healthy / stale / unknown for one table against its own SLA. "failed" is
 * never produced here — it comes from an explicit run status (see
 * generation_runs in graph.ts and page.tsx). `now` is injectable so callers
 * can judge every table against one clock, and tests can pin day boundaries.
 */
export function healthOf(latestDate: string | null, table: FreshnessTable, now: number = Date.now()): Health {
  const age = ageDays(latestDate, now);
  if (age === null) return "unknown";
  const sla = slaFor(table);
  if (sla === null) return "healthy";
  return age <= sla ? "healthy" : "stale";
}

/** Short, always-visible cadence line for a table tile, e.g. "daily · stale after 3d" or "on demand". */
export function cadenceLabel(table: FreshnessTable): string {
  const { cadence, slaDays } = FRESHNESS_SLA[table];
  return slaDays === null ? cadence : `${cadence} · stale after ${slaDays}d`;
}

/**
 * Tooltip text for a table: when it last landed, what cadence it is held to,
 * and — only once it is past its SLA — how old it is, e.g.
 * "last updated 2026-08-18 · expected monthly (stale after 35d) · 70d old".
 */
export function freshnessDetail(latestDate: string | null, table: FreshnessTable, now: number = Date.now()): string {
  const age = ageDays(latestDate, now);
  if (age === null) return "no data yet";
  const { cadence, slaDays } = FRESHNESS_SLA[table];
  const expected = `expected ${cadence}${slaDays !== null ? ` (stale after ${slaDays}d)` : ""}`;
  const overdue = healthOf(latestDate, table, now) === "stale" ? ` · ${Math.floor(age)}d old` : "";
  return `last updated ${latestDate} · ${expected}${overdue}`;
}

/**
 * Judges a list of MAX(date) rows against ONE clock reading, so a tile's dot
 * and its tooltip can never disagree across a boundary. Keeps any extra row
 * fields. Used by the Home "Table Freshness" strip; reading the clock here
 * (not in the component body) also keeps the server component free of a
 * direct Date.now() call, which react-hooks/purity rejects.
 */
export function judgeFreshness<T extends { table: FreshnessTable; latest_date: string | null }>(
  rows: readonly T[],
  now: number = Date.now(),
): Array<T & { health: Health; detail: string }> {
  return rows.map((row) => ({
    ...row,
    health: healthOf(row.latest_date, row.table, now),
    detail: freshnessDetail(row.latest_date, row.table, now),
  }));
}

/**
 * generation_runs statuses, worst first. generate_assets writes one row per
 * path (keyword / copy / angle) per run, all sharing one run_id
 * (generation/schemas.py GenerationRunRow; terminal statuses only). "running"
 * is never written today; it is kept, below the terminal failures, so the
 * caretaker bot's "busy" check in graph.ts still works if a writer adds it.
 */
const RUN_STATUS_ORDER = ["failed", "partial", "running", "success"] as const;

/**
 * One status for a set of generation_runs rows: the worst one present, so any
 * failed path marks the whole run (or run day) failed. The result does not
 * depend on row order, so two queries over the same rows always agree.
 * Matching ignores case and surrounding whitespace. null when no row carries a
 * recognised status (no rows, all NULL, or only unexpected values).
 */
export function rollUpRunStatus(statuses: readonly (string | null | undefined)[]): string | null {
  const present = new Set(statuses.map((s) => (s ?? "").trim().toLowerCase()));
  return RUN_STATUS_ORDER.find((s) => present.has(s)) ?? null;
}

/** Severity ranking used when rolling several child healths up into one parent (dept) health. */
const SEVERITY: Record<Health, number> = {
  failed: 3,
  stale: 2,
  unknown: 1,
  healthy: 0,
};

/** Worst-of across a set of child healths. Empty input reads as "unknown" (nothing to roll up). */
export function worstHealth(healths: Health[]): Health {
  if (healths.length === 0) return "unknown";
  return healths.reduce((worst, h) => (SEVERITY[h] > SEVERITY[worst] ? h : worst), "healthy" as Health);
}

export const HEALTH_STYLE: Record<Health, { dot: string; text: string; label: string; hex: string }> = {
  healthy: { dot: "bg-success", text: "text-success", label: "fresh", hex: "#34d399" },
  stale: { dot: "bg-warning", text: "text-warning", label: "stale", hex: "#fbbf24" },
  failed: { dot: "bg-danger", text: "text-danger", label: "failed", hex: "#fb7185" },
  unknown: { dot: "bg-muted/40", text: "text-muted", label: "no data yet", hex: "#8b93a8" },
};
