"use client";

import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import type { ComponentType } from "react";
import { forceCenter, forceCollide } from "d3-force";
import { ArrowLeft, Loader2 } from "lucide-react";

import { EMPTY_GRAPH } from "@/lib/api";
import type { GraphNode, GraphPayload } from "@/lib/types";
import { cn } from "@/lib/utils";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { GraphFallbackList } from "./graph-fallback-list";
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
 * Dense "nebula" default view (2026-09-02), replacing the 13-category
 * click-to-expand model entirely. The backend's expand="all" mode (see
 * app/public_api.py) returns every reel as a node plus every category as a
 * node, category<->reel membership links, and category<->category
 * co-occurrence links -- the SAME data shape a single-category expand
 * always returned, just for every category simultaneously. Category nodes
 * stay IN the simulation (their membership links are what pulls same-
 * category reels toward a shared anchor, which is the entire mechanism that
 * produces per-category clustering) but are never drawn -- see the isType
 * checks in paintNode/paintLink. Reusing the existing membership-link
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

/** Where a zero-edge node gets parked, in simulation coordinates. Still
 *  needed: category anchors with zero co-occurrence links (the "Other"
 *  bucket) still exist in the simulation even though they're never drawn,
 *  and an anchor with charge but no links would fling off exactly like a
 *  visible isolated node used to. Scaled down from the category-only
 *  layout's -690,345 to match the much smaller default charge/spread below. */
const ISOLATED_ANCHOR = { x: -180, y: 90 };

/** Reel labels stay hover-only regardless of node count -- 190 permanent
 *  labels would be the exact unreadable-stack problem the category view was
 *  built to avoid, just at a much larger scale. Category labels never draw
 *  at all in this view (category nodes aren't drawn), so the always-on
 *  threshold constants from the category-only view no longer apply. */

/** Palette anchors for the reference's "nebula" look: category hues get
 *  pulled toward one of these (never fully replaced -- "shift toward", per
 *  the brief, keeps each category still identifiably itself) rather than
 *  staying at full saturation, and value_score 5 reels get pulled toward
 *  the yellow accent instead, sparingly (5 is the top of the 1-5 scale, the
 *  smallest slice of the real corpus). */
const NEBULA_PALETTE = ["#8b5cf6", "#ec4899", "#3b82f6"]; // purple, pink, blue
const NEBULA_ACCENT = "#fbbf24"; // warm yellow, high-value reels only
const NEBULA_MIX_AMOUNT = 0.55;

/** Blends a #rrggbb hex colour toward another by `amount` (0 = original,
 *  1 = fully the target) -- used to shift each reel's category colour
 *  toward the nebula palette without discarding its original hue entirely. */
function mixHex(hex: string, toward: string, amount: number): string {
  const h = hex.replace("#", "");
  const t = toward.replace("#", "");
  const hr = parseInt(h.slice(0, 2), 16), hg = parseInt(h.slice(2, 4), 16), hb = parseInt(h.slice(4, 6), 16);
  const tr = parseInt(t.slice(0, 2), 16), tg = parseInt(t.slice(2, 4), 16), tb = parseInt(t.slice(4, 6), 16);
  const mix = (a: number, b: number) => Math.round(a + (b - a) * amount);
  const toHex = (n: number) => n.toString(16).padStart(2, "0");
  return `#${toHex(mix(hr, tr))}${toHex(mix(hg, tg))}${toHex(mix(hb, tb))}`;
}

/** Deterministic per-node palette pick (not random per frame -- a node
 *  flickering between purple and blue on every repaint would look broken,
 *  not organic). Hashes the node id into one of the three anchors. */
function nebulaAnchorFor(id: string): string {
  let hash = 0;
  for (let i = 0; i < id.length; i++) hash = (hash * 31 + id.charCodeAt(i)) >>> 0;
  return NEBULA_PALETTE[hash % NEBULA_PALETTE.length];
}

/** Dark backdrop, near-black -- the nebula's own glow supplies the colour;
 *  the background just needs to stay out of the way and give the glow
 *  somewhere dark to bloom into. */
