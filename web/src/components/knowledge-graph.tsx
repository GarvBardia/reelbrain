"use client";

import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import type { ComponentType } from "react";
import { forceCollide, forceX, forceY } from "d3-force";
import { ArrowLeft, Loader2, LocateFixed } from "lucide-react";

import { EMPTY_GRAPH } from "@/lib/api";
import type { GraphNode, GraphPayload } from "@/lib/types";
import { cn } from "@/lib/utils";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { GraphFallbackList } from "./graph-fallback-list";
import { HoverExpandCategories } from "./skiper/hover-expand-categories";
import { GlowingEffect } from "./obsidian/glowing-effect";

/**
 * THE ACTUAL BUG behind every prior "the graph still looks bad" report
 * (found 2026-08-21, confirmed against Next.js's own source, not theory).
 *
 * This used to be `next/dynamic(() => import("react-force-graph-2d"), {ssr:
 * false})`. next/dynamic's ssr:false path returns `LoadableComponent`
 * (node_modules/next/dist/shared/lib/lazy-dynamic/loadable.js) -- a PLAIN
 * function component, never wrapped in React.forwardRef. React drops any
 * `ref` passed to a plain function component (with a console warning:
 * "Function components cannot be given refs"), so `<ForceGraph2D ref={fgRef}
 * />` NEVER attached fgRef to the real react-force-graph-2d instance. Not
 * intermittently -- structurally, every single render, in every build this
 * project has ever shipped. Confirmed live: the warning fires in the
 * console, and `fgRef.current` never leaves null no matter how long you wait
 * or retry.
 *
 * This means every d3Force/d3ReheatSimulation/zoomToFit call in this
 * component's entire history -- including the "reheat" fix from the
 * previous commit -- was a no-op against a null ref. The graph has been
 * running purely on d3-force's raw defaults (charge -30, link 30) this
 * entire time, which is exactly the collapsed clump every session kept
 * re-diagnosing as a tuning problem.
 *
 * Fix: don't put a ref through next/dynamic at all. Import the module
 * manually inside a client-only effect and hold the resolved component in
 * state instead. Once rendered, `<ForceGraph2D ref={fgRef} />` is a REAL ref
 * on the REAL forwardRef component react-force-graph-2d exports -- no
 * wrapper in between to drop it. The `import()` still only ever runs in the
 * browser (useEffect never runs during the server/static-export build pass),
 * so this preserves exactly the SSR-safety `ssr:false` was providing,
 * without Next's HOC swallowing the ref.
 */
function useForceGraph2D() {
  const [Comp, setComp] = useState<ComponentType<any> | null>(null);
  useEffect(() => {
    let cancelled = false;
    import("react-force-graph-2d").then((mod) => {
      if (!cancelled) setComp(() => mod.default);
    });
    return () => {
      cancelled = true;
    };
  }, []);
  return Comp;
}

/** Below this width the physics simulation stops being explorable -- nodes
 *  overlap, labels collide, and pinch-zoom fights the pan handler. The brief
 *  asks for a graceful degrade rather than a cramped canvas, so under this
 *  breakpoint we render the same data as a tappable list instead. */
const GRAPH_MIN_WIDTH = 768;

/**
 * Plain-scroll-to-zoom (2026-09-08), replacing the Ctrl/Cmd-gated scroll
 * introduced 2026-09-02 -- a deliberate reversal, done on direct request, not
 * a rediscovery of the same bug. That earlier fix traded "scroll always
 * reaches the page" for "zooming needs a modifier"; this trade is now made
 * the other way: scrolling over the canvas zooms the graph directly, and the
 * page underneath is reachable by scrolling from outside the canvas instead.
 *
 * d3-zoom's OWN wheel handling is disabled entirely for this (see
 * enableZoomInteraction below) and replaced with a manual listener, for a
 * reason beyond "make plain scroll zoom": d3-zoom's default sensitivity
 * (d3-zoom/src/zoom.js's defaultWheelDelta) is
 * `-deltaY * (ctrlKey ? 0.02 : 0.002)` -- a demonstrable 10x multiplier
 * whenever ctrlKey is set. The PREVIOUS gate required ctrlKey (or metaKey) to
 * zoom at all, so every zoom this graph has ever done via Ctrl+scroll was
 * ALREADY running through that 10x-sensitivity branch -- confirmed by
 * reading node_modules/d3-zoom/dist/d3-zoom.js, not guessed. That is very
 * likely the real source of "too sensitive": it was never merely "scroll
 * zooms fast", it was specifically "scroll zooms at 10x the library's own
 * baseline rate", every single time, because ctrlKey was mandatory. There is
 * no public prop to override wheelDelta on react-force-graph-2d (checked
 * against its .d.ts), so a manual handler is the only way to actually set
 * sensitivity rather than inherit whichever of d3's two hard-coded rates a
 * given modifier key happens to select.
 */
const WHEEL_ZOOM_SENSITIVITY = 0.0009; // exponent per deltaY unit -- see the effect below
const WHEEL_ZOOM_MIN = 0.3;
const WHEEL_ZOOM_MAX = 8;

/**
 * Dense "nebula" default view (2026-09-02), replacing the 13-category
 * click-to-expand model entirely. The backend's expand="all" mode (see
 * app/public_api.py) returns every reel as a node plus every category as a
 * node, category<->reel membership links, and category<->category
 * co-occurrence links -- the SAME data shape a single-category expand
 * always returned, just for every category simultaneously. Category nodes
 * stay IN the simulation (their membership links are what pulls same-
 * category reels toward a shared anchor, which is the entire mechanism that
 * produces per-category clustering) and, since 2026-09-XX, are drawn too --
 * but only as small dim dots, not the large bubbles of the old category
 * view; see the type checks in paintNode/paintLink. Reusing the existing
 * membership-link
 * physics for clustering, rather than inventing reel-to-reel links the
 * backend doesn't provide, is what keeps this a frontend-only change plus
 * one small backend addition instead of a new graph algorithm.
 */

/** Extra clearance added to each node's radius for collision. Much smaller
 *  than the old category-only value (16) -- reel nodes are themselves much
 *  smaller (radius ~1.5-6px vs a category's ~8-20px), and the "tight,
 *  roughly spherical" nebula look the reference calls for needs nodes that
 *  can sit close together, not spaced as if they were still 13 large
 *  bubbles. */
const COLLIDE_PADDING = 1.5;

/** Reel labels stay hover-only regardless of node count -- 190 permanent
 *  labels would be the exact unreadable-stack problem the category view was
 *  built to avoid, just at a much larger scale. Category anchors draw as
 *  bare dots with no label at all (they are dust, not landmarks -- their
 *  name is still reachable via the native tooltip on hover), so the
 *  always-on threshold constants from the category-only view no longer
 *  apply. */

/** Accent for the rare, deliberate per-node highlight -- value_score 5 reels
 *  (the top of the 1-5 scale, the smallest slice of the real corpus) pull
 *  toward this instead of staying their category's own colour. This is the
 *  ONE colour-blend left after Section C1 (2026-09-06) reverted the
 *  category-identity blend: a 3-anchor "nebula palette" every category's hue
 *  used to be pulled toward used to make two DIFFERENT categories converge on
 *  the same colour, which defeats "which category is this node" at a glance.
 *  Categories are painted in their own real hex again; only this single
 *  high-value accent still blends. */
const NEBULA_ACCENT = "#fbbf24"; // warm yellow, high-value reels only

