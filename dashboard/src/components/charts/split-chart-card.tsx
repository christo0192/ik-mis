"use client";

import * as React from "react";
import {
  Cell,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
} from "recharts";

import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";

export interface SplitDatum {
  label: string;
  value: number;
}

interface SplitChartCardProps {
  title: string;
  description?: string;
  data: SplitDatum[];
  height?: number;
  /** Optional explicit color map by label (e.g. urgency -> color). */
  colorByLabel?: Record<string, string>;
}

/* A calm, professional categorical palette (indigo-led). */
const PALETTE = [
  "#4f46e5", // indigo-600
  "#0ea5e9", // sky-500
  "#14b8a6", // teal-500
  "#f59e0b", // amber-500
  "#8b5cf6", // violet-500
  "#ec4899", // pink-500
  "#64748b", // slate-500
  "#10b981", // emerald-500
  "#f43f5e", // rose-500
  "#a16207", // yellow-700
];

function colorFor(
  label: string,
  index: number,
  map?: Record<string, string>,
): string {
  if (map && map[label.toUpperCase()]) return map[label.toUpperCase()];
  if (map && map[label]) return map[label];
  return PALETTE[index % PALETTE.length];
}

function SplitTooltip({
  active,
  payload,
  total,
}: {
  active?: boolean;
  payload?: Array<{ payload: SplitDatum }>;
  total: number;
}) {
  if (!active || !payload || payload.length === 0) return null;
  const d = payload[0].payload;
  const pct = total > 0 ? ((d.value / total) * 100).toFixed(0) : "0";
  return (
    <div className="rounded-lg border border-border bg-surface px-3 py-2 text-xs shadow-lg shadow-black/40">
      <p className="font-semibold text-foreground">{d.label}</p>
      <p className="text-muted">
        <span className="font-medium text-foreground">{d.value}</span> ({pct}%)
      </p>
    </div>
  );
}

export function SplitChartCard({
  title,
  description,
  data,
  height = 280,
  colorByLabel,
}: SplitChartCardProps) {
  const total = data.reduce((sum, d) => sum + d.value, 0);

  return (
    <Card className="h-full">
      <CardHeader>
        <CardTitle>{title}</CardTitle>
        {description ? <CardDescription>{description}</CardDescription> : null}
      </CardHeader>
      <CardContent>
        {data.length === 0 ? (
          <div className="flex h-40 items-center justify-center text-sm text-muted">
            No data available
          </div>
        ) : (
          <div className="flex flex-col items-center gap-4 sm:flex-row sm:items-center">
            <ResponsiveContainer width="100%" height={height} className="sm:!w-1/2">
              <PieChart>
                <Pie
                  data={data}
                  dataKey="value"
                  nameKey="label"
                  innerRadius="55%"
                  outerRadius="85%"
                  paddingAngle={2}
                  stroke="var(--surface)"
                  strokeWidth={2}
                >
                  {data.map((d, i) => (
                    <Cell key={i} fill={colorFor(d.label, i, colorByLabel)} />
                  ))}
                </Pie>
                <Tooltip content={<SplitTooltip total={total} />} />
              </PieChart>
            </ResponsiveContainer>
            <ul className="w-full space-y-1.5 sm:w-1/2">
              {data.map((d, i) => {
                const pct = total > 0 ? Math.round((d.value / total) * 100) : 0;
                return (
                  <li
                    key={`${d.label}-${i}`}
                    className="flex items-center justify-between gap-2 text-xs"
                  >
                    <span className="flex min-w-0 items-center gap-2">
                      <span
                        className="h-2.5 w-2.5 shrink-0 rounded-sm"
                        style={{
                          backgroundColor: colorFor(d.label, i, colorByLabel),
                        }}
                      />
                      <span className="truncate text-muted" title={d.label}>
                        {d.label}
                      </span>
                    </span>
                    <span className="shrink-0 font-medium text-foreground">
                      {d.value}{" "}
                      <span className="font-normal text-muted/70">({pct}%)</span>
                    </span>
                  </li>
                );
              })}
            </ul>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
