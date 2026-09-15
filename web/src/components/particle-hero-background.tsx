"use client";

import { useEffect, useRef } from "react";
import * as THREE from "three";
import { EffectComposer } from "three/examples/jsm/postprocessing/EffectComposer.js";
import { RenderPass } from "three/examples/jsm/postprocessing/RenderPass.js";
import { UnrealBloomPass } from "three/examples/jsm/postprocessing/UnrealBloomPass.js";

import { cn } from "@/lib/utils";

/**
 * A decorative, GPU-instanced particle field with UnrealBloom post-
 * processing -- the same technique confirmed live at particles.casberry.in
 * (EffectComposer + RenderPass + UnrealBloomPass, real three.js addons, not
 * hand-rolled). This is a SEPARATE, purely atmospheric component: it is not
 * the data graph (react-force-graph-2d, KnowledgeGraph's category/reel
 * nodes), does not read any API data, and is not meant to replace that view
 * anywhere. It exists to be compared against AsciiHeroBackground as a
 * candidate for the hero's own ambient visual -- see
 * app/preview/particles/page.tsx for the side-by-side.
 *
 * TWO VARIANTS, one component, picked at mount via `variant` (2026-09-16):
 * building this as a single parameterised component rather than two files
 * keeps the shared machinery (renderer/composer setup, InstancedMesh
 * geometry, animation loop, disposal) written once. Only buildSun() vs.
 * buildGalaxy() differ -- everything else (bloom tuning, rotation, resize,
 * visibility gating) is shared.
 *
 * DARK BACKGROUND IS DELIBERATE, not a placeholder to swap later: unlike
 * AsciiHeroBackground (backgroundColor="transparent", meant to sit as a
 * faint 22%-opacity texture over the site's white hero), this renders an
 * ACTUAL opaque near-black clear colour. Two reasons, not one:
 *   1. UnrealBloomPass's composite step is additive-blended against
 *      whatever the render target already holds; on a genuinely transparent
 *      clear it reliably reads as a flat, wrong-looking dark rectangle
 *      instead of see-through glow (the additive math still needs a real
 *      base colour to add light ONTO for the eye to read it as "glowing",
 *      not "there"). Confirmed by trying it, not assumed -- see the
 *      preview page's own comment for the visual result.
 *   2. A sun or a galaxy reads as exactly that ON A DARK FIELD; the same
 *      particle cloud at 20% opacity over a white page reads as a grey
 *      smudge, not a glowing object. The reference tool this is copying the
 *      technique from is a full dark-page piece for the same reason.
 *  This is why the comparison lives on its own preview route with dark-hero
 *  mockup text, not spliced into the real (white) page.tsx hero yet -- so
 *  the user can judge the actual effect rather than a compromised, faded
 *  version of it. If one variant is picked, integrating it for real is a
 *  separate, deliberate decision about the hero's colour scheme, not an
 *  automatic follow-on to this component existing.
 */

export type ParticleHeroVariant = "sun" | "galaxy";

export type ParticleHeroBackgroundProps = {
  variant: ParticleHeroVariant;
  /** Total instanced particles across every layer. 8,000-15,000 is the
   *  brief's own target range for this codebase, not the reference's
   *  20,000 -- tuned down from there for a broader range of real hardware,
   *  not just whatever machine the reference demo was profiled on. */
  particleCount?: number;
  /** Radians per second of ambient Y rotation. Wallpaper-slow on purpose,
   *  same rationale as AsciiHeroBackground's own rotationSpeed. */
  rotationSpeed?: number;
  className?: string;
};

/** Below this width the effect is skipped entirely, same threshold and same
 *  reasoning as AsciiHeroBackground.MIN_WIDTH: a phone hero needs its full
 *  width for text, and a device narrow enough to trip this is disqualified
 *  by hardwareConcurrency/deviceMemory below anyway on most real phones. */
const MIN_WIDTH = 768;

