"use client";

import Link from "next/link";
import { usePathname, useSearchParams } from "next/navigation";

import { cn } from "@/lib/format";

/**
 * URL-addressable status filter chips (e.g. `/generation?status=needs_review`)
 * per docs/dashboard-vision.md §4 — every filter resolves to a linkable URL,
 * never client-only state.
 */
export function StatusFilterChips({
  options,
  paramName = "status",
  allLabel = "All",
}: {
  options: Array<{ value: string; label: string }>;
  paramName?: string;
  allLabel?: string;
}) {
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const current = searchParams.get(paramName) ?? "";

  function hrefFor(value: string) {
    const params = new URLSearchParams(searchParams.toString());
    if (value) params.set(paramName, value);
    else params.delete(paramName);
    const qs = params.toString();
    return qs ? `${pathname}?${qs}` : pathname;
  }

  function chipClass(active: boolean) {
    return cn(
      "rounded-full border px-3 py-1 text-xs font-medium transition-colors",
      active
        ? "border-accent bg-accent/15 text-accent"
        : "border-border text-muted hover:border-white/20 hover:text-foreground",
    );
  }

  return (
    <div className="flex flex-wrap gap-1.5">
      <Link href={hrefFor("")} className={chipClass(current === "")}>
        {allLabel}
      </Link>
      {options.map((opt) => (
        <Link
          key={opt.value}
          href={hrefFor(opt.value)}
          className={chipClass(current === opt.value)}
        >
          {opt.label}
        </Link>
      ))}
    </div>
  );
}
