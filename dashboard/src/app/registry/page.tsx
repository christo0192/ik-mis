import Link from "next/link";

import { SectionHeading, EmptyState, KpiCard } from "@/components/ui/misc";
import { UnavailableBanner } from "@/components/ui/unavailable-banner";
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
import { getCampaignRegistry, getUnregisteredCampaigns } from "@/lib/ce-queries";
import { formatDate } from "@/lib/format";
import { cn } from "@/lib/format";

export const dynamic = "force-dynamic";

export default async function RegistryPage({
  searchParams,
}: {
  searchParams: Promise<{ needsAttention?: string; program?: string; unregistered?: string }>;
}) {
  const sp = await searchParams;
  const needsAttentionOnly = sp.needsAttention === "1";
  const program = sp.program;
  const showUnregistered = sp.unregistered === "1";

  const [registry, unregistered] = await Promise.all([
    getCampaignRegistry({ needsAttentionOnly, program }),
    getUnregisteredCampaigns(),
  ]);

  // Program bucket breakdown, computed client-side from the full (unfiltered
  // by program) needs-attention-respecting snapshot — cheap, the registry is
  // O(hundreds) of rows.
  const allForBreakdown = program
    ? await getCampaignRegistry({ needsAttentionOnly })
    : registry;
  const programCounts = new Map<string, number>();
  for (const row of allForBreakdown.rows) {
    const key = row.program?.trim() || "(none)";
    programCounts.set(key, (programCounts.get(key) ?? 0) + 1);
  }
  const programEntries = [...programCounts.entries()].sort((a, b) => b[1] - a[1]);

  const needsAttentionCount = allForBreakdown.rows.filter((r) => r.needs_attention).length;

  function hrefWith(overrides: Record<string, string | undefined>) {
    const params = new URLSearchParams();
    const merged = {
      needsAttention: needsAttentionOnly ? "1" : undefined,
      program,
      unregistered: showUnregistered ? "1" : undefined,
      ...overrides,
    };
    for (const [k, v] of Object.entries(merged)) {
      if (v) params.set(k, v);
    }
    const qs = params.toString();
    return qs ? `/registry?${qs}` : "/registry";
  }

  return (
    <div className="space-y-6">
      <SectionHeading
        title="Registry & Attribution"
        description="campaign_registry browser — the mapping every generated asset must resolve through before it can target a campaign."
      />

      {unregistered.error ? (
        <UnavailableBanner label="unregistered-campaigns check" error={unregistered.error} />
      ) : unregistered.rows.length > 0 ? (
        <div className="rounded-xl border border-warning/30 bg-warning/5 p-4">
          <p className="text-sm font-medium text-warning">
            {unregistered.rows.length} unregistered campaign
            {unregistered.rows.length === 1 ? "" : "s"} — attribution degraded
          </p>
          <p className="mt-1 text-xs leading-relaxed text-muted">
            These campaigns appear in the latest generated content but have no row in
            campaign_registry. Warn-first enforcement (docs/campaign-naming-convention.md):
            processed, but classified &quot;Other/unknown&quot; until registered.
          </p>
          <Link
            href={hrefWith({ unregistered: showUnregistered ? undefined : "1" })}
            className="mt-2 inline-block text-xs font-medium text-warning hover:underline"
          >
            {showUnregistered ? "Hide list" : "View unregistered campaigns"}
          </Link>
        </div>
      ) : null}

      {showUnregistered ? (
        unregistered.rows.length === 0 ? (
          <EmptyState message="No unregistered campaigns." />
        ) : (
          <TableScroll>
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Campaign</TableHead>
                  <TableHead>Segment (as recorded)</TableHead>
                  <TableHead>Detail</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {unregistered.rows.map((u) => (
                  <TableRow key={u.campaign_name}>
                    <TableCell className="font-medium text-foreground">{u.campaign_name}</TableCell>
                    <TableCell>{u.segment ?? "—"}</TableCell>
                    <TableCell>
                      <Link
                        href={`/generation/${encodeURIComponent(u.segment ?? "Unknown")}/${encodeURIComponent(u.campaign_name)}`}
                        className="text-accent hover:underline"
                      >
                        View assets
                      </Link>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </TableScroll>
        )
      ) : (
        <>
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
            <KpiCard label="Registered Campaigns" value={allForBreakdown.rows.length.toLocaleString("en-IN")} />
            <KpiCard
              label="Needs Attention"
              value={needsAttentionCount.toLocaleString("en-IN")}
              accent={needsAttentionCount > 0 ? "warning" : "default"}
              hint={"other program or unparseable segment"}
            />
            <KpiCard
              label="Unregistered"
              value={unregistered.rows.length.toLocaleString("en-IN")}
              accent={unregistered.rows.length > 0 ? "danger" : "default"}
            />
            <KpiCard label="Program Buckets" value={programEntries.length.toLocaleString("en-IN")} />
          </div>

          <div className="flex flex-wrap items-center gap-2">
            <Link
              href={hrefWith({ needsAttention: needsAttentionOnly ? undefined : "1" })}
              className={cn(
                "rounded-full border px-3 py-1 text-xs font-medium transition-colors",
                needsAttentionOnly
                  ? "border-accent bg-accent/15 text-accent"
                  : "border-border text-muted hover:text-foreground",
              )}
            >
              {needsAttentionOnly ? "Showing: needs attention only" : "Filter: needs attention only"}
            </Link>
            <span className="text-muted/40">·</span>
            {programEntries.map(([p, count]) => (
              <Link
                key={p}
                href={hrefWith({ program: program === p ? undefined : p })}
                className={cn(
                  "inline-flex items-center gap-1.5 rounded-full border px-3 py-1 text-xs font-medium transition-colors",
                  program === p
                    ? "border-accent bg-accent/15 text-accent"
                    : "border-border text-muted hover:text-foreground",
                )}
              >
                {p}
                <span className="rounded-full bg-white/5 px-1.5 text-[10px]">{count}</span>
              </Link>
            ))}
          </div>

          {registry.error ? (
            <UnavailableBanner label="ce_campaign_registry" error={registry.error} />
          ) : registry.rows.length === 0 ? (
            <EmptyState message="No campaign_registry rows match this filter." />
          ) : (
            <TableScroll>
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Campaign</TableHead>
                    <TableHead>Platform</TableHead>
                    <TableHead>Program</TableHead>
                    <TableHead>Segment</TableHead>
                    <TableHead>Funnel</TableHead>
                    <TableHead>Status</TableHead>
                    <TableHead>Snapshot</TableHead>
                    <TableHead>Detail</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {registry.rows.map((r) => (
                    <TableRow key={r.campaign_name}>
                      <TableCell className="max-w-72">
                        <span className="font-medium text-foreground">{r.campaign_name}</span>
                        {r.needs_attention ? (
                          <Badge variant="high" className="ml-2 align-middle">
                            Needs attention
                          </Badge>
                        ) : null}
                      </TableCell>
                      <TableCell className="whitespace-nowrap">{r.platform ?? "—"}</TableCell>
                      <TableCell className="whitespace-nowrap">{r.program ?? "—"}</TableCell>
                      <TableCell className="whitespace-nowrap">{r.segment ?? "—"}</TableCell>
                      <TableCell className="whitespace-nowrap">{r.funnel_stage ?? "—"}</TableCell>
                      <TableCell>
                        <Badge variant={r.status === "active" ? "success" : "outline"}>
                          {r.status ?? "—"}
                        </Badge>
                      </TableCell>
                      <TableCell className="whitespace-nowrap">{formatDate(r.snapshot_date)}</TableCell>
                      <TableCell>
                        <Link
                          href={`/generation/${encodeURIComponent(r.segment ?? "Unknown")}/${encodeURIComponent(r.campaign_name)}`}
                          className="text-accent hover:underline"
                        >
                          View assets
                        </Link>
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </TableScroll>
          )}
        </>
      )}
    </div>
  );
}
