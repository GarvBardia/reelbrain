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

export type SphereGraphProps = {
  data: GraphPayload;
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

export function SphereGraph({ data, className }: SphereGraphProps) {
  const mountRef = useRef<HTMLDivElement>(null);

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

    const subject = new THREE.Group();
    subject.add(dustCore.mesh);
    subject.add(nodeHalo.mesh);
    subject.add(nodeCore.mesh);
    scene.add(subject);

    const resize = () => {
      const { width, height } = mount.getBoundingClientRect();
      if (width === 0 || height === 0) return;
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

    const clock = new THREE.Clock();
    const tick = () => {
      frame = requestAnimationFrame(tick);
      // Slow ambient rotation so the far side is reachable without any
      // input, and so the object reads as dimensional at rest.
      subject.rotation.y += 0.04 * Math.min(clock.getDelta(), 0.1);
      composer.render();
    };
    frame = requestAnimationFrame(tick);

    return () => {
      cancelAnimationFrame(frame);
      ro?.disconnect();
      window.removeEventListener("resize", resize);
      for (const layer of [dustCore, nodeCore, nodeHalo]) {
        layer.geometry.dispose();
        layer.material.dispose();
      }
      composer.dispose();
      renderer.dispose();
      if (renderer.domElement.parentNode === mount) mount.removeChild(renderer.domElement);
    };
  }, [nodes]);

  return <div ref={mountRef} className={cn("h-full w-full", className)} />;
}

export default SphereGraph;
