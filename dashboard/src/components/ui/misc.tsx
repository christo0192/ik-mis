import * as React from "react";

import { cn } from "@/lib/format";

/** External link that always opens in a new tab with safe rel attributes. */
export function ExtLink({
  href,
  children,
  className,
  label = "Open",
}: {
  href: string | null | undefined;
  children?: React.ReactNode;
  className?: string;
  label?: string;
}) {
  if (!href) return <span className="text-slate-400">—</span>;
  return (
    <a
      href={href}
      target="_blank"
      rel="noopener noreferrer"
      className={cn(
        "inline-flex items-center gap-1 font-medium text-accent hover:underline",
        className,
      )}
    >
      {children ?? label}
      <svg
        aria-hidden="true"
        viewBox="0 0 24 24"
        className="h-3 w-3"
        fill="none"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
      >
        <path d="M7 17 17 7" />
        <path d="M7 7h10v10" />
      </svg>
    </a>
  );
}

/** Page-level section heading with optional description. */
export function SectionHeading({
  title,
  description,
  className,
}: {
  title: string;
  description?: string;
  className?: string;
}) {
  return (
    <div className={cn("mb-4", className)}>
      <h2 className="text-base font-semibold tracking-tight text-foreground">
        {title}
      </h2>
      {description ? (
        <p className="mt-0.5 text-sm text-muted">{description}</p>
      ) : null}
    </div>
  );
}

/** Centered empty-state for when a query returns no rows. */
export function EmptyState({ message }: { message: string }) {
  return (
    <div className="flex min-h-32 items-center justify-center rounded-xl border border-dashed border-border bg-surface p-8 text-center text-sm text-muted">
      {message}
    </div>
  );
}

/** Compact KPI / metric card with tabular figures and an optional accent. */
export function KpiCard({
  label,
  value,
  hint,
  accent = "default",
}: {
  label: string;
  value: React.ReactNode;
  hint?: string;
  accent?: "default" | "emerald" | "indigo" | "warning" | "danger";
}) {
  const valueColor =
    accent === "emerald"
      ? "text-success"
      : accent === "indigo"
        ? "text-accent"
        : accent === "warning"
          ? "text-warning"
          : accent === "danger"
            ? "text-danger"
            : "text-foreground";
  return (
    <div className="rounded-xl border border-border bg-surface p-4 shadow-sm transition-shadow hover:border-white/20">
      <p className="text-[11px] font-medium uppercase tracking-wide text-muted">
        {label}
      </p>
      <p
        className={cn(
          "mt-1 text-2xl font-semibold tracking-tight tabular-nums",
          valueColor,
        )}
      >
        {value}
      </p>
      {hint ? <p className="mt-0.5 text-[11px] text-muted">{hint}</p> : null}
    </div>
  );
}

/** Chip list for short string arrays (skills, tools, etc.). */
export function ChipList({
  items,
  emptyText = "—",
  className,
}: {
  items: string[];
  emptyText?: string;
  className?: string;
}) {
  if (!items || items.length === 0) {
    return <span className="text-sm text-muted/70">{emptyText}</span>;
  }
  return (
    <div className={cn("flex flex-wrap gap-1.5", className)}>
      {items.map((item, i) => (
        <span
          key={`${item}-${i}`}
          className="inline-flex items-center rounded-md bg-white/5 px-2 py-0.5 text-xs font-medium text-foreground/70"
        >
          {item}
        </span>
      ))}
    </div>
  );
}
