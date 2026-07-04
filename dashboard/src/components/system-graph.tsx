"use client";

import * as React from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import {
  forceCenter,
  forceCollide,
  forceLink,
  forceManyBody,
  forceSimulation,
  type Simulation,
  type SimulationLinkDatum,
  type SimulationNodeDatum,
} from "d3-force";

import { cn } from "@/lib/format";
import { HEALTH_STYLE } from "@/lib/health";
import type { DeptBot, Dept, GraphEdge, GraphNode } from "@/lib/graph";
import { CaretakerBot } from "@/components/caretaker-bot";

interface SimNode extends GraphNode, SimulationNodeDatum {
  radius: number;
}
interface SimLink extends SimulationLinkDatum<SimNode> {
  kind: GraphEdge["kind"];
}

const DEPT_ANCHOR_FRACTION: Record<Dept, { fx: number; fy: number }> = {
  intelligence: { fx: 0.22, fy: 0.3 },
  performance: { fx: 0.78, fy: 0.3 },
  generation: { fx: 0.5, fy: 0.8 },
};

function nodeRadius(kind: GraphNode["kind"]): number {
  return kind === "department" ? 17 : 8.5;
}

function collideRadius(kind: GraphNode["kind"]): number {
  return kind === "department" ? 54 : 34;
}

