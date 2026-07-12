"use client";

import Image from "next/image";
import Link from "next/link";
import { Activity, BrainCircuit, Cpu, Orbit, ShieldCheck, Sparkles, Zap } from "lucide-react";

import { cn } from "@/lib/format";
import type { DeptBot, GraphEdge, GraphNode } from "@/lib/graph";

const tone = {
  healthy: { dot: "bg-success", ring: "border-success/50", text: "text-success", glow: "shadow-[0_0_20px_rgba(52,211,153,.28)]" },
  stale: { dot: "bg-warning", ring: "border-warning/50", text: "text-warning", glow: "shadow-[0_0_20px_rgba(251,191,36,.22)]" },
  failed: { dot: "bg-danger", ring: "border-danger/50", text: "text-danger", glow: "shadow-[0_0_20px_rgba(251,113,133,.25)]" },
  unknown: { dot: "bg-muted", ring: "border-white/20", text: "text-muted", glow: "" },
};

function HealthMarker({ node, compact = false }: { node: GraphNode; compact?: boolean }) {
  const colors = tone[node.health];
  const Icon = node.dept === "intelligence" ? BrainCircuit : node.dept === "performance" ? Activity : Sparkles;
  return (
    <Link prefetch href={node.href} aria-label={`${node.label}: ${node.health}. ${node.detail}`} className="group block min-w-0">
      <div className={cn("health-marker relative overflow-hidden rounded-2xl border bg-black/65 backdrop-blur-md transition duration-300 hover:-translate-y-0.5 hover:scale-[1.015] hover:bg-black/85", compact ? "p-2.5" : "p-3", colors.ring, colors.glow)}>
        <span className={cn("health-beacon absolute right-2.5 top-2.5 h-2 w-2 rounded-full", colors.dot)} />
        <div className="flex min-w-0 items-center gap-2 pr-3"><span className="shrink-0 rounded-lg border border-white/10 bg-white/5 p-1.5 text-violet-200"><Icon className="h-3.5 w-3.5" /></span><div className="min-w-0"><p className="truncate text-xs font-semibold text-white/90">{node.label}</p><p className={cn("text-[9px] font-semibold uppercase tracking-wider", colors.text)}>{node.health}</p></div></div>
        {!compact ? <p className="mt-2 line-clamp-2 text-[10px] leading-relaxed text-muted">{node.detail}</p> : null}
      </div>
    </Link>
  );
}

