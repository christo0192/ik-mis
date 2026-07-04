import "server-only";

import { existsSync } from "node:fs";
import { BigQuery } from "@google-cloud/bigquery";

/**
 * SERVER-ONLY BigQuery data layer.
 *
 * This module MUST NEVER be imported by a client component. The `server-only`
 * import above turns any accidental client import into a build-time error.
 *
 * Credential precedence (server-side only):
 *   1. env `MARKET_RESEARCH_BQ_SA_B64` -> base64-decode -> JSON.parse -> `credentials`
 *   2. else local key file at LOCAL_KEY_FILE via `keyFilename`
 *
 * Never log, print, or echo the SA key contents.
 */

export const PROJECT_ID = "ik-marketing-data";
export const DATASET = "Market_Research";

/**
 * MIS Generation department dataset (the "ce_" tables from
 * generation/schemas.py — campaign_registry, keyword_candidates,
 * ad_copy_drafts, audience_angles, generation_runs, value_ledger, etc.).
 * Same project, same credentials, different BigQuery dataset.
 */
export const CE_DATASET = "SIP_pattern_rag";

/** Local service-account key file used for local development (Windows path + its WSL mount). */
const LOCAL_KEY_FILES = [
  "C:\\Users\\Admin\\.gcp-keys\\market-research-bq.json",
  "/mnt/c/Users/Admin/.gcp-keys/market-research-bq.json",
];

/**
 * Fully-qualified table names: `ik-marketing-data.Market_Research.<table>`.
 */
export const TABLES = {
  jobs: `${PROJECT_ID}.${DATASET}.job_market_demand`,
  social: `${PROJECT_ID}.${DATASET}.social_trends`,
  blog: `${PROJECT_ID}.${DATASET}.blog_publications`,
  competitor: `${PROJECT_ID}.${DATASET}.competitor_intel`,
  exec: `${PROJECT_ID}.${DATASET}.executive_summary`,
} as const;

export type TableKey = keyof typeof TABLES;

/**
 * Fully-qualified Generation-dept table names:
 * `ik-marketing-data.SIP_pattern_rag.ce_<name>`. Mirrors the 12-table
 * REGISTRY in generation/schemas.py. Kept as a plain string map (not a
 * Set/allowlist) — the write-route allowlist that matters for security
 * lives separately in the API route, not here.
 */
export const CE_TABLES = {
  campaignRegistry: `${PROJECT_ID}.${CE_DATASET}.ce_campaign_registry`,
  keywordCandidates: `${PROJECT_ID}.${CE_DATASET}.ce_keyword_candidates`,
  keywordRejects: `${PROJECT_ID}.${CE_DATASET}.ce_keyword_rejects`,
  adCopyDrafts: `${PROJECT_ID}.${CE_DATASET}.ce_ad_copy_drafts`,
  audienceAngles: `${PROJECT_ID}.${CE_DATASET}.ce_audience_angles`,
  creativeBriefs: `${PROJECT_ID}.${CE_DATASET}.ce_creative_briefs`,
  creativeAssets: `${PROJECT_ID}.${CE_DATASET}.ce_creative_assets`,
  assetPerformanceFeedback: `${PROJECT_ID}.${CE_DATASET}.ce_asset_performance_feedback`,
  generationRuns: `${PROJECT_ID}.${CE_DATASET}.ce_generation_runs`,
  kwSearchTermPerf: `${PROJECT_ID}.${CE_DATASET}.ce_kw_search_term_perf`,
  leadQualityScores: `${PROJECT_ID}.${CE_DATASET}.ce_lead_quality_scores`,
  valueLedger: `${PROJECT_ID}.${CE_DATASET}.ce_value_ledger`,
} as const;

export type CeTableKey = keyof typeof CE_TABLES;

/**
 * Builds BigQuery client options based on the credential precedence.
 * Does not expose or log any secret material.
 */
