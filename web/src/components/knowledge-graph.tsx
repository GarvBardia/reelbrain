"use client";

import dynamic from "next/dynamic";
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { forceCollide } from "d3-force";
import { ArrowLeft, Loader2 } from "lucide-react";

import { getGraph } from "@/lib/api";
import type { GraphNode, GraphPayload } from "@/lib/types";
import { cn } from "@/lib/utils";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { GraphFallbackList } from "./graph-fallback-list";
import { GlowingEffect } from "./obsidian/glowing-effect";

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

/** Above this many nodes the per-node glow (three stacked arcs + a canvas
 *  shadow each) costs more than it adds. Expanding a big category can push
 *  past it, so the renderer drops to a cheaper single-arc path rather than
 *  dropping frames -- see paintNode. Tuned against the real corpus: 13
 *  category nodes by default, up to ~66 when the largest category expands. */
const GLOW_NODE_BUDGET = 90;

/** Extra clearance added to each node's radius for collision, so circles not
 *  only avoid overlapping but leave room for a label to sit between them. */
const COLLIDE_PADDING = 13;

/** Where a zero-edge node gets parked, in simulation coordinates (the layout
 *  centres on roughly 0,0). Lower-left, close enough that framing the graph
 *  does not have to zoom out much to include it. */
const ISOLATED_ANCHOR = { x: -430, y: 215 };

/** Label policy at the category level. Showing all 13 at once is the
 *  unreadable-stack problem; showing none makes the graph a guessing game.
 *  So: the biggest nodes are always labelled, anything is labelled on hover,
 *  and zooming in past the threshold reveals the rest. Nothing is actually
 *  hidden from the visitor either way -- the legend under the canvas lists
 *  every category with its count. */
const LABEL_ALWAYS_MIN_VAL = 15;
const LABEL_SHOW_ALL_ZOOM = 1.45;

