import "server-only";

import { getCeFreshness, getLatestGenerationRunStatus, getReviewQueuePressure } from "@/lib/ce-queries";
import { getMarketResearchFreshness } from "@/lib/queries";
import { getPmisFreshness } from "@/lib/pmis-queries";
import { healthOf, worstHealth, type Health } from "@/lib/health";

/**
 * Server-side builder for the Home page's system graph (docs/dashboard-vision.md
 * §2) and its caretaker bots (§2b). Turns the same freshness numbers the
 * Vital Signs tiles already use into a real node/edge map: 3 department
 * nodes + 10 pipeline/table nodes, health-colored from live BigQuery data,
 * with the closed-loop data-flow edges drawn explicitly (not decoratively).
 *
 * Nothing here renders anything — this is plain data, safe to pass from a
 * server component into the client <SystemGraph> as props.
 */

export type Dept = "intelligence" | "performance" | "generation";

export interface GraphNode {
  id: string;
  label: string;
  dept: Dept;
  kind: "department" | "table";
  health: Health;
  /** Human-readable freshness detail for the hover tooltip. */
  detail: string;
  href: string;
}

export interface GraphEdge {
  source: string;
  target: string;
  /** "flow" = the real closed-loop data pipeline (drawn bold/animated); "contains" = department-to-child grouping (drawn faint). */
  kind: "flow" | "contains";
}

export type BotActivity = "idle" | "busy" | "fixing" | "nudging";

export interface DeptBot {
  dept: Dept;
  label: string;
  activity: BotActivity;
  health: Health;
  href: string;
  /** Short reason shown in the bot's tooltip — keeps the bot state legible, not just decorative. */
  reason: string;
}

export interface SystemGraphData {
  nodes: GraphNode[];
  edges: GraphEdge[];
  bots: DeptBot[];
}

function detailFor(latestDate: string | null, health: Health): string {
  if (health === "unknown") return "no data yet";
  return `last updated ${latestDate}`;
}