const NEBULA_BACKGROUND = "#050208";

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

  const handleNodeClick = useCallback((node: any) => {
    // Category-type nodes are never drawn or hit-testable in this view (see
    // paintNode/nodePointerAreaPaint) -- there's nothing on canvas left to
    // click to focus a category now; that's legend-only. A reel node still
    // opens its source post.
    if (node.type === "reel" && node.shortcode) {
      window.open(`https://www.instagram.com/reel/${node.shortcode}/`, "_blank", "noopener");
    }
  }, []);

  /**
   * Force tuning for the dense nebula view (2026-09-02), retuned from the
   * ground up for ~190 mostly-tiny reel nodes plus ~13 invisible category
   * anchors, not 13 large visible category bubbles. The category-only
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
        ?.distance((l: any) => (l.type === "membership" ? 18 : 70))
        .strength((l: any) =>
          l.type === "membership" ? 0.75 : Math.min(0.08, 0.01 * (l.value ?? 1)),
        );

      // The hard constraint. Radius is TYPE-AWARE for the same reason charge
      // is: a category anchor's `val` is the old category-sizing formula
      // (4 + sqrt(count)*2, up to ~20px) meant for a VISIBLE bubble that
      // needed its own clearance. Left as-is here, that large invisible
      // radius would bulldoze the small reel dots parked around it away
      // from their own cluster centre -- collide has no concept of
      // "invisible", it just sees a big circle. So an anchor's collide
      // radius is capped small; only real reel nodes use their full `val`.
      fg.d3Force(
        "collide",
        forceCollide()
          .radius((n: any) => (n.type === "category" ? 3 : (n.val ?? 3) + COLLIDE_PADDING))
          .strength(0.85)
          .iterations(2),
      );

      // New 2026-09-02: anchors node POSITIONS to the origin so the cluster's
      // average position can't cumulatively drift across repeated reheats.
      // This is simulation-space only -- it has no effect on a panned/zoomed
      // CAMERA, which is the actual "stuck off-centre" bug; that half is
      // handled by onUserZoomOrPan's idle re-`zoomToFit()` above, a camera
      // behaviour that forceCenter structurally cannot provide.
      fg.d3Force("center", forceCenter(0, 0));

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
      const center = fg.d3Force("center");
      // Reel nodes only -- these are the ones actually drawn, so their
      // spread is what "tight and roughly spherical" needs verifying
      // against, not the invisible category anchors.
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
      const line =
        `[${label} @ ${new Date().toISOString().slice(11, 19)}] ` +
        `center=${center ? "present" : "MISSING"} collide=${collide ? "present" : "MISSING"} ` +
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
   *
   * The `data.level === "category"` guard this used to have is gone
   * (2026-09-02): that only made sense when `data` was EITHER a
   * category-level payload OR a single expanded category's reels, never
   * both. Under expand="all" `data` always holds every reel plus every
   * category anchor at once, and "Other" (or any future zero-degree
   * category) still needs pinning regardless.
   */
  useEffect(() => {
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

  /**
   * Auto-recentre after a pause in interaction (2026-09-02) -- the actual
   * fix for "scrolling gets stuck, has to fight to find centre".
   *
   * forceCenter (installed in the force effect below) keeps NODE POSITIONS
   * anchored to the origin -- that's simulation-space physics, and it's
   * necessary so repeated reheats can't let the cluster's average position
   * cumulatively drift. But panning/zooming the CANVAS doesn't touch node
   * positions or alpha at all; it only moves the camera transform, which
   * forceCenter has no power over. A user who drags the view away and stops
   * would otherwise stay off-centre forever with no node-physics fix able
   * to bring it back -- which is the actual reported symptom. So: debounce
   * on every zoom/pan event, and once interaction has genuinely paused,
   * call the SAME zoomToFit used on load. This is what makes the graph
   * "gravitate back to centre" the way the brief describes -- a camera
   * behaviour, achieved with a camera call, not a physics one.
   */
  const idleRecentreTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const onUserZoomOrPan = useCallback(() => {
    if (idleRecentreTimer.current) clearTimeout(idleRecentreTimer.current);
    idleRecentreTimer.current = setTimeout(() => frameGraph(), 2500);
  }, [frameGraph]);

  useEffect(() => () => clearTimeout(idleRecentreTimer.current), []);

  /**
   * Nebula node rendering (2026-09-02), replacing the 3-layer glassmorphic
   * bubble treatment entirely -- that was built for 13 large, well-spaced
   * category discs; painted onto ~190 small reel nodes at once it would be
   * both far too expensive (3 shadowBlur passes * 190 nodes every tick) and
   * visually wrong, since the reference image is small glowing points of
   * light, not frosted-glass spheres.
   *
   * Category anchor nodes are never drawn here at all -- they stay in the
   * simulation purely for their membership-link clustering effect (see the
   * force-tuning comment above). Everything below only ever runs for reel
   * nodes.
   *
   * Colour: each reel's own category colour is blended toward one of the
   * three NEBULA_PALETTE anchors (purple/pink/blue), picked deterministically
   * per node so the same reel is always the same hue. A high value_score
   * (5, the top of the 1-5 scale) pulls further toward the warm yellow
   * accent instead -- rare on purpose, since that is the smallest slice of
   * the real corpus and the reference image uses yellow as an accent, not a
   * primary.
   *
   * Brightness/size: single ctx.shadowBlur glow pass per node (one draw
   * call, not three) with size and glow radius modulated by distance from
   * the simulation origin -- forceCenter above keeps that origin genuinely
   * at the cluster's centre, so "closer to (0,0)" reliably means "closer to
   * the visual centre of the nebula", which is what lets nodes near the
   * middle read as the bright, saturated core and nodes further out fade
   * smaller and dimmer toward the edge, per the reference image.
   */
  const paintNode = useCallback(
    (node: any, ctx: CanvasRenderingContext2D, globalScale: number) => {
      // Category anchors are simulation-only in this view -- nothing to draw.
      if (node.type === "category") return;

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

      const baseColor =
        node.value_score >= 5
          ? mixHex(node.color, NEBULA_ACCENT, 0.7)
          : mixHex(node.color, nebulaAnchorFor(node.id), NEBULA_MIX_AMOUNT);

      // Size: mostly the backend's own val (already value_score-weighted),
      // nudged up slightly for centre nodes so the middle of the cluster
      // reads as visibly denser/bigger, per the brief's "brightest/biggest
      // at centre" instruction.
      const drawR = r * (0.85 + coreT * 0.35);

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
      const fontSize = Math.max(11, 12 / globalScale);
      ctx.font = `500 ${fontSize}px ui-sans-serif, system-ui, sans-serif`;
      ctx.textAlign = "center";
      ctx.textBaseline = "top";

      const label = node.label;
      const y = node.y + drawR + 6 / globalScale;

      ctx.lineWidth = 3.5 / globalScale;
      ctx.strokeStyle = "rgba(5,2,8,0.9)";
      ctx.strokeText(label, node.x, y);
      ctx.fillStyle = "#f8fafc";
      ctx.fillText(label, node.x, y);
      ctx.restore();
    },
    [hovered, expanded],
  );

  /**
   * Links are deliberately near-invisible in the nebula view (2026-09-02).
   *
   * The reference image is a field of small glowing points with NO visible
   * line mesh -- density comes entirely from the dots. Every link in this
   * data model touches at least one category anchor (membership links run
   * reel<->anchor; co-occurrence links run anchor<->anchor), and anchors are
   * never drawn, so a fully-opaque link would visibly draw a line from a
   * bright dot out to nowhere, which reads as broken rather than as
   * structure. So:
   *   - co-occurrence links (anchor<->anchor) are skipped entirely -- both
   *     endpoints are invisible, so drawing them can only ever look wrong,
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
      ctx.strokeStyle = `${reel.color}${focussed ? "22" : "0C"}`;
      ctx.lineWidth = 0.5;
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
                : /* 2026-09-02: wheel-zoom used to trap the page scroll the
                     moment the cursor crossed the canvas -- see
                     enableZoomInteraction below. This is the one-line fix
                     for a visitor who scrolls straight into that and finds
                     the page won't move: tell them the escape hatch. */
                  "Scroll to keep browsing · hold Ctrl/⌘ + scroll to zoom the graph"}
            </p>
          )}
        </div>
        <p className="text-sm tabular-nums text-muted-foreground">
          {data.total_reels.toLocaleString()} saves · {data.categories.length} categories
        </p>
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
                // cooldownTicks running via requestAnimationFrame.
                warmupTicks={400}
                // Interactive re-settling only now (category expand/collapse
                // changes graphData) -- the initial layout no longer needs
                // this to converge.
                cooldownTicks={200}
                onEngineStop={frameGraph}
                // Debounced idle re-`zoomToFit()` -- see onUserZoomOrPan
                // above. Fires on every pan/zoom event; the actual
                // recentring only happens once interaction has paused.
                onZoom={onUserZoomOrPan}
                // THE SCROLL-TRAP FIX (2026-09-02). Reported bug: a visitor
                // scrolling the page with the cursor over the canvas got
                // stuck zooming the graph instead of scrolling the page --
                // react-force-graph-2d's default wheel handler calls
                // preventDefault() unconditionally to zoom on every wheel
                // event, cursor-over-canvas or not.
                //
                // Chose the modifier-key gate (Ctrl/Cmd+scroll = zoom, plain
                // scroll = page) over the other two options considered:
                // disabling zoom entirely would remove a real, useful
                // interaction for exploring ~190 dots with no other zoom
                // affordance offered in its place; capturing wheel only
                // while actively dragging doesn't fix the actual complaint,
                // since a visitor scrolling the page never has the mouse
                // button down over the canvas in the first place. The
                // modifier gate is also the one directly supported by
                // react-force-graph-2d's own typed prop
                // (`enableZoomInteraction?: boolean |
                // ((event: MouseEvent) => boolean)`, confirmed against
                // node_modules/react-force-graph-2d/dist/react-force-graph-2d.d.ts)
                // rather than needing a manual wheel-listener workaround,
                // and it's a familiar convention (Google Maps, Figma, most
                // canvas-based editors use the same gate for the same
                // reason).
                enableZoomInteraction={(event: any) => event.ctrlKey || event.metaKey}
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
            onClick={() => focusCategory(c.slug === expanded ? null : c.slug)}
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
