"use client";

import * as React from "react";

import type { BotActivity } from "@/lib/graph";
import type { Health } from "@/lib/health";
import { cn } from "@/lib/format";

/**
 * Caretaker bot mascot (docs/dashboard-vision.md §2b) — homepage only. Pure
 * inline SVG + CSS keyframe animation, no image assets. Positioning is done
 * by the parent <SystemGraph> via the forwarded ref's `style.transform`
 * (updated every simulation tick, bypassing React state for performance —
 * see globals.css for the actual keyframes and the
 * `prefers-reduced-motion` override that freezes every animation here to a
 * single static frame automatically).
 *
 * State ladder (highest priority first): fixing > busy > nudging > idle.
 * Charming but restrained — a small idle bob, not a wall of motion.
 */
export const CaretakerBot = React.forwardRef<
  HTMLDivElement,
  {
    label: string;
    activity: BotActivity;
    health: Health;
    reason: string;
    onActivate: () => void;
    onHover: (hovering: boolean) => void;
  }
>(function CaretakerBot({ label, activity, health, reason, onActivate, onHover }, ref) {
  const bodyColor =
    activity === "fixing"
      ? health === "failed"
        ? "#fb7185"
        : "#fbbf24"
      : activity === "busy"
        ? "#818cf8"
        : activity === "nudging"
          ? "#38bdf8"
          : "#6366f1";

  return (
    <div
      ref={ref}
      className="pointer-events-auto absolute left-0 top-0 z-10"
      style={{ willChange: "transform" }}
    >
      <button
        type="button"
        onClick={onActivate}
        onMouseEnter={() => onHover(true)}
        onMouseLeave={() => onHover(false)}
        onFocus={() => onHover(true)}
        onBlur={() => onHover(false)}
        aria-label={`${label} caretaker bot — ${reason}. Activate to open ${label}.`}
        className={cn(
          "group relative flex h-9 w-9 -translate-x-1/2 -translate-y-1/2 items-center justify-center rounded-full outline-none",
          "focus-visible:ring-2 focus-visible:ring-accent focus-visible:ring-offset-2 focus-visible:ring-offset-background",
        )}
      >
        {activity === "fixing" ? (
          <span
            aria-hidden="true"
            className="absolute inset-0 rounded-full motion-safe:animate-[bot-alert_1.6s_ease-in-out_infinite]"
            style={{ backgroundColor: bodyColor, opacity: 0.35 }}
          />
        ) : null}

        <svg
          viewBox="0 0 40 40"
          className={cn(
            "relative h-8 w-8 drop-shadow-[0_2px_6px_rgba(0,0,0,0.45)]",
            activity === "idle" && "motion-safe:animate-[bot-bob_3.2s_ease-in-out_infinite]",
            activity === "busy" && "motion-safe:animate-[bot-bob_1.1s_ease-in-out_infinite]",
            activity === "fixing" && "motion-safe:animate-[bot-wiggle_0.9s_ease-in-out_infinite]",
            activity === "nudging" && "motion-safe:animate-[bot-sway_1.6s_ease-in-out_infinite]",
          )}
        >
          {/* antenna */}
          <line x1="20" y1="4" x2="20" y2="10" stroke={bodyColor} strokeWidth="2" strokeLinecap="round" />
          <circle
            cx="20"
            cy="3.5"
            r="2.2"
            fill={bodyColor}
            className={activity === "busy" ? "motion-safe:animate-[bot-blip_0.7s_ease-in-out_infinite]" : undefined}
          />
          {/* head/body */}
          <rect x="8" y="10" width="24" height="20" rx="8" fill={bodyColor} opacity="0.92" />
          <rect x="8" y="10" width="24" height="20" rx="8" fill="none" stroke="#05070c" strokeOpacity="0.25" strokeWidth="1" />
          {/* eyes */}
          <circle cx="16" cy="20" r="2.4" fill="#05070c" />
          <circle cx="24" cy="20" r="2.4" fill="#05070c" />
        </svg>

        {activity === "fixing" ? (
          <span
            aria-hidden="true"
            className="absolute -bottom-1 -right-1 text-[13px] leading-none motion-safe:animate-[bot-wrench_1.4s_ease-in-out_infinite]"
          >
            🔧
          </span>
        ) : null}
        {activity === "busy" ? (
          <span
            aria-hidden="true"
            className="absolute -bottom-1 -right-1 text-[11px] leading-none motion-safe:animate-[bot-spin_1.4s_linear_infinite]"
          >
            ⚙️
          </span>
        ) : null}
        {activity === "nudging" ? (
          <span aria-hidden="true" className="absolute -bottom-1 -right-1 text-[11px] leading-none">
            📥
          </span>
        ) : null}
      </button>
    </div>
  );
});
