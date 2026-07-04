"use client";

import * as React from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  LabelList,
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

export interface BarDatum {
  /** Category label shown on the axis. */
  label: string;
  /** Primary numeric value. */
  value: number;
  /** Optional secondary value (e.g. emerging count) rendered as a highlight. */
  secondary?: number;
}

interface BarChartCardProps {
  title: string;
  description?: string;
  data: BarDatum[];
  /** "horizontal" = bars grow rightward with category labels on the Y axis. */
  layout?: "horizontal" | "vertical";
  /** Bar fill color. */
  color?: string;
  /** Label for the primary series in the tooltip. */
  valueName?: string;
  /** Label for the secondary series in the tooltip. */
  secondaryName?: string;
  /**
   * When true, `secondary` is treated as a sub-count of `value` and bars where
   * the sub-count dominates are drawn in the strong color (others muted).
   * Leave false when `secondary` is an unrelated metric (e.g. an average).
   */
  highlightSecondary?: boolean;
  height?: number;
}

const ACCENT = "#4f46e5"; // indigo-600
const ACCENT_SOFT = "#a5b4fc"; // indigo-300

function ChartTooltip({
  active,
  payload,
  valueName,
  secondaryName,
}: {
  active?: boolean;
  payload?: Array<{ payload: BarDatum }>;
  valueName: string;
  secondaryName?: string;
}) {
  if (!active || !payload || payload.length === 0) return null;
  const d = payload[0].payload;
  return (
    <div className="rounded-lg border border-border bg-surface px-3 py-2 text-xs shadow-lg shadow-black/40">
      <p className="mb-1 font-semibold text-foreground">{d.label}</p>
      <p className="text-muted">
        {valueName}: <span className="font-medium text-foreground">{d.value}</span>
      </p>
      {secondaryName !== undefined && d.secondary !== undefined ? (
        <p className="text-muted">
          {secondaryName}:{" "}
          <span className="font-medium text-accent">{d.secondary}</span>
        </p>
      ) : null}
    </div>
  );
}

export function BarChartCard({
  title,
  description,
  data,
  layout = "horizontal",
  color = ACCENT,
  valueName = "Count",
  secondaryName,
  highlightSecondary = false,
  height = 320,
}: BarChartCardProps) {
  const isHorizontal = layout === "horizontal";

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
            {isHorizontal ? (
              <BarChart
                data={data}
                layout="vertical"
                margin={{ top: 4, right: 32, bottom: 4, left: 8 }}
              >
                <CartesianGrid
                  horizontal={false}
                  stroke="rgba(255,255,255,0.08)"
                  strokeDasharray="3 3"
                />
                <XAxis
                  type="number"
                  tick={{ fontSize: 11, fill: "#8b93a8" }}
                  axisLine={false}
                  tickLine={false}
                />
                <YAxis
                  type="category"
                  dataKey="label"
                  width={140}
                  tick={{ fontSize: 11, fill: "#c3c9d6" }}
                  axisLine={false}
                  tickLine={false}
                />
                <Tooltip
                  cursor={{ fill: "rgba(79,70,229,0.06)" }}
                  content={
                    <ChartTooltip
                      valueName={valueName}
                      secondaryName={secondaryName}
                    />
                  }
                />
                <Bar dataKey="value" radius={[0, 4, 4, 0]} fill={color}>
                  <LabelList
                    dataKey="value"
                    position="right"
                    className="fill-[#8b93a8]"
                    style={{ fontSize: 11 }}
                  />
                  {highlightSecondary
                    ? data.map((d, i) => (
                        <Cell
                          key={i}
                          fill={
                            (d.secondary ?? 0) >= d.value * 0.5 ? color : ACCENT_SOFT
                          }
                        />
                      ))
                    : null}
                </Bar>
              </BarChart>
            ) : (
              <BarChart
                data={data}
                margin={{ top: 16, right: 8, bottom: 4, left: 8 }}
              >
                <CartesianGrid
                  vertical={false}
                  stroke="rgba(255,255,255,0.08)"
                  strokeDasharray="3 3"
                />
                <XAxis
                  dataKey="label"
                  tick={{ fontSize: 11, fill: "#c3c9d6" }}
                  axisLine={false}
                  tickLine={false}
                  interval={0}
                  angle={data.length > 6 ? -25 : 0}
                  textAnchor={data.length > 6 ? "end" : "middle"}
                  height={data.length > 6 ? 60 : 30}
                />
                <YAxis
                  tick={{ fontSize: 11, fill: "#8b93a8" }}
                  axisLine={false}
                  tickLine={false}
                  allowDecimals={false}
                />
                <Tooltip
                  cursor={{ fill: "rgba(79,70,229,0.06)" }}
                  content={
                    <ChartTooltip
                      valueName={valueName}
                      secondaryName={secondaryName}
                    />
                  }
                />
                <Bar dataKey="value" radius={[4, 4, 0, 0]} fill={color}>
                  <LabelList
                    dataKey="value"
                    position="top"
                    className="fill-[#8b93a8]"
                    style={{ fontSize: 11 }}
                  />
                </Bar>
              </BarChart>
            )}
          </ResponsiveContainer>
        )}
      </CardContent>
    </Card>
  );
}
