/**
 * Unit tests for the per-table freshness SLA model in health.ts (2026-10-27).
 *
 * Run with `npm test` — plain `node --test` on Node's native TypeScript type
 * stripping (Node >=22.18), so no test framework dependency. The explicit
 * `./health.ts` extension is required by Node's resolver (tsconfig
 * allowImportingTsExtensions makes it legal for tsc too).
 *
 * `now` is injected everywhere so day-boundary cases are deterministic. Dates
 * are BigQuery DATEs (YYYY-MM-DD), read as 00:00 UTC, exactly as in prod.
 */
import assert from "node:assert/strict";
import { describe, test } from "node:test";

import {
  FRESHNESS_SLA,
  HEALTH_STYLE,
  ageDays,
  cadenceLabel,
  freshnessDetail,
  healthOf,
  judgeFreshness,
  slaFor,
  worstHealth,
} from "./health.ts";
import type { FreshnessTable, Health } from "./health.ts";

/** Tuesday 2026-10-27 17:30 IST — the incident-verification day. */
const NOW = Date.parse("2026-10-27T12:00:00Z");
const DAY = 86_400_000;
const TABLES = Object.keys(FRESHNESS_SLA) as FreshnessTable[];

describe("FRESHNESS_SLA", () => {
  test("covers exactly the tables the dashboard reads freshness for", () => {
    // ce-queries.ts FRESHNESS_SOURCES labels are typed FreshnessTable, so a
    // label without an entry here already fails `tsc --noEmit`; this pins the
    // full set so an entry can't be dropped (or a stray one added) silently.
    assert.deepEqual([...TABLES].sort(), [
      "ad_copy_drafts",
      "audience_angles",
      "campaign_registry",
      "creative_briefs",
      "generation_runs",
      "job_market_demand",
      "keyword_candidates",
      "keyword_rejects",
      "kw_search_term_perf",
      "lead_quality_scores",
      "pmis_daily_segment_facts",
      "social_trends",
      "value_ledger",
    ]);
  });

  test("every SLA is a positive whole number of days, or null for on-demand tables", () => {
    for (const table of TABLES) {
      const { cadence, slaDays } = FRESHNESS_SLA[table];
      assert.ok(["daily", "weekly", "monthly", "on demand"].includes(cadence), `${table}: cadence ${cadence}`);
      if (slaDays === null) {
        assert.equal(cadence, "on demand", `${table}: only on-demand tables may have no SLA`);
      } else {
        assert.ok(Number.isInteger(slaDays) && slaDays > 0, `${table}: slaDays ${slaDays}`);
        assert.notEqual(cadence, "on demand", `${table}: an on-demand table must not have an SLA`);
      }
    }
  });

  test("pins the agreed per-table SLAs (2026-10-27 decisions)", () => {
    const expected: Record<FreshnessTable, number | null> = {
      // daily, run_date-stamped
      generation_runs: 3,
      lead_quality_scores: 3,
      keyword_candidates: 3,
      ad_copy_drafts: 3,
      audience_angles: 3,
      // daily, data-date-stamped (+1 day lag)
      kw_search_term_perf: 4,
      value_ledger: 4,
      pmis_daily_segment_facts: 4,
      // event-driven
      keyword_rejects: 7,
      // weekly (Monday IST + catch-up)
      campaign_registry: 9,
      // monthly Intelligence
      social_trends: 35,
      job_market_demand: 35,
      // no pipeline writer
      creative_briefs: null,
    };
    for (const table of TABLES) assert.equal(slaFor(table), expected[table], table);
    assert.equal(FRESHNESS_SLA.campaign_registry.cadence, "weekly");
    assert.equal(FRESHNESS_SLA.social_trends.cadence, "monthly");
    assert.equal(FRESHNESS_SLA.job_market_demand.cadence, "monthly");
    assert.equal(FRESHNESS_SLA.creative_briefs.cadence, "on demand");
  });
});

describe("ageDays", () => {
  test("is null for missing or unparseable dates", () => {
    assert.equal(ageDays(null, NOW), null);
    assert.equal(ageDays("", NOW), null);
    assert.equal(ageDays("garbage", NOW), null);
  });

  test("measures fractional days from 00:00 UTC of the DATE", () => {
    assert.equal(ageDays("2026-10-26", NOW), 1.5);
    assert.equal(ageDays("2026-10-27", NOW), 0.5);
    assert.equal(ageDays("2026-08-18", NOW), 70.5);
  });

  test("defaults `now` to the current time", () => {
    const today = new Date().toISOString().slice(0, 10);
    const age = ageDays(today);
    assert.ok(age !== null && age >= 0 && age < 1, `age ${age}`);
  });
});

