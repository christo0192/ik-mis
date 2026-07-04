import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { ChipList, SectionHeading, ExtLink, EmptyState } from "@/components/ui/misc";
import { getCompetitors } from "@/lib/queries";
import { formatINR } from "@/lib/format";

export const dynamic = "force-dynamic";

/** Render ad-intensity (1–5) as filled/empty dots. */
function AdIntensity({ value }: { value: number | null }) {
  if (value === null) return <span className="text-sm text-muted/70">—</span>;
  return (
    <span className="inline-flex items-center gap-1" aria-label={`Ad intensity ${value} of 5`}>
      {[1, 2, 3, 4, 5].map((n) => (
        <span
          key={n}
          className={
            n <= value
              ? "h-2 w-4 rounded-full bg-accent"
              : "h-2 w-4 rounded-full bg-white/10"
          }
        />
      ))}
      <span className="ml-1 text-xs font-medium text-muted">{value}/5</span>
    </span>
  );
}

function MetaItem({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div>
      <p className="text-[11px] font-medium uppercase tracking-wide text-muted">
        {label}
      </p>
      <div className="mt-0.5 text-sm text-foreground">{value}</div>
    </div>
  );
}

export default async function CompetitorPage() {
  const competitors = await getCompetitors();

  return (
    <div className="space-y-6">
      <SectionHeading
        title="Competitor Battlecard"
        description={`${competitors.length} competitors in the latest snapshot. IK advantages vs IK gaps, side by side.`}
      />

      {competitors.length === 0 ? (
        <EmptyState message="No competitor intel in the latest snapshot." />
      ) : (
        <div className="space-y-5">
          {competitors.map((c, i) => (
            <Card key={`${c.competitor}-${i}`}>
              <CardHeader className="border-b border-border">
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div>
                    <CardTitle className="text-base">
                      {c.competitor ?? "Unknown competitor"}
                    </CardTitle>
                    {c.program ? (
                      <p className="mt-0.5 text-sm text-muted">{c.program}</p>
                    ) : null}
                  </div>
                  <AdIntensity value={c.ad_intensity} />
                </div>
              </CardHeader>

              <CardContent className="space-y-5 pt-4">
                {/* Metadata row */}
                <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
                  <MetaItem
                    label="Price"
                    value={c.price_inr !== null ? formatINR(c.price_inr) : "Not disclosed"}
                  />
                  <MetaItem
                    label="Duration"
                    value={
                      c.duration_months !== null
                        ? `${c.duration_months} mo`
                        : "—"
                    }
                  />
                  <MetaItem label="Mentor Quality" value={c.mentor_quality ?? "—"} />
                  <MetaItem
                    label="Certifications"
                    value={c.certifications ?? "—"}
                  />
                </div>

                {c.curriculum_overview ? (
                  <div>
                    <p className="text-[11px] font-medium uppercase tracking-wide text-muted">
                      Curriculum Overview
                    </p>
                    <p className="mt-1 text-sm leading-relaxed text-foreground/80">
                      {c.curriculum_overview}
                    </p>
                  </div>
                ) : null}

                <div>
                  <p className="mb-1.5 text-[11px] font-medium uppercase tracking-wide text-muted">
                    Key Tools
                  </p>
                  <ChipList items={c.key_tools} emptyText="Not specified" />
                </div>

                {/* IK advantages vs gaps — two columns */}
                <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
                  <div className="rounded-lg border border-success/30 bg-success/10 p-3">
                    <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-success">
                      ✓ IK Advantages
                    </p>
                    {c.ik_advantages.length === 0 ? (
                      <p className="text-xs text-muted/70">—</p>
                    ) : (
                      <ul className="space-y-1.5">
                        {c.ik_advantages.map((adv, j) => (
                          <li
                            key={j}
                            className="flex gap-2 text-sm leading-snug text-foreground/80"
                          >
                            <span className="mt-1 h-1.5 w-1.5 shrink-0 rounded-full bg-success/100" />
                            {adv}
                          </li>
                        ))}
                      </ul>
                    )}
                  </div>

                  <div className="rounded-lg border border-danger/30 bg-danger/10 p-3">
                    <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-danger">
                      ✕ IK Gaps
                    </p>
                    {c.ik_gaps.length === 0 ? (
                      <p className="text-xs text-muted/70">—</p>
                    ) : (
                      <ul className="space-y-1.5">
                        {c.ik_gaps.map((gap, j) => (
                          <li
                            key={j}
                            className="flex gap-2 text-sm leading-snug text-foreground/80"
                          >
                            <span className="mt-1 h-1.5 w-1.5 shrink-0 rounded-full bg-danger" />
                            {gap}
                          </li>
                        ))}
                      </ul>
                    )}
                  </div>
                </div>

                {c.curriculum_gaps_vs_ik.length > 0 ? (
                  <div>
                    <p className="mb-1.5 text-[11px] font-medium uppercase tracking-wide text-muted">
                      Their Curriculum Gaps vs IK
                    </p>
                    <ChipList items={c.curriculum_gaps_vs_ik} />
                  </div>
                ) : null}

                {c.reference_url ? (
                  <div className="pt-1">
                    <ExtLink href={c.reference_url} label="Source" />
                  </div>
                ) : null}
              </CardContent>
            </Card>
          ))}
        </div>
      )}
    </div>
  );
}
