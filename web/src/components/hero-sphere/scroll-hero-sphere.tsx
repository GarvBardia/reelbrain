"use client";

import { useEffect, useRef } from "react";
import * as THREE from "three";
import { EffectComposer } from "three/examples/jsm/postprocessing/EffectComposer.js";
import { RenderPass } from "three/examples/jsm/postprocessing/RenderPass.js";
import { UnrealBloomPass } from "three/examples/jsm/postprocessing/UnrealBloomPass.js";

import { cn } from "@/lib/utils";

/**
 * The scroll-sequence particle sphere: a pink -> purple -> blue instanced
 * particle cloud on a WHITE page, built for the hero's scroll-driven
 * camera dolly.
 *
 * WHY THIS IS A SEPARATE FILE FROM particle-hero-background.tsx, rather
 * than a new variant inside it: that component is a finished, verified
 * piece (Sun/Galaxy, dark field, its own bloom tuning) that this work was
 * explicitly told not to rewrite. Its whole scene lives inside one
 * useEffect with no external control surface -- no progress input, no way
 * to drive the camera from outside -- so adding scroll-driven dolly,
 * rush-out and fade to it would have meant restructuring its core rather
 * than extending it. This file therefore re-implements the same TECHNIQUE
 * (EffectComposer + RenderPass + UnrealBloomPass over an InstancedMesh)
 * against a different requirement, and leaves the original untouched and
 * still working at its own preview. Duplication of ~60 lines of scene
 * boilerplate is the deliberate cost of not touching verified code.
 *
 * BLOOM ON A WHITE BACKGROUND -- read before re-tuning:
 * UnrealBloomPass is ADDITIVE. It extracts pixels above `threshold`,
 * blurs them, and ADDS that back over the base render. Adding light to a
 * white base is a no-op: white is already clipped at 1.0, so there is no
 * headroom for a glow to occupy. This is physics, not tuning -- the same
 * pass that makes these particles glow beautifully on near-black
 * contributes almost nothing on white, and no strength/threshold pair
 * changes that. What IS achievable on white, and what this is tuned for:
 * crisp, saturated, individually-legible colour points, with the halo
 * coming from a second, larger, low-opacity HALO instance layer (see
 * buildHaloMesh) rather than from bloom. Semi-transparent colour over
 * white reads as a soft tint spreading outward from each point, which is
 * the white-background equivalent of a glow.
 * The bloom pass is kept in the pipeline anyway, at low strength, because
 * it still does real work at the END of the scroll dolly: once the camera
 * is inside the cloud, dense overlapping particles do push local regions
 * bright enough to bloom against the white, which is what sells the
 * "rushing past a light source" moment. It is not vestigial, it is just
 * doing nothing at progress 0 -- by design.
 */

export type ScrollHeroSphereProps = {
  /** Total instanced particles. 11,000 matches the previous component's
   *  tuned count for this codebase's target hardware. */
  particleCount?: number;
  /** Radians per second of ambient Y rotation at rest. */
  rotationSpeed?: number;
  /**
   * Normalised 0..1 dolly progress, read as a FUNCTION on every animation
   * frame rather than passed as a value prop.
   *
   * This is the key interface decision of the whole sequence. A `progress:
   * number` prop would re-render this component on every scroll event --
   * i.e. tear down nothing but still re-run React reconciliation 60x/sec
   * against a component whose entire payload is an imperative WebGL scene
   * -- and would also force the scene's useEffect to either re-run or
   * carry progress in a ref anyway. Reading a getter inside the existing
   * RAF loop keeps the scroll->render path entirely outside React: zero
   * re-renders, one source of truth, and it works identically whether the
   * caller backs it with a plain ref (the Stage 2 debug slider) or a
   * framer-motion MotionValue (the Stage 3 scroll scrub).
   *
   * Deliberately kept scroll-AGNOSTIC: this component knows nothing about
   * scrolling, pinning, or ScrollTrigger. It knows a number between 0 and
   * 1. That's what makes it independently testable with a slider.
   */
  progressSource?: () => number;
  className?: string;
};

