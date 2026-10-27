import Link from "next/link";

import { SectionHeading } from "@/components/ui/misc";
import { SystemGraph } from "@/components/system-graph";
import { cn, formatDate, formatINR } from "@/lib/format";
import { cadenceLabel, HEALTH_STYLE, judgeFreshness, type Health } from "@/lib/health";
import {
  getCeFreshness,
  getLatestGenerationRunStatus,
  getLeadQualityHeadline,
  getReviewQueuePressure,
  getUnregisteredCampaigns,
  getValueLedgerHeadline,
  type CeFreshnessRow,
} from "@/lib/ce-queries";
import { getPmisFreshness } from "@/lib/pmis-queries";
import { buildSystemGraph } from "@/lib/graph";

export const dynamic = "force-dynamic";

function VitalTile({
  label,
  value,
  hint,
  href,
  tone = "default",
}: {
  label: string;
  value: React.ReactNode;
  hint?: React.ReactNode;
  href: string;
  tone?: "default" | "success" | "warning" | "danger";
}) {
  const valueColor =
    tone === "success"
      ? "text-success"
      : tone === "warning"
        ? "text-warning"
        : tone === "danger"
          ? "text-danger"
          : "text-foreground";
  return (
    <Link
      href={href}
      className="group relative overflow-hidden rounded-2xl border border-white/[.08] bg-[linear-gradient(145deg,rgba(255,255,255,.055),rgba(255,255,255,.012))] p-5 shadow-[0_16px_45px_rgba(0,0,0,.22)] transition duration-300 before:absolute before:inset-x-0 before:top-0 before:h-px before:bg-gradient-to-r before:from-transparent before:via-violet-200/50 before:to-transparent hover:-translate-y-1 hover:border-accent/35"
    >
      <p className="text-[11px] font-medium uppercase tracking-wide text-muted">
        {label}
      </p>
      <p className={cn("mt-1 text-2xl font-semibold tracking-tight tabular-nums", valueColor)}>
        {value}
      </p>
      {hint ? <p className="mt-0.5 text-[11px] text-muted">{hint}</p> : null}
      <p className="mt-2 text-[11px] font-medium text-accent opacity-0 transition-opacity group-hover:opacity-100">
        View →
      </p>
    </Link>
  );
}

