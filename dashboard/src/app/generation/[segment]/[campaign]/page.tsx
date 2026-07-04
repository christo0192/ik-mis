import Link from "next/link";

import { Breadcrumbs } from "@/components/breadcrumbs";
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
import { ApproveRejectButtons } from "@/components/approve-reject-buttons";
import {
  getAdCopyDrafts,
  getAudienceAngles,
  getCampaignRegistryRow,
  getKeywordCandidates,
} from "@/lib/ce-queries";
import { formatDate } from "@/lib/format";

export const dynamic = "force-dynamic";

function MetaItem({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div>
      <p className="text-[11px] font-medium uppercase tracking-wide text-muted">{label}</p>
      <div className="mt-0.5 text-sm text-foreground/80">{value}</div>
    </div>
  );
}

export default async function CampaignDetailPage({
  params,
}: {
  params: Promise<{ segment: string; campaign: string }>;
}) {
  const { segment: rawSegment, campaign: rawCampaign } = await params;
  const segment = decodeURIComponent(rawSegment);
  const campaignName = decodeURIComponent(rawCampaign);

  const [registry, keywords, drafts, angles] = await Promise.all([
    getCampaignRegistryRow(campaignName),
    getKeywordCandidates({ campaignName }),
    getAdCopyDrafts({ campaignName }),
    getAudienceAngles({ campaignName }),
  ]);

  const registryRow = registry.rows[0] ?? null;

  return (
    <div className="space-y-6">
      <Breadcrumbs
        items={[
          { label: "Generation", href: "/generation" },
          { label: segment, href: `/generation/${encodeURIComponent(segment)}` },
          { label: campaignName },
        ]}
      />
      <SectionHeading title={campaignName} description="Campaign detail — registry row, keyword candidates, ad copy drafts, and audience angles." />

      {registry.error ? (
        <UnavailableBanner label="ce_campaign_registry" error={registry.error} />
      ) : registryRow ? (
        <div className="rounded-xl border border-border bg-surface p-4">
          <div className="flex flex-wrap items-center gap-2">
            <p className="text-xs font-medium uppercase tracking-wide text-muted">Registry</p>
            {registryRow.needs_attention ? (
              <Badge variant="high">Needs attention</Badge>
            ) : (
              <Badge variant="success">Registered</Badge>
            )}
          </div>
          <div className="mt-3 grid grid-cols-2 gap-4 sm:grid-cols-4">
            <MetaItem label="Platform" value={registryRow.platform ?? "—"} />
            <MetaItem label="Program" value={registryRow.program ?? "—"} />
            <MetaItem label="Segment" value={registryRow.segment ?? "—"} />
            <MetaItem label="Geo" value={registryRow.geo ?? "—"} />
            <MetaItem label="Funnel stage" value={registryRow.funnel_stage ?? "—"} />
            <MetaItem label="Platform campaign ID" value={registryRow.platform_campaign_id ?? "—"} />
            <MetaItem label="Status" value={registryRow.status ?? "—"} />
            <MetaItem label="Snapshot date" value={formatDate(registryRow.snapshot_date)} />
          </div>
        </div>
      ) : (
        <div className="rounded-xl border border-dashed border-danger/30 bg-danger/5 p-4 text-sm">
          <p className="font-medium text-danger">Not found in campaign_registry</p>
          <p className="mt-1 text-xs leading-relaxed text-muted">
            This campaign has generated assets but no registry row — CPQL/attribution is not
            trustworthy at the campaign level for it yet (warn-first enforcement, see{" "}
            <span className="font-mono">docs/campaign-naming-convention.md</span>).
          </p>
        </div>
      )}

      <section className="space-y-3">
        <SectionHeading title="Keyword Candidates" />
        {keywords.error ? (
          <UnavailableBanner label="ce_keyword_candidates" error={keywords.error} />
        ) : keywords.rows.length === 0 ? (
          <EmptyState message="No keyword candidates for this campaign." />
        ) : (
          <TableScroll>
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Keyword</TableHead>
                  <TableHead>Kind</TableHead>
                  <TableHead>Verdict</TableHead>
                  <TableHead>Score</TableHead>
                  <TableHead>Source Signal</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead>Review</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {keywords.rows.map((k, i) => (
                  <TableRow key={`${k.keyword}-${i}`}>
                    <TableCell className="whitespace-nowrap font-medium text-foreground">
                      {k.keyword}
                    </TableCell>
                    <TableCell>
                      <Badge variant={k.kind === "negative" ? "high" : "default"}>{k.kind ?? "new"}</Badge>
                    </TableCell>
                    <TableCell>
                      <Badge variant={verdictVariant(k.relevance_verdict)}>{k.relevance_verdict ?? "—"}</Badge>
                    </TableCell>
                    <TableCell className="tabular-nums">
                      {k.relevance_score !== null ? k.relevance_score.toFixed(2) : "—"}
                    </TableCell>
                    <TableCell className="max-w-80 text-xs text-muted">{k.source_signal ?? "—"}</TableCell>
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
        )}
      </section>

      <section className="space-y-3">
        <SectionHeading title="Ad Copy Drafts" />
        {drafts.error ? (
          <UnavailableBanner label="ce_ad_copy_drafts" error={drafts.error} />
        ) : drafts.rows.length === 0 ? (
          <EmptyState message="No ad copy drafts for this campaign." />
        ) : (
          <TableScroll>
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Asset</TableHead>
                  <TableHead>Headline</TableHead>
                  <TableHead>Description / CTA</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead>Review</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {drafts.rows.map((d, i) => (
                  <TableRow key={`${d.asset_id}-${i}`}>
                    <TableCell>
                      <Link
                        href={`/generation/${encodeURIComponent(segment)}/${encodeURIComponent(campaignName)}/${encodeURIComponent(d.asset_id)}`}
                        className="font-mono text-xs text-accent hover:underline"
                      >
                        {d.asset_id}
                      </Link>
                    </TableCell>
                    <TableCell className="max-w-64 font-medium text-foreground">{d.headline}</TableCell>
                    <TableCell className="max-w-64 text-xs text-muted">
                      {d.description ?? "—"}
                      {d.cta ? <span className="mt-0.5 block text-accent">{d.cta}</span> : null}
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
        )}
      </section>

      <section className="space-y-3">
        <SectionHeading title="Audience Angles" />
        {angles.error ? (
          <UnavailableBanner label="ce_audience_angles" error={angles.error} />
        ) : angles.rows.length === 0 ? (
          <EmptyState message="No audience angles for this campaign." />
        ) : (
          <TableScroll>
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Asset</TableHead>
                  <TableHead>Angle</TableHead>
                  <TableHead>Persona</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead>Review</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {angles.rows.map((a, i) => (
                  <TableRow key={`${a.asset_id}-${i}`}>
                    <TableCell>
                      <Link
                        href={`/generation/${encodeURIComponent(segment)}/${encodeURIComponent(campaignName)}/${encodeURIComponent(a.asset_id)}`}
                        className="font-mono text-xs text-accent hover:underline"
                      >
                        {a.asset_id}
                      </Link>
                    </TableCell>
                    <TableCell className="max-w-64">
                      <span className="font-medium text-foreground">{a.angle_name}</span>
                      {a.angle_description ? (
                        <span className="mt-0.5 block text-xs text-muted">{a.angle_description}</span>
                      ) : null}
                    </TableCell>
                    <TableCell className="max-w-48 text-xs text-muted">{a.persona ?? "—"}</TableCell>
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
        )}
      </section>
    </div>
  );
}
