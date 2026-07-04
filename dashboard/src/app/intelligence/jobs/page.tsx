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
import { SectionHeading, ExtLink, EmptyState, KpiCard } from "@/components/ui/misc";
import {
  getJobs,
  getJobsByFamily,
  getJobsByRole,
  getJobsByTier,
  getJobStats,
  getTopSkills,
} from "@/lib/queries";
import { formatDate, formatSalaryRange } from "@/lib/format";

export const dynamic = "force-dynamic";

export default async function JobsPage() {
  const [jobs, stats, byRole, byFamily, byTier, topSkills] = await Promise.all([
    getJobs(),
    getJobStats(),
    getJobsByRole(),
    getJobsByFamily(),
    getJobsByTier(),
    getTopSkills(15),
  ]);

  const emergingPct =
    stats.total > 0 ? Math.round((stats.emerging / stats.total) * 100) : 0;

  const roleData = byRole.slice(0, 14).map((r) => ({
    label: r.normalized_role,
    value: r.count,
    secondary: r.emerging_count,
  }));
  const skillData = topSkills.map((s) => ({ label: s.key, value: s.count }));
  const familyData = byFamily.map((f) => ({ label: f.key, value: f.count }));
  const tierData = byTier.map((t) => ({ label: t.key, value: t.count }));

  return (
    <div className="space-y-8">
      <SectionHeading
        title="Job Market Demand"
        description={`${jobs.length} postings in the latest snapshot. Bars show total demand; darker = emerging-heavy roles.`}
      />

      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <KpiCard label="Total Postings" value={stats.total.toLocaleString("en-IN")} />
        <KpiCard label="Distinct Roles" value={stats.role_count.toLocaleString("en-IN")} />
        <KpiCard
          label="Emerging"
          value={`${emergingPct}%`}
          hint={`${stats.emerging.toLocaleString("en-IN")} postings`}
          accent="emerald"
        />
        <KpiCard
          label="In Window"
          value={stats.in_window.toLocaleString("en-IN")}
          hint="within tracking window"
        />
      </div>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <BarChartCard
          title="Demand by Role"
          description="Postings per normalized role (top 14)."
          data={roleData}
          layout="horizontal"
          valueName="Postings"
          secondaryName="Emerging"
          highlightSecondary
          height={Math.max(280, roleData.length * 26)}
        />
        <BarChartCard
          title="Top Skills in Demand"
          description="Skill mentions across postings (UNNEST, top 15)."
          data={skillData}
          layout="horizontal"
          color="#0ea5e9"
          valueName="Mentions"
          height={Math.max(280, skillData.length * 26)}
        />
        <SplitChartCard
          title="Role Family Split"
          description="Share of postings by role family."
          data={familyData}
        />
        <SplitChartCard
          title="Company Tier Split"
          description="Share of postings by company tier."
          data={tierData}
        />
      </div>

      <section>
        <SectionHeading title="Postings" description="Latest snapshot, ranked by applicant interest." />
        {jobs.length === 0 ? (
          <EmptyState message="No job postings in the latest snapshot." />
        ) : (
          <TableScroll>
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Title</TableHead>
                  <TableHead>Company</TableHead>
                  <TableHead>Tier</TableHead>
                  <TableHead>Seniority</TableHead>
                  <TableHead>Type</TableHead>
                  <TableHead>Salary (INR)</TableHead>
                  <TableHead>Posted</TableHead>
                  <TableHead>Link</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {jobs.map((j, i) => (
                  <TableRow key={`${j.title}-${j.company}-${i}`}>
                    <TableCell className="max-w-72">
                      <span className="font-medium text-foreground">
                        {j.title ?? "—"}
                      </span>
                      {j.is_emerging ? (
                        <Badge variant="success" className="ml-2 align-middle">
                          Emerging
                        </Badge>
                      ) : null}
                      {j.normalized_role ? (
                        <span className="mt-0.5 block text-xs text-muted">
                          {j.normalized_role}
                        </span>
                      ) : null}
                    </TableCell>
                    <TableCell>{j.company ?? "—"}</TableCell>
                    <TableCell>
                      <Badge variant="outline">{j.company_tier ?? "—"}</Badge>
                    </TableCell>
                    <TableCell className="whitespace-nowrap">
                      {j.seniority_level ?? "—"}
                    </TableCell>
                    <TableCell className="whitespace-nowrap">
                      {j.employment_type ?? "—"}
                    </TableCell>
                    <TableCell className="whitespace-nowrap">
                      {formatSalaryRange(j.salary_min_inr, j.salary_max_inr)}
                    </TableCell>
                    <TableCell className="whitespace-nowrap">
                      {formatDate(j.posted_date)}
                    </TableCell>
                    <TableCell>
                      <ExtLink href={j.reference_url} label="View" />
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
