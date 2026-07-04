import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Badge, urgencyVariant } from "@/components/ui/badge";
import { SectionHeading, EmptyState } from "@/components/ui/misc";
import { getExecPriorities, getOverviewStats } from "@/lib/queries";
import { formatDate } from "@/lib/format";

export const dynamic = "force-dynamic";

export default async function OverviewPage() {
  const [stats, priorities] = await Promise.all([
    getOverviewStats(),
    getExecPriorities(),
  ]);

  return (
    <div className="space-y-8">
      {/* KPI cards */}
      <section>
        <SectionHeading
          title="Data Snapshot"
          description="Row counts and latest run date per source table."
        />
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5">
          {stats.map((s) => (
            <Card
              key={s.table_id}
              className="transition-shadow hover:shadow-md"
            >
              <CardContent className="pt-5">
                <p className="text-xs font-medium uppercase tracking-wide text-muted">
                  {s.label}
                </p>
                <p className="mt-1 text-3xl font-semibold tracking-tight tabular-nums text-foreground">
                  {s.row_count.toLocaleString("en-IN")}
                </p>
                <p className="mt-1 text-[11px] text-muted">
                  {s.latest_run_date ? formatDate(s.latest_run_date) : "—"}
                </p>
              </CardContent>
            </Card>
          ))}
        </div>
      </section>

      {/* Executive priorities */}
      <section>
        <SectionHeading
          title="Executive Priorities"
          description="Ranked strategic recommendations from the latest research run."
        />
        {priorities.length === 0 ? (
          <EmptyState message="No executive summary rows in the latest snapshot." />
        ) : (
          <div className="space-y-3">
            {priorities.map((p, i) => (
              <Card key={p.rank ?? i} className="overflow-hidden">
                <div className="flex">
                  {/* Rank rail */}
                  <div className="flex w-14 shrink-0 flex-col items-center justify-center border-r border-border bg-white/[0.03]">
                    <span className="text-[10px] font-medium uppercase text-muted">
                      Rank
                    </span>
                    <span className="text-2xl font-bold tabular-nums text-foreground">
                      {p.rank ?? "–"}
                    </span>
                  </div>

                  <div className="min-w-0 flex-1">
                    <CardHeader className="pb-2">
                      <div className="flex flex-wrap items-center gap-2">
                        {p.urgency ? (
                          <Badge variant={urgencyVariant(p.urgency)}>
                            {p.urgency}
                          </Badge>
                        ) : null}
                        {p.sections.map((s) => (
                          <Badge key={s} variant="info">
                            {s}
                          </Badge>
                        ))}
                        {p.effort_estimate ? (
                          <span className="text-[11px] text-muted">
                            Effort: {p.effort_estimate}
                          </span>
                        ) : null}
                        {p.revenue_impact ? (
                          <span className="text-[11px] text-muted">
                            · Impact: {p.revenue_impact}
                          </span>
                        ) : null}
                      </div>
                      <CardTitle className="mt-2 text-[15px] leading-snug">
                        {p.direction ?? "Untitled priority"}
                      </CardTitle>
                    </CardHeader>
                    <CardContent className="space-y-3 pt-0">
                      {p.actionable_insight ? (
                        <p className="text-sm leading-relaxed text-foreground/80">
                          {p.actionable_insight}
                        </p>
                      ) : null}

                      {p.gap_vs_competitors ? (
                        <div className="rounded-lg bg-warning/10 px-3 py-2">
                          <p className="text-[11px] font-semibold uppercase tracking-wide text-warning">
                            Gap vs competitors
                          </p>
                          <p className="mt-0.5 text-xs text-warning/90">
                            {p.gap_vs_competitors}
                          </p>
                        </div>
                      ) : null}

                      {p.supporting_data ? (
                        <div className="rounded-lg bg-white/[0.03] px-3 py-2">
                          <p className="text-[11px] font-semibold uppercase tracking-wide text-muted">
                            Supporting data
                          </p>
                          <p className="mt-0.5 font-mono text-[11px] leading-relaxed text-muted">
                            {p.supporting_data}
                          </p>
                        </div>
                      ) : null}
                    </CardContent>
                  </div>
                </div>
              </Card>
            ))}
          </div>
        )}
      </section>
    </div>
  );
}
