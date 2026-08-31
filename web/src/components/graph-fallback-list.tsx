"use client";

import { ArrowLeft, ChevronRight, ExternalLink } from "lucide-react";

import type { GraphPayload } from "@/lib/types";
import { Button } from "@/components/ui/button";

/**
 * The small-screen view of the SAME graph data.
 *
 * Deliberately a list, not a shrunken canvas: a force simulation squeezed into
 * a 360px viewport overlaps its own nodes, collides its labels, and fights the
 * browser's pinch-zoom. The information the graph carries -- which categories
 * exist, how big each is, what's inside one -- survives a list intact, and the
 * tap targets are far better. Same data, same colours.
 *
 * `onExpand` is a LOCAL state setter now (2026-09-02), not a network refetch --
 * the desktop canvas moved to fetching every reel at once (expand="all"), so
 * `data` already contains everything; drilling into one category on mobile is
 * just filtering what's already in hand, same as the desktop view dimming
 * everything outside the focused category instead of re-fetching.
 */
export function GraphFallbackList({
  data,
  expanded,
  onExpand,
}: {
  data: GraphPayload;
  expanded: string | null;
  onExpand: (slug: string | null) => void;
}) {
  // Filtered by category, not just by type=="reel" -- with `data` now holding
  // every reel across every category (expand="all"), the unfiltered set used
  // to work by coincidence: the OLD model re-fetched a payload scoped to just
  // one category, so "every reel node in `data`" and "this category's reels"
  // were the same list. They no longer are.
  const reelNodes = data.nodes.filter((n) => n.type === "reel" && n.category === expanded);
  const current = data.categories.find((c) => c.slug === expanded);

  if (expanded && current) {
    return (
      <div className="p-4">
        <Button size="sm" variant="outline" className="mb-4" onClick={() => onExpand(null)}>
          <ArrowLeft className="h-3.5 w-3.5" />
          All categories
        </Button>
        <div className="mb-3 flex items-center gap-2">
          <span className="h-3 w-3 rounded-full" style={{ backgroundColor: current.color }} />
          <h3 className="font-semibold text-slate-900">{current.label}</h3>
          <span className="text-sm tabular-nums text-slate-400">{current.count}</span>
        </div>
        <ul className="divide-y">
          {reelNodes.map((n) => (
            <li key={n.id}>
              <a
                href={`https://www.instagram.com/reel/${n.shortcode}/`}
                target="_blank"
                rel="noopener noreferrer"
                className="flex items-start gap-3 py-3 active:bg-slate-50"
              >
                <span
                  className="mt-1.5 h-2 w-2 shrink-0 rounded-full"
                  style={{ backgroundColor: n.color }}
                />
                <span className="flex-1 text-sm leading-snug text-slate-700">{n.label}</span>
                <ExternalLink className="mt-0.5 h-3.5 w-3.5 shrink-0 text-slate-300" />
              </a>
            </li>
          ))}
        </ul>
      </div>
    );
  }

  return (
    <ul className="divide-y">
      {data.categories.map((c) => (
        <li key={c.slug}>
          <button
            onClick={() => onExpand(c.slug)}
            className="flex w-full items-center gap-3 px-4 py-3.5 text-left active:bg-slate-50"
          >
            <span className="h-3 w-3 shrink-0 rounded-full" style={{ backgroundColor: c.color }} />
            <span className="flex-1 font-medium text-slate-800">{c.label}</span>
            {/* A bar makes relative size readable without the physics sim. */}
            <span
              className="h-1.5 rounded-full"
              style={{
                backgroundColor: c.color,
                width: `${Math.max(8, (c.count / (data.categories[0]?.count || 1)) * 72)}px`,
                opacity: 0.35,
              }}
            />
            <span className="w-8 text-right text-sm tabular-nums text-slate-400">{c.count}</span>
            <ChevronRight className="h-4 w-4 shrink-0 text-slate-300" />
          </button>
        </li>
      ))}
    </ul>
  );
}