export default async function HomePage() {
  const [freshness, pmisLatest, lastRunStatus, pressure, ledger, leadQuality, unregistered, graph] =
    await Promise.all([
      getCeFreshness(),
      getPmisFreshness(),
      getLatestGenerationRunStatus(),
      getReviewQueuePressure(),
      getValueLedgerHeadline(),
      getLeadQualityHeadline(),
      getUnregisteredCampaigns(),
      buildSystemGraph(),
    ]);

  // pmis_daily_segment_facts has no system-graph node — it only rolls up into
  // the Performance department — so when it froze at 2026-10-15 (LinkedIn API
  // version sunset, 2026-10-27 incident) Performance went amber with no visible
  // stale child. Listing it beside the ce_* tables (and in the Pipeline Health
  // counts, which read the same list) makes the cause visible. It stays out of
  // ce-queries.ts FRESHNESS_SOURCES because that list mirrors the Generation
  // pipeline's own ce_* schemas; this reuses the same getPmisFreshness query
  // the graph already runs.
  const tables: CeFreshnessRow[] = [
    ...freshness,
    { table: "pmis_daily_segment_facts", latest_date: pmisLatest },
  ];

  // Every tile is judged against its own cadence SLA on one clock reading;
  // a failed latest generation run still overrides generation_runs' health.
  const healths = judgeFreshness(tables).map((h) =>
    h.table === "generation_runs" && lastRunStatus === "failed"
      ? { ...h, health: "failed" as Health, detail: `latest run failed · ${h.detail}` }
      : h,
  );
  const healthyCount = healths.filter((h) => h.health === "healthy").length;
  const staleCount = healths.filter((h) => h.health === "stale").length;
  const failedCount = healths.filter((h) => h.health === "failed").length;
  const unknownCount = healths.filter((h) => h.health === "unknown").length;

  const ledgerRow = ledger.rows[0] ?? null;
  const lqRow = leadQuality.rows[0] ?? null;

  const anyLiveData = healths.some((h) => h.latest_date !== null);

  return (
    <div className="space-y-10">
      {/* Vital signs — small, calm tiles (vision §2) */}
      <section>
        <SectionHeading
          title="System pulse"
          description="Live operational signals from across the intelligence loop. Every metric links to its source records."
        />
        <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
          <VitalTile
            label="Pipeline Health"
            href="/runs"
            value={
              anyLiveData ? (
                <span className="flex items-baseline gap-2 text-lg">
                  <span className="text-success">{healthyCount} fresh</span>
                  <span className={staleCount > 0 ? "text-warning" : "text-muted/50"}>
                    {staleCount} stale
                  </span>
                  <span className={failedCount > 0 ? "text-danger" : "text-muted/50"}>
                    {failedCount} failed
                  </span>
                </span>
              ) : (
                <span className="text-lg text-muted">no data yet</span>
              )
            }
            hint={
              unknownCount > 0
                ? `${unknownCount} table${unknownCount === 1 ? "" : "s"} never populated`
                : "all tables reporting"
            }
            tone={failedCount > 0 ? "danger" : staleCount > 0 ? "warning" : "default"}
          />
          <VitalTile
            label="Review Queue"
            href="/generation?status=proposed"
            value={
              pressure.unavailable.length === 3 ? (
                <span className="text-lg text-muted">no data yet</span>
              ) : (
                pressure.total.toLocaleString("en-IN")
              )
            }
            hint={
              pressure.unavailable.length === 3
                ? "pipeline hasn't run live"
                : `${pressure.keywordCandidates} keywords · ${pressure.adCopyDrafts} drafts · ${pressure.audienceAngles} angles${pressure.unavailable.length > 0 ? ` · ${pressure.unavailable.length} table(s) unreachable` : ""}`
            }
            tone={pressure.total > 0 ? "warning" : "default"}
          />
          <VitalTile
            label="Value Ledger"
            href="/runs"
            value={
              ledger.error || !ledgerRow ? (
                <span className="text-lg text-muted">no data yet</span>
              ) : ledgerRow.flaggedInrLatestRun !== null ? (
                formatINR(ledgerRow.flaggedInrLatestRun)
              ) : (
                <span className="text-lg text-muted">—</span>
              )
            }
            hint={
              ledger.error || !ledgerRow
                ? "value_ledger not populated"
                : `flagged waste, latest run (${formatDate(ledgerRow.latestRunDate)})${
                    ledgerRow.realizedInrToDate !== null
                      ? ` · ${formatINR(ledgerRow.realizedInrToDate)} realized`
                      : " · nothing realized yet"
                  }`
            }
            tone={ledgerRow?.flaggedInrLatestRun ? "success" : "default"}
          />
          <VitalTile
            label="Unregistered Campaigns"
            href="/registry?unregistered=1"
            value={
              unregistered.error ? (
                <span className="text-lg text-muted">no data yet</span>
              ) : (
                unregistered.rows.length.toLocaleString("en-IN")
              )
            }
            hint={
              unregistered.error
                ? "registry/content tables unreachable"
                : unregistered.rows.length > 0
                  ? "attribution degraded — warn-first"
                  : "all generated content resolves through the registry"
            }
            tone={unregistered.rows.length > 0 ? "danger" : "default"}
          />
        </div>

        {lqRow || leadQuality.error ? (
          <div className="mt-3 grid grid-cols-2 gap-3 lg:grid-cols-4">
            <VitalTile
              label="Quality Leads"
              href="/performance"
              value={
                lqRow?.totalQualityLeads !== null && lqRow
                  ? lqRow.totalQualityLeads!.toLocaleString("en-IN")
                  : "no data yet"
              }
              hint={
                lqRow
                  ? `of ${lqRow.totalLeads?.toLocaleString("en-IN") ?? "—"} leads · period to ${formatDate(lqRow.periodEnd)}`
                  : "lead_quality_scores not populated"
              }
            />
            <VitalTile
              label="Blended CPQL"
              href="/performance"
              value={lqRow?.blendedCpqlInr !== null && lqRow ? formatINR(lqRow.blendedCpqlInr) : "—"}
              hint="segment-level only — per-campaign CPQL pending registry confirmation"
            />
          </div>
        ) : null}
      </section>

      {/* Live system graph (vision §2, §2b) — nodes/edges/bots computed server-side in lib/graph.ts */}
      <section>
        <SectionHeading
          title="Neural system diagnostics"
          description="Your MIS visualized as a living machine. Each health marker is a real pipeline or data surface—select one to inspect it."
        />
        <SystemGraph nodes={graph.nodes} edges={graph.edges} bots={graph.bots} />
      </section>

      {/* Per-table freshness strip — the honest data behind "pipeline health" */}
      <section>
        <SectionHeading
          title="Table Freshness"
          description="MAX(run_date / snapshot_date / date) per Generation-dept table, plus the PMIS segment facts behind Performance. Each table is judged against its own cadence SLA (daily / weekly / on demand); hover a tile for details. Unreachable tables show as “no data yet”, never as zero."
        />
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-5">
          {healths.map((h) => {
            const style = HEALTH_STYLE[h.health];
            return (
              <div
                key={h.table}
                title={h.detail}
                className="flex items-center gap-2.5 rounded-lg border border-border bg-surface px-3 py-2.5"
              >
                <span className={cn("h-2 w-2 shrink-0 rounded-full", style.dot)} />
                <div className="min-w-0 leading-tight">
                  <p className="truncate font-mono text-[11px] text-foreground/80">
                    {h.table}
                  </p>
                  <p className={cn("text-[11px]", style.text)}>
                    {h.latest_date ? formatDate(h.latest_date) : style.label}
                  </p>
                  <p className="truncate text-[10px] text-muted">{cadenceLabel(h.table)}</p>
                </div>
              </div>
            );
          })}
        </div>
      </section>
    </div>
  );
}