export function KnowledgeGraph({ initial }: { initial: GraphPayload }) {
  const [data, setData] = useState<GraphPayload>(initial);
  const [expanded, setExpanded] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  // Expansion failures used to be swallowed entirely (a bare `catch {}`), so a
  // failed click looked like an unresponsive UI. Now surfaced inline.
  const [expandError, setExpandError] = useState<string | null>(null);
  const [hovered, setHovered] = useState<GraphNode | null>(null);

  const wrapRef = useRef<HTMLDivElement>(null);
  const fgRef = useRef<any>(null);
  const [size, setSize] = useState({ width: 0, height: 0 });

  // Keep the canvas in sync with whatever `initial` the parent last fetched --
  // otherwise a successful retry at the page level would leave this component
  // showing the stale payload it mounted with.
  useEffect(() => {
    setData(initial);
    setExpanded(null);
  }, [initial]);

  /**
   * ForceGraph2D needs explicit pixel dimensions; it cannot size itself from
   * CSS.
   *
   * The measurement is deliberately NOT left to ResizeObserver alone. This
   * component previously rendered `size.width > 0 && <ForceGraph2D/>`, so if
   * RO's first callback never arrived the graph area stayed silently
   * empty -- no canvas, no error, no spinner. That was reproduced directly:
   * in a browser tab that is not compositing frames, RO never fires at all,
   * and the whole graph vanished while the surrounding legend rendered fine.
   * Same visible symptom as the CORS outage, entirely different cause, which
   * is exactly why it is worth removing rather than explaining away.
   *
   * So: measure synchronously on mount (useLayoutEffect, before paint), then
   * use RO *and* a window-resize listener only to keep that measurement
   * current. Any one of the three succeeding is enough to draw the graph.
   */
  useLayoutEffect(() => {
    const el = wrapRef.current;
    if (!el) return;

    const measure = () => {
      const { width, height } = el.getBoundingClientRect();
      // Guard against a 0-height read while the element is still laying out;
      // the CSS class fixes the height, so fall back to it rather than
      // handing the canvas a zero.
      if (width > 0) setSize({ width, height: height || 560 });
    };

    measure();

    let ro: ResizeObserver | undefined;
    if (typeof ResizeObserver !== "undefined") {
      ro = new ResizeObserver(measure);
      ro.observe(el);
    }
    window.addEventListener("resize", measure);
    return () => {
      ro?.disconnect();
      window.removeEventListener("resize", measure);
    };
  }, []);

  const isNarrow = size.width > 0 && size.width < GRAPH_MIN_WIDTH;

  const load = useCallback(async (category: string | null) => {
    setLoading(true);
    setExpandError(null);
    try {
      setData(await getGraph(category ?? undefined));
      setExpanded(category);
    } catch (err) {
      setExpandError(
        err instanceof Error ? err.message : "Could not load that category.",
      );
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

  /**
   * Force tuning.
   *
   * The clumping had a specific cause that charge alone could not fix: at the
   * category level this is a near-COMPLETE graph. 12 of the 13 nodes share at
   * least one reel with almost every other, giving 52 co-occurrence edges out
   * of a possible 66. d3's link force pulls on every one of those, so the
   * previous -420 charge was fighting 52 springs and lost -- everything
   * collapsed to a ball with labels stacked on top of each other.
   *
   * So the fix is three-part, not just "more charge":
   *   - forceCollide gives a HARD non-overlap constraint (charge is a soft
   *     inverse-square falloff and never guarantees separation),
   *   - link strength is weighted DOWN so a dense mesh of weak
   *     co-occurrences cannot out-pull the repulsion, while a genuinely
   *     strong pairing still reads as closer,
   *   - charge goes up and decay goes down so the layout has both the force
   *     and the time to actually expand before it freezes.
   */
  useEffect(() => {
    const fg = fgRef.current;
    if (!fg) return;

    fg.d3Force("charge")
      ?.strength(-1150)
      // Without a cap, repulsion between the far-apart nodes keeps inflating
      // the layout every tick and zoomToFit just scales everything back down
      // -- net effect is a small clump again, just after a longer wait.
      .distanceMax(620);

    fg.d3Force("link")
      ?.distance((l: any) => (l.type === "membership" ? 70 : 240))
      // d3's default link strength is 1/min(degree of endpoints). With
      // near-uniform high degree that lands around 0.1 for EVERY edge, so 52
      // of them sum into one strong inward pull. Scaling by co-occurrence
      // weight keeps the meaningful pairings and mutes the noise.
      .strength((l: any) =>
        l.type === "membership" ? 0.55 : Math.min(0.09, 0.012 * (l.value ?? 1)),
      );

    // The hard constraint. Radius is the node's own radius plus padding, so
    // two circles can never intersect and there is room for a label between
    // them. 2 iterations resolves chains of contacts (a node squeezed between
    // two others) that a single pass leaves overlapping.
    fg.d3Force(
      "collide",
      forceCollide()
        .radius((n: any) => (n.val ?? 6) + COLLIDE_PADDING)
        .strength(0.92)
        .iterations(2),
    );

    // THE ACTUAL BUG FIX. Custom forces can only be installed imperatively,
    // i.e. after mount -- by which point the simulation has already been
    // running (and cooling) on d3's DEFAULTS. Without a reheat, everything
    // set above lands on a sim whose alpha is ~0 and therefore changes
    // nothing: the layout stays at d3's defaults of charge -30 / link 30.
    // Reproduced with the real payload -- those defaults give 16 overlapping
    // pairs in a 254x101px clump, which is precisely the reported screenshot.
    //
    // d3AlphaDecay / d3VelocityDecay are deliberately NOT set here: they are
    // declarative PROPS on the component, not methods on the imperative
    // handle. Calling them here throws, which would abort this effect before
    // the reheat below and silently preserve the very bug it fixes.
    fg.d3ReheatSimulation();
  }, [data]);

  /**
   * Nodes with zero edges get no link force at all, so charge alone flings
   * them to wherever the repulsion gradient points -- which is why "Other"
   * drifted off on its own and read as "the graph is broken".
   *
   * Confirmed against the live API rather than assumed: "Other" genuinely has
   * degree 0, and structurally always will. A reel only lands in `other` when
   * NONE of its topics map to a parent category, so its category set is the
   * single element {other} -- and co-occurrence edges are built from PAIRS
   * within that set. A one-element set yields no pairs, so the bucket can
   * never earn an edge. It is correct data, not a bug.
   *
   * Given that, it gets a deliberate home: pinned to the lower-left, reading
   * as a parked miscellaneous bucket rather than an escapee. Generalised to
   * any isolated node so a future zero-degree category behaves the same.
   */
  useEffect(() => {
    if (data.level !== "category") return;
    const degree = new Map<string, number>();
    for (const l of data.links) {
      const s = typeof l.source === "string" ? l.source : (l.source as GraphNode).id;
      const t = typeof l.target === "string" ? l.target : (l.target as GraphNode).id;
      degree.set(s, (degree.get(s) ?? 0) + 1);
      degree.set(t, (degree.get(t) ?? 0) + 1);
    }
    for (const n of data.nodes as any[]) {
      if ((degree.get(n.id) ?? 0) === 0) {
        // Close enough that zoomToFit does not have to zoom way out to
        // include it, far enough to read as deliberately set apart.
        n.fx = ISOLATED_ANCHOR.x;
        n.fy = ISOLATED_ANCHOR.y;
      } else {
        n.fx = undefined;
        n.fy = undefined;
      }
    }
  }, [data]);

  /**
   * Auto-frame every node. 400ms ease, 40px padding.
   *
   * Wired to BOTH onEngineStop and a timer after any data change, because
   * onEngineStop only fires when a running simulation cools -- it does not
   * fire if the sim is already cold, which happens on a re-render with
   * unchanged data. Relying on it alone leaves initial zoom/pan to chance,
   * which is how a correctly-spread layout can still arrive off-screen.
   */
  const frameGraph = useCallback(() => {
    fgRef.current?.zoomToFit(400, 40);
  }, []);

  useEffect(() => {
    if (!data.nodes.length) return;
    // Mid-flight fit so the graph is framed while it is still expanding;
    // onEngineStop re-fits once it settles.
    const t = setTimeout(frameGraph, 900);
    return () => clearTimeout(t);
  }, [data, frameGraph]);

  const cheapMode = data.nodes.length > GLOW_NODE_BUDGET;

  /**
   * Glassmorphic node rendering.
   *
   * Canvas has no backdrop-filter, so "frosted glass" is composited by hand:
   *   1. an outer bloom via ctx.shadowBlur in the node's own colour,
   *   2. a translucent radial-gradient body (opaque-ish core -> transparent
   *      rim) which is what reads as depth/blur rather than a flat disc,
   *   3. a bright top-left specular arc, the highlight a glass sphere gets
   *      from a light source, which is what stops it looking like a sticker.
   * Shadows are always reset before the next primitive -- canvas shadow state
   * is global and leaks into every later draw (including the labels) if left
   * set, which shows up as muddy text.
   */
  const paintNode = useCallback(
    (node: any, ctx: CanvasRenderingContext2D, globalScale: number) => {
      const r = node.val;
      // The force simulation assigns x/y on its first tick, so the very first
      // paint can arrive with them still undefined. createRadialGradient
      // THROWS on a non-finite argument (rather than no-opping), and that
      // throw propagates out of the render loop and kills the canvas
      // entirely -- the graph vanishes while everything around it renders
      // fine. Caught live in a browser; this guard is the fix.
      if (!Number.isFinite(node.x) || !Number.isFinite(node.y) || !Number.isFinite(r)) return;

      const isCategory = node.type === "category";
      const isHovered = hovered?.id === node.id;
      const dim = expanded !== null && isCategory && node.category !== expanded;

      ctx.save();
      if (dim) ctx.globalAlpha = 0.28;

      if (!cheapMode) {
        // 1. Outer bloom. Sits under everything else.
        ctx.shadowColor = node.color;
        ctx.shadowBlur = (isHovered ? 26 : 16) / Math.max(globalScale, 0.6);
        ctx.beginPath();
        ctx.arc(node.x, node.y, r * 0.92, 0, 2 * Math.PI);
        ctx.fillStyle = `${node.color}55`;
        ctx.fill();
        ctx.shadowBlur = 0;
        ctx.shadowColor = "transparent";

        // 2. Translucent glass body.
        const body = ctx.createRadialGradient(
          node.x - r * 0.35, node.y - r * 0.35, r * 0.1,
          node.x, node.y, r,
        );
        body.addColorStop(0, `${node.color}F2`);
        body.addColorStop(0.55, `${node.color}CC`);
        body.addColorStop(1, `${node.color}66`);
        ctx.beginPath();
        ctx.arc(node.x, node.y, r, 0, 2 * Math.PI);
        ctx.fillStyle = body;
        ctx.fill();

        // 3. Specular highlight.
        ctx.beginPath();
        ctx.arc(node.x - r * 0.3, node.y - r * 0.34, r * 0.42, Math.PI * 1.05, Math.PI * 1.95);
        ctx.strokeStyle = "rgba(255,255,255,0.75)";
        ctx.lineWidth = Math.max(0.8, r * 0.13);
        ctx.stroke();
      } else {
        ctx.beginPath();
        ctx.arc(node.x, node.y, r, 0, 2 * Math.PI);
        ctx.fillStyle = `${node.color}D9`;
        ctx.fill();
      }

      // Crisp rim keeps the shape legible against white where the glass body
      // fades out to near-transparent.
      ctx.beginPath();
      ctx.arc(node.x, node.y, r, 0, 2 * Math.PI);
      ctx.strokeStyle = isHovered ? "rgba(255,255,255,0.95)" : "rgba(255,255,255,0.7)";
      ctx.lineWidth = (isHovered ? 2.4 : 1.4) / globalScale;
      ctx.stroke();
      ctx.restore();

      /**
       * Label policy -- the other half of the unreadable-mess fix.
       *
       * Previously EVERY category label drew unconditionally, so 13 of them
       * stacked into an illegible pile the moment the layout was even
       * slightly tight. Now a label draws when it has earned the space:
       *   - hovered (always, any node),
       *   - the biggest categories, which are also the best separated,
       *   - everything, once zoomed in far enough for the room to exist.
       * Reel labels stay hover-only; 65 of them at once is the hairball the
       * category-first design exists to avoid.
       */
      const showLabel =
        isHovered ||
        (isCategory &&
          (globalScale >= LABEL_SHOW_ALL_ZOOM || r >= LABEL_ALWAYS_MIN_VAL));
      if (!showLabel) return;

      ctx.save();
      if (dim) ctx.globalAlpha = 0.35;
      const fontSize = isCategory ? Math.max(12, 13 / globalScale) : Math.max(10, 11 / globalScale);
      ctx.font = `${isCategory ? 600 : 400} ${fontSize}px ui-sans-serif, system-ui, sans-serif`;
      ctx.textAlign = "center";
      ctx.textBaseline = "top";

      const label = isCategory ? `${node.label} · ${node.count}` : node.label;
      const y = node.y + r + 6 / globalScale;

      // Halo behind the text keeps it legible where it crosses an edge.
      ctx.lineWidth = 3.5 / globalScale;
      ctx.strokeStyle = "rgba(255,255,255,0.95)";
      ctx.strokeText(label, node.x, y);
      ctx.fillStyle = isCategory ? "#0f172a" : "#475569";
      ctx.fillText(label, node.x, y);
      ctx.restore();
    },
    [hovered, expanded, cheapMode],
  );

  /**
   * Edges as soft colour-blended gradients rather than flat grey: each line is
   * a linear gradient running from its source node's colour to its target's,
   * so a link between Claude Ecosystem (orange) and Web & Design (magenta)
   * visibly carries both. Weight still drives width, so the co-occurrence
   * signal survives the restyle.
   */
  const paintLink = useCallback(
    (link: any, ctx: CanvasRenderingContext2D) => {
      const s = link.source;
      const t = link.target;
      // Before the simulation's first tick these are still id strings, and
      // even once resolved to objects their coordinates can briefly be
      // undefined. createLinearGradient throws on non-finite input exactly
      // like createRadialGradient does -- same crash, same vanished canvas.
      if (!s || !t || typeof s !== "object" || typeof t !== "object") return;
      if (!Number.isFinite(s.x) || !Number.isFinite(s.y)) return;
      if (!Number.isFinite(t.x) || !Number.isFinite(t.y)) return;

      const membership = link.type === "membership";
      const focussed =
        expanded !== null &&
        (s.category === expanded || t.category === expanded);
      const alpha = expanded === null ? (membership ? "40" : "38") : focussed ? "5A" : "14";

      const grad = ctx.createLinearGradient(s.x, s.y, t.x, t.y);
      grad.addColorStop(0, `${s.color}${alpha}`);
      grad.addColorStop(1, `${t.color}${alpha}`);

      ctx.beginPath();
      ctx.moveTo(s.x, s.y);
      ctx.lineTo(t.x, t.y);
      ctx.strokeStyle = grad;
      ctx.lineWidth = membership ? 0.9 : Math.min(3.2, 0.6 + (link.value ?? 1) * 0.24);
      ctx.lineCap = "round";
      ctx.stroke();
    },
    [expanded],
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

      {expandError && (
        <div className="mb-3 flex items-center justify-between gap-3 rounded-lg border border-amber-200 bg-amber-50 px-4 py-2.5 text-sm text-amber-900">
          <span>{expandError}</span>
          <Button
            size="sm"
            variant="outline"
            className="shrink-0 bg-white"
            onClick={() => void load(expanded)}
          >
            Retry
          </Button>
        </div>
      )}

      {/* The glass shell. GlowingEffect is an absolutely-positioned overlay
          that lights the border edge nearest the cursor; it is inert on touch
          (no pointer to track), so the mobile list view below loses nothing. */}
      <div
        className={cn(
          "relative rounded-[1.35rem] p-px",
          "bg-gradient-to-br from-slate-200/80 via-slate-100 to-slate-200/80",
          "shadow-[0_1px_2px_rgba(15,23,42,0.04),0_12px_40px_-12px_rgba(79,70,229,0.18)]",
        )}
      >
        <GlowingEffect
          variant="white"
          blur={14}
          spread={44}
          proximity={120}
          borderWidth={2}
          movementDuration={0.35}
          className="rounded-[1.35rem]"
        />
        <div
          ref={wrapRef}
          className={cn(
            "relative w-full overflow-hidden rounded-[1.3rem]",
            // Faint tint + blur so the card reads as frosted glass sitting on
            // the animated background rather than an opaque white box punched
            // out of it.
            "bg-white/85 backdrop-blur-xl",
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
                backgroundColor="rgba(0,0,0,0)"
                nodeCanvasObject={paintNode}
                nodePointerAreaPaint={(node: any, color: string, ctx: CanvasRenderingContext2D) => {
                  ctx.fillStyle = color;
                  ctx.beginPath();
                  ctx.arc(node.x, node.y, node.val * 1.6, 0, 2 * Math.PI);
                  ctx.fill();
                }}
                onNodeClick={handleNodeClick}
                onNodeHover={(n: any) => setHovered(n ?? null)}
                // Native tooltip, so the categories that do not draw a
                // permanent label are still identifiable by pointing at them
                // (and are exposed to assistive tech, which a canvas-painted
                // label is not).
                nodeLabel={(n: any) =>
                  n.type === "category" ? `${n.label} — ${n.count} saves` : n.label
                }
                linkCanvasObject={paintLink}
                // Simulation pacing lives here, as PROPS -- these are not
                // available on the imperative handle (see the note in the
                // force effect). Defaults are 0.0228 / 0.4, which freeze a
                // 13-node layout while it is still mid-expansion.
                d3AlphaDecay={0.0115}
                d3VelocityDecay={0.3}
                // Ticks run before the first paint, so the graph arrives
                // already spread rather than visibly exploding outward from
                // a central clump.
                warmupTicks={80}
                // Longer runway to match the slower decay above.
                cooldownTicks={320}
                onEngineStop={frameGraph}
                enableNodeDrag={false}
              />
            )
          )}
        </div>
      </div>

      {/* Legend doubles as an alternate control surface: some people will
          reach for a named list rather than clicking a dot. */}
      <div className="mt-5 flex flex-wrap gap-x-4 gap-y-2">
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
              style={{
                backgroundColor: c.color,
                boxShadow: `0 0 8px ${c.color}66`,
              }}
            />
            <span className="text-slate-600 group-hover:text-slate-900">{c.label}</span>
            <span className="tabular-nums text-slate-400">{c.count}</span>
          </button>
        ))}
      </div>
    </div>
  );
}