export function SystemGraph({ nodes }: { nodes: GraphNode[]; edges: GraphEdge[]; bots: DeptBot[] }) {
  const healthy = nodes.filter((node) => node.health === "healthy").length;

  return (
    <div className="diagnostic-grid neural-chamber relative isolate min-h-[940px] overflow-hidden rounded-[28px] border border-violet-200/20 bg-[#04040c] shadow-[0_0_0_1px_rgba(139,92,246,.08),0_35px_100px_rgba(0,0,0,.6),0_0_90px_rgba(109,40,217,.12)] sm:min-h-[860px] lg:min-h-[780px]">
      <div className="absolute inset-x-0 top-0 z-20 flex items-center justify-between border-b border-white/8 bg-black/20 px-5 py-4 backdrop-blur-xl sm:px-7">
        <div className="flex items-center gap-3">
          <span className="neural-core relative flex h-9 w-9 items-center justify-center rounded-xl border border-violet-200/50 bg-violet-400/20 text-white shadow-[0_0_28px_rgba(167,139,250,.5)]"><Cpu className="h-4 w-4" /><span className="absolute inset-0 animate-ping rounded-xl border border-violet-300/30 motion-reduce:hidden" /></span>
          <div><p className="text-xs font-semibold uppercase tracking-[.2em] text-white/90">MIS Neural Core</p><p className="text-[11px] text-muted">Interactive pipeline diagnostics</p></div>
        </div>
        <div className="hidden items-center gap-2 rounded-full border border-success/25 bg-success/8 px-3 py-1.5 text-xs text-success sm:flex"><Activity className="h-3.5 w-3.5" />{healthy}/{nodes.length} systems nominal</div>
      </div>

      <div className="ai-stars absolute inset-0 opacity-80" />
      <div className="absolute left-1/2 top-[73%] h-[42%] aspect-square -translate-x-1/2 -translate-y-1/2 rounded-full border border-cyan-200/10 shadow-[0_0_80px_rgba(34,211,238,.11),inset_0_0_70px_rgba(139,92,246,.12)] sm:top-[70%] sm:h-[48%] lg:top-[53%] lg:h-[65%]" />
      <div className="energy-ring energy-ring-one absolute left-1/2 top-[73%] h-[36%] aspect-square -translate-x-1/2 -translate-y-1/2 rounded-full border border-violet-300/20 sm:top-[70%] sm:h-[42%] lg:top-[53%] lg:h-[55%]" />
      <div className="energy-ring energy-ring-two absolute left-1/2 top-[73%] h-[30%] aspect-square -translate-x-1/2 -translate-y-1/2 rounded-full border border-cyan-300/15 sm:top-[70%] sm:h-[34%] lg:top-[53%] lg:h-[45%]" />
      <svg aria-hidden="true" className="pointer-events-none absolute inset-0 z-[8] hidden h-full w-full md:block" viewBox="0 0 1200 780" preserveAspectRatio="none">
        <defs><linearGradient id="neuralFlow" x1="0" x2="1"><stop stopColor="#22d3ee" stopOpacity=".08"/><stop offset=".5" stopColor="#c4b5fd" stopOpacity=".9"/><stop offset="1" stopColor="#22d3ee" stopOpacity=".08"/></linearGradient><filter id="flowGlow"><feGaussianBlur stdDeviation="3" result="blur"/><feMerge><feMergeNode in="blur"/><feMergeNode in="SourceGraphic"/></feMerge></filter></defs>
        {[140, 280, 440, 600].map((y, index) => <path key={`l-${y}`} d={`M 80 ${y} C 280 ${y}, 360 ${250 + index * 70}, 520 ${340 + index * 20}`} className="telemetry-path" stroke="url(#neuralFlow)" filter="url(#flowGlow)" />)}
        {[160, 300, 460, 620].map((y, index) => <path key={`r-${y}`} d={`M 1120 ${y} C 920 ${y}, 840 ${260 + index * 70}, 680 ${350 + index * 18}`} className="telemetry-path telemetry-path-delayed" stroke="url(#neuralFlow)" filter="url(#flowGlow)" />)}
      </svg>
      <div className="absolute left-1/2 top-[73%] h-[46%] w-[82%] -translate-x-1/2 -translate-y-1/2 rounded-[50%] bg-violet-500/20 blur-3xl sm:top-[70%] sm:h-[54%] lg:top-[54%] lg:h-[72%] lg:w-[58%]" />
      <div className="robot-merge absolute left-1/2 top-[73%] z-10 h-[48%] w-[min(82%,390px)] -translate-x-1/2 -translate-y-1/2 sm:top-[70%] sm:h-[56%] lg:top-[53%] lg:h-[76%] lg:w-[min(42%,470px)]">
        <Image src="/visuals/mis-diagnostic-robot.webp" alt="MIS diagnostic robot representing live system health" fill priority sizes="(max-width: 768px) 78vw, 510px" className="robot-avatar object-contain drop-shadow-[0_0_26px_rgba(139,92,246,.45)]" />
      </div>

      <div className="absolute left-5 top-24 z-20 hidden items-center gap-2 text-[9px] uppercase tracking-[.18em] text-cyan-200/70 sm:flex"><Zap className="h-3 w-3" />Inference mesh active</div>
      <div className="absolute right-5 top-24 z-20 hidden items-center gap-2 text-[9px] uppercase tracking-[.18em] text-violet-200/70 sm:flex"><Orbit className="h-3 w-3" />Autonomy layer 03</div>

      <div className="absolute inset-x-0 bottom-5 z-30 mx-auto flex w-fit items-center gap-2 rounded-full border border-white/10 bg-black/55 px-4 py-2 text-[11px] text-muted backdrop-blur-xl">
        <ShieldCheck className="h-3.5 w-3.5 text-success" /><span>Live telemetry</span><span className="h-1 w-1 rounded-full bg-white/20" /><span>Click a marker to inspect</span>
      </div>

      <div className="absolute bottom-20 left-4 top-28 z-30 hidden w-[clamp(150px,15vw,190px)] grid-rows-6 gap-2 lg:grid xl:left-6">
        {nodes.filter((_, index) => index % 2 === 0).slice(0, 6).map((node) => <HealthMarker key={node.id} node={node} />)}
      </div>
      <div className="absolute bottom-20 right-4 top-28 z-30 hidden w-[clamp(150px,15vw,190px)] grid-rows-6 gap-2 lg:grid xl:right-6">
        {nodes.filter((_, index) => index % 2 === 1).slice(0, 6).map((node) => <HealthMarker key={node.id} node={node} />)}
      </div>

      <div className="absolute inset-x-3 top-20 z-30 grid grid-cols-2 gap-2 rounded-2xl border border-white/[.06] bg-black/30 p-2 backdrop-blur-sm sm:inset-x-5 sm:grid-cols-3 lg:hidden">
        {nodes.map((node) => <HealthMarker key={node.id} node={node} compact />)}
      </div>
    </div>
  );
}