describe("healthOf", () => {
  test("missing, empty or invalid dates are unknown for every table", () => {
    for (const table of TABLES) {
      for (const latest of [null, "", "garbage"]) {
        assert.equal(healthOf(latest, table, NOW), "unknown", `${table} ${JSON.stringify(latest)}`);
      }
    }
  });

  test("on-demand creative_briefs is never stale, but still unknown with no data", () => {
    assert.equal(healthOf("2026-10-27", "creative_briefs", NOW), "healthy");
    assert.equal(healthOf("2020-01-01", "creative_briefs", NOW), "healthy");
    assert.equal(healthOf(null, "creative_briefs", NOW), "unknown");
  });

  test("monthly Intelligence tables hold for 35 days", () => {
    assert.equal(healthOf("2026-09-27", "job_market_demand", NOW), "healthy"); // 30.5d
    assert.equal(healthOf("2026-09-21", "job_market_demand", NOW), "stale"); // 36.5d
    assert.equal(healthOf("2026-08-18", "job_market_demand", NOW), "stale"); // prod value on 2026-10-27
    assert.equal(healthOf("2026-08-25", "social_trends", NOW), "stale"); // prod value on 2026-10-27
    assert.equal(healthOf("2026-10-01", "social_trends", NOW), "healthy");
  });

  test("data-date daily tables allow the built-in 1-day lag without false alarms", () => {
    assert.equal(healthOf("2026-10-26", "kw_search_term_perf", NOW), "healthy"); // normal: IST yesterday
    assert.equal(healthOf("2026-10-24", "kw_search_term_perf", NOW), "healthy"); // 3.5d: one missed run
    assert.equal(healthOf("2026-10-23", "kw_search_term_perf", NOW), "stale"); // 4.5d
    assert.equal(healthOf("2026-10-25", "value_ledger", NOW), "healthy"); // prod value on 2026-10-27
    assert.equal(healthOf("2026-10-23", "value_ledger", NOW), "stale");
  });

  test("pmis_daily_segment_facts frozen at 2026-10-15 (LinkedIn 426) reads stale", () => {
    assert.equal(healthOf("2026-10-15", "pmis_daily_segment_facts", NOW), "stale");
    assert.equal(healthOf("2026-10-24", "pmis_daily_segment_facts", NOW), "healthy");
  });

  test("run_date daily tables go stale after 3 days", () => {
    assert.equal(healthOf("2026-10-25", "generation_runs", NOW), "healthy"); // 2.5d
    assert.equal(healthOf("2026-10-24", "generation_runs", NOW), "stale"); // 3.5d
    assert.equal(healthOf("2026-10-24", "lead_quality_scores", NOW), "stale");
    assert.equal(healthOf("2026-10-25", "keyword_candidates", NOW), "healthy");
  });

  test("event-driven keyword_rejects and weekly campaign_registry", () => {
    assert.equal(healthOf("2026-10-26", "keyword_rejects", NOW), "healthy"); // prod value on 2026-10-27
    assert.equal(healthOf("2026-10-21", "keyword_rejects", NOW), "healthy"); // 6.5d
    assert.equal(healthOf("2026-10-19", "keyword_rejects", NOW), "stale"); // 8.5d
    assert.equal(healthOf("2026-10-19", "campaign_registry", NOW), "healthy"); // last Monday, 8.5d
    assert.equal(healthOf("2026-10-17", "campaign_registry", NOW), "stale"); // 10.5d
  });

  test("replaces the old single 8-day threshold in both directions", () => {
    // Under STALE_AFTER_DAYS = 8 a daily table 5.5 days behind read "fresh"...
    assert.equal(healthOf("2026-10-22", "generation_runs", NOW), "stale");
    // ...and a monthly table 20.5 days into its cycle read "stale".
    assert.equal(healthOf("2026-10-07", "job_market_demand", NOW), "healthy");
  });

  test("age exactly equal to the SLA is healthy; 1ms past it is stale", () => {
    const latest = "2026-10-01";
    const then = Date.parse(latest);
    for (const table of TABLES) {
      const sla = slaFor(table);
      if (sla === null) continue;
      assert.equal(healthOf(latest, table, then + sla * DAY), "healthy", `${table} at ${sla}d`);
      assert.equal(healthOf(latest, table, then + sla * DAY + 1), "stale", `${table} at ${sla}d + 1ms`);
    }
  });

  test("a future-dated row (clock skew) is not stale", () => {
    assert.equal(healthOf("2026-10-29", "generation_runs", NOW), "healthy");
  });

  test("defaults `now` to the current time", () => {
    const today = new Date().toISOString().slice(0, 10);
    assert.equal(healthOf(today, "generation_runs"), "healthy");
    assert.equal(healthOf("2000-01-01", "generation_runs"), "stale");
  });

  test("the table argument is required and checked at the type level", () => {
    // Compile-time guard, never executed: `npx tsc --noEmit` fails if `table`
    // ever becomes optional again or stops rejecting unknown table names.
    const typeChecksOnly = () => {
      // @ts-expect-error -- table is required: there is no global default SLA
      healthOf("2026-10-26");
      // @ts-expect-error -- unknown table names are rejected
      healthOf("2026-10-26", "not_a_table", NOW);
    };
    assert.equal(typeof typeChecksOnly, "function");
  });
});