/** Blends a #rrggbb hex colour toward another by `amount` (0 = original,
 *  1 = fully the target). Used for the value_score-5 accent above and for
 *  dimming a category anchor dot toward the backdrop. */
function mixHex(hex: string, toward: string, amount: number): string {
  const h = hex.replace("#", "");
  const t = toward.replace("#", "");
  const hr = parseInt(h.slice(0, 2), 16), hg = parseInt(h.slice(2, 4), 16), hb = parseInt(h.slice(4, 6), 16);
  const tr = parseInt(t.slice(0, 2), 16), tg = parseInt(t.slice(2, 4), 16), tb = parseInt(t.slice(4, 6), 16);
  const mix = (a: number, b: number) => Math.round(a + (b - a) * amount);
  const toHex = (n: number) => n.toString(16).padStart(2, "0");
  return `#${toHex(mix(hr, tr))}${toHex(mix(hg, tg))}${toHex(mix(hb, tb))}`;
}

function hexToHsl(hex: string): [number, number, number] {
  const h = hex.replace("#", "");
  const r = parseInt(h.slice(0, 2), 16) / 255, g = parseInt(h.slice(2, 4), 16) / 255, b = parseInt(h.slice(4, 6), 16) / 255;
  const max = Math.max(r, g, b), min = Math.min(r, g, b);
  const light = (max + min) / 2;
  let hue = 0, sat = 0;
  if (max !== min) {
    const d = max - min;
    sat = light > 0.5 ? d / (2 - max - min) : d / (max + min);
    if (max === r) hue = (g - b) / d + (g < b ? 6 : 0);
    else if (max === g) hue = (b - r) / d + 2;
    else hue = (r - g) / d + 4;
    hue /= 6;
  }
  return [hue * 360, sat * 100, light * 100];
}

function hslToHex(h: number, s: number, l: number): string {
  h /= 360; s /= 100; l /= 100;
  let r: number, g: number, b: number;
  if (s === 0) {
    r = g = b = l;
  } else {
    const hue2rgb = (p: number, q: number, t: number) => {
      if (t < 0) t += 1;
      if (t > 1) t -= 1;
      if (t < 1 / 6) return p + (q - p) * 6 * t;
      if (t < 1 / 2) return q;
      if (t < 2 / 3) return p + (q - p) * (2 / 3 - t) * 6;
      return p;
    };
    const q = l < 0.5 ? l * (1 + s) : l + s - l * s;
    const p = 2 * l - q;
    r = hue2rgb(p, q, h + 1 / 3);
    g = hue2rgb(p, q, h);
    b = hue2rgb(p, q, h - 1 / 3);
  }
  const toHex = (n: number) => Math.round(n * 255).toString(16).padStart(2, "0");
  return `#${toHex(r)}${toHex(g)}${toHex(b)}`;
}

/**
 * Section C, round 2 (2026-09-07): softer/muted category colour, replacing
 * C1's full-saturation solid fill -- a distinct instruction from, and
 * superseding, the "vivid solid colour" call in the previous round. Pulls
 * saturation down to under half and lifts lightness toward pastel, but goes
 * through HSL rather than mixHex's RGB blend specifically so HUE is
 * untouched: mixHex-toward-anything shifts the hue itself (that's what made
 * the original nebula blend collapse different categories into the same
 * colour), where the actual ask here is "same hue family, calmer version of
 * it" -- categories still need to read as different colours, just quieter
 * ones. Saturation and lightness targets were tuned by eye against a real
 * screenshot (see the session report), not picked blind. */
function mutedHex(hex: string): string {
  const [h, s, l] = hexToHsl(hex);
  return hslToHex(h, s * 0.42, Math.min(80, l + 16));
}

/** Stable per-link pseudo-random integer, from the link's own endpoint ids.
 *  Used to jitter membership link distance so a hub's leaves don't all land
 *  on one exact radius. Must be deterministic: recomputing it per tick would
 *  make the layout shimmer instead of settle. */
function linkJitter(l: any): number {
  const s = typeof l.source === "object" ? l.source.id : l.source;
  const t = typeof l.target === "object" ? l.target.id : l.target;
  const key = `${s}>${t}`;
  let hash = 0;
  for (let i = 0; i < key.length; i++) hash = (hash * 31 + key.charCodeAt(i)) >>> 0;
  return hash;
}

/** Dark backdrop, near-black -- the nebula's own glow supplies the colour;
 *  the background just needs to stay out of the way and give the glow
 *  somewhere dark to bloom into. */
const NEBULA_BACKGROUND = "#050208";

/** The one colour every link is drawn in (2026-09-07, Section C round 2),
 *  replacing per-reel-coloured edges. A muted slate rather than a pure grey
 *  or the background's own value -- visible as structure against
 *  NEBULA_BACKGROUND without reading as a colour category of its own, which
 *  is the entire point of a single neutral link colour. */
const LINK_COLOR = "#64748b";

/**
 * Drawn-radius scale for reel nodes (2026-09-XX) -- the actual lever for the
 * reference image's fine grain, and the reason the earlier zoomToFit-padding
 * change did not deliver it.
 *
 * Apparent grain is a RATIO: drawn node radius over inter-node spacing. Both
 * zoomToFit padding and any uniform shrink of the simulation scale multiply
 * the numerator and denominator by the same factor, so they change how big
 * the cluster looks and nothing about how fine it looks -- tighter padding
 * just magnified the same chunky bubbles. Only changing radius INDEPENDENTLY
 * of spacing moves the ratio.
 *
 * Which is also why this lives here and not in the backend's `val`: `val`
 * feeds forceCollide's radius as well as the drawing, so shrinking it would
 * shrink the dots and the gaps between them together -- the cluster would
 * contract, zoomToFit would zoom back in to fill the frame, and the grain
 * would end up exactly where it started. Scaling at paint time leaves the
 * physics (and therefore the cluster's footprint on screen) untouched, so
 * the same 176 nodes spread over the same area as smaller points.
 *
 * The hit target deliberately does NOT get scaled -- nodePointerAreaPaint
 * still uses the unscaled `val`, so shrinking the dots does not make them
 * harder to hover or click.
 */
const NODE_RADIUS_SCALE = 0.6;

/** Fixed draw radius for a category anchor dot. Deliberately NOT the node's
 *  own `val` -- that is the old visible-bubble formula (4 + sqrt(count)*2,
 *  up to ~20px) and would make each anchor dwarf every reel around it.
 *  Anchors sit just under the smallest DRAWN reel so they read as the finest
 *  dust in the field rather than as peers of the reel nodes: smallest reel
 *  is val 2.4 (value_score 1) * 0.85 (edge coreT) * the scale above.
 *  Derived rather than hand-tuned so it tracks NODE_RADIUS_SCALE instead of
 *  silently becoming reel-sized the next time that changes. Their collide
 *  radius is tuned separately in the force effect and is unrelated to this. */
const ANCHOR_DOT_RADIUS = 2.4 * 0.85 * NODE_RADIUS_SCALE * 0.85;

