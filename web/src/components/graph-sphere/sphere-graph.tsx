"use client";

import { useEffect, useMemo, useRef } from "react";

import * as THREE from "three";
import { EffectComposer } from "three/examples/jsm/postprocessing/EffectComposer.js";
import { RenderPass } from "three/examples/jsm/postprocessing/RenderPass.js";
import { UnrealBloomPass } from "three/examples/jsm/postprocessing/UnrealBloomPass.js";

import type { GraphNode, GraphPayload } from "@/lib/types";
import { cn } from "@/lib/utils";
import {
  BLOOM_RADIUS,
  BLOOM_STRENGTH,
  BLOOM_THRESHOLD,
  SCENE_BACKGROUND,
  buildDust,
  buildInstancedPoints,
  fibonacciSphere,
  gradientByLatitude,
  makeRand,
} from "./sphere-core";

/**
 * The real, interactive, data-driven sphere: every reel in the corpus is
 * one point on it.
 *
 * This replaces react-force-graph-2d as the primary desktop graph. The
 * difference that matters most is not the renderer, it is what POSITION
 * means. The old graph ran a d3 force simulation -- charge, collision,
 * link and centering forces -- so where a node sat encoded its
 * relationships, and the layout had to settle before it was readable.
 * Here, position encodes nothing but "an even slot on a sphere". There is
 * no simulation, no settling, no jitter, and no implied connection between
 * neighbours. Positions are final on frame one and identical on every
 * visit.
 */

/** Radius the real reel nodes sit at. Just outside the dust envelope
 *  (sphere-core's dust is generated to 2.0 below) so the data layer reads
 *  as a distinct shell around the decorative cloud rather than being lost
 *  inside it -- which also keeps every node unobstructed for hit-testing. */
const NODE_SHELL_RADIUS = 2.3;

/** Dust envelope, deliberately smaller than the node shell. */
const DUST_MAX_R = 2.15;

/**
 * Decorative dust count. Far fewer than the hero's 11,000: here the real
 * nodes carry the visual weight and the dust is only there so 191 points
 * don't read as sparse. Keeping it high enough to feel dense, low enough
 * that it never competes with the data for attention.
 *
 * These are NOT data. They have no shortcode, are never raycast, and are
 * a separate InstancedMesh from the real nodes precisely so that
 * "clickable thing" and "decorative thing" cannot be confused by the
 * hit-testing code.
 */
const DUST_COUNT = 6500;

/** Camera distance limits for wheel zoom. Lower bound keeps the camera
 *  outside the node shell (2.3) so zooming can never put the viewer
 *  *inside* the data and lose the object; upper bound stops the sphere
 *  shrinking to a speck. */
const MIN_CAMERA_Z = 3.4;
const MAX_CAMERA_Z = 12;

/**
 * Wheel sensitivity, in world units (zoom) and radians (spin) per pixel of
 * wheel delta. A mouse notch is ~100px of deltaY, so zoom moves ~0.6 units
 * per notch -- about 14 notches to cross the whole range, which is enough
 * travel to feel deliberate without being a chore.
 *
 * The two axes are read independently and mean different things (deltaY =
 * zoom, deltaX = spin) rather than one being a modifier-key variant of the
 * other, because on a trackpad they ARE independent physical gestures:
 * a two-finger vertical scroll and a two-finger horizontal swipe. A mouse
 * with no horizontal wheel simply never produces deltaX and only ever
 * zooms, which is the correct fallback.
 */
const ZOOM_PER_DELTA = 0.006;
const SPIN_PER_DELTA = 0.003;

/** Wheel events in DOM_DELTA_LINE mode report lines, not pixels. Firefox
 *  does this for real mouse wheels; without normalising, the same physical
 *  notch would move ~100x less there than in Chrome. */
const PIXELS_PER_WHEEL_LINE = 16;

/** How fast the camera catches up to the zoom target, per second. Smoothing
 *  exists because trackpad wheel events arrive as a burst of tiny deltas;
 *  applying them straight to the camera reads as stutter. */
const ZOOM_CATCHUP = 10;

export type SphereGraphProps = {
  data: GraphPayload;
  /** Called with a reel's shortcode when its node is clicked. The sphere
   *  deliberately does not know what happens next -- fetching the reel and
   *  opening the detail modal belongs to the route, which already owns
   *  that pattern. */
  onSelectReel?: (shortcode: string) => void;
  className?: string;
};

