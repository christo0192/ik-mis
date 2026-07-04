// Standalone BigQuery connectivity check.
//
// Mirrors the credential-loading logic in src/lib/bq.ts but runs without the
// Next.js dev server. Prints each table_id with its row_count.
//
//   node scripts/health-check.mjs
//
// Credential precedence (server-side only):
//   1. env MARKET_RESEARCH_BQ_SA_B64 -> base64-decode -> JSON.parse -> credentials
//   2. else local key file via keyFilename
//
// Never log or print the SA key contents.

import { existsSync } from "node:fs";
import { BigQuery } from "@google-cloud/bigquery";

const PROJECT_ID = "ik-marketing-data";
const DATASET = "Market_Research";
const LOCAL_KEY_FILE = "C:\\Users\\Admin\\.gcp-keys\\market-research-bq.json";

function buildClientOptions() {
  const base = { projectId: PROJECT_ID };

  const b64 = process.env.MARKET_RESEARCH_BQ_SA_B64;
  if (b64 && b64.trim().length > 0) {
    let credentials;
    try {
      const json = Buffer.from(b64, "base64").toString("utf8");
      credentials = JSON.parse(json);
    } catch {
      throw new Error(
        "MARKET_RESEARCH_BQ_SA_B64 is set but is not valid base64-encoded JSON.",
      );
    }
    return { ...base, credentials };
  }

  if (existsSync(LOCAL_KEY_FILE)) {
    return { ...base, keyFilename: LOCAL_KEY_FILE };
  }

  throw new Error(
    "No BigQuery credentials found. Set MARKET_RESEARCH_BQ_SA_B64 or provide the local key file.",
  );
}

async function main() {
  const bq = new BigQuery(buildClientOptions());

  const [rows] = await bq.query({
    query: `SELECT table_id, row_count
            FROM \`${PROJECT_ID}.${DATASET}.__TABLES__\`
            ORDER BY table_id`,
    useLegacySql: false,
  });

  console.log(`Connected to ${PROJECT_ID}.${DATASET}. Tables:`);
  for (const r of rows) {
    console.log(`  ${r.table_id}: ${Number(r.row_count)} rows`);
  }
  console.log(`\nOK — ${rows.length} table(s).`);
}

main().catch((err) => {
  console.error("Health check FAILED:", err?.message ?? err);
  process.exit(1);
});