export function SystemGraph({
  nodes,
  edges,
  bots,
}: {
  nodes: GraphNode[];
  edges: GraphEdge[];
  bots: DeptBot[];
}) {
  const router = useRouter();
  const containerRef = React.useRef<HTMLDivElement>(null);
  const canvasRef = React.useRef<HTMLCanvasElement>(null);
  const simRef = React.useRef<Simulation<SimNode, SimLink> | null>(null);
  const simNodesRef = React.useRef<SimNode[]>([]);
  const hoveredIdRef = React.useRef<string | null>(null);
  const dashOffsetRef = React.useRef(0);
  const reducedMotionRef = React.useRef(false);
  const botRefs = React.useRef<Record<string, HTMLDivElement | null>>({});

  const [dims, setDims] = React.useState({ width: 720, height: 440 });
  const [tooltip, setTooltip] = React.useState<{
    node: SimNode;
    screenX: number;
    screenY: number;
  } | null>(null);
  const [botTooltip, setBotTooltip] = React.useState<{
    dept: Dept;
    label: string;
    reason: string;
    x: number;
    y: number;
  } | null>(null);

  // ---- Responsive sizing -------------------------------------------------
  React.useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    const ro = new ResizeObserver((entries) => {
      const entry = entries[0];
      if (!entry) return;
      const width = Math.max(320, entry.contentRect.width);
      const height = width < 640 ? 420 : 460;
      setDims((prev) => (prev.width === width && prev.height === height ? prev : { width, height }));
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  // ---- prefers-reduced-motion --------------------------------------------
  React.useEffect(() => {
    const mq = window.matchMedia("(prefers-reduced-motion: reduce)");
    reducedMotionRef.current = mq.matches;
    const handler = (e: MediaQueryListEvent) => {
      reducedMotionRef.current = e.matches;
    };
    mq.addEventListener("change", handler);
    return () => mq.removeEventListener("change", handler);
  }, []);

  // ---- Build + run the simulation ----------------------------------------
  React.useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    const { width, height } = dims;
    const dpr = typeof window !== "undefined" ? window.devicePixelRatio || 1 : 1;
    canvas.width = Math.round(width * dpr);
    canvas.height = Math.round(height * dpr);
    canvas.style.width = `${width}px`;
    canvas.style.height = `${height}px`;
    ctx.scale(dpr, dpr);

    // Deterministic seed layout: department anchors form a triangle; each
    // dept's children spiral out from their department's anchor. This keeps
    // the very first frame legible instead of a random scatter, whether or
    // not the simulation is allowed to keep moving afterward.
    const childIndex: Record<Dept, number> = { intelligence: 0, performance: 0, generation: 0 };
    const simNodes: SimNode[] = nodes.map((n) => {
      const anchor = DEPT_ANCHOR_FRACTION[n.dept];
      const ax = anchor.fx * width;
      const ay = anchor.fy * height;
      if (n.kind === "department") {
        return { ...n, x: ax, y: ay, radius: nodeRadius(n.kind) };
      }
      const i = childIndex[n.dept]++;
      const angle = i * 2.4 + (n.dept === "performance" ? Math.PI : 0);
      const r = 78 + i * 16;
      return { ...n, x: ax + Math.cos(angle) * r, y: ay + Math.sin(angle) * r, radius: nodeRadius(n.kind) };
    });
    const nodeById = new Map(simNodes.map((n) => [n.id, n]));
    const simLinks: SimLink[] = edges
      .filter((e) => nodeById.has(e.source) && nodeById.has(e.target))
      .map((e) => ({ source: e.source, target: e.target, kind: e.kind }));

    simNodesRef.current = simNodes;

    const sim = forceSimulation<SimNode>(simNodes)
      .force(
        "charge",
        forceManyBody<SimNode>().strength((d) => (d.kind === "department" ? -420 : -140)),
      )
      .force(
        "link",
        forceLink<SimNode, SimLink>(simLinks)
          .id((d) => d.id)
          .distance((l) => (l.kind === "contains" ? 62 : 118))
          .strength((l) => (l.kind === "contains" ? 0.85 : 0.18)),
      )
      .force("center", forceCenter(width / 2, height / 2))
      .force(
        "collide",
        forceCollide<SimNode>().radius((d) => collideRadius(d.kind)).strength(0.9),
      )
      .alphaDecay(reducedMotionRef.current ? 0.08 : 0.018)
      .velocityDecay(0.32);

    if (!reducedMotionRef.current) {
      // Keep a small amount of energy in the system permanently — this is
      // the "gentle continuous motion / breathing" the vision asks for,
      // achieved with d3-force alone rather than a second animation system.
      sim.alphaTarget(0.025);
    } else {
      // Converge synchronously before the first paint — no animated pop-in.
      sim.stop();
      for (let i = 0; i < 300; i++) sim.tick();
    }

    function draw() {
      if (!ctx) return;
      ctx.clearRect(0, 0, width, height);

      // Keep every node inside the canvas — a hard bound so the perpetual
      // alphaTarget "breathing" never drifts nodes off-screen over time.
      for (const n of simNodes) {
        const r = collideRadius(n.kind);
        n.x = Math.max(r, Math.min(width - r, n.x ?? width / 2));
        n.y = Math.max(r, Math.min(height - r, n.y ?? height / 2));
      }

      // Edges
      for (const l of simLinks) {
        const s = l.source as SimNode;
        const t = l.target as SimNode;
        if (typeof s.x !== "number" || typeof t.x !== "number") continue;
        ctx.beginPath();
        if (l.kind === "contains") {
          ctx.setLineDash([]);
          ctx.strokeStyle = "rgba(139,147,168,0.16)";
          ctx.lineWidth = 1;
        } else {
          ctx.setLineDash(reducedMotionRef.current ? [] : [5, 5]);
          ctx.lineDashOffset = -dashOffsetRef.current;
          ctx.strokeStyle = "rgba(129,140,248,0.45)";
          ctx.lineWidth = 1.4;
        }
        ctx.moveTo(s.x!, s.y!);
        ctx.lineTo(t.x!, t.y!);
        ctx.stroke();
      }
      ctx.setLineDash([]);

      // Nodes
      const now = performance.now();
      for (const n of simNodes) {
        if (typeof n.x !== "number" || typeof n.y !== "number") continue;
        const style = HEALTH_STYLE[n.health];
        const r = n.radius;
        const isHovered = hoveredIdRef.current === n.id;

        if (n.health === "healthy" && !reducedMotionRef.current) {
          const pulse = 0.5 + 0.5 * Math.sin(now / 650 + r);
          ctx.beginPath();
          ctx.arc(n.x, n.y, r + 4 + pulse * 3, 0, Math.PI * 2);
          ctx.fillStyle = `${style.hex}22`;
          ctx.fill();
        }

        ctx.beginPath();
        ctx.arc(n.x, n.y, isHovered ? r + 2 : r, 0, Math.PI * 2);
        ctx.fillStyle = style.hex;
        ctx.globalAlpha = n.kind === "department" ? 0.95 : 0.85;
        ctx.fill();
        ctx.globalAlpha = 1;
        ctx.lineWidth = isHovered ? 2 : 1;
        ctx.strokeStyle = "rgba(5,7,12,0.55)";
        ctx.stroke();

        ctx.font = n.kind === "department" ? "600 12px var(--font-geist-sans), sans-serif" : "500 10px var(--font-geist-sans), sans-serif";
        ctx.fillStyle = n.kind === "department" ? "#e7eaf2" : "#c3c9d6";
        ctx.textAlign = "center";
        ctx.fillText(n.label, n.x, n.y + r + (n.kind === "department" ? 16 : 12));
      }

      // Bots follow their department node without triggering React renders.
      for (const bot of bots) {
        const dNode = nodeById.get(`dept:${bot.dept}`);
        const el = botRefs.current[bot.dept];
        if (dNode && el && typeof dNode.x === "number" && typeof dNode.y === "number") {
          const bx = dNode.x + dNode.radius * 1.5;
          const by = dNode.y - dNode.radius * 1.5;
          el.style.transform = `translate(${bx}px, ${by}px)`;
        }
      }
    }

    if (reducedMotionRef.current) {
      // One static frame — sim is already stopped and pre-converged above.
      draw();
    } else {
      // d3-force's own timer (itself requestAnimationFrame-driven) fires
      // "tick" continuously while alphaTarget keeps alpha above alphaMin —
      // that's the sole draw loop; no separate rAF needed.
      sim.on("tick", () => {
        dashOffsetRef.current = (dashOffsetRef.current + 0.35) % 12;
        draw();
      });
    }

    simRef.current = sim;

    return () => {
      sim.stop();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [nodes, edges, bots, dims.width, dims.height]);

  // ---- Pointer interaction ------------------------------------------------
  function hitTest(clientX: number, clientY: number): SimNode | null {
    const canvas = canvasRef.current;
    if (!canvas) return null;
    const rect = canvas.getBoundingClientRect();
    const mx = clientX - rect.left;
    const my = clientY - rect.top;
    let best: SimNode | null = null;
    let bestDist = Infinity;
    for (const n of simNodesRef.current) {
      if (typeof n.x !== "number" || typeof n.y !== "number") continue;
      const d = Math.hypot(n.x - mx, n.y - my);
      const hitR = n.radius + 6;
      if (d <= hitR && d < bestDist) {
        best = n;
        bestDist = d;
      }
    }
    return best;
  }

  function onMouseMove(e: React.MouseEvent<HTMLCanvasElement>) {
    const hit = hitTest(e.clientX, e.clientY);
    hoveredIdRef.current = hit?.id ?? null;
    if (canvasRef.current) canvasRef.current.style.cursor = hit ? "pointer" : "default";
    if (hit) {
      const rect = containerRef.current?.getBoundingClientRect();
      setTooltip({
        node: hit,
        screenX: e.clientX - (rect?.left ?? 0),
        screenY: e.clientY - (rect?.top ?? 0),
      });
    } else {
      setTooltip((prev) => (prev ? null : prev));
    }
  }

  function onMouseLeave() {
    hoveredIdRef.current = null;
    setTooltip(null);
  }

  function onClick(e: React.MouseEvent<HTMLCanvasElement>) {
    const hit = hitTest(e.clientX, e.clientY);
    if (hit) router.push(hit.href);
  }

  const healthy = nodes.filter((n) => n.health === "healthy").length;
  const stale = nodes.filter((n) => n.health === "stale").length;
  const failed = nodes.filter((n) => n.health === "failed").length;

  return (
    <div
      ref={containerRef}
      className="relative w-full min-h-[420px] overflow-hidden rounded-xl border border-border bg-surface/60 sm:min-h-[460px]"
    >
      <canvas
        ref={canvasRef}
        role="img"
        aria-label={`System map: ${nodes.length} nodes across Intelligence, Performance, and Generation — ${healthy} fresh, ${stale} stale, ${failed} failed. Click a node to open its pipeline page.`}
        onMouseMove={onMouseMove}
        onMouseLeave={onMouseLeave}
        onClick={onClick}
      />

      {bots.map((bot) => (
        <CaretakerBot
          key={bot.dept}
          ref={(el) => {
            botRefs.current[bot.dept] = el;
          }}
          label={bot.label}
          activity={bot.activity}
          health={bot.health}
          reason={bot.reason}
          onActivate={() => router.push(bot.href)}
          onHover={(hovering) => {
            // Captured inside the event handler (not during render) — reading
            // a ref's current DOM position at render time trips
            // react-hooks/refs; reading it here, in response to a real
            // pointer event, is exactly what refs are for.
            if (!hovering) {
              setBotTooltip((prev) => (prev?.dept === bot.dept ? null : prev));
              return;
            }
            const el = botRefs.current[bot.dept];
            const containerEl = containerRef.current;
            if (!el || !containerEl) return;
            const elRect = el.getBoundingClientRect();
            const contRect = containerEl.getBoundingClientRect();
            setBotTooltip({
              dept: bot.dept,
              label: bot.label,
              reason: bot.reason,
              x: elRect.left + elRect.width / 2 - contRect.left,
              y: elRect.top - contRect.top,
            });
          }}
        />
      ))}

      {botTooltip ? (
        <div
          className="pointer-events-none absolute z-20 max-w-56 -translate-x-1/2 rounded-lg border border-border bg-surface px-3 py-2 text-xs shadow-lg shadow-black/40"
          style={{ left: botTooltip.x, top: botTooltip.y - 54 }}
        >
          <p className="font-semibold text-foreground">{botTooltip.label} bot</p>
          <p className="mt-0.5 text-muted">{botTooltip.reason}</p>
        </div>
      ) : null}

      {tooltip ? (
        <div
          className="pointer-events-none absolute z-20 max-w-60 -translate-x-1/2 rounded-lg border border-border bg-surface px-3 py-2 text-xs shadow-lg shadow-black/40"
          style={{ left: tooltip.screenX, top: tooltip.screenY - 14 }}
        >
          <p className="flex items-center gap-1.5 font-semibold text-foreground">
            <span className={cn("h-2 w-2 rounded-full", HEALTH_STYLE[tooltip.node.health].dot)} />
            {tooltip.node.label}
          </p>
          <p className="mt-0.5 text-muted">{tooltip.node.detail}</p>
          <p className="mt-1 text-[11px] font-medium text-accent">Click to open →</p>
        </div>
      ) : null}

      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 border-t border-border px-4 py-2 text-[11px] text-muted">
        <LegendDot health="healthy" label="Fresh" />
        <LegendDot health="stale" label="Stale" />
        <LegendDot health="failed" label="Failed" />
        <LegendDot health="unknown" label="No data yet" />
        <span className="ml-auto hidden sm:inline">Click any node or bot to open its page</span>
      </div>

      {/* Accessible fallback: the graph IS the nav, so every destination it
          reaches must also be keyboard/screen-reader operable without the
          canvas. Visually hidden until focused. */}
      <nav aria-label="System map destinations (accessible list)" className="sr-only">
        <ul>
          {nodes.map((n) => (
            <li key={n.id}>
              <Link href={n.href} className="focus:not-sr-only">
                {n.label} — {HEALTH_STYLE[n.health].label}
              </Link>
            </li>
          ))}
          {bots.map((b) => (
            <li key={`bot-${b.dept}`}>
              <Link href={b.href} className="focus:not-sr-only">
                {b.label} caretaker bot — {b.reason}
              </Link>
            </li>
          ))}
        </ul>
      </nav>
    </div>
  );
}

function LegendDot({ health, label }: { health: keyof typeof HEALTH_STYLE; label: string }) {
  return (
    <span className="inline-flex items-center gap-1.5">
      <span className={cn("h-1.5 w-1.5 rounded-full", HEALTH_STYLE[health].dot)} />
      {label}
    </span>
  );
}