/** A real reel node, resolved to its final place on the sphere. */
export type PlacedNode = {
  shortcode: string;
  label: string;
  category: string;
  categoryLabel: string;
  valueScore: number;
  position: THREE.Vector3;
  color: THREE.Color;
};

/**
 * THE 1:1 MAPPING. Every real reel gets exactly one node; no duplicates,
 * no omissions, nothing invented.
 *
 * Dedupe by shortcode is belt-and-braces rather than distrust: the backend
 * already guarantees one node per reel (build_graph keeps a `seen_reels`
 * set precisely because a reel can belong to several categories and would
 * otherwise be emitted once per membership). Enforcing it again here means
 * a future backend change can't silently double a reel into two clickable
 * points that open the same modal.
 *
 * ORDERING IS A DESIGN DECISION, not incidental. Reels are sorted by
 * category first, then by value score, then by shortcode. Fibonacci
 * placement walks the sorted list in order, so consecutive list entries
 * land in adjacent slots on the sphere -- which means each category
 * occupies a contiguous band rather than being scattered. That gives
 * category a spatial reading without spending the colour channel on it
 * (see the colour note below), and the sort is fully deterministic, so a
 * given reel is in the same place on every visit.
 */
export function placeNodes(data: GraphPayload): PlacedNode[] {
  const categoryLabels = new Map(data.categories.map((c) => [c.slug, c.label]));

  const byShortcode = new Map<string, GraphNode>();
  for (const node of data.nodes) {
    if (node.type !== "reel") continue;
    if (!node.shortcode) continue; // nothing to click through to
    if (!byShortcode.has(node.shortcode)) byShortcode.set(node.shortcode, node);
  }

  // Array.from rather than [...map.values()]: this project's tsconfig
  // target predates downlevelIteration, so spreading a Map iterator is a
  // compile error here.
  const sorted = Array.from(byShortcode.values()).sort((a, b) => {
    if (a.category !== b.category) return a.category.localeCompare(b.category);
    const av = a.value_score ?? 0;
    const bv = b.value_score ?? 0;
    if (av !== bv) return bv - av;
    return (a.shortcode ?? "").localeCompare(b.shortcode ?? "");
  });

  const points = fibonacciSphere(sorted.length);

  return sorted.map((node, i) => {
    const position = points[i].clone().multiplyScalar(NODE_SHELL_RADIUS);
    return {
      shortcode: node.shortcode as string,
      label: node.label,
      category: node.category,
      categoryLabel: categoryLabels.get(node.category) ?? node.category,
      valueScore: node.value_score ?? 1,
      position,
      /**
       * COLOUR DECISION (flagged in the brief, chosen deliberately):
       * the palette stays the pink -> purple -> blue gradient, mapped to
       * LATITUDE, rather than switching to per-category hues.
       *
       * Two reasons, one aesthetic and one that is closer to a bug:
       *  - Aesthetic: there are 13 categories with their own unrelated
       *    hex colours (orange, teal, red, green...). Painting those onto
       *    the sphere turns a cohesive two-tone object into confetti, and
       *    the brief's own constraint was that it must still read as
       *    cohesive rather than become a rainbow mess.
       *  - Structural: the promoted Stage 1 gradient was keyed to RADIUS,
       *    and every real node sits on the sphere surface at one radius.
       *    Keeping it literally as-is would paint all ~191 nodes the same
       *    colour. Latitude is the nearest axis that actually varies
       *    across the visible surface, so the palette survives intact and
       *    still reads as a gradient.
       * Category is not lost, it is moved to channels better suited to it:
       * spatial grouping (the contiguous bands the sort above produces),
       * the hover label, and the detail modal -- all of which name the
       * category in words rather than asking anyone to decode a hue.
       */
      color: gradientByLatitude(position.y, NODE_SHELL_RADIUS),
    };
  });
}