/** Camera z at progress 0 (whole sphere in frame) and at progress 1
 *  (camera deep inside the cloud, particles streaming past the edges of
 *  frame). END_Z sits inside the 2.2-unit envelope on purpose -- that IS
 *  the "flying into it" moment. Anything the camera passes within the
 *  0.1 near plane is clipped, which is correct: those particles have gone
 *  behind the viewer. */
const START_Z = 4.4;
/**
 * 1.15, not the 0.25 first tried. At 0.25 the camera ends up sitting
 * essentially ON the dense core, and core particles a quarter-unit away
 * subtend so much of the frame that their icosahedron facets become
 * visible -- the end of the dolly rendered as a flat wall of magenta
 * hexagons, which reads as a broken shader rather than as flying through
 * a particle field. Verified on a screenshot at progress 1.0, not
 * predicted. 1.15 puts the camera inside the outer cloud with the core
 * still ahead of it, which is the "in among the particles" look wanted,
 * while keeping every particle far enough away to stay a point.
 *
 * Stage 4's outward rush is the other half of this: by the time progress
 * approaches 1 the particles are also moving AWAY from the centre, so the
 * core has thinned out by the time the camera arrives. The two are tuned
 * together -- pulling END_Z back further than this makes the rush-past
 * read as distant, pushing it closer re-introduces the facet wall.
 */
const END_Z = 1.15;

/**
 * Exponential damping factor, per second. Higher = snappier, lower =
 * floatier. Applied as 1 - exp(-k*dt) rather than a fixed per-frame lerp
 * alpha so the smoothing is FRAME-RATE INDEPENDENT -- a fixed alpha makes
 * the dolly visibly faster on a 144Hz display than on a 60Hz one, which is
 * the kind of bug that only shows up on someone else's machine.
 */
const DAMPING_PER_SECOND = 6.5;

/**
 * Progress at which the particles stop being a sphere being flown into and
 * start being a rush of light being flown THROUGH -- they accelerate
 * outward and fade out over [RUSH_START, 1].
 *
 * 0.6 matches the window the graph fades/scales in over on the other side
 * of the handoff, because the two have to happen SIMULTANEOUSLY for the
 * illusion to work. If the particles cleared first there'd be an empty
 * white beat before the graph arrived (a cut); if the graph arrived first
 * it would appear through a wall of particles (a crossfade). Overlapping
 * them is what makes one object read as becoming the other.
 *
 * RUSH_SCALE is how far outward the cloud expands over that window. This
 * is a GROUP scale, not per-instance position writes: scaling the parent
 * pushes every particle radially away from the centre, which is exactly
 * the required motion, at zero per-frame cost for 11,000 instances. The
 * alternative -- rewriting 11,000 instance matrices every frame -- would
 * buy nothing visible and cost real milliseconds.
 */
const RUSH_START = 0.6;
const RUSH_SCALE = 1.9;

/** Page-background white, matching globals.css's real --background:
 *  0 0% 100%. The scene clears to this so the canvas is seamless against
 *  the page rather than a visible rectangle sitting on it. */
const SCENE_BACKGROUND = 0xffffff;

/** Envelope radius of the cloud in world units. */
const MAX_R = 2.2;

/**
 * Bloom tuning for a WHITE field -- see the module docstring for why these
 * are so much lower than the dark-background component's (0.9-1.0 strength,
 * 0.3-0.36 threshold). At a 0.75 threshold almost nothing in the resting
 * sphere qualifies as "bright" against white, which is correct: at rest
 * this should read as crisp points, not haze. It only starts contributing
 * once the dolly puts the camera inside the densest part of the cloud.
 */
const HALO_OPACITY = 0.13;

const BLOOM_STRENGTH = 0.35;
const BLOOM_RADIUS = 0.4;
const BLOOM_THRESHOLD = 0.75;

