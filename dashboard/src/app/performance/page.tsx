import { SectionHeading, EmptyState, KpiCard } from "@/components/ui/misc";
import { UnavailableBanner } from "@/components/ui/unavailable-banner";
import { Badge, type BadgeProps } from "@/components/ui/badge";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
  TableScroll,
} from "@/components/ui/table";
import { SegmentTrendChart, type SegmentTrendPoint } from "@/components/charts/segment-trend-chart";
import { getPmisAnomalies, getPmisSegmentTrend } from "@/lib/pmis-queries";
import { getLeadQualityScores } from "@/lib/ce-queries";
import { formatDate, formatINR } from "@/lib/format";

export const dynamic = "force-dynamic";

/**
 * Performance pipeline page (docs/dashboard-vision.md §3). Reads the
 * anomaly-engine's own `pmis_*` tables (segment-grain spend/lead facts +
 * detected anomalies — see src/lib/pmis-queries.ts for the schema
 * provenance) plus this repo's `ce_lead_quality_scores`. Same
 * safeQuery/honest-state pattern as every other page: a query that can't
 * run renders <UnavailableBanner>, never a fake zero.
 */

function severityVariant(severity: string | null): BadgeProps["variant"] {
  switch ((severity ?? "").toUpperCase()) {
    case "CRITICAL":
      return "critical";
    case "WARNING":
      return "high";
    case "POSITIVE":
      return "success";
    default:
      return "outline";
  }
}

function pct(n: number | null): string {
  if (n === null) return "—";
  const sign = n > 0 ? "+" : "";
  return `${sign}${n.toFixed(1)}%`;
}