function buildClientOptions(): ConstructorParameters<typeof BigQuery>[0] {
  const base = { projectId: PROJECT_ID } as const;

  const b64 = process.env.MARKET_RESEARCH_BQ_SA_B64;
  if (b64 && b64.trim().length > 0) {
    let credentials: Record<string, unknown>;
    try {
      const json = Buffer.from(b64, "base64").toString("utf8");
      credentials = JSON.parse(json);
    } catch {
      // Do not include the decoded contents in the error — avoid leaking secrets.
      throw new Error(
        "MARKET_RESEARCH_BQ_SA_B64 is set but is not valid base64-encoded JSON.",
      );
    }
    return { ...base, credentials };
  }

  // Explicit local key-file override (e.g. `.env.local`, gitignored) — lets a
  // developer point at any SA key file (e.g. one with different dataset
  // grants) without touching LOCAL_KEY_FILES. Never logged.
  const overridePath = process.env.MARKET_RESEARCH_BQ_KEY_FILE;
  if (overridePath && overridePath.trim().length > 0 && existsSync(overridePath)) {
    return { ...base, keyFilename: overridePath };
  }

  const keyFile = LOCAL_KEY_FILES.find((p) => existsSync(p));
  if (keyFile) {
    return { ...base, keyFilename: keyFile };
  }

  throw new Error(
    "No BigQuery credentials found. Set MARKET_RESEARCH_BQ_SA_B64 (base64 of the " +
      "service-account JSON), MARKET_RESEARCH_BQ_KEY_FILE (a local key-file path), " +
      "or provide the local key file.",
  );
}

/**
 * Singleton BigQuery client, created LAZILY on first use — never at module
 * import time. This matters twice over:
 *   1. `next build` imports route modules to collect page data; a
 *      credential-less build environment must not crash the build.
 *   2. A missing credential at runtime must surface as a per-query error
 *      (caught by `safeQuery` -> honest "no data yet" UI), not a module-load
 *      crash that 500s every page.
 * Cached across hot reloads in development via globalThis.
 */
const globalForBq = globalThis as unknown as { __bqClient?: BigQuery };

export function getBq(): BigQuery {
  if (globalForBq.__bqClient) return globalForBq.__bqClient;
  const client = new BigQuery(buildClientOptions());
  if (process.env.NODE_ENV !== "production") {
    globalForBq.__bqClient = client;
  } else {
    // In production the module instance persists for the server lifetime;
    // still memoize on globalThis for consistency.
    globalForBq.__bqClient = client;
  }
  return client;
}

/**
 * Generic, parameterized query helper.
 *
 * @param sql    Standard SQL. Use `@name` placeholders with the `params` map.
 * @param params Named query parameters.
 * @returns      Array of typed rows.
 */
export async function query<T = Record<string, unknown>>(
  sql: string,
  params?: Record<string, unknown>,
): Promise<T[]> {
  const [rows] = await getBq().query({
    query: sql,
    params,
    // Standard SQL is the default; set explicitly for clarity/safety.
    useLegacySql: false,
  });
  return rows as T[];
}

/**
 * Result envelope for `safeQuery` — distinguishes "query ran, zero rows" from
 * "query could not run" so callers can render an honest empty-vs-unavailable
 * state (docs/dashboard-vision.md §5: "never fake zeros").
 */
export interface SafeResult<T> {
  rows: T[];
  /** null when the query succeeded (even with zero rows). Otherwise a short, safe-to-display reason (never the raw stack). */
  error: string | null;
}

/**
 * Wraps `query()` so a missing table, a permission error, or a transient BQ
 * outage degrades to `{ rows: [], error: "..." }` instead of throwing and
 * 500-ing the page. Every Generation-department (`ce_*`) query in
 * `ce-queries.ts` goes through this — those tables may not exist yet in a
 * fresh BigQuery project, and that must render as "no data yet" rather than
 * crash.
 */
export async function safeQuery<T = Record<string, unknown>>(
  sql: string,
  params?: Record<string, unknown>,
): Promise<SafeResult<T>> {
  try {
    const rows = await query<T>(sql, params);
    return { rows, error: null };
  } catch (err) {
    const rawMessage = err instanceof Error ? err.message : String(err);
    // Keep the surfaced message short and non-sensitive; BQ error text can be
    // verbose but does not include credentials. Also strip the GCP project
    // id — this message renders directly in <UnavailableBanner>, and the
    // project id is otherwise-unnecessary infrastructure detail to expose.
    // BigQuery uses both `project.dataset.table` (query text) and
    // `project:dataset.table` (e.g. "Not found: Table ...") separators.
    const message = rawMessage.split(`${PROJECT_ID}.`).join("").split(`${PROJECT_ID}:`).join("");
    return { rows: [], error: message.slice(0, 300) };
  }
}
