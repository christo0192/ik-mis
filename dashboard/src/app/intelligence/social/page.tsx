import { BarChartCard } from "@/components/charts/bar-chart-card";
import { SplitChartCard } from "@/components/charts/split-chart-card";
import { Badge } from "@/components/ui/badge";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
  TableScroll,
} from "@/components/ui/table";
import { SectionHeading, ExtLink, EmptyState } from "@/components/ui/misc";
import {
  getSocial,
  getSocialByCategory,
  getSocialBySource,
} from "@/lib/queries";
import { formatCompact } from "@/lib/format";

export const dynamic = "force-dynamic";

/** Sentiment 1–5 -> badge variant. */
function sentimentVariant(s: number | null) {
  if (s === null) return "outline" as const;
  if (s >= 4) return "success" as const;
  if (s <= 2) return "critical" as const;
  return "medium" as const;
}

export default async function SocialPage() {
  const [social, bySource, byCategory] = await Promise.all([
    getSocial(),
    getSocialBySource(),
    getSocialByCategory(),
  ]);

  // Bar chart: count per source, with avg sentiment carried as the secondary value.
  const sourceData = bySource.map((s) => ({
    label: s.source,
    value: s.count,
    secondary: s.avg_sentiment ?? undefined,
  }));
  const categoryData = byCategory.map((c) => ({ label: c.key, value: c.count }));

  return (
    <div className="space-y-8">
      <SectionHeading
        title="Social Trends"
        description={`${social.length} signals in the latest snapshot across GitHub, communities, and forums.`}
      />

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <BarChartCard
          title="Signals by Source"
          description="Count per source; hover for average sentiment (1–5)."
          data={sourceData}
          layout="horizontal"
          valueName="Signals"
          secondaryName="Avg sentiment"
          height={Math.max(260, sourceData.length * 30)}
        />
        <SplitChartCard
          title="Category Split"
          description="Share of signals by category."
          data={categoryData}
        />
      </div>

      <section>
        <SectionHeading title="Signals" description="Latest snapshot, ranked by engagement." />
        {social.length === 0 ? (
          <EmptyState message="No social signals in the latest snapshot." />
        ) : (
          <TableScroll>
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Source</TableHead>
                  <TableHead>Topic</TableHead>
                  <TableHead>Category</TableHead>
                  <TableHead>Sentiment</TableHead>
                  <TableHead>Engagement</TableHead>
                  <TableHead>Link</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {social.map((s, i) => (
                  <TableRow key={`${s.topic}-${i}`}>
                    <TableCell className="whitespace-nowrap font-medium text-foreground">
                      {s.source ?? "—"}
                    </TableCell>
                    <TableCell className="max-w-80">{s.topic ?? "—"}</TableCell>
                    <TableCell className="whitespace-nowrap">
                      {s.category ? (
                        <Badge variant="info">{s.category}</Badge>
                      ) : (
                        "—"
                      )}
                    </TableCell>
                    <TableCell>
                      {s.sentiment !== null ? (
                        <Badge variant={sentimentVariant(s.sentiment)}>
                          {s.sentiment}/5
                        </Badge>
                      ) : (
                        "—"
                      )}
                    </TableCell>
                    <TableCell className="whitespace-nowrap">
                      {s.engagement ?? formatCompact(s.engagement_value)}
                    </TableCell>
                    <TableCell>
                      <ExtLink href={s.reference_url} label="View" />
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
