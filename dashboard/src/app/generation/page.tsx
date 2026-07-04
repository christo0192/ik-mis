import Link from "next/link";

import { SectionHeading, EmptyState } from "@/components/ui/misc";
import { UnavailableBanner } from "@/components/ui/unavailable-banner";
import { Badge, statusVariant, verdictVariant } from "@/components/ui/badge";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
  TableScroll,
} from "@/components/ui/table";
import { QueryTabs } from "@/components/query-tabs";
import { StatusFilterChips } from "@/components/status-filter-chips";
import { ApproveRejectButtons } from "@/components/approve-reject-buttons";
import {
  getAdCopyDrafts,
  getAudienceAngles,
  getKeywordCandidates,
  getKeywordRejects,
  getSegmentsSummary,
} from "@/lib/ce-queries";
import { listExportFiles, resolveExportsDir } from "@/lib/exports-dir";
import { formatDate } from "@/lib/format";

export const dynamic = "force-dynamic";

const TAB_VALUES = ["keywords", "copy", "angles", "rejects"] as const;
type TabValue = (typeof TAB_VALUES)[number];

const STATUS_OPTIONS = [
  { value: "proposed", label: "Proposed" },
  { value: "needs_review", label: "Needs Review" },
  { value: "approved", label: "Approved" },
  { value: "rejected", label: "Rejected" },
  { value: "launched", label: "Launched" },
];

