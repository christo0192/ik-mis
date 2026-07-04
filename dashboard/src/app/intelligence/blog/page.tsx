import { SplitChartCard } from "@/components/charts/split-chart-card";
import { Badge, urgencyVariant } from "@/components/ui/badge";
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
  getBlog,
  getBlogByCurriculumMatch,
  getBlogByUrgency,
} from "@/lib/queries";
import { formatDate } from "@/lib/format";

export const dynamic = "force-dynamic";

const URGENCY_COLORS: Record<string, string> = {
  CRITICAL: "#fb7185", // danger
  HIGH: "#fbbf24", // warning
  MEDIUM: "#818cf8", // accent
  LOW: "#8b93a8", // muted
};

/** Curriculum match -> badge variant. */
function matchVariant(m: string | null) {
  const v = (m ?? "").toLowerCase();
  if (v.includes("yes") || v.includes("full") || v.includes("strong"))
    return "success" as const;
  if (v.includes("partial")) return "high" as const;
  if (v.includes("no") || v.includes("gap")) return "critical" as const;
  return "outline" as const;
}

export default async function BlogPage() {
  const [blog, byUrgency, byMatch] = await Promise.all([
    getBlog(),
    getBlogByUrgency(),
    getBlogByCurriculumMatch(),
  ]);

  const urgencyData = byUrgency.map((u) => ({ label: u.key, value: u.count }));
  const matchData = byMatch.map((m) => ({ label: m.key, value: m.count }));

  return (
    <div className="space-y-8">
      <SectionHeading
        title="Blog & Content Signals"
        description={`${blog.length} publications in the latest snapshot, with recommended skills and curriculum fit.`}
      />

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <SplitChartCard
          title="Urgency Split"
          description="Share of publications by urgency."
          data={urgencyData}
          colorByLabel={URGENCY_COLORS}
        />
        <SplitChartCard
          title="Curriculum Match Split"
          description="How publications map to IK's curriculum."
          data={matchData}
        />
      </div>

      <section>
        <SectionHeading title="Publications" description="Latest snapshot, newest first." />
        {blog.length === 0 ? (
          <EmptyState message="No blog publications in the latest snapshot." />
        ) : (
          <TableScroll>
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Publication</TableHead>
                  <TableHead>Title</TableHead>
                  <TableHead>Recommended Skill</TableHead>
                  <TableHead>Audience</TableHead>
                  <TableHead>Urgency</TableHead>
                  <TableHead>Curriculum Match</TableHead>
                  <TableHead>Published</TableHead>
                  <TableHead>Link</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {blog.map((b, i) => (
                  <TableRow key={`${b.title}-${i}`}>
                    <TableCell className="whitespace-nowrap font-medium text-foreground">
                      {b.publication ?? "—"}
                    </TableCell>
                    <TableCell className="max-w-72">
                      {b.title ?? "—"}
                      {b.summary ? (
                        <span className="mt-0.5 block text-xs text-muted line-clamp-2">
                          {b.summary}
                        </span>
                      ) : null}
                    </TableCell>
                    <TableCell className="whitespace-nowrap">
                      {b.recommended_skill ? (
                        <Badge variant="info">{b.recommended_skill}</Badge>
                      ) : (
                        "—"
                      )}
                    </TableCell>
                    <TableCell className="whitespace-nowrap">
                      {b.audience ?? "—"}
                    </TableCell>
                    <TableCell>
                      {b.urgency ? (
                        <Badge variant={urgencyVariant(b.urgency)}>
                          {b.urgency}
                        </Badge>
                      ) : (
                        "—"
                      )}
                    </TableCell>
                    <TableCell>
                      {b.curriculum_match ? (
                        <Badge variant={matchVariant(b.curriculum_match)}>
                          {b.curriculum_match}
                        </Badge>
                      ) : (
                        "—"
                      )}
                    </TableCell>
                    <TableCell className="whitespace-nowrap">
                      {formatDate(b.published_date)}
                    </TableCell>
                    <TableCell>
                      <ExtLink href={b.reference_url} label="Read" />
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