export async function buildSystemGraph(): Promise<SystemGraphData> {
  const [ceFreshness, mrFreshness, pmisLatest, lastRunStatus, reviewPressure] = await Promise.all([
    getCeFreshness(),
    getMarketResearchFreshness(),
    getPmisFreshness(),
    getLatestGenerationRunStatus(),
    getReviewQueuePressure(),
  ]);

  const ceByTable = new Map(ceFreshness.map((f) => [f.table, f.latest_date]));
  const mrByTable = new Map(mrFreshness.map((f) => [f.table, f.latest_date]));

  const healthFor = (latest: string | null) => healthOf(latest);

  // generation_runs carries an explicit status; a hard "failed" folds into
  // that table's health regardless of how recent the failed run was (a
  // same-day failed run is still a failure, not "fresh").
  const generationRunsLatest = ceByTable.get("generation_runs") ?? null;
  const generationRunsHealth: Health =
    lastRunStatus === "failed" ? "failed" : healthFor(generationRunsLatest);

  const socialLatest = mrByTable.get("social_trends") ?? null;
  const jobsLatest = mrByTable.get("job_market_demand") ?? null;
  const kwPerfLatest = ceByTable.get("kw_search_term_perf") ?? null;
  const leadQualityLatest = ceByTable.get("lead_quality_scores") ?? null;
  const registryLatest = ceByTable.get("campaign_registry") ?? null;
  const keywordLatest = ceByTable.get("keyword_candidates") ?? null;
  const copyLatest = ceByTable.get("ad_copy_drafts") ?? null;
  const anglesLatest = ceByTable.get("audience_angles") ?? null;
  const rejectsLatest = ceByTable.get("keyword_rejects") ?? null;
  const ledgerLatest = ceByTable.get("value_ledger") ?? null;

  const socialHealth = healthFor(socialLatest);
  const jobsHealth = healthFor(jobsLatest);
  const kwPerfHealth = healthFor(kwPerfLatest);
  const leadQualityHealth = healthFor(leadQualityLatest);
  const pmisHealth = healthFor(pmisLatest);
  const registryHealth = healthFor(registryLatest);
  const keywordHealth = healthFor(keywordLatest);
  const copyHealth = healthFor(copyLatest);
  const anglesHealth = healthFor(anglesLatest);
  const rejectsHealth = healthFor(rejectsLatest);
  const ledgerHealth = healthFor(ledgerLatest);

  // "Review queue" isn't its own BQ table — it's the human-review step over
  // keyword_candidates + ad_copy_drafts + audience_angles. Its health is the
  // worst of the three underlying tables it draws from.
  const reviewQueueHealth = worstHealth([keywordHealth, copyHealth, anglesHealth]);

  const intelligenceHealth = worstHealth([socialHealth, jobsHealth]);
  const performanceHealth = worstHealth([kwPerfHealth, leadQualityHealth, pmisHealth]);
  const generationHealth = worstHealth([
    registryHealth,
    keywordHealth,
    copyHealth,
    reviewQueueHealth,
    rejectsHealth,
    ledgerHealth,
    generationRunsHealth,
  ]);

  const nodes: GraphNode[] = [
    // Department nodes
    {
      id: "dept:intelligence",
      label: "Intelligence",
      dept: "intelligence",
      kind: "department",
      health: intelligenceHealth,
      detail: "Market signals — job market, social trends",
      href: "/intelligence",
    },
    {
      id: "dept:performance",
      label: "Performance",
      dept: "performance",
      kind: "department",
      health: performanceHealth,
      detail: "Spend, leads, search-term performance",
      href: "/performance",
    },
    {
      id: "dept:generation",
      label: "Generation",
      dept: "generation",
      kind: "department",
      health: generationHealth,
      detail: "Keyword gate, copy drafts, review, export",
      href: "/generation",
    },

    // Intelligence children
    {
      id: "table:social_trends",
      label: "Social Trends",
      dept: "intelligence",
      kind: "table",
      health: socialHealth,
      detail: detailFor(socialLatest, socialHealth),
      href: "/intelligence/social",
    },
    {
      id: "table:job_market_demand",
      label: "Job Market",
      dept: "intelligence",
      kind: "table",
      health: jobsHealth,
      detail: detailFor(jobsLatest, jobsHealth),
      href: "/intelligence/jobs",
    },

    // Performance children
    {
      id: "table:kw_search_term_perf",
      label: "Search Term Perf",
      dept: "performance",
      kind: "table",
      health: kwPerfHealth,
      detail: detailFor(kwPerfLatest, kwPerfHealth),
      href: "/performance",
    },
    {
      id: "table:lead_quality_scores",
      label: "Lead Quality",
      dept: "performance",
      kind: "table",
      health: leadQualityHealth,
      detail: detailFor(leadQualityLatest, leadQualityHealth),
      href: "/performance",
    },

    // Generation children
    {
      id: "table:campaign_registry",
      label: "Campaign Registry",
      dept: "generation",
      kind: "table",
      health: registryHealth,
      detail: detailFor(registryLatest, registryHealth),
      href: "/registry",
    },
    {
      id: "table:keyword_gate",
      label: "Keyword Gate",
      dept: "generation",
      kind: "table",
      health: keywordHealth,
      detail: detailFor(keywordLatest, keywordHealth),
      href: "/generation?tab=keywords",
    },
    {
      id: "table:copy_drafts",
      label: "Copy Drafts",
      dept: "generation",
      kind: "table",
      health: copyHealth,
      detail: detailFor(copyLatest, copyHealth),
      href: "/generation?tab=copy",
    },
    {
      id: "table:review_queue",
      label: "Review Queue",
      dept: "generation",
      kind: "table",
      health: reviewQueueHealth,
      detail:
        reviewPressure.unavailable.length === 3
          ? "no data yet"
          : `${reviewPressure.total.toLocaleString("en-IN")} item${reviewPressure.total === 1 ? "" : "s"} pending review`,
      href: "/generation?status=proposed",
    },
    {
      id: "table:negatives_export",
      label: "Negatives Export",
      dept: "generation",
      kind: "table",
      health: rejectsHealth,
      detail: detailFor(rejectsLatest, rejectsHealth),
      href: "/generation?tab=rejects",
    },
    {
      id: "table:value_ledger",
      label: "Value Ledger",
      dept: "generation",
      kind: "table",
      health: ledgerHealth,
      detail: detailFor(ledgerLatest, ledgerHealth),
      href: "/runs",
    },
  ];

  const edges: GraphEdge[] = [
    // Containment (dept -> child), drawn faint
    { source: "dept:intelligence", target: "table:social_trends", kind: "contains" },
    { source: "dept:intelligence", target: "table:job_market_demand", kind: "contains" },
    { source: "dept:performance", target: "table:kw_search_term_perf", kind: "contains" },
    { source: "dept:performance", target: "table:lead_quality_scores", kind: "contains" },
    { source: "dept:generation", target: "table:campaign_registry", kind: "contains" },
    { source: "dept:generation", target: "table:keyword_gate", kind: "contains" },
    { source: "dept:generation", target: "table:copy_drafts", kind: "contains" },
    { source: "dept:generation", target: "table:review_queue", kind: "contains" },
    { source: "dept:generation", target: "table:negatives_export", kind: "contains" },
    { source: "dept:generation", target: "table:value_ledger", kind: "contains" },

    // The closed loop (docs/dashboard-vision.md §2): Intelligence + Performance
    // signals feed the keyword gate; gate + copy flow into review; review
    // splits into export (rejects) and the registry (approved, launch-bound);
    // both feed back into live Performance; lead quality closes the loop back
    // into the gate; value ledger records the ledger of decisions along the way.
    { source: "table:social_trends", target: "table:keyword_gate", kind: "flow" },
    { source: "table:job_market_demand", target: "table:keyword_gate", kind: "flow" },
    { source: "table:kw_search_term_perf", target: "table:keyword_gate", kind: "flow" },
    { source: "table:keyword_gate", target: "table:review_queue", kind: "flow" },
    { source: "table:copy_drafts", target: "table:review_queue", kind: "flow" },
    { source: "table:review_queue", target: "table:negatives_export", kind: "flow" },
    { source: "table:review_queue", target: "table:campaign_registry", kind: "flow" },
    { source: "table:negatives_export", target: "table:kw_search_term_perf", kind: "flow" },
    { source: "table:campaign_registry", target: "table:lead_quality_scores", kind: "flow" },
    { source: "table:lead_quality_scores", target: "table:keyword_gate", kind: "flow" },
    { source: "table:keyword_gate", target: "table:value_ledger", kind: "flow" },
    { source: "table:lead_quality_scores", target: "table:value_ledger", kind: "flow" },
  ];

  // ---------------------------------------------------------------------
  // Caretaker bots (§2b) — one per department, state-driven, never random.
  //
  // "Currently executing" is honestly hard to derive today: generation_runs
  // only ever writes a terminal status ("success" | "partial" | "failed",
  // see generation/schemas.py) once a run finishes — there is no in-progress
  // row to detect. We still check for a literal "running" status in case a
  // future writer emits one (harmless no-op today), but we do not fabricate
  // a busy state from a same-day completed run — that would be a fake
  // "AI-level feel" the vision explicitly rules out (§5: never fake states).
  // ---------------------------------------------------------------------
  const generationExecuting = (lastRunStatus ?? "").toLowerCase() === "running";

  function botFor(dept: Dept, deptHealth: Health, href: string, executing: boolean, pileUp: boolean): DeptBot {
    const label = dept === "intelligence" ? "Intelligence" : dept === "performance" ? "Performance" : "Generation";
    if (deptHealth === "failed" || deptHealth === "stale") {
      return {
        dept,
        label,
        activity: "fixing",
        health: deptHealth,
        href,
        reason: deptHealth === "failed" ? "a pipeline run failed" : "data hasn't refreshed recently",
      };
    }
    if (executing) {
      return { dept, label, activity: "busy", health: deptHealth, href, reason: "a run is executing now" };
    }
    if (pileUp) {
      return { dept, label, activity: "nudging", health: deptHealth, href, reason: "review queue is piling up" };
    }
    return { dept, label, activity: "idle", health: deptHealth, href, reason: "all clear" };
  }

  const bots: DeptBot[] = [
    botFor("intelligence", intelligenceHealth, "/intelligence", false, false),
    botFor("performance", performanceHealth, "/performance", false, false),
    botFor(
      "generation",
      generationHealth,
      "/runs",
      generationExecuting,
      reviewPressure.total > 0,
    ),
  ];

  return { nodes, edges, bots };
}