export function SphereGraph({ data, onSelectReel, className }: SphereGraphProps) {
  const mountRef = useRef<HTMLDivElement>(null);

  // The hover label is driven IMPERATIVELY, not through React state.
  // Hover is resolved once per animation frame (see the raycast in `tick`),
  // and routing that through setState would re-render this component up to
  // 60 times a second -- which, because the whole WebGL scene lives in an
  // effect keyed on `nodes`, is pure waste at best. Writing to these nodes
  // directly keeps the render loop free of React entirely.
  const labelRef = useRef<HTMLDivElement>(null);
  const labelCategoryRef = useRef<HTMLSpanElement>(null);
  const labelTextRef = useRef<HTMLSpanElement>(null);

  // Same reasoning applied to the click callback: held in a ref so that a
  // parent passing an inline arrow function can't tear down and rebuild the
  // entire Three.js scene on every one of its own renders.
  const onSelectRef = useRef(onSelectReel);
  onSelectRef.current = onSelectReel;

  // Placement is pure and deterministic, so it only needs redoing when the
  // payload itself changes -- not on every render.
  const nodes = useMemo(() => placeNodes(data), [data]);

  /**
   * The 1:1 guarantee, enforced rather than assumed.
   *
   * "Every real reel gets exactly one clickable point, no duplicates, no
   * missing reels" is the correctness requirement of this whole view, and
   * it is the kind of thing that fails silently: a handful of reels
   * quietly absent from a 191-point sphere is invisible to the eye. So the
   * placed count is checked against the corpus count the API reports
   * separately (total_reels), and any mismatch is surfaced loudly instead
   * of being rendered as if fine.
   *
   * A mismatch is not necessarily a bug in this file -- a reel with no
   * shortcode is skipped deliberately above, since there would be nothing
   * to open -- but it should never be discovered by a visitor wondering
   * where their save went.
   */
  useEffect(() => {
    if (data.total_reels > 0 && nodes.length !== data.total_reels) {
      // eslint-disable-next-line no-console
      console.warn(
        `[sphere-graph] node/reel count mismatch: placed ${nodes.length} nodes for ` +
          `${data.total_reels} reels reported by the API. Some reels are not reachable.`,
      );
    }
  }, [nodes.length, data.total_reels]);

  useEffect(() => {
    const mount = mountRef.current;
    if (!mount || nodes.length === 0) return;

    let frame = 0;

    const scene = new THREE.Scene();
    scene.background = new THREE.Color(SCENE_BACKGROUND);

    const camera = new THREE.PerspectiveCamera(50, 1, 0.1, 100);
    camera.position.set(0, 0, 7.2);

    const renderer = new THREE.WebGLRenderer({ antialias: true });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));

    const composer = new EffectComposer(renderer);
    composer.addPass(new RenderPass(scene, camera));
    const bloomPass = new UnrealBloomPass(
      new THREE.Vector2(1, 1),
      BLOOM_STRENGTH,
      BLOOM_RADIUS,
      BLOOM_THRESHOLD,
    );
    composer.addPass(bloomPass);

    // --- decorative dust (never interactive) ---
    const rand = makeRand(0x51ed270b);
    const dust = buildDust(rand, DUST_COUNT, DUST_MAX_R);
    const dustCore = buildInstancedPoints(
      dust.map((d) => d.position),
      dust.map((d) => d.color),
      dust.map((d) => d.scale),
      0.036,
      0.8,
      0,
    );

    // --- the real nodes ---
    // Size carries value_score, the same signal the old graph encoded in
    // node radius, so the visual weighting a returning visitor already
    // learned still means the same thing.
    const nodeScales = nodes.map((n) => 0.85 + (n.valueScore - 1) * 0.16);
    const nodeCore = buildInstancedPoints(
      nodes.map((n) => n.position),
      nodes.map((n) => n.color),
      nodeScales,
      0.058,
      1,
      1,
    );
    const nodeHalo = buildInstancedPoints(
      nodes.map((n) => n.position),
      nodes.map((n) => n.color),
      nodeScales.map((s) => s * 1.25),
      0.11,
      0.16,
      0,
    );

    /**
     * The hover highlight: a single extra sphere, moved to whichever node
     * is under the cursor, rather than rewriting that node's instance
     * matrix in the InstancedMesh. One mesh and a position write per frame
     * is cheaper than touching a 191-entry instance buffer, and -- more
     * importantly -- it means the data layer's buffers are written once at
     * build time and never mutated, so a hover can't corrupt the placement
     * that the whole 1:1 guarantee rests on.
     */
    const highlight = new THREE.Mesh(
      new THREE.IcosahedronGeometry(0.058, 2),
      new THREE.MeshBasicMaterial({ toneMapped: false }),
    );
    highlight.scale.setScalar(1.75);
    highlight.visible = false;

    const subject = new THREE.Group();
    subject.add(dustCore.mesh);
    subject.add(nodeHalo.mesh);
    subject.add(nodeCore.mesh);
    subject.add(highlight);
    scene.add(subject);

    // Cached rather than read per frame: both the raycast and the label
    // projection need the canvas box, and getBoundingClientRect in a rAF
    // loop forces layout every frame.
    let viewW = 1;
    let viewH = 1;

    const resize = () => {
      const { width, height } = mount.getBoundingClientRect();
      if (width === 0 || height === 0) return;
      viewW = width;
      viewH = height;
      camera.aspect = width / height;
      camera.updateProjectionMatrix();
      renderer.setSize(width, height);
      composer.setSize(width, height);
      bloomPass.setSize(width, height);
    };

    mount.appendChild(renderer.domElement);
    resize();

    const ro = typeof ResizeObserver !== "undefined" ? new ResizeObserver(resize) : undefined;
    ro?.observe(mount);
    window.addEventListener("resize", resize);

    // ---------------------------------------------------------------
    // INTERACTION
    // ---------------------------------------------------------------

    const raycaster = new THREE.Raycaster();
    const pointer = new THREE.Vector2();
    const projected = new THREE.Vector3();
    let pointerInside = false;
    /** Index into `nodes`, or -1. The single source of truth for both the
     *  label and the click target -- a click can only ever open the reel
     *  that is visibly highlighted, because it reads this same value
     *  rather than re-deriving a hit of its own. */
    let hovered = -1;
    let labelFor = -1;
    let targetZ = camera.position.z;

    const onPointerMove = (e: PointerEvent) => {
      const rect = mount.getBoundingClientRect();
      pointer.x = ((e.clientX - rect.left) / rect.width) * 2 - 1;
      pointer.y = -(((e.clientY - rect.top) / rect.height) * 2 - 1);
      pointerInside = true;
    };

    const onPointerLeave = () => {
      pointerInside = false;
    };

    const onClick = () => {
      if (hovered < 0) return;
      onSelectRef.current?.(nodes[hovered].shortcode);
    };

    /**
     * Wheel: deltaY zooms, deltaX spins. preventDefault is unconditional
     * and the listener is explicitly non-passive, which is the whole reason
     * this view is its own route: the old graph's wheel handling fought the
     * page's own scroll. Here the page is h-screen with overflow hidden, so
     * there is nothing being suppressed -- the wheel has no other job.
     */
    const onWheel = (e: WheelEvent) => {
      e.preventDefault();
      const unit = e.deltaMode === 1 ? PIXELS_PER_WHEEL_LINE : 1;
      if (e.deltaY !== 0) {
        targetZ = Math.min(
          MAX_CAMERA_Z,
          Math.max(MIN_CAMERA_Z, targetZ + e.deltaY * unit * ZOOM_PER_DELTA),
        );
      }
      if (e.deltaX !== 0) {
        // Negated so the sphere follows the fingers: swiping left drags the
        // near face left, the way dragging a physical globe would.
        subject.rotation.y -= e.deltaX * unit * SPIN_PER_DELTA;
      }
    };

    mount.addEventListener("pointermove", onPointerMove);
    mount.addEventListener("pointerleave", onPointerLeave);
    mount.addEventListener("click", onClick);
    // Non-passive is required for preventDefault to actually take effect on
    // wheel; browsers default wheel listeners to passive.
    mount.addEventListener("wheel", onWheel, { passive: false });

    const clock = new THREE.Clock();
    const tick = () => {
      frame = requestAnimationFrame(tick);
      const dt = Math.min(clock.getDelta(), 0.1);

      // Slow ambient rotation so the far side is reachable without any
      // input, and so the object reads as dimensional at rest. It PAUSES
      // while a node is hovered: otherwise the thing being pointed at
      // drifts out from under the cursor, and clicking becomes a game of
      // leading a moving target.
      if (hovered < 0) subject.rotation.y += 0.04 * dt;

      camera.position.z += (targetZ - camera.position.z) * Math.min(1, ZOOM_CATCHUP * dt);

      /**
       * Hit-testing happens HERE, in the frame loop, not in the pointermove
       * handler. The sphere rotates and zooms under a stationary cursor, so
       * a hover resolved only on mouse movement would go stale the moment
       * the subject moved. Re-testing every frame keeps "what is under the
       * cursor" continuously true.
       *
       * It raycasts the HALO layer only. That is deliberate on two counts:
       * the halo instances are ~2x the radius of the visible core dots, so
       * the hit target is forgiving rather than pixel-hunting; and the
       * decorative dust is a different mesh entirely and is never passed
       * in, so there is no way for a dust particle to register as a hover
       * or a click. Only real reels are hittable, structurally.
       */
      subject.updateMatrixWorld();
      let hit = -1;
      if (pointerInside) {
        raycaster.setFromCamera(pointer, camera);
        const hits = raycaster.intersectObject(nodeHalo.mesh, false);
        // Sorted near-to-far by three, so [0] is the front-facing node --
        // a ray through the sphere also strikes the back of it, and
        // hovering the far side through the near side would be wrong.
        const first = hits.length > 0 ? hits[0].instanceId : undefined;
        if (typeof first === "number") hit = first;
      }

      if (hit !== hovered) {
        hovered = hit;
        mount.style.cursor = hovered >= 0 ? "pointer" : "default";
        highlight.visible = hovered >= 0;
        if (hovered >= 0) {
          const node = nodes[hovered];
          highlight.position.copy(node.position);
          // The node's own colour, deepened, so the highlight reads as
          // "this one" without introducing a colour the palette doesn't
          // already contain.
          highlight.material.color.copy(node.color).multiplyScalar(0.78);
        }
      }

      const label = labelRef.current;
      if (label) {
        if (hovered < 0) {
          label.style.opacity = "0";
          labelFor = -1;
        } else {
          const node = nodes[hovered];
          // Text only when the node actually changes -- rewriting DOM text
          // 60x/second for an unchanged string is needless layout churn.
          if (labelFor !== hovered) {
            labelFor = hovered;
            if (labelCategoryRef.current) labelCategoryRef.current.textContent = node.categoryLabel;
            if (labelTextRef.current) labelTextRef.current.textContent = node.label;
          }
          // Project the node's CURRENT world position to screen space, so
          // the label tracks it through rotation and zoom.
          projected.copy(node.position).applyMatrix4(subject.matrixWorld).project(camera);
          const x = (projected.x * 0.5 + 0.5) * viewW;
          const y = (-projected.y * 0.5 + 0.5) * viewH;
          label.style.transform = `translate(-50%, -100%) translate(${x}px, ${y - 16}px)`;
          label.style.opacity = "1";
        }
      }

      composer.render();
    };
    frame = requestAnimationFrame(tick);

    return () => {
      cancelAnimationFrame(frame);
      ro?.disconnect();
      window.removeEventListener("resize", resize);
      mount.removeEventListener("pointermove", onPointerMove);
      mount.removeEventListener("pointerleave", onPointerLeave);
      mount.removeEventListener("click", onClick);
      mount.removeEventListener("wheel", onWheel);
      highlight.geometry.dispose();
      highlight.material.dispose();
      for (const layer of [dustCore, nodeCore, nodeHalo]) {
        layer.geometry.dispose();
        layer.material.dispose();
      }
      composer.dispose();
      renderer.dispose();
      if (renderer.domElement.parentNode === mount) mount.removeChild(renderer.domElement);
    };
  }, [nodes]);

  return (
    <div className={cn("relative h-full w-full", className)}>
      <div ref={mountRef} className="h-full w-full" />
      {/*
        The hover label. Rendered once and moved, rather than mounted and
        unmounted per hover -- see the refs above for why hover never
        touches React state.

        It is DOM rather than something drawn into the canvas specifically
        so it inherits the site's real typography (Manrope, via body) at a
        real CSS pixel size. The old canvas graph had to hand-specify
        `600 12px Manrope` and divide by globalScale to survive zoom; here
        the text simply is the site's text, and zooming the scene doesn't
        scale it at all.

        pointer-events-none is load-bearing: the label sits over the canvas
        near the cursor, and if it could take the pointer it would steal the
        hover that produced it and flicker.
      */}
      <div
        ref={labelRef}
        aria-hidden
        className="pointer-events-none absolute left-0 top-0 z-10 max-w-[19rem] rounded-lg border border-slate-200/80 bg-white/95 px-3 py-2 opacity-0 shadow-sm backdrop-blur-sm transition-opacity duration-100"
      >
        <span
          ref={labelCategoryRef}
          className="block text-[10px] font-semibold uppercase tracking-widest text-slate-400"
        />
        {/* The reel text is already a short snippet, not a full title: the
            API sends it pre-truncated at a word boundary (_short_label in
            public_api.py), which is the same string the old graph's
            hover-only labels showed. */}
        <span
          ref={labelTextRef}
          className="mt-0.5 block text-xs font-medium leading-snug text-slate-700"
        />
      </div>
    </div>
  );
}

export default SphereGraph;
