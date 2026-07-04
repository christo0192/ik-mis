import * as React from "react";

import { cn } from "@/lib/format";

type BadgeVariant =
  | "default"
  | "outline"
  | "critical"
  | "high"
  | "medium"
  | "low"
  | "success"
  | "info"
  | "accent";

const VARIANTS: Record<BadgeVariant, string> = {
  default: "bg-white/5 text-foreground/80 border-white/10",
  outline: "bg-transparent text-muted border-border",
  critical: "bg-danger/15 text-danger border-danger/30",
  high: "bg-warning/15 text-warning border-warning/30",
  medium: "bg-white/5 text-muted border-white/10",
  low: "bg-white/5 text-muted/80 border-white/10",
  success: "bg-success/15 text-success border-success/30",
  info: "bg-info/15 text-info border-info/30",
  accent: "bg-accent/15 text-accent border-accent/30",
};

export interface BadgeProps extends React.HTMLAttributes<HTMLSpanElement> {
  variant?: BadgeVariant;
}

export function Badge({ className, variant = "default", ...props }: BadgeProps) {
  return (
    <span
      className={cn(
        "inline-flex items-center rounded-md border px-2 py-0.5 text-xs font-medium whitespace-nowrap",
        VARIANTS[variant],
        className,
      )}
      {...props}
    />
  );
}

/** Map an urgency string to a badge variant (CRITICAL=red, HIGH=amber, else slate). */
export function urgencyVariant(urgency: string | null | undefined): BadgeVariant {
  switch ((urgency ?? "").toUpperCase()) {
    case "CRITICAL":
      return "critical";
    case "HIGH":
      return "high";
    case "MEDIUM":
      return "medium";
    default:
      return "low";
  }
}

/** Map a review-queue / asset status to a badge variant. */
export function statusVariant(status: string | null | undefined): BadgeVariant {
  switch ((status ?? "").toLowerCase()) {
    case "approved":
    case "launched":
    case "active":
      return "success";
    case "rejected":
    case "archived":
    case "failed":
      return "critical";
    case "needs_review":
    case "borderline":
    case "partial":
    case "paused":
      return "high";
    case "proposed":
      return "info";
    default:
      return "outline";
  }
}

/** Map a relevance verdict to a badge variant. */
export function verdictVariant(verdict: string | null | undefined): BadgeVariant {
  switch ((verdict ?? "").toLowerCase()) {
    case "relevant":
      return "success";
    case "borderline":
      return "high";
    case "irrelevant":
      return "critical";
    default:
      return "outline";
  }
}