/**
 * Is this device worth running a bloom-postprocessed instanced particle
 * scene on? Identical gate to AsciiHeroBackground.shouldRender, reused
 * rather than re-derived: reduced-motion, narrow viewports, and low-core/
 * low-memory devices are disqualified for the same reasons there, and this
 * scene is if anything MORE expensive per frame (a full-screen bloom blur
 * pass every frame vs. an 18fps-throttled DOM rebuild). No static-image
 * fallback is substituted for a disqualified device -- consistent with how
 * AsciiHeroBackground handles the same cutoff: this is pure background art,
 * so "render nothing, let the page's own background show" is the correct
 * fallback, not a fabricated screenshot standing in for a live effect nobody
 * asked to see faked. The main data graph's own mobile fallback
 * (GraphFallbackList) is a different case entirely -- that swaps in a REAL
 * alternate UI because the graph carries information a visitor needs;
 * nothing here carries information at all.
 */
function shouldRender(): boolean {
  if (typeof window === "undefined") return false;
  if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return false;
  if (window.innerWidth < MIN_WIDTH) return false;
  const cores = navigator.hardwareConcurrency;
  if (typeof cores === "number" && cores > 0 && cores <= 4) return false;
  const mem = (navigator as any).deviceMemory;
  if (typeof mem === "number" && mem > 0 && mem <= 4) return false;
  return true;
}

/** Deterministic RNG, same mulberry32 construction AsciiHeroBackground uses
 *  for its mycelium -- a background that silently reshuffles itself between
 *  visits/reloads reads as instability, not life, there and here alike. */
