import * as THREE from "three";

/**
 * Shared machinery for the particle sphere, in both the roles it now plays:
 * the static decorative hero visual (hero-sphere.tsx) and the real,
 * interactive, data-driven graph (sphere-graph.tsx).
 *
 * WHAT THIS IS PROMOTED FROM, and what was deliberately left behind:
 * the look here is lifted UNCHANGED from the overnight branch's Stage 1
 * work (scroll-hero-sphere.tsx, since deleted -- it exists only in git
 * history now) -- same deep-saturated pink/purple/blue
 * palette, same white scene background, same two-layer core+halo instancing,
 * same bloom tuning, same deterministic mulberry32 seeding. The scroll
 * machinery around it (camera dolly, progress getter, particle rush-out,
 * graph cross-fade) is NOT promoted: it was reported as janky and the
 * sphere is a static hero visual now, so every constant that existed only
 * to serve the dolly (START_Z/END_Z, DAMPING_PER_SECOND, RUSH_START,
 * RUSH_SCALE, DRIFT_AMOUNT) is gone rather than carried along dormant.
 *
 * BLOOM ON A WHITE BACKGROUND -- the constraint that shapes all of this,
 * kept from the original because it is physics rather than taste:
 * UnrealBloomPass is ADDITIVE. It extracts pixels above `threshold`, blurs
 * them, and adds that back over the base render. Adding light to a white
 * base is a no-op -- white is already clipped at 1.0, so a glow has no
 * headroom to occupy. No strength/threshold pair changes that. What reads
 * on white instead is: crisp, deeply saturated colour points, with the
 * halo coming from a second larger low-opacity instance layer (a
 * semi-transparent saturated colour over white lightens toward a tint,
 * which is the white-background equivalent of a glow). The bloom pass is
 * still wired because it earns its place when the camera is zoomed close
 * enough for dense overlapping particles to push a region bright.
 */

/** Scene clear colour: the site's real --background (globals.css is
 *  `0 0% 100%`), so the canvas is seamless against the page rather than a
 *  visible rectangle sitting on it. */
export const SCENE_BACKGROUND = 0xffffff;

/** Outer envelope of the decorative dust cloud, in world units. */
export const MAX_R = 2.2;

/**
 * The palette, unchanged from Stage 1. Deeper and more saturated than a
 * dark-background version would be: on black a colour needs to be BRIGHT
 * to separate from the field, on white it needs to be DEEP, because
 * separation comes from being darker than the page rather than lighter.
 */
export const PINK = 0xe8118a;
export const PURPLE = 0x7c16e8;
export const BLUE = 0x1b45e0;

/** Bloom tuning for a white field -- see the module docstring for why these
 *  are so much lower than a dark-background equivalent would be. */
export const BLOOM_STRENGTH = 0.35;
export const BLOOM_RADIUS = 0.4;
export const BLOOM_THRESHOLD = 0.75;

// The device gate lives in can-render.ts, which imports no three -- see
// that file for why. Re-exported here so existing importers are unaffected.
export { MIN_WIDTH, prefersReducedMotion, canRenderSphere } from "./can-render";

/** Deterministic RNG (mulberry32). A sphere that reshuffles itself between
 *  reloads reads as instability, not life -- and for the data sphere it
 *  would also mean a given reel moving every visit, which destroys any
 *  chance of spatial familiarity. */
