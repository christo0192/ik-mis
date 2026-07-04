import { SectionHeading, EmptyState, KpiCard } from "@/components/ui/misc";
import { UnavailableBanner } from "@/components/ui/unavailable-banner";
import { Badge, statusVariant } from "@/components/ui/badge";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
  TableScroll,
} from "@/components/ui/table";
import { getGenerationRuns } from "@/lib/ce-queries";
import { formatDate } from "@/lib/format";

export const dynamic = "force-dynamic";

/** passed/generated -> "72%" (or em dash when undefined). */
function passRate(passed: number | null, generated: number | null): string {
  if (passed === null || generated === null || generated === 0) return "—";
  return `${Math.round((passed / generated) * 100)}%`;
}

function dropRate(rejected: number | null, generated: number | null): string {
  if (rejected === null || generated === null || generated === 0) return "—";
  return `${Math.round((rejected / generated) * 100)}%`;
}

export default async function RunsPage() {
  const runs = await getGenerationRuns(200);

  const latestRunDate = runs.rows[0]?.run_date ?? null;
  const latestRows = runs.rows.filter((r) => r.run_date === latestRunDate);
  const latestGenerated = latestRows.reduce(
    (sum, r) => sum + (r.candidates_generated ?? 0),
    0,
  );
  const latestPassed = latestRows.reduce((sum, r) => sum + (r.passed_filter ?? 0), 0);
  const latestFailed = latestRows.filter((r) => r.status === "failed").length;
  const latestPartial = latestRows.filter((r) => r.status === "partial").length;

  return (
    <div className="space-y-6">
      <SectionHeading
        title="Runs & Health"
        description="generation_runs — one row per pipeline run per path (keyword / copy / angle), with per-run counts, filter pass rates, and drop notes."
      />

      {runs.error ? (
        <UnavailableBanner label="ce_generation_runs" error={runs.error} />
      ) : runs.rows.length === 0 ? (
        <EmptyState message="No generation runs recorded yet — the pipeline hasn't run live." />
      ) : (
        <>
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
            <KpiCard
              label="Latest Run"
              value={formatDate(latestRunDate)}
              hint={`${latestRows.length} path${latestRows.length === 1 ? "" : "s"}`}
            />
            <KpiCard
              label="Candidates (latest)"
              value={latestGenerated.toLocaleString("en-IN")}
            />
            <KpiCard
              label="Passed Filter (latest)"
              value={latestPassed.toLocaleString("en-IN")}
              accent="emerald"
              hint={passRate(latestPassed, latestGenerated)}
            />
            <KpiCard
              label="Problem Paths (latest)"
              value={(latestFailed + latestPartial).toLocaleString("en-IN")}
              accent={latestFailed > 0 ? "danger" : latestPartial > 0 ? "warning" : "default"}
              hint={`${latestFailed} failed · ${latestPartial} partial`}
            />
          </div>

          <TableScroll>
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Run Date</TableHead>
                  <TableHead>Run ID</TableHead>
                  <TableHead>Path</TableHead>
                  <TableHead>Generated</TableHead>
                  <TableHead>Passed</TableHead>
                  <TableHead>Borderline</TableHead>
                  <TableHead>Rejected</TableHead>
                  <TableHead>Drop Rate</TableHead>
                  <TableHead>LLM Calls</TableHead>
                  <TableHead>Duration</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead>Notes</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {runs.rows.map((r, i) => (
                  <TableRow key={`${r.run_id}-${r.path}-${i}`}>
                    <TableCell className="whitespace-nowrap">
                      {formatDate(r.run_date)}
                    </TableCell>
                    <TableCell className="whitespace-nowrap font-mono text-xs">
                      {r.run_id}
                    </TableCell>
                    <TableCell>
                      <Badge variant="accent">{r.path ?? "—"}</Badge>
                    </TableCell>
                    <TableCell className="tabular-nums">
                      {r.candidates_generated ?? "—"}
                    </TableCell>
                    <TableCell className="tabular-nums text-success">
                      {r.passed_filter ?? "—"}
                    </TableCell>
                    <TableCell className="tabular-nums text-warning">
                      {r.borderline ?? "—"}
                    </TableCell>
                    <TableCell className="tabular-nums text-danger">
                      {r.rejected ?? "—"}
                    </TableCell>
                    <TableCell className="tabular-nums">
                      {dropRate(r.rejected, r.candidates_generated)}
                    </TableCell>
                    <TableCell className="tabular-nums">{r.llm_calls ?? "—"}</TableCell>
                    <TableCell className="whitespace-nowrap tabular-nums">
                      {r.duration_seconds !== null ? `${r.duration_seconds.toFixed(1)}s` : "—"}
                    </TableCell>
                    <TableCell>
                      <Badge variant={statusVariant(r.status)}>{r.status ?? "—"}</Badge>
                    </TableCell>
                    <TableCell className="max-w-96">
                      {r.notes ? (
                        <details className="text-xs text-muted">
                          <summary className="cursor-pointer text-foreground/70 hover:text-foreground">
                            {r.notes.slice(0, 60)}
                            {r.notes.length > 60 ? "…" : ""}
                          </summary>
                          <pre className="scroll-thin mt-1.5 max-h-48 overflow-auto whitespace-pre-wrap rounded-lg bg-white/[0.03] p-2 font-mono text-[11px] leading-relaxed">
                            {r.notes}
                          </pre>
                        </details>
                      ) : (
                        "—"
                      )}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </TableScroll>
        </>
      )}
    </div>
  );
}