function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / (1024 * 1024)).toFixed(1)} MB`;
}

export default async function GenerationPage({
  searchParams,
}: {
  searchParams: Promise<{ tab?: string; status?: string }>;
}) {
  const sp = await searchParams;
  const tab: TabValue = TAB_VALUES.includes(sp.tab as TabValue)
    ? (sp.tab as TabValue)
    : "keywords";
  const status = sp.status || undefined;

  const [segments, keywords, drafts, angles, rejects] = await Promise.all([
    getSegmentsSummary(),
    getKeywordCandidates({ status }),
    getAdCopyDrafts({ status }),
    getAudienceAngles({ status }),
    getKeywordRejects(),
  ]);

  const exportsAvailable = resolveExportsDir() !== null;
  const exportFiles = tab === "rejects" ? listExportFiles() : [];

  return (
    <div className="space-y-6">
      <SectionHeading
        title="Generation — Review Queue"
        description="Keyword candidates, ad copy drafts, and audience angles proposed by the Generation pipeline. Approve/reject writes back to BigQuery directly."
      />

      {segments.rows.length > 0 ? (
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-xs font-medium uppercase tracking-wide text-muted">
            Browse by segment
          </span>
          {segments.rows.map((s) => (
            <Link
              key={s.segment}
              href={`/generation/${encodeURIComponent(s.segment)}`}
              className="inline-flex items-center gap-1.5 rounded-full border border-border px-3 py-1 text-xs font-medium text-muted transition-colors hover:border-white/20 hover:text-foreground"
            >
              {s.segment}
              <span className="rounded-full bg-white/5 px-1.5 text-[10px] text-foreground/70">
                {s.campaign_count} campaigns
              </span>
              {s.pending_count > 0 ? (
                <span className="rounded-full bg-warning/15 px-1.5 text-[10px] font-semibold text-warning">
                  {s.pending_count} pending
                </span>
              ) : null}
            </Link>
          ))}
        </div>
      ) : null}

      <QueryTabs
        defaultValue="keywords"
        options={[
          { value: "keywords", label: "Keywords", count: keywords.rows.length },
          { value: "copy", label: "Ad Copy Drafts", count: drafts.rows.length },
          { value: "angles", label: "Audience Angles", count: angles.rows.length },
          { value: "rejects", label: "Rejects / Negatives", count: rejects.rows.length },
        ]}
      />

      {tab !== "rejects" ? <StatusFilterChips options={STATUS_OPTIONS} /> : null}

      {tab === "keywords" ? (
        keywords.error ? (
          <UnavailableBanner label="ce_keyword_candidates" error={keywords.error} />
        ) : keywords.rows.length === 0 ? (
          <EmptyState message="No keyword candidates in the latest run (or none match this filter)." />
        ) : (
          <TableScroll>
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Campaign</TableHead>
                  <TableHead>Keyword</TableHead>
                  <TableHead>Kind</TableHead>
                  <TableHead>Verdict</TableHead>
                  <TableHead>Score</TableHead>
                  <TableHead>Filter Reason</TableHead>
                  <TableHead>Source Signal</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead>Review</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {keywords.rows.map((k, i) => (
                  <TableRow key={`${k.campaign_name}-${k.keyword}-${i}`}>
                    <TableCell className="max-w-52">
                      <Link
                        href={`/generation/${encodeURIComponent(k.segment ?? "Unknown")}/${encodeURIComponent(k.campaign_name)}`}
                        className="font-medium text-foreground hover:text-accent"
                      >
                        {k.campaign_name}
                      </Link>
                      <span className="mt-0.5 block text-[11px] text-muted">
                        {k.segment ?? "—"}
                      </span>
                    </TableCell>
                    <TableCell className="whitespace-nowrap font-medium text-foreground">
                      {k.keyword}
                      {k.match_type ? (
                        <span className="ml-1.5 text-[11px] text-muted">
                          ({k.match_type})
                        </span>
                      ) : null}
                    </TableCell>
                    <TableCell>
                      <Badge variant={k.kind === "negative" ? "high" : "default"}>
                        {k.kind ?? "new"}
                      </Badge>
                    </TableCell>
                    <TableCell>
                      <Badge variant={verdictVariant(k.relevance_verdict)}>
                        {k.relevance_verdict ?? "—"}
                      </Badge>
                    </TableCell>
                    <TableCell className="tabular-nums">
                      {k.relevance_score !== null ? k.relevance_score.toFixed(2) : "—"}
                    </TableCell>
                    <TableCell className="max-w-64 text-xs text-muted">
                      {k.filter_reason ?? "—"}
                    </TableCell>
                    <TableCell className="max-w-72 text-xs text-muted">
                      {k.source_signal ?? "—"}
                    </TableCell>
                    <TableCell>
                      <Badge variant={statusVariant(k.status)}>{k.status ?? "—"}</Badge>
                    </TableCell>
                    <TableCell>
                      <ApproveRejectButtons
                        table="keyword_candidates"
                        status={k.status}
                        where={{
                          run_date: k.run_date ?? "",
                          run_id: k.run_id ?? "",
                          campaign_name: k.campaign_name,
                          keyword: k.keyword,
                        }}
                      />
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </TableScroll>
        )
      ) : null}

      {tab === "copy" ? (
        drafts.error ? (
          <UnavailableBanner label="ce_ad_copy_drafts" error={drafts.error} />
        ) : drafts.rows.length === 0 ? (
          <EmptyState message="No ad copy drafts in the latest run (or none match this filter)." />
        ) : (
          <TableScroll>
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Campaign</TableHead>
                  <TableHead>Asset</TableHead>
                  <TableHead>Headline</TableHead>
                  <TableHead>Description / CTA</TableHead>
                  <TableHead>Source Signal</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead>Review</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {drafts.rows.map((d, i) => (
                  <TableRow key={`${d.asset_id}-${i}`}>
                    <TableCell className="max-w-52">
                      <Link
                        href={`/generation/${encodeURIComponent(d.segment ?? "Unknown")}/${encodeURIComponent(d.campaign_name)}`}
                        className="font-medium text-foreground hover:text-accent"
                      >
                        {d.campaign_name}
                      </Link>
                      <span className="mt-0.5 block text-[11px] text-muted">
                        {d.segment ?? "—"} · {d.platform ?? "—"}
                      </span>
                    </TableCell>
                    <TableCell>
                      <Link
                        href={`/generation/${encodeURIComponent(d.segment ?? "Unknown")}/${encodeURIComponent(d.campaign_name)}/${encodeURIComponent(d.asset_id)}`}
                        className="font-mono text-xs text-accent hover:underline"
                      >
                        {d.asset_id}
                      </Link>
                    </TableCell>
                    <TableCell className="max-w-64 font-medium text-foreground">
                      {d.headline}
                    </TableCell>
                    <TableCell className="max-w-64 text-xs text-muted">
                      {d.description ?? "—"}
                      {d.cta ? (
                        <span className="mt-0.5 block text-accent">{d.cta}</span>
                      ) : null}
                    </TableCell>
                    <TableCell className="max-w-64 text-xs text-muted">
                      {d.source_signal ?? "—"}
                    </TableCell>
                    <TableCell>
                      <Badge variant={statusVariant(d.status)}>{d.status ?? "—"}</Badge>
                    </TableCell>
                    <TableCell>
                      <ApproveRejectButtons
                        table="ad_copy_drafts"
                        status={d.status}
                        where={{
                          run_date: d.run_date ?? "",
                          run_id: d.run_id ?? "",
                          campaign_name: d.campaign_name,
                          asset_id: d.asset_id,
                        }}
                      />
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </TableScroll>
        )
      ) : null}

      {tab === "angles" ? (
        angles.error ? (
          <UnavailableBanner label="ce_audience_angles" error={angles.error} />
        ) : angles.rows.length === 0 ? (
          <EmptyState message="No audience angles in the latest run (or none match this filter)." />
        ) : (
          <TableScroll>
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Campaign</TableHead>
                  <TableHead>Asset</TableHead>
                  <TableHead>Angle</TableHead>
                  <TableHead>Persona</TableHead>
                  <TableHead>Source Signal</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead>Review</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {angles.rows.map((a, i) => (
                  <TableRow key={`${a.asset_id}-${i}`}>
                    <TableCell className="max-w-52">
                      <Link
                        href={`/generation/${encodeURIComponent(a.segment ?? "Unknown")}/${encodeURIComponent(a.campaign_name)}`}
                        className="font-medium text-foreground hover:text-accent"
                      >
                        {a.campaign_name}
                      </Link>
                      <span className="mt-0.5 block text-[11px] text-muted">
                        {a.segment ?? "—"} · {a.platform ?? "—"}
                      </span>
                    </TableCell>
                    <TableCell>
                      <Link
                        href={`/generation/${encodeURIComponent(a.segment ?? "Unknown")}/${encodeURIComponent(a.campaign_name)}/${encodeURIComponent(a.asset_id)}`}
                        className="font-mono text-xs text-accent hover:underline"
                      >
                        {a.asset_id}
                      </Link>
                    </TableCell>
                    <TableCell className="max-w-64">
                      <span className="font-medium text-foreground">{a.angle_name}</span>
                      {a.angle_description ? (
                        <span className="mt-0.5 block text-xs text-muted">
                          {a.angle_description}
                        </span>
                      ) : null}
                    </TableCell>
                    <TableCell className="max-w-48 text-xs text-muted">
                      {a.persona ?? "—"}
                    </TableCell>
                    <TableCell className="max-w-64 text-xs text-muted">
                      {a.source_signal ?? "—"}
                    </TableCell>
                    <TableCell>
                      <Badge variant={statusVariant(a.status)}>{a.status ?? "—"}</Badge>
                    </TableCell>
                    <TableCell>
                      <ApproveRejectButtons
                        table="audience_angles"
                        status={a.status}
                        where={{
                          run_date: a.run_date ?? "",
                          run_id: a.run_id ?? "",
                          campaign_name: a.campaign_name,
                          asset_id: a.asset_id,
                        }}
                      />
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </TableScroll>
        )
      ) : null}

      {tab === "rejects" ? (
        <div className="space-y-4">
          <div className="rounded-xl border border-border bg-surface p-4">
            <p className="text-xs font-medium uppercase tracking-wide text-muted">
              Negative-keyword exports
            </p>
            {!exportsAvailable ? (
              <p className="mt-1.5 text-sm text-muted">
                The exports directory isn&apos;t reachable from this deployment. CSV
                downloads are only available when the dashboard runs alongside the
                pipeline&apos;s local <code className="font-mono text-xs">generation/exports/</code>{" "}
                folder.
              </p>
            ) : exportFiles.length === 0 ? (
              <p className="mt-1.5 text-sm text-muted">No export files found yet.</p>
            ) : (
              <ul className="mt-2 space-y-1.5">
                {exportFiles.map((f) => (
                  <li key={f.name} className="flex items-center justify-between gap-3 text-sm">
                    <a
                      href={`/api/exports/${encodeURIComponent(f.name)}`}
                      className="font-medium text-accent hover:underline"
                    >
                      {f.name}
                    </a>
                    <span className="text-xs text-muted">
                      {formatBytes(f.sizeBytes)} · {formatDate(f.modifiedAt)}
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </div>

          {rejects.error ? (
            <UnavailableBanner label="ce_keyword_rejects" error={rejects.error} />
          ) : rejects.rows.length === 0 ? (
            <EmptyState message="No keyword rejects in the latest run." />
          ) : (
            <TableScroll>
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Campaign</TableHead>
                    <TableHead>Keyword</TableHead>
                    <TableHead>Reject Reason</TableHead>
                    <TableHead>Score</TableHead>
                    <TableHead>Source Signal</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {rejects.rows.map((r, i) => (
                    <TableRow key={`${r.campaign_name}-${r.keyword}-${i}`}>
                      <TableCell className="max-w-52">
                        <span className="font-medium text-foreground">{r.campaign_name}</span>
                        <span className="mt-0.5 block text-[11px] text-muted">
                          {r.segment ?? "—"}
                        </span>
                      </TableCell>
                      <TableCell className="whitespace-nowrap font-medium text-foreground">
                        {r.keyword}
                      </TableCell>
                      <TableCell className="max-w-72 text-xs text-muted">
                        {r.reject_reason ?? "—"}
                      </TableCell>
                      <TableCell className="tabular-nums">
                        {r.relevance_score !== null ? r.relevance_score.toFixed(2) : "—"}
                      </TableCell>
                      <TableCell className="max-w-72 text-xs text-muted">
                        {r.source_signal ?? "—"}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </TableScroll>
          )}
        </div>
      ) : null}
    </div>
  );
}