export default async function PerformancePage() {
  const [trend, anomalies, leadQuality] = await Promise.all([
    getPmisSegmentTrend(30),
    getPmisAnomalies(30),
    getLeadQualityScores(50),
  ]);

  // Pivot (date, segment, spend/iql) rows into one row per date with a
  // column per segment, for the multi-line trend charts.
  const segmentsSeen = new Set<string>();
  const spendByDate = new Map<string, SegmentTrendPoint>();
  const iqlByDate = new Map<string, SegmentTrendPoint>();
  for (const r of trend.rows) {
    segmentsSeen.add(r.segment);
    const spendRow = spendByDate.get(r.date) ?? { date: r.date };
    spendRow[r.segment] = r.spend;
    spendByDate.set(r.date, spendRow);

    const iqlRow = iqlByDate.get(r.date) ?? { date: r.date };
    iqlRow[r.segment] = r.iql;
    iqlByDate.set(r.date, iqlRow);
  }
  const segments = [...segmentsSeen].sort();
  const spendData = [...spendByDate.values()].sort((a, b) => a.date.localeCompare(b.date));
  const iqlData = [...iqlByDate.values()].sort((a, b) => a.date.localeCompare(b.date));

  const latestDate = trend.rows.length > 0 ? trend.rows[trend.rows.length - 1].date : null;
  const latestRows = trend.rows.filter((r) => r.date === latestDate);
  const totalSpendLatest = latestRows.reduce((sum, r) => sum + (r.spend ?? 0), 0);
  const totalIqlLatest = latestRows.reduce((sum, r) => sum + (r.iql ?? 0), 0);
  const blendedCpiql = totalIqlLatest > 0 ? totalSpendLatest / totalIqlLatest : null;
  const totalSalesLatest = latestRows.reduce((sum, r) => sum + (r.sales ?? 0), 0);

  const criticalCount = anomalies.rows.filter((a) => (a.severity ?? "").toUpperCase() === "CRITICAL").length;
  const warningCount = anomalies.rows.filter((a) => (a.severity ?? "").toUpperCase() === "WARNING").length;

  return (
    <div className="space-y-8">
      <SectionHeading
        title="Performance"
        description="Segment funnel (pmis_daily_segment_facts), detected anomalies (pmis_anomaly_facts), and lead-quality / CPQL (ce_lead_quality_scores)."
      />

      {trend.error ? (
        <UnavailableBanner label="pmis_daily_segment_facts" error={trend.error} />
      ) : (
        <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
          <KpiCard label="Latest Day" value={latestDate ? formatDate(latestDate) : "—"} />
          <KpiCard label="Spend (latest day)" value={formatINR(totalSpendLatest)} />
          <KpiCard
            label="IQLs (latest day)"
            value={totalIqlLatest.toLocaleString("en-IN")}
            hint={`${totalSalesLatest.toLocaleString("en-IN")} sales same day`}
            accent="emerald"
          />
          <KpiCard
            label="Blended CPIQL"
            value={blendedCpiql !== null ? formatINR(blendedCpiql) : "—"}
            accent={blendedCpiql !== null ? "indigo" : "default"}
          />
        </div>
      )}

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <SegmentTrendChart
          title="Spend by Segment"
          description="Trailing 30 days, pmis_daily_segment_facts."
          data={spendData}
          segments={segments}
          valuePrefix="₹"
        />
        <SegmentTrendChart
          title="IQLs by Segment"
          description="Trailing 30 days, pmis_daily_segment_facts."
          data={iqlData}
          segments={segments}
        />
      </div>

      <section>
        <SectionHeading
          title="Anomalies"
          description={
            anomalies.error
              ? undefined
              : `Latest detection run${anomalies.rows[0]?.run_date ? ` (${formatDate(anomalies.rows[0].run_date)})` : ""} — ${criticalCount} critical, ${warningCount} warning.`
          }
        />
        {anomalies.error ? (
          <UnavailableBanner label="pmis_anomaly_facts" error={anomalies.error} />
        ) : anomalies.rows.length === 0 ? (
          <EmptyState message="No anomalies flagged in the latest detection run." />
        ) : (
          <TableScroll>
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Severity</TableHead>
                  <TableHead>Scope</TableHead>
                  <TableHead>Entity</TableHead>
                  <TableHead>Metric</TableHead>
                  <TableHead>Yesterday</TableHead>
                  <TableHead>Baseline</TableHead>
                  <TableHead>Change</TableHead>
                  <TableHead>Direction</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {anomalies.rows.map((a, i) => (
                  <TableRow key={`${a.entity}-${a.metric}-${i}`}>
                    <TableCell>
                      <Badge variant={severityVariant(a.severity)}>
                        {a.severity ?? "unjudged"}
                        {!a.severityIsJudged && a.severity ? (
                          <span className="ml-1 font-normal opacity-70">(candidate)</span>
                        ) : null}
                      </Badge>
                    </TableCell>
                    <TableCell className="whitespace-nowrap">{a.scope ?? "—"}</TableCell>
                    <TableCell className="whitespace-nowrap font-medium text-foreground">
                      {a.entity ?? "—"}
                    </TableCell>
                    <TableCell className="whitespace-nowrap font-mono text-xs">{a.metric ?? "—"}</TableCell>
                    <TableCell className="tabular-nums">
                      {a.yesterday !== null ? a.yesterday.toLocaleString("en-IN") : "—"}
                    </TableCell>
                    <TableCell className="tabular-nums text-muted">
                      {a.baseline_mean !== null ? a.baseline_mean.toLocaleString("en-IN") : "—"}
                    </TableCell>
                    <TableCell
                      className={
                        a.direction === "adverse"
                          ? "tabular-nums text-danger"
                          : a.direction === "favorable"
                            ? "tabular-nums text-success"
                            : "tabular-nums"
                      }
                    >
                      {pct(a.pct_change)}
                    </TableCell>
                    <TableCell className="capitalize">{a.direction ?? "—"}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </TableScroll>
        )}
      </section>

      <section>
        <SectionHeading
          title="Lead Quality"
          description="ce_lead_quality_scores, latest scored period — segment-level only per docs/dashboard-vision.md §5 (per-campaign CPQL is pending registry confirmation)."
        />
        {leadQuality.error ? (
          <UnavailableBanner label="ce_lead_quality_scores" error={leadQuality.error} />
        ) : leadQuality.rows.length === 0 ? (
          <EmptyState message="No lead-quality rows scored yet." />
        ) : (
          <TableScroll>
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Segment</TableHead>
                  <TableHead>Period</TableHead>
                  <TableHead>Leads</TableHead>
                  <TableHead>Quality Leads</TableHead>
                  <TableHead>Quality Ratio</TableHead>
                  <TableHead>Spend</TableHead>
                  <TableHead>CPL</TableHead>
                  <TableHead>CPQL</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {leadQuality.rows.map((r, i) => (
                  <TableRow key={`${r.segment}-${i}`}>
                    <TableCell className="font-medium text-foreground">{r.segment ?? "—"}</TableCell>
                    <TableCell className="whitespace-nowrap text-xs text-muted">
                      {formatDate(r.period_start)} – {formatDate(r.period_end)}
                    </TableCell>
                    <TableCell className="tabular-nums">{r.leads?.toLocaleString("en-IN") ?? "—"}</TableCell>
                    <TableCell className="tabular-nums text-success">
                      {r.quality_leads?.toLocaleString("en-IN") ?? "—"}
                    </TableCell>
                    <TableCell className="tabular-nums">
                      {r.quality_ratio !== null ? `${Math.round(r.quality_ratio * 100)}%` : "—"}
                    </TableCell>
                    <TableCell className="tabular-nums">{formatINR(r.spend_inr)}</TableCell>
                    <TableCell className="tabular-nums">{r.cpl_inr !== null ? formatINR(r.cpl_inr) : "—"}</TableCell>
                    {/* CPQL is display-gated honesty (vision §5): null renders as "—", never a fabricated zero. */}
                    <TableCell className="tabular-nums font-medium text-foreground">
                      {r.cpql_inr !== null ? formatINR(r.cpql_inr) : "—"}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </TableScroll>
        )}
      </section>
    </div>
  );
}
