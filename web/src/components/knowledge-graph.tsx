"use client";

import dynamic from "next/dynamic";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ArrowLeft, Loader2 } from "lucide-react";

import { API_BASE } from "@/lib/api";
import type { GraphNode, GraphPayload } from "@/lib/types";
import { cn } from "@/lib/utils";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { GraphFallbackList } from "./graph-fallback-list";

// react-force-graph-2d reaches for `window` and a canvas at module scope, so
// it can never be part of a server render. ssr:false is not optional here.
const ForceGraph2D = dynamic(() => import("react-force-graph-2d"), {
  ssr: false,
  loading: () => (
    <div className="flex h-full items-center justify-center text-muted-foreground">
      <Loader2 className="h-5 w-5 animate-spin" />
    </div>
  ),
});

/** Below this width the physics simulation stops being explorable -- nodes
 *  overlap, labels collide, and pinch-zoom fights the pan handler. The brief
 *  asks for a graceful degrade rather than a cramped canvas, so under this
 *  breakpoint we render the same data as a tappable list instead. */
const GRAPH_MIN_WIDTH = 768;

export function KnowledgeGraph({ initial }: { initial: GraphPayload }) {
  const [data, setData] = useState<GraphPayload>(initial);
  const [expanded, setExpanded] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [hovered, setHovered] = useState<GraphNode | null>(null);

  const wrapRef = useRef<HTMLDivElement>(null);
  const fgRef = useRef<any>(null);
  const [size, setSize] = useState({ width: 0, height: 0 });

  // ForceGraph2D needs explicit pixel dimensions; it cannot size itself from
  // CSS. ResizeObserver keeps it correct through window resizes and the
  // mobile browser-chrome collapse that changes viewport height mid-scroll.
  useEffect(() => {
    const el = wrapRef.current;
    if (!el) return;
    const ro = new ResizeObserver(([entry]) => {
      const { width, height } = entry.contentRect;
      setSize({ width, height });
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const isNarrow = size.width > 0 && size.width < GRAPH_MIN_WIDTH;

  const load = useCallback(async (category: string | null) => {
    setLoading(true);
    try {
      const url = category
        ? `${API_BASE}/api/public/graph?expand=${encodeURIComponent(category)}`
        : `${API_BASE}/api/public/graph`;
      const res = await fetch(url);
      if (!res.ok) throw new Error(String(res.status));
      setData(await res.json());
      setExpanded(category);
    } catch {
      // Leave the current view in place. A failed expand should be a no-op,
      // not a blank canvas.
    } finally {
      setLoading(false);
    }
  }, []);

  const handleNodeClick = useCallback(
    (node: any) => {
      if (node.type === "category" && node.category !== expanded) {
        void load(node.category);
        return;
      }
      if (node.type === "reel" && node.shortcode) {
        window.open(`https://www.instagram.com/reel/${node.shortcode}/`, "_blank", "noopener");
      }
    },
    [expanded, load],
  );

  // Give the simulation more breathing room than the default so ~13 nodes
  // spread across the canvas instead of clumping in the middle.
  useEffect(() => {
    const fg = fgRef.current;
    if (!fg) return;
    fg.d3Force("charge")?.strength(-420);
    fg.d3Force("link")?.distance((l: any) => (l.type === "membership" ? 55 : 190));
  }, [data]);

  const paintNode = useCallback(
    (node: any, ctx: CanvasRenderingContext2D, globalScale: number) => {
      const r = node.val;
      const isCategory = node.type === "category";
      const isHovered = hovered?.id === node.id;

      // Soft outer glow so a vibrant dot still reads against pure white.
      ctx.beginPath();
      ctx.arc(node.x, node.y, r * 1.55, 0, 2 * Math.PI);
      ctx.fillStyle = `${node.color}1f`;
      ctx.fill();

      ctx.beginPath();
      ctx.arc(node.x, node.y, r, 0, 2 * Math.PI);
      ctx.fillStyle = node.color;
      ctx.fill();
      ctx.lineWidth = isHovered ? 2.5 / globalScale : 1.5 / globalScale;
      ctx.strokeStyle = "#ffffff";
      ctx.stroke();

      // Labels only for categories (and the hovered reel). Drawing 50 reel
      // labels at once is exactly the unreadable-hairball failure the
      // category-first design exists to avoid.
      if (!isCategory && !isHovered) return;

      const fontSize = isCategory ? Math.max(12, 13 / globalScale) : Math.max(10, 11 / globalScale);
      ctx.font = `${isCategory ? 600 : 400} ${fontSize}px ui-sans-serif, system-ui, sans-serif`;
      ctx.textAlign = "center";
      ctx.textBaseline = "top";

      const label = isCategory ? `${node.label} · ${node.count}` : node.label;
      const y = node.y + r + 5 / globalScale;

      // Halo behind the text keeps it legible where it crosses an edge.
      ctx.lineWidth = 3 / globalScale;
      ctx.strokeStyle = "rgba(255,255,255,0.95)";
      ctx.strokeText(label, node.x, y);
      ctx.fillStyle = isCategory ? "#0f172a" : "#475569";
      ctx.fillText(label, node.x, y);
    },
    [hovered],
  );

  const currentCategory = useMemo(
    () => data.categories.find((c) => c.slug === expanded),
    [data.categories, expanded],
  );

  return (
    <div className="relative">
      {/* Controls sit above the canvas so they stay reachable while panning. */}
      <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          {expanded ? (
            <>
              <Button size="sm" variant="outline" onClick={() => void load(null)}>
                <ArrowLeft className="h-3.5 w-3.5" />
                All categories
              </Button>
              <Badge
                className="border-transparent text-white"
                style={{ backgroundColor: currentCategory?.color }}
              >
                {currentCategory?.label} · {currentCategory?.count}
              </Badge>
            </>
          ) : (
            <p className="text-sm text-muted-foreground">
              {isNarrow ? "Tap a category to explore it." : "Click a category to expand it."}
            </p>
          )}
          {loading && <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />}
        </div>
        <p className="text-sm tabular-nums text-muted-foreground">
          {data.total_reels.toLocaleString()} saves · {data.categories.length} categories
        </p>
      </div>

      <div
        ref={wrapRef}
        className={cn(
          "relative w-full overflow-hidden rounded-2xl border bg-white",
          isNarrow ? "h-auto" : "h-[560px]",
        )}
      >
        {isNarrow ? (
          <GraphFallbackList
            data={data}
            expanded={expanded}
            onExpand={(slug) => void load(slug)}
          />
        ) : (
          size.width > 0 && (
            <ForceGraph2D
              ref={fgRef}
              graphData={data as any}
              width={size.width}
              height={size.height}
              backgroundColor="#ffffff"
              nodeCanvasObject={paintNode}
              nodePointerAreaPaint={(node: any, color: string, ctx: CanvasRenderingContext2D) => {
                ctx.fillStyle = color;
                ctx.beginPath();
                ctx.arc(node.x, node.y, node.val * 1.6, 0, 2 * Math.PI);
                ctx.fill();
              }}
              onNodeClick={handleNodeClick}
              onNodeHover={(n: any) => setHovered(n ?? null)}
              linkColor={(l: any) => (l.type === "membership" ? "#cbd5e1" : "#e2e8f0")}
              linkWidth={(l: any) => (l.type === "membership" ? 0.8 : Math.min(3, 0.5 + l.value * 0.22))}
              cooldownTicks={120}
              onEngineStop={() => fgRef.current?.zoomToFit(420, 60)}
              enableNodeDrag={false}
            />
          )
        )}
      </div>

      {/* Legend doubles as an alternate control surface: some people will
          reach for a named list rather than clicking a dot. */}
      <div className="mt-4 flex flex-wrap gap-x-4 gap-y-2">
        {data.categories.map((c) => (
          <button
            key={c.slug}
            onClick={() => void load(c.slug === expanded ? null : c.slug)}
            className={cn(
              "group flex items-center gap-2 text-sm transition-opacity",
              expanded && expanded !== c.slug ? "opacity-40 hover:opacity-100" : "opacity-100",
            )}
          >
            <span
              className="h-2.5 w-2.5 shrink-0 rounded-full"
              style={{ backgroundColor: c.color }}
            />
            <span className="text-slate-600 group-hover:text-slate-900">{c.label}</span>
            <span className="tabular-nums text-slate-400">{c.count}</span>
          </button>
        ))}
      </div>
    </div>
  );
}
