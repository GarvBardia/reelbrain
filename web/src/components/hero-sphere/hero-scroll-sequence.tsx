"use client";

import { useEffect, useRef, useState } from "react";
import dynamic from "next/dynamic";
import { useScroll } from "framer-motion";

import { canRenderSphere } from "./scroll-hero-sphere";

/**
 * The scroll-driven hero sequence: a tall scroll section, a sticky viewport
 * inside it, and a normalised 0..1 progress value derived from how far
 * through the tall section the page has scrolled.
 *
 * NO GSAP / ScrollTrigger, and no Lenis. The brief allowed either but said
 * to prefer what's already installed, and framer-motion (11.11) is already
 * a dependency here -- it drives ReelDetail's modal, BlurFade and
 * NumberTicker. Its `useScroll({ target, offset })` gives exactly the
 * scrubbed 0..1 this needs, and CSS `position: sticky` does the pinning
 * without a library pinning the element for us. Net new dependencies for
 * this whole sequence: zero.
 *
 * WHY SCROLL PROGRESS IS READ, NOT SUBSCRIBED:
 * `scrollYProgress` is a framer MotionValue -- it updates outside React and
 * does NOT re-render on change unless something subscribes it into state.
 * Nothing here does. The sphere is handed `() => scrollYProgress.get()` and
 * samples it inside its own animation frame. So a full scroll through the
 * sequence causes zero React renders; the only state in this component is
 * the one-shot booleans below, which flip at most twice per visit.
 */

const ScrollHeroSphere = dynamic(
  () => import("./scroll-hero-sphere").then((m) => m.ScrollHeroSphere),
  { ssr: false },
);

/**
 * Height of the scroll track, as a multiple of the viewport. The sticky
 * child occupies 100vh of it, so scroll TRAVEL is (SCROLL_VH - 100)vh --
 * at 260vh on a 900px viewport that's ~1,440px of scrolling to move the
 * dolly from 0 to 1.
 *
 * Tuned for the brief's "roughly 1-1.5 seconds at a normal reading-pace
 * scroll, err longer rather than shorter": a brisk continuous trackpad
 * scroll runs somewhere around 1,000-1,500 px/sec, which puts this at
 * ~1.0-1.4s. A shorter track makes the dolly feel like a jump-cut; a much
 * longer one makes the page feel stuck, which is the more common failure
 * of scroll-jacked heroes and the one worth erring away from.
 */
const SCROLL_VH = 260;

export function HeroScrollSequence() {
  const sectionRef = useRef<HTMLDivElement>(null);

  // Whether this device gets the sequence at all. Resolved on the client
  // after mount, never during render -- canRenderSphere() reads
  // window/navigator, and calling it during render would either crash SSR
  // or (worse) produce a server/client hydration mismatch. Defaults to
  // false so the no-JS and pre-hydration states are the SIMPLE ones.
  const [enabled, setEnabled] = useState(false);
  useEffect(() => setEnabled(canRenderSphere()), []);

  const { scrollYProgress } = useScroll({
    target: sectionRef,
    // "start start" -> "end end": progress hits 0 when the section's top
    // reaches the viewport top, and 1 when its bottom reaches the viewport
    // bottom. With a sticky 100vh child that maps exactly onto "the whole
    // time the sticky panel is pinned", which is what makes the dolly
    // start and finish precisely while the sphere is the only thing on
    // screen -- rather than burning progress while the section is still
    // scrolling into view.
    offset: ["start start", "end end"],
  });

  // A device that can't run the sphere shouldn't be scroll-jacked through
  // a 260vh track to watch nothing happen. Reduced-motion and low-power
  // devices get a plain, static, normal-height section instead -- same
  // answer canRenderSphere() gives the sphere itself, so the two can't
  // disagree about whether the sequence is running.
  if (!enabled) {
    return (
      <section className="mx-auto flex h-[70vh] max-w-6xl items-center justify-center px-6">
        <p className="text-sm text-slate-400">
          Scroll sequence disabled on this device (reduced motion, narrow viewport, or
          low-power hardware).
        </p>
      </section>
    );
  }

  return (
    <div ref={sectionRef} style={{ height: `${SCROLL_VH}vh` }} className="relative">
      {/* The pinned frame. `sticky top-0` rather than a library pin: the
          element stays put for exactly as long as its parent is scrolling
          past, which is the same window useScroll is measuring, so the two
          cannot drift out of sync the way a JS-pinned element and a
          separately-computed progress value can. */}
      <div className="sticky top-0 h-screen w-full overflow-hidden">
        <ScrollHeroSphere progressSource={() => scrollYProgress.get()} />
      </div>
    </div>
  );
}

export default HeroScrollSequence;
