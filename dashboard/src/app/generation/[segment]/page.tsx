import Link from "next/link";

import { Breadcrumbs } from "@/components/breadcrumbs";
import { SectionHeading, EmptyState } from "@/components/ui/misc";
import { UnavailableBanner } from "@/components/ui/unavailable-banner";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
  TableScroll,
} from "@/components/ui/table";
import { getCampaignsInSegment } from "@/lib/ce-queries";

export const dynamic = "force-dynamic";

export default async function SegmentPage({
  params,
}: {
  params: Promise<{ segment: string }>;
}) {
  const { segment: rawSegment } = await params;
  const segment = decodeURIComponent(rawSegment);

  const campaigns = await getCampaignsInSegment(segment);

  return (
    <div className="space-y-6">
      <Breadcrumbs
        items={[{ label: "Generation", href: "/generation" }, { label: segment }]}
      />
      <SectionHeading
        title={`${segment} — Campaigns`}
        description="Campaigns with generated keyword/copy/angle assets in the latest run for this segment."
      />

      {campaigns.error ? (
        <UnavailableBanner label="Generation-dept content tables" error={campaigns.error} />
      ) : campaigns.rows.length === 0 ? (
        <EmptyState message={`No campaigns with generated content found for segment "${segment}".`} />
      ) : (
        <TableScroll>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Campaign</TableHead>
                <TableHead>Assets</TableHead>
                <TableHead>Pending Review</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {campaigns.rows.map((c) => (
                <TableRow key={c.campaign_name}>
                  <TableCell>
                    <Link
                      href={`/generation/${encodeURIComponent(segment)}/${encodeURIComponent(c.campaign_name)}`}
                      className="font-medium text-foreground hover:text-accent"
                    >
                      {c.campaign_name}
                    </Link>
                  </TableCell>
                  <TableCell className="tabular-nums">{c.asset_count}</TableCell>
                  <TableCell className="tabular-nums">
                    {c.pending_count > 0 ? (
                      <span className="font-medium text-warning">{c.pending_count}</span>
                    ) : (
                      <span className="text-muted">0</span>
                    )}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </TableScroll>
      )}
    </div>
  );
}