/** Below this width the effect is skipped -- same threshold and reasoning
 *  as particle-hero-background.tsx and ascii-hero-background.tsx before it,
 *  reused rather than re-derived. */
const MIN_WIDTH = 768;

export function prefersReducedMotion(): boolean {
  if (typeof window === "undefined") return false;
  return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

/**
 * Is this device worth running bloom-postprocessed instanced particles on?
 * Same gate the two prior hero backgrounds use. Exported because the scroll
 * sequence needs the SAME answer to decide whether to pin and scrub at all
 * -- a device that can't render the sphere shouldn't be scroll-jacked
 * through a dolly it will never see.
 */
export function canRenderSphere(): boolean {
  if (typeof window === "undefined") return false;
  if (prefersReducedMotion()) return false;
  if (window.innerWidth < MIN_WIDTH) return false;
  const cores = navigator.hardwareConcurrency;
  if (typeof cores === "number" && cores > 0 && cores <= 4) return false;
  const mem = (navigator as any).deviceMemory;
  if (typeof mem === "number" && mem > 0 && mem <= 4) return false;
  return true;
}

/** Deterministic RNG (mulberry32), same construction the other two hero
 *  backgrounds use -- a sphere that reshuffles between reloads reads as
 *  instability, not life. */
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

function randomOnSphere(rand: () => number): THREE.Vector3 {
  // z/theta parameterisation, not two angles -- two angles clusters points
  // at the poles rather than distributing them uniformly.
  const z = rand() * 2 - 1;
  const theta = rand() * Math.PI * 2;
  const r = Math.sqrt(Math.max(0, 1 - z * z));
  return new THREE.Vector3(r * Math.cos(theta), r * Math.sin(theta), z);
}

type Particle = { position: THREE.Vector3; color: THREE.Color; scale: number };

/**
 * Pink -> purple -> blue, keyed to each particle's own distance from the
 * centre, so the gradient is a real spatial gradient rather than a random
 * colour mix. Bright pink/magenta core, purple at mid-radius, blue at the
 * outer edge. Density is centre-heavy via the cubed-radius sample, so the
 * core reads as a bright mass and the edge as thinning dust.
 */
function buildParticles(rand: () => number, count: number): Particle[] {
  const particles: Particle[] = [];
  // Deeper and more saturated than the dark-background version's palette.
  // On black, a colour needs to be BRIGHT to separate from the field; on
  // white it needs to be DEEP, because separation now comes from being
  // darker than the page rather than lighter than it. Using the dark
  // version's lighter hues here produced pale, washed-out dust -- fixed by
  // pulling all three toward fully-saturated mid-tones.
  const pink = new THREE.Color(0xe8118a);
  const purple = new THREE.Color(0x7c16e8);
  const blue = new THREE.Color(0x1b45e0);

  for (let i = 0; i < count; i++) {
    // `u` is the uniform sample BEFORE the cubed-radius curve is applied.
    // Position uses the curved value (centre-dense cloud); COLOUR uses the
    // raw uniform one. That split matters and was a real bug first time
    // round: colouring by the curved radius meant that because ~all
    // particles land at small r, ~all particles were pink, and the purple
    // and blue ends of the gradient existed mathematically but had almost
    // no particles in them -- the sphere rendered as flat magenta dust.
    // Colouring by `u` instead spreads the three colour bands evenly across
    // the particle POPULATION while leaving the spatial distribution
    // centre-heavy, so the gradient is visible as a gradient and the cloud
    // still looks like a cloud.
    // Exponent 1.15, not the 2.4 the dark-background component uses. This
    // took three attempts to get right and the reason is worth recording:
    // a spatial colour gradient and an aggressively centre-weighted
    // density are in direct tension. At 2.4, ~all particles land at small
    // r, so whatever colour is mapped to the core swallows the sphere and
    // the outer colours have almost no particles to render -- first attempt
    // came out flat magenta, second (colouring by the pre-curve uniform
    // instead) came out flat purple, because that fixed the population
    // split but broke the spatial mapping. 1.15 keeps a real centre bias
    // while leaving enough population out at mid and high radius for
    // purple and blue to actually exist on screen, which is what lets
    // colour be keyed to TRUE spatial radius below -- a real gradient
    // across the sphere, not a gradient across an invisible population.
    const r = MAX_R * Math.pow(rand(), 1.15);
    const position = randomOnSphere(rand).multiplyScalar(r);

    const t = Math.min(1, r / MAX_R);
    // Jitter the gradient stop (not the position) so the pink->purple and
    // purple->blue transitions read as organic blending rather than two
    // mechanically perfect bands meeting at a seam.
    const tj = Math.min(1, Math.max(0, t + (rand() - 0.5) * 0.18));
    // Stops at 0 / 0.45 / 1.0 rather than an even 0 / 0.5 / 1.0: the outer
    // shell of a sphere holds far more VOLUME (and so far more particles)
    // than the inner one at equal radial thickness, so an even split puts
    // most of the population in the pink->purple half and leaves blue as a
    // thin rim nobody can see. Moving the purple stop inward gives blue the
    // outer 55% of the radius, which is what makes it read on screen.
    const color =
      tj < 0.45
        ? pink.clone().lerp(purple, tj / 0.45)
        : purple.clone().lerp(blue, (tj - 0.45) / 0.55);

    // On WHITE, "fading out" means lightening toward the background, not
    // darkening -- the inverse of the dark-background version, where edge
    // particles were multiplied DOWN toward black.
    //
    // Applied only to the outer third (t > 0.66), and gently. The first
    // attempt faded across the whole radius at 0.55 strength and bleached
    // out precisely the particles carrying the BLUE end of the gradient --
    // the outer ones -- so the sphere read as pink dust with no visible
    // purple-to-blue progression at all. Keeping the inner two-thirds at
    // full saturation is what makes the gradient legible AS a gradient,
    // which is the whole requirement here.
    const fadeT = Math.max(0, (t - 0.84) / 0.16);
    if (fadeT > 0) color.lerp(new THREE.Color(1, 1, 1), fadeT * 0.4);

    particles.push({ position, color, scale: (0.32 + rand() * 0.45) * (1 - t * 0.28) });
  }

  return particles;
}

/** One InstancedMesh, per-instance colour via setColorAt (three's own
 *  instanceColor attribute -- genuine per-instance vertex colour, not a
 *  uniform and not a post-process tint). */
function buildInstances(
  particles: Particle[],
  radius: number,
  opacity: number,
  detail = 0,
): { mesh: THREE.InstancedMesh; geometry: THREE.IcosahedronGeometry; material: THREE.MeshBasicMaterial } {
  // `detail` matters only at the END of the dolly. At rest every particle
  // is a sub-pixel dot and a 12-vertex icosahedron is plenty; once the
  // camera is inside the cloud the nearest particles fill real screen
  // area and a detail-0 solid renders as a visibly HEXAGONAL blob, which
  // reads as a broken shader. Caught on a screenshot at progress 1.0.
  // Detail 1 (42 verts) is enough to read as round at that distance --
  // and is applied only to the core layer, since the halo layer is a
  // blurred 13%-opacity tint whose silhouette nobody can resolve.
  const geometry = new THREE.IcosahedronGeometry(radius, detail);
  const material = new THREE.MeshBasicMaterial({
    toneMapped: false,
    transparent: true,
    opacity,
    depthWrite: opacity >= 1,
  });
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
  return { mesh, geometry, material };
}

export function ScrollHeroSphere({
  particleCount = 11000,
  rotationSpeed = 0.05,
  progressSource,
  className,
}: ScrollHeroSphereProps) {
  const mountRef = useRef<HTMLDivElement>(null);

  // Held in a ref so the scene effect never re-runs when the caller passes
  // a new closure identity -- the effect below reads .current at frame
  // time, so a changed getter is picked up without tearing down WebGL.
  const progressSourceRef = useRef(progressSource);
  progressSourceRef.current = progressSource;

  useEffect(() => {
    const mount = mountRef.current;
    if (!mount || !canRenderSphere()) return;

    let frame = 0;

    const scene = new THREE.Scene();
    scene.background = new THREE.Color(SCENE_BACKGROUND);

    const camera = new THREE.PerspectiveCamera(50, 1, 0.1, 100);
    camera.position.set(0, 0, 4.4);

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

    const rand = makeRand(0x51ed270b);
    const particles = buildParticles(rand, particleCount);

    // Two layers, same positions and colours: a crisp core dot, and a
    // larger, very transparent HALO around it. On a dark field this halo
    // would be redundant (bloom does it), but on white bloom cannot --
    // see the module docstring. A semi-transparent saturated colour over
    // white lightens toward a tint, which is exactly how a halo reads on a
    // light page.
    const core = buildInstances(particles, 0.035, 1, 1);
    const halo = buildInstances(particles, 0.09, HALO_OPACITY);

    const subject = new THREE.Group();
    subject.add(halo.mesh);
    subject.add(core.mesh);
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

    // Rect test rather than IntersectionObserver, same reasoning as the
    // other two hero backgrounds: IO's async first callback can latch a
    // wrong isIntersecting:false for an element that never scrolls again.
    const onScreen = () => {
      const rect = mount.getBoundingClientRect();
      return rect.bottom > 0 && rect.top < window.innerHeight;
    };

    const clock = new THREE.Clock();
    // Damped progress, lagging the raw input. Never assign the target
    // directly -- raw scroll input is jittery (trackpad momentum, wheel
    // steps, scrollbar drags) and a camera that tracks it 1:1 reads as
    // cheap and twitchy rather than as a dolly move.
    let damped = 0;

    const tick = () => {
      frame = requestAnimationFrame(tick);
      if (!onScreen()) return;
      const dt = Math.min(clock.getDelta(), 0.1); // clamp: a backgrounded tab returns a huge first delta

      const raw = progressSourceRef.current?.() ?? 0;
      const target = Math.min(1, Math.max(0, raw));
      damped += (target - damped) * (1 - Math.exp(-DAMPING_PER_SECOND * dt));

      camera.position.z = START_Z + (END_Z - START_Z) * damped;

      // Rush + dissolve. Zero over [0, RUSH_START], then 0..1 over the
      // handoff window, so nothing about the resting sphere changes until
      // the graph starts arriving.
      const rush = Math.max(0, (damped - RUSH_START) / (1 - RUSH_START));
      subject.scale.setScalar(1 + rush * RUSH_SCALE);
      const remaining = 1 - rush;
      core.material.opacity = remaining;
      halo.material.opacity = HALO_OPACITY * remaining;
      // Once the core is translucent it must stop writing depth, or the
      // particles nearest the camera punch depth-buffer holes that hide
      // the ones behind them and the dissolve reads as chunks vanishing
      // rather than a field thinning out.
      core.material.depthWrite = remaining >= 1;

      subject.rotation.y += rotationSpeed * dt;
      composer.render();
    };
    frame = requestAnimationFrame(tick);

    return () => {
      cancelAnimationFrame(frame);
      ro?.disconnect();
      window.removeEventListener("resize", resize);
      core.geometry.dispose();
      core.material.dispose();
      halo.geometry.dispose();
      halo.material.dispose();
      composer.dispose();
      renderer.dispose();
      if (renderer.domElement.parentNode === mount) mount.removeChild(renderer.domElement);
    };
  }, [particleCount, rotationSpeed]);

  return <div ref={mountRef} aria-hidden className={cn("h-full w-full", className)} />;
}

export default ScrollHeroSphere;
