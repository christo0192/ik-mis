import "server-only";

import { CE_DATASET, PROJECT_ID, safeQuery, type SafeResult } from "@/lib/bq";

/**
 * SERVER-ONLY read layer for the anomaly-engine's own `pmis_*` tables
 * (same project + dataset as the `ce_*` tables, `SIP_pattern_rag`, but a
 * separate schema family written by `/mnt/c/mcp-servers/anomaly-engine`,
 * not by this repo's Generation pipeline). Confirmed live against
 * `bq_writer.py`'s `SCHEMAS` dict (pmis_daily_segment_facts,
 * pmis_anomaly_facts) — see docs/dashboard-vision.md §3.
 *
 * Read-only. This dashboard never writes to any pmis_ table.
 */

const PMIS_SEGMENT_FACTS = `${PROJECT_ID}.${CE_DATASET}.pmis_daily_segment_facts`;
const PMIS_ANOMALY_FACTS = `${PROJECT_ID}.${CE_DATASET}.pmis_anomaly_facts`;

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

export interface PmisSegmentDay {
  date: string;
  segment: string;
  spend: number | null;
  iql: number | null;
  cpiql: number | null;
  wa: number | null;
  junk: number | null;
  sales: number | null;
  revenue: number | null;
}

/** Trailing `days` of per-(date, segment) facts, for the segment trend chart. */
export async function getPmisSegmentTrend(days = 30): Promise<SafeResult<PmisSegmentDay>> {
  const clampedDays = Math.min(Math.max(Math.trunc(days) || 1, 1), 180);
  const result = await safeQuery<Record<string, unknown>>(
    `SELECT date, segment, spend, iql, cpiql, wa, junk, sales, revenue
       FROM \`${PMIS_SEGMENT_FACTS}\`
      WHERE date >= DATE_SUB((SELECT MAX(date) FROM \`${PMIS_SEGMENT_FACTS}\`), INTERVAL @days DAY)
      ORDER BY date, segment`,
    { days: clampedDays },
  );
  return {
    rows: result.rows.map((r) => ({
      date: dateStr(r.date) ?? "",
      segment: String(r.segment ?? "Unknown"),
      spend: num(r.spend),
      iql: num(r.iql),
      cpiql: num(r.cpiql),
      wa: num(r.wa),
      junk: num(r.junk),
      sales: num(r.sales),
      revenue: num(r.revenue),
    })),
    error: result.error,
  };
}

export interface PmisAnomalyRow {
  run_date: string | null;
  scope: string | null;
  entity: string | null;
  metric: string | null;
  yesterday: number | null;
  baseline_mean: number | null;
  z: number | null;
  pct_change: number | null;
  direction: string | null;
  /** LLM-judged severity when the judgment step has run; falls back to the rule-based candidate severity when not yet judged (never fabricated — see the honest "(unjudged)" flag below). */
  severity: string | null;
  severityIsJudged: boolean;
}

/** The most recent anomaly-detection run's candidates, most severe first. */
export async function getPmisAnomalies(limit = 50): Promise<SafeResult<PmisAnomalyRow>> {
  const clampedLimit = Math.min(Math.max(Math.trunc(limit) || 1, 1), 200);
  const result = await safeQuery<Record<string, unknown>>(
    `SELECT run_date, scope, entity, metric, yesterday, baseline_mean, z,
            pct_change, direction, candidate_severity, judged_severity
       FROM \`${PMIS_ANOMALY_FACTS}\`
      WHERE run_date = (SELECT MAX(run_date) FROM \`${PMIS_ANOMALY_FACTS}\`)
      ORDER BY ABS(COALESCE(pct_change, 0)) DESC
      LIMIT @limit`,
    { limit: clampedLimit },
  );
  return {
    rows: result.rows.map((r) => {
      const judged = str(r.judged_severity);
      return {
        run_date: dateStr(r.run_date),
        scope: str(r.scope),
        entity: str(r.entity),
        metric: str(r.metric),
        yesterday: num(r.yesterday),
        baseline_mean: num(r.baseline_mean),
        z: num(r.z),
        pct_change: num(r.pct_change),
        direction: str(r.direction),
        severity: judged ?? str(r.candidate_severity),
        severityIsJudged: judged !== null,
      };
    }),
    error: result.error,
  };
}

/** MAX(date) — feeds the Performance department's node/bot health on the Home system graph. */
export async function getPmisFreshness(): Promise<string | null> {
  const result = await safeQuery<{ latest: unknown }>(
    `SELECT MAX(date) AS latest FROM \`${PMIS_SEGMENT_FACTS}\``,
  );
  return result.error ? null : dateStr(result.rows[0]?.latest);
}