describe("freshnessDetail", () => {
  test("stale tables show cadence, SLA and how overdue they are", () => {
    assert.equal(
      freshnessDetail("2026-08-18", "job_market_demand", NOW),
      "last updated 2026-08-18 · expected monthly (stale after 35d) · 70d old",
    );
    assert.equal(
      freshnessDetail("2026-10-15", "pmis_daily_segment_facts", NOW),
      "last updated 2026-10-15 · expected daily (stale after 4d) · 12d old",
    );
  });

  test("healthy tables show cadence and SLA but no age", () => {
    assert.equal(
      freshnessDetail("2026-10-26", "kw_search_term_perf", NOW),
      "last updated 2026-10-26 · expected daily (stale after 4d)",
    );
    assert.equal(
      freshnessDetail("2026-10-19", "campaign_registry", NOW),
      "last updated 2026-10-19 · expected weekly (stale after 9d)",
    );
  });

  test("on-demand tables have no SLA and never an age", () => {
    const detail = freshnessDetail("2020-01-01", "creative_briefs", NOW);
    assert.equal(detail, "last updated 2020-01-01 · expected on demand");
    assert.ok(!detail.includes("stale after"));
    assert.ok(!detail.includes("d old"));
  });

  test("reads 'no data yet' exactly when health is unknown", () => {
    for (const table of TABLES) {
      for (const latest of [null, "", "garbage"]) {
        assert.equal(freshnessDetail(latest, table, NOW), "no data yet");
        assert.equal(healthOf(latest, table, NOW), "unknown");
      }
    }
  });
});

describe("cadenceLabel", () => {
  test("is the always-visible cadence line for each strip tile", () => {
    assert.equal(cadenceLabel("generation_runs"), "daily · stale after 3d");
    assert.equal(cadenceLabel("pmis_daily_segment_facts"), "daily · stale after 4d");
    assert.equal(cadenceLabel("keyword_rejects"), "daily · stale after 7d");
    assert.equal(cadenceLabel("campaign_registry"), "weekly · stale after 9d");
    assert.equal(cadenceLabel("job_market_demand"), "monthly · stale after 35d");
    assert.equal(cadenceLabel("creative_briefs"), "on demand");
  });
});

describe("judgeFreshness", () => {
  test("judges each row against its own SLA on one clock and keeps extra fields", () => {
    const rows = [
      { table: "generation_runs" as const, latest_date: "2026-10-26", extra: 1 },
      { table: "pmis_daily_segment_facts" as const, latest_date: "2026-10-15", extra: 2 },
      { table: "creative_briefs" as const, latest_date: null, extra: 3 },
    ];
    assert.deepEqual(judgeFreshness(rows, NOW), [
      {
        table: "generation_runs",
        latest_date: "2026-10-26",
        extra: 1,
        health: "healthy",
        detail: "last updated 2026-10-26 · expected daily (stale after 3d)",
      },
      {
        table: "pmis_daily_segment_facts",
        latest_date: "2026-10-15",
        extra: 2,
        health: "stale",
        detail: "last updated 2026-10-15 · expected daily (stale after 4d) · 12d old",
      },
      { table: "creative_briefs", latest_date: null, extra: 3, health: "unknown", detail: "no data yet" },
    ]);
  });

  test("the same date can be healthy for one table and stale for another", () => {
    const [registry, runs] = judgeFreshness(
      [
        { table: "campaign_registry", latest_date: "2026-10-22" },
        { table: "generation_runs", latest_date: "2026-10-22" },
      ],
      NOW,
    );
    assert.equal(registry.health, "healthy"); // weekly, 5.5d
    assert.equal(runs.health, "stale"); // daily, 5.5d
  });

  test("empty input and default clock", () => {
    assert.deepEqual(judgeFreshness([], NOW), []);
    const today = new Date().toISOString().slice(0, 10);
    assert.equal(judgeFreshness([{ table: "generation_runs", latest_date: today }])[0].health, "healthy");
  });
});

describe("worstHealth (unchanged roll-up)", () => {
  test("empty input is unknown", () => {
    assert.equal(worstHealth([]), "unknown");
  });

  test("failed > stale > unknown > healthy", () => {
    assert.equal(worstHealth(["healthy", "stale", "failed"]), "failed");
    assert.equal(worstHealth(["stale", "healthy"]), "stale");
    assert.equal(worstHealth(["healthy", "unknown"]), "unknown");
    assert.equal(worstHealth(["healthy", "healthy"]), "healthy");
  });

  test("a stale pmis child turns the Performance roll-up stale", () => {
    const performance: Health[] = [
      healthOf("2026-10-26", "kw_search_term_perf", NOW),
      healthOf("2026-10-27", "lead_quality_scores", NOW),
      healthOf("2026-10-15", "pmis_daily_segment_facts", NOW),
    ];
    assert.equal(worstHealth(performance), "stale");
  });
});

describe("HEALTH_STYLE", () => {
  test("has a style for every health state", () => {
    for (const h of ["healthy", "stale", "failed", "unknown"] as Health[]) {
      assert.ok(HEALTH_STYLE[h].dot && HEALTH_STYLE[h].text && HEALTH_STYLE[h].label, h);
    }
  });
});