export function KnowledgeGraph({ initial }: { initial: GraphPayload }) {
  const ForceGraph2D = useForceGraph2D();
  const [data, setData] = useState<GraphPayload>(initial);
  // "Focused" category, purely local now (2026-09-02) -- see focusCategory
  // below. No loading/error state needed anymore: there's no network call
  // left in this component to fail, since `initial` already carries every
  // reel (expand="all", fetched once by the parent page).
  const [expanded, setExpanded] = useState<string | null>(null);
  const [hovered, setHovered] = useState<GraphNode | null>(null);

  const wrapRef = useRef<HTMLDivElement>(null);
  /** The bordered card the canvas sits in -- scroll target for strip clicks. */
  const cardRef = useRef<HTMLDivElement>(null);
  const fgRef = useRef<any>(null);
  const [size, setSize] = useState({ width: 0, height: 0 });

  /**
   * Live force-state readout, gated behind ?debug=graph.
   *
   * Added 2026-08-21 because a headless d3 simulation and a console.log
   * are not evidence the FIX works in the actual deployed page -- only a
   * real screenshot of real state is. Canvas painting depends on rAF,
   * which some environments never fire (documented tool-pane limitation),
   * so this reads and displays the SAME numbers a screenshot would need
   * to show as plain DOM text instead: what force values are actually
   * installed on the live simulation, and where the real node objects
   * (mutated in place by d3, not a re-derived copy) actually sit right
   * now. If this box is wrong, the fix is wrong -- no interpretation
   * required, and it shows up in a plain screenshot like anything else
   * on the page.
   */
  const [debugInfo, setDebugInfo] = useState<string | null>(null);
  const debugOn =
    typeof window !== "undefined" &&
    new URLSearchParams(window.location.search).get("debug") === "graph";

  /**
   * THE SECOND real bug (found 2026-08-21, right after fixing the ref).
   *
   * force-graph's own update() runs `state.forceLayout.stop().alpha(1)
   * .nodes(state.graphData.nodes)` -- and the synchronous warmupTicks loop
   * that follows it -- SYNCHRONOUSLY DURING REACT'S RENDER PHASE, whenever
   * the `graphData` prop changes. That happens BEFORE any useEffect runs,
   * including the one below that installs our custom charge/collide/link
   * forces. If the real API payload lands before ForceGraph2D's own chunk
   * has resolved, the FIRST-EVER graphData assignment carrying real nodes
   * can run its ENTIRE synchronous warmup against a freshly-created,
   * DEFAULT-force simulation (charge -30, no collide) -- and once that
   * warmup settles into ITS OWN local minimum, more ticks later don't move
   * it (confirmed live: bumping warmupTicks 80 -> 400 changed nothing,
   * because the sim had already converged under the WRONG forces).
   *
   * Fix: never let ForceGraph2D see the real graphData until our forces are
   * confirmed installed at least once. Until then it gets EMPTY_GRAPH (0
   * nodes, so whatever forces are or aren't attached is moot). Once the
   * force effect below succeeds even a single time, this flips true and the
   * real data flows through for the first time -- onto a simulation that
   * already has the right forces on it before its warmup ever runs.
   */
  const [forcesReady, setForcesReady] = useState(false);

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

  /**
   * Manual wheel-to-zoom (2026-09-08) -- see WHEEL_ZOOM_SENSITIVITY above
   * for why this exists instead of a prop. A plain, non-passive native
   * listener (not React's onWheel, which React attaches passively by
   * default and so cannot reliably preventDefault) on wrapRef, the exact
   * element already measured for canvas sizing above -- reused rather than
   * adding a second ref for the same DOM node.
   *
   * preventDefault() here is deliberate and reverses the earlier scroll-trap
   * fix on purpose: scrolling over the canvas now zooms the graph instead of
   * scrolling the page, matching the plain-scroll-to-zoom behaviour asked
   * for. The page is still reachable by scrolling from outside the canvas.
   *
   * fg.zoom(k) (not d3-zoom's own scaleTo) is the only zoom entry point used
   * -- it is force-graph's own public setter (confirmed against
   * node_modules/force-graph/dist/force-graph.mjs), goes through the same
   * d3-zoom instance underneath, and keeps this consistent with every other
   * imperative zoom call in this component (zoomToFit, etc.).
   */
  useEffect(() => {
    if (isNarrow) return;
    const el = wrapRef.current;
    if (!el) return;

    const onWheel = (e: WheelEvent) => {
      const fg = fgRef.current;
      if (!fg) return;
      e.preventDefault();
      const current: number = fg.zoom();
      const factor = Math.pow(2, -e.deltaY * WHEEL_ZOOM_SENSITIVITY);
      const next = Math.min(WHEEL_ZOOM_MAX, Math.max(WHEEL_ZOOM_MIN, current * factor));
      fg.zoom(next, 0); // 0ms transition -- an immediate response is what "scroll to zoom" needs
    };

    el.addEventListener("wheel", onWheel, { passive: false });
    return () => el.removeEventListener("wheel", onWheel);
  }, [isNarrow]);

  /**
   * Local-only focus (2026-09-02) -- no network call, no loading/error
   * state. `data` already holds every reel across every category (the
   * parent page fetches expand="all" once), so "focusing" a category is
   * just dimming/filtering what's already in hand. Replaces the old
   * `load(category)` re-fetch entirely; the mobile list (GraphFallbackList)
   * uses this exact same setter for its drill-down view.
   */
  const focusCategory = useCallback((category: string | null) => {
    setExpanded(category);
  }, []);

  /**
   * The category strip's PRIMARY action: focus the category, then make sure
   * the result is actually on screen.
   *
   * Focusing reuses focusCategory unchanged -- there is no separate
   * "expand to reels" path to call. Since the move to expand="all" every
   * reel is always on the canvas, so focusing a category highlights its nodes
   * and dims the rest rather than fetching anything. (Clicking a category
   * node on the canvas does nothing at all: anchors draw at ~2px and
   * deliberately carry no click action -- see handleNodeClick.)
   *
   * Scrolls only when SELECTING. Un-pinning by clicking the same tile again
   * passes null, and yanking the viewport around for "I've cleared the
   * filter" would be motion the user did not ask for.
   */
  const focusCategoryAndReveal = useCallback(
    (category: string | null) => {
      focusCategory(category);
      if (!category) return;
      cardRef.current?.scrollIntoView({ behavior: "smooth", block: "center" });
    },
    [focusCategory],
  );

  const handleNodeClick = useCallback((node: any) => {
    // Category anchors are drawn (faintly) but carry no click action: at
    // ~1px they are far too small to be a reliable target, so focusing a
    // category stays legend-only. A reel node still opens its source post.
    if (node.type === "reel" && node.shortcode) {
      window.open(`https://www.instagram.com/reel/${node.shortcode}/`, "_blank", "noopener");
    }
  }, []);

  /**
   * Force tuning for the dense nebula view (2026-09-02), retuned from the
   * ground up for ~190 mostly-tiny reel nodes plus ~13 barely-visible
   * category anchors, not 13 large category bubbles. The category-only
   * tuning (charge -2400, link distance up to 380) was sized for nodes with
   * radius 8-20px needing real separation from each other; reel nodes are
   * radius ~1.5-6px and the brief explicitly wants them "tight and roughly
   * spherical", so charge/link distance both come down by roughly an order
   * of magnitude, and collide radius is now TYPE-AWARE: a category anchor
   * keeps its full formula-derived `val` for correctly spacing category
   * CLUSTERS apart from each other, but that same large radius would
   * otherwise bulldoze the small reel dots away from their own cluster
   * centre if collide treated it like a visible node -- so an anchor's
   * effective collide radius is capped small. Same for charge: an anchor
   * repels at a much gentler strength than reel nodes do, since its job is
   * to be an attraction point (via its membership links), not an obstacle.
   *
   * forceCenter(0, 0) is new here too -- keeps the cluster's node POSITIONS
   * anchored to the origin so repeated reheats (e.g. a future data change)
   * can't let the average position cumulatively drift. This is a genuinely
   * different problem from "the camera stays panned away after a user
   * drags it", which forceCenter cannot fix at all (it only ever moves node
   * positions, never the pan/zoom transform) -- see onUserZoomOrPan above
   * for that half of the fix.
   */
  useEffect(() => {
    let cancelled = false;
    let tries = 0;

    const applyForces = () => {
      if (cancelled) return;
      const fg = fgRef.current;

      // THE REAL BUG (found 2026-08-20, confirmed in the live console).
      // ForceGraph2D is a dynamic(ssr:false) import, so on first load its
      // chunk has NOT resolved when this effect first fires -- fgRef.current
      // is still null. The previous version did `if (!fg) return;` and, since
      // `data` never changed again, NEVER re-ran: every force below was
      // silently skipped and d3ReheatSimulation() never fired. The graph ran
      // on d3's raw defaults (charge -30 / link 30) the entire time -- the
      // exact collapsed clump every prior "fix" was trying to solve. The math
      // was always right; it just never reached a live simulation. So instead
      // of no-opping, wait for the ref to mount, then apply.
      if (!fg) {
        if (tries++ < 60) setTimeout(applyForces, 50); // up to ~3s
        return;
      }

      // Retuned 2026-09-02 for ~190 mostly-tiny reel nodes instead of 13
      // large category bubbles -- see the block comment above. Charge is
      // TYPE-AWARE: reel nodes repel each other gently (just enough that
      // collide has room to resolve contacts without a rigid packed grid),
      // while category anchors repel at a much smaller magnitude so they
      // behave as attraction points for their own membership links rather
      // than pushing the reel cluster apart. distanceMax is pulled way in
      // too -- 1000 was sized so 13 well-separated bubbles could feel each
      // other's repulsion across the whole canvas; at reel scale that same
      // range just flattens the whole nebula into one diffuse haze instead
      // of a tight sphere with a bright, dense centre.
      fg.d3Force("charge")
        ?.strength((n: any) => (n.type === "category" ? -18 : -34))
        .distanceMax(220);

      fg.d3Force("link")
        // Short membership distance is what actually produces "tight and
        // roughly spherical" -- it pulls each reel in close to its
        // category anchor instead of letting charge push it out to where
        // the old 110px separation used to land it. Co-occurrence links
        // (category<->category) get a modest distance so category
        // clusters still sit apart from each other rather than fully
        // overlapping into one indistinguishable ball.
        // Membership distance is jittered deterministically per link rather
        // than fixed at 18. A hub whose leaves all sit at exactly one radius
        // resolves into a geometrically perfect annulus once collide spaces
        // them evenly -- which is what made the "other" bucket read as a
        // machine-drawn ring rather than a cluster. +/- a few units is enough
        // to break the symmetry without loosening the clustering. Hashed off
        // the link's own endpoints so it is stable across reheats instead of
        // shimmering every tick.
        ?.distance((l: any) =>
          l.type === "membership" ? 14 + (linkJitter(l) % 9) : 70,
        )
        .strength((l: any) =>
          l.type === "membership" ? 0.75 : Math.min(0.08, 0.01 * (l.value ?? 1)),
        );

      // The hard constraint. Radius is TYPE-AWARE for the same reason charge
      // is: a category anchor's `val` is the old category-sizing formula
      // (4 + sqrt(count)*2, up to ~20px) meant for a VISIBLE bubble that
      // needed its own clearance. Left as-is here, that large radius --
      // still far bigger than the ~1px dot an anchor now draws as --
      // would bulldoze the small reel dots parked around it away
      // from their own cluster centre -- collide has no concept of
      // how small it is drawn, it just sees a big circle. So an anchor's collide
      // radius is capped small; only real reel nodes use their full `val`.
      fg.d3Force(
        "collide",
        forceCollide()
          .radius((n: any) => (n.type === "category" ? 3 : (n.val ?? 3) + COLLIDE_PADDING))
          .strength(0.85)
          .iterations(2),
      );

      // Softened 2026-09-XX: forceCenter(0,0) was replaced with a
      // forceX/forceY pull (same idea, no longer a hard re-centering
      // constraint every tick). forceCenter effectively recomputes and
      // cancels the simulation's average position each tick, which fights a
      // user-driven pan just as hard as it fights drift -- there is no way
      // to tell the two apart from inside forceCenter's own math. A per-node
      // pull toward the origin keeps the cluster from wandering off over
      // repeated reheats, and makes paintNode's coreT distance-from-centre
      // falloff physically real (nodes truly do sit closer to (0,0) nearer
      // the cluster's centre). This is simulation-space only either way --
      // it has no effect on a panned/zoomed CAMERA, which forceX/forceY
      // (like forceCenter before it) structurally cannot touch; see the
      // Recenter button below for that half, user-triggered rather than
      // auto-firing on idle.
      //
      // 0.03 -> 0.09 (2026-09-03): forceX/forceY are the ONLY forces acting
      // on a disconnected component apart from charge, which pushes it away.
      // The "other" bucket is exactly that: cat:other carries 30 membership
      // links and zero co-occurrence links, and each of its 30 reels has
      // degree 1, so the whole star is structurally severed from the rest of
      // the graph (verified against the live payload, not assumed). At 0.03
      // charge won and it drifted off as a detached satellite, which both
      // read as an artifact and forced zoomToFit to zoom out to include it.
      //
      // 0.09 -> 1.0 (2026-09-06, Section C2 of the nav/security/graph audit).
      // The brief's own reference point -- Obsidian's real graph settings,
      // repel:center = 11.63:0.81, roughly 14:1 -- was explicitly NOT a
      // literal value to copy (Obsidian's simulation units are its own,
      // unrelated to d3-force's), only a directional signal that OUR
      // repel:center ratio (charge magnitude 34 : centering strength 0.09,
      // roughly 378:1) was far more repel-dominant than a graph most people
      // already recognise as "circular". Tested iteratively against
      // ?debug=graph's bbox ratio, not guessed at once: 0.09->0.20 barely
      // moved it (1.19->1.20 -- isotropic centering alone can't fix an
      // asymmetry baked into the link/charge topology, it can only shrink
      // the whole shape uniformly), 0.20->0.4->0.7 progressively closed it
      // (1.20->1.08->1.02), and 1.0 landed at 0.99 -- effectively the 1:1
      // Obsidian-style circle the brief asked for. Pushed one step further
      // to 1.4 to confirm 1.0 wasn't leaving gains on the table: ratio did
      // NOT improve (1.01, no better than 1.0's 0.99) but collide started
      // losing (2/1218 sampled overlaps, the first non-zero reading across
      // every value tested) -- so 1.0 is the actual ceiling, not an
      // arbitrary stop. Every reading held steady between the +1.5s and +4s
      // debug snapshots at each step, confirming a real settled equilibrium,
      // not a still-moving mid-simulation snapshot.
      fg.d3Force("x", forceX(0).strength(1.0));
      fg.d3Force("y", forceY(0).strength(1.0));

      // Reheat so the forces above actually move a simulation that mounted and
      // cooled on d3's defaults. d3AlphaDecay / d3VelocityDecay are set as
      // PROPS on the component, not here -- they're not on the imperative
      // handle and calling them would throw.
      fg.d3ReheatSimulation();

      // See the comment on forcesReady above: this is the moment forces are
      // confirmed on the simulation, so it's the moment it's safe to let
      // ForceGraph2D see the real graphData for the first time.
      setForcesReady(true);

      if (debugOn) snapshotDebug(fg, "reheat");
    };

    // Snapshot what's ACTUALLY installed and where the REAL node objects
    // actually are -- not a re-derived copy, the exact objects d3 mutates via
    // .tick(). Sampled at three points so the overlay shows progression
    // (or the lack of it) rather than one moment that could be misleading:
    // immediately after reheat (t=0, before rAF has had any chance to run
    // more ticks), and twice more after real wall-clock time has passed, by
    // which point a real browser's rAF loop should have run many cooldown
    // ticks if the reheat genuinely took.
    const snapshotDebug = (fg: any, label: string) => {
      const collide = fg.d3Force("collide");
      const centerX = fg.d3Force("x");
      const centerY = fg.d3Force("y");
      // Reel nodes only -- these are the ones actually drawn, so their
      // spread is what "tight and roughly spherical" needs verifying
      // against, not the faint category anchors.
      const reels = (data.nodes as any[]).filter(
        (n) => n.type === "reel" && Number.isFinite(n.x) && Number.isFinite(n.y),
      );
      const cx = reels.reduce((s, n) => s + n.x, 0) / (reels.length || 1);
      const cy = reels.reduce((s, n) => s + n.y, 0) / (reels.length || 1);
      const dists = reels.map((n) => Math.hypot(n.x - cx, n.y - cy));
      const meanR = dists.reduce((s, d) => s + d, 0) / (dists.length || 1);
      const maxR = dists.length ? Math.max(...dists) : 0;
      let overlaps = 0,
        pairs = 0;
      for (let i = 0; i < reels.length; i++) {
        for (let j = i + 1; j < Math.min(reels.length, i + 8); j++) {
          const a = reels[i],
            b = reels[j];
          const d = Math.hypot(a.x - b.x, a.y - b.y);
          const gap = d - (a.val + b.val);
          pairs++;
          if (gap < 0) overlaps++;
        }
      }
      // Bounding box and its aspect ratio: the check for "is the equilibrium
      // actually circular". 1.00 is a square bbox (circular blob); a detached
      // satellite component shows up here as a ratio far from 1 long before
      // it is obvious by eye.
      const xs = reels.map((n) => n.x), ys = reels.map((n) => n.y);
      const bw = xs.length ? Math.max(...xs) - Math.min(...xs) : 0;
      const bh = ys.length ? Math.max(...ys) - Math.min(...ys) : 0;
      const ratio = bh > 0 ? bw / bh : 0;
      // Category anchors are measured separately because zoomToFit frames
      // EVERY node, not just the drawn reels -- so a single anchor thrown
      // wide is enough to make the fit zoom out until the whole nebula is a
      // speck, while the reel-only numbers above still look perfectly healthy.
      const cats = (data.nodes as any[]).filter(
        (n) => n.type === "category" && Number.isFinite(n.x) && Number.isFinite(n.y),
      );
      const catMax = cats.length
        ? Math.round(Math.max(...cats.map((n) => Math.hypot(n.x, n.y))))
        : 0;
      const line =
        `[${label} @ ${new Date().toISOString().slice(11, 19)}] ` +
        `bbox=${Math.round(bw)}x${Math.round(bh)} ratio=${ratio.toFixed(2)} ` +
        `anchorMaxDist=${catMax}px ` +
        `x/y=${centerX && centerY ? "present" : "MISSING"} collide=${collide ? "present" : "MISSING"} ` +
        `reels=${reels.length} centroid=(${Math.round(cx)},${Math.round(cy)}) ` +
        `meanRadiusFromCentroid=${Math.round(meanR)}px maxRadius=${Math.round(maxR)}px ` +
        `overlaps(sampled)=${overlaps}/${pairs}`;
      setDebugInfo((prev) => (prev ? prev + "\n" + line : line));
    };

    applyForces();
    let t1: ReturnType<typeof setTimeout> | undefined;
    let t2: ReturnType<typeof setTimeout> | undefined;
    if (debugOn) {
      t1 = setTimeout(() => fgRef.current && snapshotDebug(fgRef.current, "+1.5s"), 1500);
      t2 = setTimeout(() => fgRef.current && snapshotDebug(fgRef.current, "+4s"), 4000);
    }
    return () => {
      cancelled = true;
      clearTimeout(t1);
      clearTimeout(t2);
    };
  }, [data, debugOn]);

  /**
   * The zero-degree pinning effect that used to live here was REMOVED
   * (2026-09-03) because it had silently become dead code, and the "detached
   * ring" it was blamed for has a different cause entirely.
   *
   * Its premise -- "cat:other has degree 0 and structurally always will" --
   * was true of the OLD category-level payload, where the only links were
   * co-occurrence edges and a one-element category set can never produce a
   * pair. Under expand="all" every category also carries a membership link
   * per reel, so cat:other now has degree 30 and NO node in the payload has
   * degree 0 at all (checked against the live response: zero matches). The
   * effect therefore pinned nothing, and re-homing ISOLATED_ANCHOR would
   * have changed nothing either.
   *
   * What actually produced the ring: cat:other has 30 membership links and
   * zero co-occurrence links, and each of its 30 reels has degree exactly 1,
   * so {cat:other + its reels} is a DISCONNECTED star component. Nothing
   * links it to the rest of the graph, so only forceX/forceY hold it near
   * the origin while charge pushes it away -- and 30 leaves at one identical
   * link distance resolve into a geometrically perfect annulus. Both halves
   * are addressed in the force effect above: forceX/forceY strength raised
   * to 0.09 so the component is held in, and membership distance jittered
   * per-link so the ring cannot be perfect.
   */

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
    // Padding tightened 40 -> 12 (2026-09-XX): with only ~190 real nodes
    // there is no way to reach a 2000-node reference image's grain, but the
    // same nodes filling more of the 480px frame gets closer to its density
    // without inventing data. 12 still keeps the outermost dots off the
    // rounded corners.
    fgRef.current?.zoomToFit(400, 12);
  }, []);

  /**
   * Suppresses exactly ONE onEngineStop re-fit: the one caused by the user
   * dragging a node (2026-09-02).
   *
   * Enabling enableNodeDrag brought back a camera move nobody asked for.
   * force-graph's drag-end handler calls `d3AlphaTarget(0).resetCountdown()`
   * to let the released node settle, and resetCountdown zeroes cntTicks --
   * so once the sim cools, `if (++state.cntTicks > state.cooldownTicks)
   * state.onEngineStop()` (force-graph.mjs:539) fires like any other engine
   * stop. Wired straight to frameGraph, that re-fits the camera a couple of
   * seconds after every node drag, which is indistinguishable to a user from
   * the idle pan-snap that was deliberately removed earlier.
   *
   * The distinction worth preserving is WHO caused the motion. A data change
   * or the initial load should frame the graph; a disturbance the user made
   * on purpose should be left alone, and the node's own drift back to
   * equilibrium is the whole point of allowing the drag. So the flag is set
   * while dragging and consumed by the next engine stop.
   *
   * Set from onNodeDrag rather than a drag-start hook because
   * react-force-graph-2d exposes no onNodeDragStart (confirmed against
   * react-force-graph-2d.d.ts: only onNodeDrag and onNodeDragEnd). onNodeDrag
   * fires on every drag move, so the flag is reliably true well before
   * release -- and it is a ref, not state, precisely so setting it on every
   * mousemove costs no re-render.
   */
  const skipNextEngineStopFit = useRef(false);

  const handleEngineStop = useCallback(() => {
    if (skipNextEngineStopFit.current) {
      // Consume the flag rather than leaving it set: the NEXT engine stop
      // (a data change, say) should still frame the graph normally.
      skipNextEngineStopFit.current = false;
      return;
    }
    frameGraph();
  }, [frameGraph]);

  useEffect(() => {
    if (!data.nodes.length) return;
    // Mid-flight fit so the graph is framed while it is still expanding;
    // onEngineStop re-fits once it settles.
    const t = setTimeout(frameGraph, 900);
    return () => clearTimeout(t);
  }, [data, frameGraph]);

  /**
   * Nebula node rendering (2026-09-02), replacing the 3-layer glassmorphic
   * bubble treatment entirely -- that was built for 13 large, well-spaced
   * category discs; painted onto ~190 small reel nodes at once it would be
   * both far too expensive (3 shadowBlur passes * 190 nodes every tick) and
   * visually wrong, since the reference image is small glowing points of
   * light, not frosted-glass spheres.
   *
   * Category anchor nodes take an early branch of their own: a small dim
   * dot, no glow, no label (see ANCHOR_DOT_RADIUS). Their real job is still
   * the membership-link clustering effect described in the force-tuning
   * comment above; drawing them is just a way to add real points to a field
   * that only has ~190 of them. Everything after that branch runs for reel
   * nodes only.
   *
   * Colour: each reel is painted in its OWN category's hue -- no blending
   * toward a shared 3-hue nebula palette (removed 2026-09-06, Section C1;
   * that blend pulled every category toward whichever of purple/pink/blue
   * its id happened to hash to, which is what made two DIFFERENT categories
   * start looking like the same colour). Round 2 (2026-09-07) softened that
   * further: category hex now goes through mutedHex (HSL desaturate +
   * lighten, hue untouched) rather than being painted at full saturation --
   * calmer/pastel, but categories are still each other's real hue, just
   * quieter, so "which category is this" still reads at a glance. A high
   * value_score (5, the top of the 1-5 scale) still pulls toward the warm
   * yellow accent, unmuted -- that is a distinct, deliberate per-node
   * highlight, and a softened accent would defeat its own point.
   *
   * Brightness/size: single ctx.shadowBlur glow pass per node (one draw
   * call, not three) with size and glow radius modulated by distance from
   * the simulation origin -- the weak forceX/forceY pull toward (0,0) above
   * keeps that origin genuinely at the cluster's centre, so "closer to
   * (0,0)" reliably means "closer to
   * the visual centre of the nebula", which is what lets nodes near the
   * middle read as the bright, saturated core and nodes further out fade
   * smaller and dimmer toward the edge, per the reference image.
   */
  const paintNode = useCallback(
    (node: any, ctx: CanvasRenderingContext2D, globalScale: number) => {
      // Category anchors: drawn (2026-09-XX), but deliberately faint and
      // small rather than at their formula-derived `val` (which is sized for
      // the old visible-bubble view and would tower over every reel dot).
      // They were skipped entirely before; showing them adds ~13 real points
      // to the field at zero data cost, since the simulation already
      // positions them at the centre of each category's own reel cluster.
      // Kept as a separate early-return branch, not folded into the reel
      // path, because almost none of the reel treatment below (value_score
      // accent, coreT sizing, hover label) is meaningful for an anchor.
      if (node.type === "category") {
        if (!Number.isFinite(node.x) || !Number.isFinite(node.y)) return;
        const dimmed = expanded !== null && node.category !== expanded;
        ctx.save();
        ctx.globalAlpha = dimmed ? 0.12 : 0.35;
        ctx.beginPath();
        ctx.arc(node.x, node.y, ANCHOR_DOT_RADIUS, 0, 2 * Math.PI);
        // Desaturated toward the backdrop so an anchor reads as part of the
        // dust rather than competing with the reels it sits among.
        ctx.fillStyle = mixHex(node.color, NEBULA_BACKGROUND, 0.45);
        ctx.fill();
        ctx.restore();
        return;
      }

      const r = node.val;
      // The force simulation assigns x/y on its first tick, so the very first
      // paint can arrive with them still undefined. A non-finite arc radius
      // or gradient argument throws and kills the whole canvas (confirmed
      // live in an earlier session) -- guard against that.
      if (!Number.isFinite(node.x) || !Number.isFinite(node.y) || !Number.isFinite(r)) return;

      const isHovered = hovered?.id === node.id;
      const dim = expanded !== null && node.category !== expanded;

      // Distance-from-centre drives the "bright core, soft edge" look. 260
      // is roughly the outer radius the retuned forces settle the cluster
      // into (verified against the debug overlay, not guessed) -- past that,
      // brightness/size bottom out rather than continuing to fade to nothing.
      const dist = Math.hypot(node.x, node.y);
      const coreT = Math.max(0, 1 - dist / 260); // 1 at centre, 0 at/past the edge

      // Section C, round 2: solid and still per-category (no nebula-palette
      // blend), but through mutedHex rather than node.color directly -- a
      // calmer, more desaturated version of each category's real hue, not
      // the vivid full-saturation fill round 1 shipped. The value_score-5
      // accent stays vivid on purpose: it is a rare, deliberate highlight,
      // and a muted accent would defeat its own point of standing out.
      const baseColor =
        node.value_score >= 5 ? mixHex(node.color, NEBULA_ACCENT, 0.7) : mutedHex(node.color);

      // Size: mostly the backend's own val (already value_score-weighted),
      // nudged up slightly for centre nodes so the middle of the cluster
      // reads as visibly denser/bigger, per the brief's "brightest/biggest
      // at centre" instruction.
      const drawR = r * (0.85 + coreT * 0.35) * NODE_RADIUS_SCALE;

      ctx.save();
      if (dim) ctx.globalAlpha = 0.22;

      // Single glow pass. Blur and fill alpha both scale with coreT so
      // centre nodes genuinely read as "white-hot" and edge nodes fade
      // toward a dim, small point rather than every node having identical
      // presence regardless of position.
      ctx.shadowColor = baseColor;
      ctx.shadowBlur = ((isHovered ? 22 : 10) + coreT * 10) / Math.max(globalScale, 0.6);
      ctx.beginPath();
      ctx.arc(node.x, node.y, drawR, 0, 2 * Math.PI);
      ctx.fillStyle = isHovered ? "#ffffff" : baseColor;
      ctx.globalAlpha *= 0.55 + coreT * 0.4;
      ctx.fill();
      ctx.shadowBlur = 0;
      ctx.shadowColor = "transparent";
      ctx.globalAlpha = dim ? 0.22 : 1;

      // A brighter, near-white core disc on top -- the "white-hot centre"
      // the reference calls out, applied to individual high-coreT nodes
      // rather than the whole cluster, so it reads as texture within the
      // nebula rather than a flat wash.
      if (coreT > 0.55 || isHovered) {
        ctx.beginPath();
        ctx.arc(node.x, node.y, drawR * 0.45, 0, 2 * Math.PI);
        ctx.fillStyle = `rgba(255,255,255,${(isHovered ? 0.9 : coreT * 0.6).toFixed(2)})`;
        ctx.fill();
      }
      ctx.restore();

      /**
       * Label policy: hover-only, always -- with ~190 nodes on screen at
       * once there is no zoom-dependent threshold that avoids either an
       * illegible permanent pile (low zoom) or a still-crowded label field
       * (high zoom, since reels cluster tightly by design here). A single
       * hover-only rule is simpler and matches what the mobile list already
       * provides as the browsable alternative.
       */
      if (!isHovered) return;

      ctx.save();
      // Text is drawn in GRAPH units and then scaled by globalScale, so
      // `11 / globalScale` is what holds it at a constant 11 screen px. The
      // old `Math.max(11, 12 / globalScale)` floor inverted that above
      // ~1.09x zoom: once 12/globalScale fell under 11 the constant won, and
      // rendered size (fontSize * globalScale) grew without bound -- 22px at
      // 2x, 44px at 4x. Zooming in to read a label made it balloon.
      // 11 -> 7 screen px (2026-09-03): 11 read as oversized against ~3px
      // dots even before the double-render above was making it worse.
      const fontSize = 7 / globalScale;
      ctx.font = `400 ${fontSize}px ui-sans-serif, system-ui, sans-serif`;
      ctx.textAlign = "center";
      ctx.textBaseline = "top";

      const label = node.label;
      const y = node.y + drawR + 6 / globalScale;

      // Halo thinned (3.5 -> 2 screen px): 3.5px of stroke around an 11px
      // glyph read as a black outline rather than as separation from the
      // backdrop. Fill muted off near-white so a label sits quietly on the
      // field until it is the one being hovered.
      ctx.lineWidth = 1.5 / globalScale;
      ctx.strokeStyle = "rgba(5,2,8,0.9)";
      ctx.strokeText(label, node.x, y);
      ctx.fillStyle = "rgba(226,232,240,0.72)";
      ctx.fillText(label, node.x, y);
      ctx.restore();
    },
    [hovered, expanded],
  );

  /**
   * Links are deliberately near-invisible in the nebula view (2026-09-02).
   * (Anchors themselves became faintly visible later, but at ~1px they are
   * still far too small to make a line drawn to them read as structure.)
   *
   * The reference image is a field of small glowing points with NO visible
   * line mesh -- density comes entirely from the dots. Every link in this
   * data model touches at least one category anchor (membership links run
   * reel<->anchor; co-occurrence links run anchor<->anchor), and anchors are
   * barely visible, so a fully-opaque link would visibly draw a line from a
   * bright dot out to what looks like nowhere, which reads as broken rather
   * than as structure. So:
   *   - co-occurrence links (anchor<->anchor) are skipped entirely -- both
   *     endpoints are ~1px dust, so drawing them can only ever look wrong,
   *     never informative.
   *   - membership links (reel<->anchor) draw only extremely faintly, and
   *     only when a category is focused/hovered-relevant, as a subtle cue
   *     that a group of nearby dots belongs together, without reading as a
   *     grid or web the way the old category view's edges did.
   */
  const paintLink = useCallback(
    (link: any, ctx: CanvasRenderingContext2D) => {
      if (link.type !== "membership") return;

      const s = link.source;
      const t = link.target;
      if (!s || !t || typeof s !== "object" || typeof t !== "object") return;
      if (!Number.isFinite(s.x) || !Number.isFinite(s.y)) return;
      if (!Number.isFinite(t.x) || !Number.isFinite(t.y)) return;

      const reel = s.type === "reel" ? s : t;
      const focussed = expanded !== null && reel.category === expanded;
      if (expanded !== null && !focussed) return;

      ctx.beginPath();
      ctx.moveTo(s.x, s.y);
      ctx.lineTo(t.x, t.y);
      // Raised 2026-09-03. "0C" is alpha 12/255 -- under 5% -- which is why
      // edges read as noise rather than as connections. Staying thin is the
      // right style, but thin and *visible* are independent knobs, and only
      // the opacity was wrong.
      //
      // Single neutral colour for every link, not per-category (2026-09-07,
      // Section C, round 2). `${reel.color}` used to tint each edge with
      // whichever reel it happened to touch, which meant edges were
      // competing with the (now-muted) node fills for the same attention --
      // a link's job here is to read as quiet structure, not as another
      // category signal. LINK_COLOR is a fixed slate, one step up from
      // NEBULA_BACKGROUND, so edges stay visible against the near-black
      // canvas without carrying any colour identity of their own.
      ctx.strokeStyle = `${LINK_COLOR}${focussed ? "66" : "40"}`;
      ctx.lineWidth = 0.7;
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
          {expanded && currentCategory ? (
            <>
              <Button size="sm" variant="outline" onClick={() => focusCategory(null)}>
                <ArrowLeft className="h-3.5 w-3.5" />
                All categories
              </Button>
              <Badge
                className="border-transparent text-white"
                style={{ backgroundColor: currentCategory.color }}
              >
                {currentCategory.label} · {currentCategory.count}
              </Badge>
            </>
          ) : (
            <p className="text-sm text-muted-foreground">
              {isNarrow
                ? "Tap a category below to explore it."
                : /* 2026-09-08: plain scroll now zooms the graph directly
                     (see WHEEL_ZOOM_SENSITIVITY below) rather than requiring
                     Ctrl/Cmd, reversing the earlier scroll-trap fix on
                     purpose. The hint flips to match: it used to explain the
                     escape hatch out of an accidental zoom-trap; now it
                     tells a visitor who scrolls straight onto the canvas
                     that this zooms rather than moving the page. */
                  "Scroll to zoom the graph · drag a node or the canvas to move it"}
            </p>
          )}
        </div>
        <div className="flex items-center gap-3">
          {!isNarrow && (
            // Manual recenter (replaces the auto idle-recentre removed
            // 2026-09-XX -- see the note on frameGraph below). Same
            // zoomToFit() call as initial load/onEngineStop, just
            // user-triggered instead of firing on its own a few seconds
            // after the user stops panning.
            <Button
              size="sm"
              variant="outline"
              className="gap-1.5"
              onClick={frameGraph}
              title="Recenter the graph"
            >
              <LocateFixed className="h-3.5 w-3.5" />
              Recenter
            </Button>
          )}
          <p className="text-sm tabular-nums text-muted-foreground">
            {data.total_reels.toLocaleString()} saves · {data.categories.length} categories
          </p>
        </div>
      </div>

      {/* Live force-state readout -- see the comment on debugInfo above.
          Plain DOM text, ?debug=graph only, verifiable in a screenshot
          without needing canvas paint or DevTools. */}
      {debugOn && (
        <pre className="mb-3 whitespace-pre-wrap rounded-lg border border-emerald-300 bg-emerald-50 p-3 font-mono text-xs text-emerald-900">
          {debugInfo ?? "(waiting for fgRef to mount...)"}
        </pre>
      )}

      {/* A thin gradient hairline border, not glass -- the frosted/translucent
          fill this used to sit on was removed (2026-08-21). GlowingEffect is
          an absolutely-positioned overlay that lights the border edge nearest
          the cursor; it is inert on touch (no pointer to track), so the
          mobile list view below loses nothing. */}
      <div
        ref={cardRef}
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
            // Flat near-black backdrop for the canvas view (2026-09-02) --
            // applied via inline style below, since it needs to match
            // NEBULA_BACKGROUND exactly. GraphFallbackList (the <768px view)
            // is styled entirely in dark text for a light background and was
            // out of scope for this pass, so it deliberately keeps bg-white.
            isNarrow && "bg-white",
            // Shrunk from 760px (2026-09-02): the brief asked for the section
            // to stop dominating the viewport. 480px still comfortably shows
            // the whole nebula at a normal zoomToFit framing, while leaving
            // page content above/below visible without scrolling through an
            // oversized graph first.
            isNarrow ? "h-auto" : "h-[480px]",
          )}
          style={isNarrow ? undefined : { backgroundColor: NEBULA_BACKGROUND }}
        >
          {isNarrow ? (
            <GraphFallbackList data={data} expanded={expanded} onExpand={focusCategory} />
          ) : !ForceGraph2D ? (
            // Module still resolving (see useForceGraph2D above) -- same
            // spinner next/dynamic's `loading` option used to show.
            <div className="flex h-full items-center justify-center text-muted-foreground">
              <Loader2 className="h-5 w-5 animate-spin" />
            </div>
          ) : (
            size.width > 0 && (
              <ForceGraph2D
                ref={fgRef}
                // Gated on forcesReady -- see the comment above. Ensures
                // ForceGraph2D never runs its synchronous warmup against
                // real nodes before our custom forces are actually on the
                // simulation.
                graphData={(forcesReady ? data : EMPTY_GRAPH) as any}
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
                // Category anchors ONLY (2026-09-03). This prop drives
                // force-graph's HTML tooltip (.float-tooltip-kap), which is a
                // completely separate render path from the label paintNode
                // draws on the canvas. Returning a string for reels meant a
                // hovered reel printed its name TWICE -- the canvas copy under
                // the node with its dark halo, and the tooltip copy tracking
                // the cursor a few px away -- which read as one ghosted,
                // double-printed label. Anchors keep the tooltip because they
                // draw no canvas label at all and are only ~2px wide, so it is
                // their only means of identification. "" is falsy, so no
                // tooltip element is shown for reels.
                nodeLabel={(n: any) =>
                  n.type === "category" ? `${n.label} — ${n.count} saves` : ""
                }
                linkCanvasObject={paintLink}
                // Simulation pacing lives here, as PROPS -- these are not
                // available on the imperative handle (see the note in the
                // force effect). Library defaults are 0.0228 / 0.4 (confirmed
                // against node_modules/force-graph/dist/force-graph.mjs, not
                // assumed); this codebase has never actually run at those --
                // an earlier round already moved them to 0.0115 / 0.3 so a
                // 13-node layout wouldn't freeze mid-expansion.
                //
                // 0.0115 -> 0.01 and 0.3 -> 0.2 (2026-09-08, damping/cooling
                // sweep -- the one axis of force-tuning no prior round had
                // touched; every earlier round adjusted force MAGNITUDE
                // (charge/repel/link/center), never how slowly the
                // simulation cools once released). Lower alphaDecay means
                // more ticks pass before alpha decays toward its stopping
                // point, i.e. slower cooling, a longer settle. Lower
                // velocityDecay is friction on each node's own velocity
                // (unrelated to alpha) -- less of it means a released node
                // (drag-end, in particular, now that node drag is enabled)
                // carries more momentum into its settle instead of damping
                // out almost immediately.
                d3AlphaDecay={0.01}
                d3VelocityDecay={0.2}
                // Ticks run SYNCHRONOUSLY before the first paint (a plain
                // for-loop over forceLayout.tick() -- see
                // force-graph/src/canvas-force-graph.js), so the graph
                // arrives already spread rather than visibly un-clumping
                // over the next few seconds. 400 matches the tick count a
                // headless run of these exact force params needed to reach
                // 0 overlaps (789x827, 141px min gap) against the live
                // corpus -- warmup and cooldown ticks are the same
                // forceLayout.tick() call either way, so this makes the
                // FIRST paint do the convergence work that used to depend on
                // cooldownTicks running via requestAnimationFrame. NOT
                // touched by this sweep -- it governs the one-time initial
                // layout, a different concern from interactive re-settling.
                warmupTicks={400}
                // Interactive re-settling only (category expand/collapse,
                // and now node-drag release). 200 -> 600 in the same sweep
                // as the alphaDecay change above, and for the same reason:
                // a lower alphaDecay means MORE ticks are needed to reach the
                // same low-alpha stopping point (roughly 460 ticks at 0.0115
                // vs. roughly 530 at 0.01, by alpha = (1-decay)^ticks), so
                // raising cooldownTicks isn't just "let it settle longer" in
                // the abstract -- it's what keeps this sweep's own slower
                // decay from being cut off mid-settle by a tick budget sized
                // for the old, faster decay. cooldownTime is left at its
                // 15000ms library default (confirmed in force-graph.mjs,
                // not assumed) -- 600 ticks is comfortably under that at any
                // plausible frame rate, so it was never the binding
                // constraint and doesn't need raising to matter here.
                cooldownTicks={600}
                onEngineStop={handleEngineStop}
                // Marks the settle that follows a user's own drag so
                // handleEngineStop skips its re-fit -- see the comment there.
                onNodeDrag={() => {
                  skipNextEngineStopFit.current = true;
                }}
                // Plain-scroll-to-zoom (2026-09-08) supersedes the earlier
                // Ctrl/Cmd-gated scroll-trap fix -- see
                // WHEEL_ZOOM_SENSITIVITY's comment above for the full
                // reasoning and the two-part diagnosis (mandatory modifier +
                // d3-zoom's own 10x ctrlKey multiplier) behind why the old
                // setup felt "too sensitive". d3-zoom's own wheel handling
                // is disabled outright (false, not a predicate) so it can
                // never fire alongside the manual listener above and
                // double-apply a zoom to the same wheel event; that manual
                // listener is now the only path a wheel event reaches the
                // graph's zoom through. enablePanInteraction is untouched --
                // mouse-drag panning is a separate d3-zoom code path from
                // wheel and was never part of either the old or new
                // sensitivity issue.
                enableZoomInteraction={false}
                // Sane bounds on the zoom this component can reach, via
                // either the manual wheel handler above or any imperative
                // .zoom() call (zoomToFit, Recenter) -- react-force-graph-2d
                // has no default limit (d3-zoom's own scaleExtent defaults
                // to [0, Infinity], confirmed in d3-zoom's source), so
                // without this a fast enough scroll could zoom the ~190-node
                // cluster down to a single indistinguishable point or out
                // past anything legible.
                minZoom={WHEEL_ZOOM_MIN}
                maxZoom={WHEEL_ZOOM_MAX}
                // Enabled 2026-09-02 -- this, not any camera behaviour, is
                // what makes a disturbed node "come back". force-graph's own
                // drag-end handler un-pins the node it just dragged
                // (`if (initPos.fx === undefined) obj.fx = undefined`) and
                // then calls `d3AlphaTarget(0).resetCountdown()`, which
                // re-warms the cooled simulation so charge/link/collide can
                // pull the node back to equilibrium. That is the Obsidian
                // behaviour, and it needs no onNodeDragEnd of our own -- the
                // prop was simply gating it off. Distinct from the idle
                // camera re-fit removed earlier: that one undid deliberate
                // PANNING, which is navigation, not a disturbance.
                enableNodeDrag={true}
              />
            )
          )}
        </div>
      </div>

      {/* Legend/selector, now the Skiper UI hover-expand strip (2026-09-03).
          Same job as the flat dot-row it replaces -- name every category, show
          its size, and let you pin one -- but the colour swatch is the tile
          itself, so hovering along the strip reads as browsing rather than as
          scanning a key. Fed from `data.categories`, the exact array the
          canvas already uses, so there is no second request. */}
      <HoverExpandCategories
        className="mt-5"
        categories={data.categories}
        selected={expanded}
        onSelect={focusCategoryAndReveal}
      />

      {/* Attribution is a condition of Skiper UI's free licence ("Attribution
          to Skiper UI is required when using the free version"). No format is
          specified, so it sits here as a quiet footer line. */}
      <p className="mt-2 text-[11px] text-slate-400">
        Category strip by{" "}
        <a
          href="https://skiper-ui.com"
          target="_blank"
          rel="noopener noreferrer"
          className="underline decoration-slate-300 underline-offset-2 hover:text-slate-600"
        >
          Skiper UI
        </a>
      </p>
    </div>
  );
}
