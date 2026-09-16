/**
 * The device gate, deliberately in its own module with NO `three` import.
 *
 * That separation is the entire point of this file existing rather than
 * the two functions staying in sphere-core.ts. The /graph route has to ask
 * "can this device render the sphere?" BEFORE deciding whether to load the
 * sphere at all -- and sphere-core imports all of three plus the
 * postprocessing passes, so importing the question from there would drag
 * the heaviest asset on the site into the route bundle to answer it, on
 * exactly the low-end devices the answer is "no" for.
 *
 * sphere-core re-exports these, so nothing else has to know they moved.
 */

/** Below this width neither sphere renders -- same threshold and reasoning
 *  as ascii-hero-background.tsx and particle-hero-background.tsx before it. */
export const MIN_WIDTH = 768;

export function prefersReducedMotion(): boolean {
  if (typeof window === "undefined") return false;
  return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

/**
 * Is this device worth running a bloom-postprocessed instanced particle
 * scene on? Unchanged gate from the previous hero backgrounds.
 *
 * NOTE the difference in consequence between the two callers: for the hero
 * this means "render nothing, it's decoration". For /graph it means "render
 * the tappable list instead", because there the sphere is carrying real
 * data a visitor actually needs, and silently showing nothing would be
 * hiding the content rather than skipping an ornament.
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
