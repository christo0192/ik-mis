"use client";

import * as React from "react";
import { useRouter } from "next/navigation";

import { cn } from "@/lib/format";

export type ReviewTable = "keyword_candidates" | "ad_copy_drafts" | "audience_angles";

/**
 * Approve/Reject controls for one review-queue row. Posts to the dashboard's
 * only write route (src/app/api/generation/review/route.ts), then refreshes
 * the server component tree so the row reflects the new status.
 */
export function ApproveRejectButtons({
  table,
  where,
  status,
}: {
  table: ReviewTable;
  where: Record<string, string>;
  status: string | null;
}) {
  const router = useRouter();
  const [busy, setBusy] = React.useState<"approved" | "rejected" | null>(null);
  const [error, setError] = React.useState<string | null>(null);

  async function act(next: "approved" | "rejected") {
    setError(null);
    setBusy(next);
    try {
      const res = await fetch("/api/generation/review", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ table, status: next, where }),
      });
      const data = (await res.json()) as { ok: boolean; error?: string };
      if (!res.ok || !data.ok) {
        throw new Error(data.error ?? `Request failed (${res.status})`);
      }
      router.refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(null);
    }
  }

  const isApproved = status === "approved";
  const isRejected = status === "rejected";

  return (
    <div className="flex flex-col items-start gap-1">
      <div className="flex items-center gap-1.5">
        <button
          type="button"
          disabled={busy !== null || isApproved}
          onClick={() => act("approved")}
          className={cn(
            "rounded-md border px-2 py-1 text-xs font-medium transition-colors disabled:cursor-not-allowed",
            isApproved
              ? "border-success/40 bg-success/15 text-success opacity-70"
              : "border-success/30 bg-success/10 text-success hover:bg-success/20",
          )}
        >
          {busy === "approved" ? "…" : isApproved ? "Approved" : "Approve"}
        </button>
        <button
          type="button"
          disabled={busy !== null || isRejected}
          onClick={() => act("rejected")}
          className={cn(
            "rounded-md border px-2 py-1 text-xs font-medium transition-colors disabled:cursor-not-allowed",
            isRejected
              ? "border-danger/40 bg-danger/15 text-danger opacity-70"
              : "border-danger/30 bg-danger/10 text-danger hover:bg-danger/20",
          )}
        >
          {busy === "rejected" ? "…" : isRejected ? "Rejected" : "Reject"}
        </button>
      </div>
      {error ? (
        <span className="max-w-56 text-[11px] leading-snug text-danger">{error}</span>
      ) : null}
    </div>
  );
}
