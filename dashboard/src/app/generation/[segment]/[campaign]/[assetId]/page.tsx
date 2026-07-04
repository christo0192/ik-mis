import { Breadcrumbs } from "@/components/breadcrumbs";
import { SectionHeading } from "@/components/ui/misc";
import { UnavailableBanner } from "@/components/ui/unavailable-banner";
import { Badge, statusVariant } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import { ApproveRejectButtons } from "@/components/approve-reject-buttons";
import { getAdCopyDraftByAssetId, getAudienceAngleByAssetId } from "@/lib/ce-queries";
import { formatDate } from "@/lib/format";

export const dynamic = "force-dynamic";

function Field({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div>
      <p className="text-[11px] font-medium uppercase tracking-wide text-muted">{label}</p>
      <div className="mt-1 text-sm leading-relaxed text-foreground/90">{value}</div>
    </div>
  );
}

export default async function AssetDetailPage({
  params,
}: {
  params: Promise<{ segment: string; campaign: string; assetId: string }>;
}) {
  const { segment: rawSegment, campaign: rawCampaign, assetId: rawAssetId } = await params;
  const segment = decodeURIComponent(rawSegment);
  const campaignName = decodeURIComponent(rawCampaign);
  const assetId = decodeURIComponent(rawAssetId);

  const [draftResult, angleResult] = await Promise.all([
    getAdCopyDraftByAssetId(assetId),
    getAudienceAngleByAssetId(assetId),
  ]);

  const draft = draftResult.rows[0] ?? null;
  const angle = angleResult.rows[0] ?? null;

  const crumbs = [
    { label: "Generation", href: "/generation" },
    { label: segment, href: `/generation/${encodeURIComponent(segment)}` },
    { label: campaignName, href: `/generation/${encodeURIComponent(segment)}/${encodeURIComponent(campaignName)}` },
    { label: assetId },
  ];

  if (draftResult.error && angleResult.error) {
    return (
      <div className="space-y-6">
        <Breadcrumbs items={crumbs} />
        <UnavailableBanner
          label="ce_ad_copy_drafts / ce_audience_angles"
          error={`${draftResult.error} | ${angleResult.error}`}
        />
      </div>
    );
  }

  if (!draft && !angle) {
    return (
      <div className="space-y-6">
        <Breadcrumbs items={crumbs} />
        <SectionHeading title={assetId} description="Asset detail" />
        <div className="rounded-xl border border-dashed border-border bg-surface p-8 text-center text-sm text-muted">
          No asset with this ID was found in ad_copy_drafts or audience_angles.
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <Breadcrumbs items={crumbs} />
      <SectionHeading title={assetId} description={`Asset detail — ${campaignName}`} />

      {draft ? (
        <Card>
          <CardContent className="space-y-4 pt-5">
            <div className="flex flex-wrap items-center gap-2">
              <p className="text-xs font-medium uppercase tracking-wide text-muted">Ad Copy Draft</p>
              <Badge variant={statusVariant(draft.status)}>{draft.status ?? "—"}</Badge>
            </div>
            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
              <Field label="Headline" value={draft.headline} />
              <Field label="CTA" value={draft.cta ?? "—"} />
              <Field label="Primary text" value={draft.primary_text ?? "—"} />
              <Field label="Description" value={draft.description ?? "—"} />
              <Field label="Platform" value={draft.platform ?? "—"} />
              <Field label="Edit distance" value={draft.edit_distance ?? "—"} />
            </div>
            <Field
              label="Grounding signal (source_signal)"
              value={
                <span className="block rounded-lg bg-white/[0.03] px-3 py-2 font-mono text-xs leading-relaxed text-foreground/80">
                  {draft.source_signal ?? "—"}
                </span>
              }
            />
            <div className="flex flex-wrap items-center gap-4 border-t border-border pt-3 text-xs text-muted">
              <span>Run date: {formatDate(draft.run_date)}</span>
              <span>Run ID: {draft.run_id ?? "—"}</span>
              {draft.reviewed_by ? <span>Reviewed by: {draft.reviewed_by}</span> : null}
              {draft.reviewed_at ? <span>Reviewed at: {formatDate(draft.reviewed_at)}</span> : null}
            </div>
            <ApproveRejectButtons
              table="ad_copy_drafts"
              status={draft.status}
              where={{
                run_date: draft.run_date ?? "",
                run_id: draft.run_id ?? "",
                campaign_name: draft.campaign_name,
                asset_id: draft.asset_id,
              }}
            />
          </CardContent>
        </Card>
      ) : null}

      {angle ? (
        <Card>
          <CardContent className="space-y-4 pt-5">
            <div className="flex flex-wrap items-center gap-2">
              <p className="text-xs font-medium uppercase tracking-wide text-muted">Audience Angle</p>
              <Badge variant={statusVariant(angle.status)}>{angle.status ?? "—"}</Badge>
            </div>
            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
              <Field label="Angle" value={angle.angle_name} />
              <Field label="Persona" value={angle.persona ?? "—"} />
              <Field label="Platform" value={angle.platform ?? "—"} />
            </div>
            <Field label="Description" value={angle.angle_description ?? "—"} />
            <Field
              label="Grounding signal (source_signal)"
              value={
                <span className="block rounded-lg bg-white/[0.03] px-3 py-2 font-mono text-xs leading-relaxed text-foreground/80">
                  {angle.source_signal ?? "—"}
                </span>
              }
            />
            <div className="flex flex-wrap items-center gap-4 border-t border-border pt-3 text-xs text-muted">
              <span>Run date: {formatDate(angle.run_date)}</span>
              <span>Run ID: {angle.run_id ?? "—"}</span>
              {angle.reviewed_by ? <span>Reviewed by: {angle.reviewed_by}</span> : null}
              {angle.reviewed_at ? <span>Reviewed at: {formatDate(angle.reviewed_at)}</span> : null}
            </div>
            <ApproveRejectButtons
              table="audience_angles"
              status={angle.status}
              where={{
                run_date: angle.run_date ?? "",
                run_id: angle.run_id ?? "",
                campaign_name: angle.campaign_name,
                asset_id: angle.asset_id,
              }}
            />
          </CardContent>
        </Card>
      ) : null}
    </div>
  );
}