function makeRand(seed: number) {
  let s = seed;
  return () => {
    s |= 0;
    s = (s + 0x6d2b79f5) | 0;
    let t = Math.imul(s ^ (s >>> 15), 1 | s);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

type Particle = { position: THREE.Vector3; color: THREE.Color; scale: number };

/**
 * SUN: layered solar model -- white-gold core, orange-red mid-layer, dim red
 * outer layer, plus a handful of curved arc trails standing in for
 * prominence/magnetic-loop structures.
 *
 * Layered by SAMPLING FROM THREE DISCRETE POPULATIONS rather than one
 * continuous radius->colour gradient: a gradient reads as a smooth ball: a
 * solid sun photo. Three visibly distinct shells (dense/bright core, wider/
 * dimmer mid, sparse/dim outer) is what actually reads as "layered" at a
 * glance, which is what the brief asks for.
 */
function buildSun(rand: () => number, count: number): Particle[] {
  const particles: Particle[] = [];

  const CORE = Math.round(count * 0.35);
  const MID = Math.round(count * 0.4);
  const OUTER = count - CORE - MID - 600; // 600 reserved for the arc trails below

  const coreGold = new THREE.Color(0xfff2c6);
  const coreWhite = new THREE.Color(0xffffff);
  const midOrange = new THREE.Color(0xff8a3d);
  const midRed = new THREE.Color(0xff4d1a);
  const outerRed = new THREE.Color(0x7a1f10);
  const outerDim = new THREE.Color(0x4a140b);

  // Core: tight, near-spherical, biased hard toward the centre (cubed radius
  // sample) so it reads as a genuinely bright mass, not a diffuse haze.
  for (let i = 0; i < CORE; i++) {
    const r = 0.55 * Math.pow(rand(), 3);
    const dir = randomOnSphere(rand);
    particles.push({
      position: dir.multiplyScalar(r),
      color: coreWhite.clone().lerp(coreGold, rand()),
      scale: 0.45 + rand() * 0.5,
    });
  }

  // Mid-layer: a wider shell, radius sampled between the core's edge and a
  // real gap further out -- the gap (not a continuous range from 0) is what
  // keeps this reading as a separate band from the core rather than its
  // fuzzy edge.
  for (let i = 0; i < MID; i++) {
    const r = 0.65 + rand() * 0.85;
    const dir = randomOnSphere(rand);
    particles.push({
      position: dir.multiplyScalar(r),
      color: midOrange.clone().lerp(midRed, rand()),
      scale: 0.3 + rand() * 0.4,
    });
  }

  // Outer: sparse, dim, the widest shell -- deliberately LOW density per
  // unit volume (same particle budget over a much bigger shell than core/
  // mid get) so it reads as thinning out, not as a third equally-solid
  // layer.
  for (let i = 0; i < OUTER; i++) {
    const r = 1.55 + rand() * 1.1;
    const dir = randomOnSphere(rand);
    particles.push({
      position: dir.multiplyScalar(r),
      color: outerRed.clone().lerp(outerDim, rand()),
      scale: 0.22 + rand() * 0.3,
    });
  }

  // Prominence arcs: 5 curved loops, each a CatmullRom arc that leaves the
  // core surface, bows outward, and returns -- a real magnetic-loop
  // silhouette rather than a random scatter. Particles are sampled ALONG
  // each curve's length, not at its control points, so the loop reads as a
  // continuous line of light once bloomed.
  const ARCS = 5;
  const perArc = Math.floor(600 / ARCS);
  for (let a = 0; a < ARCS; a++) {
    const start = randomOnSphere(rand).multiplyScalar(0.62);
    // The end point is offset from the start along the sphere's surface
    // (not a fully independent random point), which is what keeps the arc
    // short and loop-shaped instead of a long chord across the whole star.
    const axis = randomOnSphere(rand);
    const end = start
      .clone()
      .applyAxisAngle(axis, 0.9 + rand() * 0.8)
      .normalize()
      .multiplyScalar(0.62);
    const apexHeight = 0.55 + rand() * 0.55;
    const mid = start
      .clone()
      .add(end)
      .multiplyScalar(0.5)
      .add(start.clone().add(end).multiplyScalar(0.5).normalize().multiplyScalar(apexHeight));
    const curve = new THREE.CatmullRomCurve3([start, mid, end]);
    const arcColor = midOrange.clone().lerp(coreGold, 0.4);
    for (let i = 0; i < perArc; i++) {
      const t = i / (perArc - 1);
      const p = curve.getPoint(t);
      // Tiny jitter off the curve so the arc reads as a particle stream, not
      // a perfectly rigid wire.
      p.add(new THREE.Vector3(rand() - 0.5, rand() - 0.5, rand() - 0.5).multiplyScalar(0.02));
      particles.push({ position: p, color: arcColor.clone(), scale: 0.22 + rand() * 0.18 });
    }
  }

  return particles;
}

/**
 * GALAXY NEBULA: a dense, roughly spherical star-cluster cloud, brightest
 * and densest near the centre, thinning toward the edges.
 *
 * Colour is now a RADIAL gradient keyed off each particle's own distance
 * from centre -- bright pink/magenta at the core, through purple at
 * mid-radius, to blue at the outer edge (2026-09-17, replacing the earlier
 * purple/pink/cyan/gold random-pair mix). That earlier version picked two
 * of four palette entries independently per particle with no relationship
 * to position, so cyan and gold specks could land anywhere, including deep
 * in the core -- which reads as "colourful scatter", not the cohesive
 * pink-to-blue gradient asked for here. Keying colour to radius the same
 * way DENSITY is already keyed to radius (see the cubed-radius sample
 * below) is what makes it read as one continuous gradient across the
 * sphere rather than confetti.
 *
 * The centre-heavy density comes from the SAME cubed-radius trick buildSun
 * uses for its core, applied to the WHOLE cloud this time rather than to
 * one of three shells -- that is what makes this read as one continuous
 * cluster rather than the sun's deliberately-banded structure.
 */
function buildGalaxy(rand: () => number, count: number): Particle[] {
  const particles: Particle[] = [];
  const MAX_R = 2.2;

  // Three anchor colours, radius-keyed: pink/magenta -> purple -> blue.
  // Two-stage lerp (pink->purple over the inner half, purple->blue over the
  // outer half) rather than a single three-stop gradient library, since
  // this is the only place in the file that needs one and pulling in a
  // colour-ramp dependency for one gradient would be a worse trade than 6
  // lines of lerp.
  const pink = new THREE.Color(0xff3fa8); // bright pink/magenta, core
  const purple = new THREE.Color(0x9b3dff); // mid-radius
  const blue = new THREE.Color(0x3d6bff); // outer edge

  for (let i = 0; i < count; i++) {
    // Cubed radius sample: most particles land close to the centre, a
    // rapidly-thinning tail reaches out to the full envelope -- "brightest
    // and densest near centre, thinning toward edges" as an actual density
    // function, not a colour trick layered on uniform scatter.
    const r = MAX_R * Math.pow(rand(), 2.4);
    const dir = randomOnSphere(rand);
    const position = dir.multiplyScalar(r);

    const t = Math.min(1, r / MAX_R);
    // A little jitter on the gradient stop itself (not on position) so the
    // transition reads as organic blending rather than two mechanically
    // perfect bands meeting at a hard seam.
    const tj = Math.min(1, Math.max(0, t + (rand() - 0.5) * 0.18));
    const color =
      tj < 0.5 ? pink.clone().lerp(purple, tj / 0.5) : purple.clone().lerp(blue, (tj - 0.5) / 0.5);

    // Brightness falls off with radius too (not just density), which is
    // what makes the CENTRE read as brightest rather than merely densest --
    // a sparse but full-brightness particle at the edge would still catch
    // the eye and break the falloff.
    const falloff = 1 - t * 0.7;
    color.multiplyScalar(0.5 + falloff * 0.7);

    particles.push({ position, color, scale: (0.22 + rand() * 0.4) * (0.6 + falloff * 0.6) });
  }

  return particles;
}

function randomOnSphere(rand: () => number): THREE.Vector3 {
  // Uniform point on a unit sphere via the standard normalised-Gaussian-ish
  // rejection-free method: two angles is NOT uniform (clusters at poles),
  // so this uses the z/theta parameterisation instead.
  const z = rand() * 2 - 1;
  const theta = rand() * Math.PI * 2;
  const r = Math.sqrt(Math.max(0, 1 - z * z));
  return new THREE.Vector3(r * Math.cos(theta), r * Math.sin(theta), z);
}

/**
 * Per-variant bloom tuning -- tuned by eye against real screenshots (see
 * the preview page), not guessed once and left alone. The sun's much
 * denser, brighter core pushed the composite toward solid white at the
 * galaxy's settings, so threshold/strength are not shared across variants
 * even though the rest of the pipeline is.
 *
 * strength 1.15/1.35 -> 0.7/0.8, threshold 0.22/0.12 -> 0.4/0.32 (second
 * pass, same day): the first tuning blew the dense core out to solid white
 * over a wide enough radius that it sat directly under the headline and
 * genuinely hurt readability -- caught on a real screenshot, not assumed.
 * That pass overcorrected the other way (also caught on screenshot): with
 * strength down at 0.7-0.8 the bloom read as barely-there scattered embers
 * rather than a glowing object, losing the "soft glow" the brief actually
 * asked for along with the blowout. Settled a third time at 0.9/1.0,
 * threshold 0.36/0.3 -- enough headroom above the readability scrim (added
 * alongside this in the preview page) that a soft core glow survives
 * without re-clipping to solid white.
 */
const BLOOM_SETTINGS: Record<ParticleHeroVariant, { strength: number; radius: number; threshold: number }> = {
  sun: { strength: 0.9, radius: 0.5, threshold: 0.36 },
  galaxy: { strength: 1.0, radius: 0.5, threshold: 0.3 },
};

const GEOMETRY_DETAIL = 0; // icosahedron subdivision -- 0 is the cheapest non-degenerate sphere-ish shape (12 verts)

export function ParticleHeroBackground({
  variant,
  particleCount = 11000,
  rotationSpeed = 0.06,
  className,
}: ParticleHeroBackgroundProps) {
  const mountRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const mount = mountRef.current;
    if (!mount || !shouldRender()) return;

    let disposed = false;
    let frame = 0;

    const scene = new THREE.Scene();
    // Near-black, not pure #000 -- see the module docstring for why this is
    // a real opaque clear colour rather than "transparent" like
    // AsciiHeroBackground uses. A hair off pure black (0x030105) so the
    // deepest shadow still reads as part of the same colour system as the
    // rest of the page's near-blacks (NEBULA_BACKGROUND in
    // knowledge-graph.tsx is #050208 -- this is deliberately close to that,
    // not a coincidence, so the two dark surfaces on this site don't clash
    // if they ever appear near each other).
    const bg = new THREE.Color(0x030105);
    scene.background = bg;
    scene.fog = new THREE.FogExp2(bg.getHex(), variant === "sun" ? 0.045 : 0.05);

    const camera = new THREE.PerspectiveCamera(50, 1, 0.1, 100);
    camera.position.set(0, 0, variant === "sun" ? 4.6 : 5.4);

    const renderer = new THREE.WebGLRenderer({ antialias: true });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));

    const composer = new EffectComposer(renderer);
    composer.addPass(new RenderPass(scene, camera));
    const { strength, radius, threshold } = BLOOM_SETTINGS[variant];
    const bloomPass = new UnrealBloomPass(new THREE.Vector2(1, 1), strength, radius, threshold);
    composer.addPass(bloomPass);

    const rand = makeRand(variant === "sun" ? 0x9e3779b9 : 0x51ed270b);
    const particles = variant === "sun" ? buildSun(rand, particleCount) : buildGalaxy(rand, particleCount);

    const geometry = new THREE.IcosahedronGeometry(0.028, GEOMETRY_DETAIL);
    const material = new THREE.MeshBasicMaterial({ toneMapped: false });
    const mesh = new THREE.InstancedMesh(geometry, material, particles.length);
    const m = new THREE.Matrix4();
    particles.forEach((p, i) => {
      m.makeScale(p.scale, p.scale, p.scale);
      m.setPosition(p.position);
      mesh.setMatrixAt(i, m);
      mesh.setColorAt(i, p.color);
    });
    mesh.instanceMatrix.needsUpdate = true;
    if (mesh.instanceColor) mesh.instanceColor.needsUpdate = true;

    const subject = new THREE.Group();
    subject.add(mesh);
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

    // Same rect-test visibility gate as AsciiHeroBackground, same reasoning:
    // an IntersectionObserver's async first callback can land after layout
    // and latch a wrong isIntersecting:false for a hero that never scrolls
    // again. A rect test recomputed every tick cannot latch.
    const onScreen = () => {
      const rect = mount.getBoundingClientRect();
      return rect.bottom > 0 && rect.top < window.innerHeight;
    };

    const clock = new THREE.Clock();
    const tick = () => {
      frame = requestAnimationFrame(tick);
      if (!onScreen()) return;
      const dt = clock.getDelta();
      subject.rotation.y += rotationSpeed * dt;
      composer.render();
    };
    frame = requestAnimationFrame(tick);

    return () => {
      disposed = true;
      cancelAnimationFrame(frame);
      ro?.disconnect();
      window.removeEventListener("resize", resize);
      geometry.dispose();
      material.dispose();
      composer.dispose();
      renderer.dispose();
      if (renderer.domElement.parentNode === mount) mount.removeChild(renderer.domElement);
      void disposed;
    };
  }, [variant, particleCount, rotationSpeed]);

  return <div ref={mountRef} aria-hidden className={cn("h-full w-full", className)} />;
}

export default ParticleHeroBackground;
