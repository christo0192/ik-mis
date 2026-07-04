"use client";

import * as React from "react";
import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { formatDate } from "@/lib/format";

/** A calm, professional categorical palette (indigo-led) — same family as split-chart-card.tsx. */
const PALETTE = [
  "#4f46e5", // indigo-600
  "#0ea5e9", // sky-500
  "#14b8a6", // teal-500
  "#f59e0b", // amber-500
  "#ec4899", // pink-500
];

export interface SegmentTrendPoint {
  date: string;
  [segment: string]: string | number | null;
}

function TrendTooltip({
  active,
  payload,
  label,
  valuePrefix = "",
}: {
  active?: boolean;
  payload?: Array<{ dataKey: string; value: number | null; color: string }>;
  label?: string;
  valuePrefix?: string;
}) {
  if (!active || !payload || payload.length === 0) return null;
  return (
    <div className="rounded-lg border border-border bg-surface px-3 py-2 text-xs shadow-lg shadow-black/40">
      <p className="mb-1 font-semibold text-foreground">{formatDate(label)}</p>
      {payload.map((p) => (
        <p key={p.dataKey} className="flex items-center gap-1.5 text-muted">
          <span className="h-1.5 w-1.5 rounded-full" style={{ backgroundColor: p.color }} />
          {p.dataKey}:{" "}
          <span className="font-medium text-foreground">
            {p.value === null ? "—" : `${valuePrefix}${p.value.toLocaleString("en-IN")}`}
          </span>
        </p>
      ))}
    </div>
  );
}

export function SegmentTrendChart({
  title,
  description,
  data,
  segments,
  valuePrefix = "",
  height = 280,
}: {
  title: string;
  description?: string;
  data: SegmentTrendPoint[];
  segments: string[];
  valuePrefix?: string;
  height?: number;
}) {
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
          <ResponsiveContainer width="100%" height={height}>
            <LineChart data={data} margin={{ top: 4, right: 12, bottom: 4, left: 0 }}>
              <CartesianGrid vertical={false} stroke="rgba(255,255,255,0.08)" strokeDasharray="3 3" />
              <XAxis
                dataKey="date"
                tickFormatter={(v) => formatDate(v)}
                tick={{ fontSize: 10, fill: "#8b93a8" }}
                axisLine={false}
                tickLine={false}
                minTickGap={24}
              />
              <YAxis tick={{ fontSize: 11, fill: "#8b93a8" }} axisLine={false} tickLine={false} width={44} />
              <Tooltip content={<TrendTooltip valuePrefix={valuePrefix} />} />
              <Legend wrapperStyle={{ fontSize: 11, color: "#8b93a8" }} />
              {segments.map((seg, i) => (
                <Line
                  key={seg}
                  type="monotone"
                  dataKey={seg}
                  stroke={PALETTE[i % PALETTE.length]}
                  strokeWidth={2}
                  dot={false}
                  connectNulls
                  isAnimationActive={false}
                />
              ))}
            </LineChart>
          </ResponsiveContainer>
        )}
      </CardContent>
    </Card>
  );
}