export function makeRand(seed: number) {
  let s = seed;
  return () => {
    s |= 0;
    s = (s + 0x6d2b79f5) | 0;
    let t = Math.imul(s ^ (s >>> 15), 1 | s);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

/** Uniform point on a unit sphere. z/theta parameterisation, not two
 *  angles -- two angles clusters points at the poles. */
export function randomOnSphere(rand: () => number): THREE.Vector3 {
  const z = rand() * 2 - 1;
  const theta = rand() * Math.PI * 2;
  const r = Math.sqrt(Math.max(0, 1 - z * z));
  return new THREE.Vector3(r * Math.cos(theta), r * Math.sin(theta), z);
}

/**
 * Fibonacci (golden-spiral) sphere: `count` points spread as evenly as
 * possible over a unit sphere SURFACE, deterministically.
 *
 * This is what places the real reel nodes, and it is deliberately not a
 * force simulation. The old graph used d3-force with charge/collide/link
 * forces, which meant node positions encoded RELATIONSHIPS -- nodes near
 * each other were connected. Nothing about this arrangement implies any
 * relationship between neighbours: it is an even display arrangement and
 * nothing more, which is exactly the instruction. Two consequences worth
 * knowing: there is no settling (positions are final on frame one, no
 * jitter to wait out), and a given reel lands in the same place every
 * visit.
 */
export function fibonacciSphere(count: number): THREE.Vector3[] {
  const points: THREE.Vector3[] = [];
  if (count <= 0) return points;
  // Golden angle. Successive points advance by this around the polar axis
  // while marching linearly down y, which is what produces the even
  // spiral rather than the pole-clustering a naive lat/long grid gives.
  const golden = Math.PI * (3 - Math.sqrt(5));
  for (let i = 0; i < count; i++) {
    // y from +1 to -1, offset by half a step so the first and last points
    // sit just inside the poles rather than exactly on them (a point
    // exactly at a pole has no meaningful longitude and reads as an
    // outlier).
    const y = count === 1 ? 0 : 1 - (i / (count - 1)) * 2;
    const radius = Math.sqrt(Math.max(0, 1 - y * y));
    const theta = golden * i;
    points.push(new THREE.Vector3(Math.cos(theta) * radius, y, Math.sin(theta) * radius));
  }
  return points;
}

/**
 * The gradient, as a function of LATITUDE (y over the sphere's radius)
 * rather than of radius.
 *
 * This is the one thing that genuinely had to change when the sphere
 * became data-driven, and it is worth stating plainly: the Stage 1
 * gradient was radius-keyed (pink core -> blue rim), which cannot work for
 * the real nodes, because every real node sits on the sphere SURFACE and
 * therefore shares one radius -- a radius-keyed gradient would paint all
 * 191 of them the identical colour. Mapping the same three palette stops
 * to latitude instead keeps the exact pink -> purple -> blue identity
 * while giving it somewhere to actually vary across the visible surface.
 *
 * The decorative dust still uses the radius-keyed version (see
 * gradientByRadius) because dust fills the VOLUME, where radius does vary.
 * The two together are what keep the promoted look intact: a pink-cored
 * cloud with a latitude-graded shell of real nodes over it.
 */
export function gradientByLatitude(y: number, radius: number): THREE.Color {
  const pink = new THREE.Color(PINK);
  const purple = new THREE.Color(PURPLE);
  const blue = new THREE.Color(BLUE);
  // y in [-radius, radius] -> t in [0, 1], top of the sphere = 0 = pink.
  const t = Math.min(1, Math.max(0, (radius - y) / (2 * radius)));
  return t < 0.5
    ? pink.clone().lerp(purple, t / 0.5)
    : purple.clone().lerp(blue, (t - 0.5) / 0.5);
}

/** Radius-keyed gradient, kept exactly as Stage 1 had it, for the volume
 *  dust. Stops at 0/0.45/1.0 rather than an even 0/0.5/1.0 because a
 *  sphere's outer shell holds far more volume -- and therefore far more
 *  particles -- than the inner one at equal radial thickness, so an even
 *  split leaves blue as a rim too thin to read. */
export function gradientByRadius(t: number, jitter: number): THREE.Color {
  const pink = new THREE.Color(PINK);
  const purple = new THREE.Color(PURPLE);
  const blue = new THREE.Color(BLUE);
  const tj = Math.min(1, Math.max(0, t + jitter));
  return tj < 0.45
    ? pink.clone().lerp(purple, tj / 0.45)
    : purple.clone().lerp(blue, (tj - 0.45) / 0.55);
}

export type DustParticle = {
  position: THREE.Vector3;
  color: THREE.Color;
  scale: number;
};

/**
 * The decorative volume dust -- non-interactive, carries no data, and is
 * never raycast against.
 *
 * Density exponent 1.15, not something steeper: a spatial colour gradient
 * and an aggressively centre-weighted density are in direct tension, and
 * at 2.4 (the value a purely decorative version can afford) nearly every
 * particle lands at small radius, so the core colour swallows the sphere
 * and the outer colours have almost no particles to render. 1.15 keeps a
 * real centre bias while leaving enough population at mid and high radius
 * for purple and blue to exist on screen.
 */
export function buildDust(rand: () => number, count: number, maxR = MAX_R): DustParticle[] {
  const dust: DustParticle[] = [];
  const white = new THREE.Color(1, 1, 1);
  for (let i = 0; i < count; i++) {
    const r = maxR * Math.pow(rand(), 1.15);
    const position = randomOnSphere(rand).multiplyScalar(r);
    const t = Math.min(1, r / maxR);
    const color = gradientByRadius(t, (rand() - 0.5) * 0.18);
    // Fading out on WHITE means lightening toward the background, not
    // darkening -- the inverse of a dark-background build. Confined to
    // the outer 16% so it thins the rim without bleaching the blue that
    // lives there.
    const fadeT = Math.max(0, (t - 0.84) / 0.16);
    if (fadeT > 0) color.lerp(white, fadeT * 0.4);
    dust.push({ position, color, scale: (0.32 + rand() * 0.45) * (1 - t * 0.28) });
  }
  return dust;
}

/**
 * One InstancedMesh with per-instance colour via setColorAt -- three's real
 * instanceColor attribute, not a uniform and not a post-process tint.
 *
 * `detail` matters only when particles are close to the camera: a detail-0
 * icosahedron is fine as a sub-pixel dot and visibly HEXAGONAL once it
 * fills real screen area, which is a bug the previous build hit and fixed
 * the same way. Dust stays at detail 0 (it is never approached and the
 * halo layer has no resolvable silhouette anyway); the real interactive
 * nodes use detail 1, since zooming in on them is an explicit feature here.
 */
export function buildInstancedPoints(
  positions: THREE.Vector3[],
  colors: THREE.Color[],
  scales: number[],
  radius: number,
  opacity: number,
  detail = 0,
): {
  mesh: THREE.InstancedMesh;
  geometry: THREE.IcosahedronGeometry;
  material: THREE.MeshBasicMaterial;
} {
  const geometry = new THREE.IcosahedronGeometry(radius, detail);
  const material = new THREE.MeshBasicMaterial({
    toneMapped: false,
    transparent: opacity < 1,
    opacity,
    depthWrite: opacity >= 1,
  });
  const mesh = new THREE.InstancedMesh(geometry, material, positions.length);
  const m = new THREE.Matrix4();
  positions.forEach((p, i) => {
    const s = scales[i] ?? 1;
    m.makeScale(s, s, s);
    m.setPosition(p);
    mesh.setMatrixAt(i, m);
    mesh.setColorAt(i, colors[i] ?? new THREE.Color(PURPLE));
  });
  mesh.instanceMatrix.needsUpdate = true;
  if (mesh.instanceColor) mesh.instanceColor.needsUpdate = true;
  return { mesh, geometry, material };
}
