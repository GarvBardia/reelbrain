"use client";

import { useEffect, useRef, useState } from "react";
import dynamic from "next/dynamic";
import { motion, useMotionValueEvent, useScroll, useTransform } from "framer-motion";

import { EMPTY_GRAPH, getGraph } from "@/lib/api";
import { useApi } from "@/lib/use-api";
import { KnowledgeGraph } from "@/components/knowledge-graph";
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

/**
 * The handoff window. Must match RUSH_START in scroll-hero-sphere.tsx --
 * the particles rushing outward and the graph scaling in are the SAME
 * event seen from two sides, and staggering them turns a transition into
 * a crossfade.
 */
const HANDOFF_START = 0.6;

/** The graph starts small enough to read as "the thing the sphere became",
 *  not as a separate panel sliding in. 0.72 rather than something smaller
 *  because react-force-graph's canvas has its own internal zoomToFit
 *  framing -- scaling the wrapper much below this makes node labels
 *  illegibly small during the transition rather than merely distant. */
const GRAPH_START_SCALE = 0.72;

/** Pointer events return to the graph only this close to the end. Below
 *  it the graph is still translucent and still moving, and a click landing
 *  on a half-faded layer is the classic way these transitions feel broken. */
const INTERACTIVE_AT = 0.98;

export function HeroScrollSequence() {
  const sectionRef = useRef<HTMLDivElement>(null);

  /**
   * Whether this device gets the sequence at all, resolved in a LAZY
   * INITIALISER -- i.e. during the first render, not in an effect after it.
   *
   * This looks like the thing you're not supposed to do (canRenderSphere()
   * touches window/navigator), and it would be wrong in a server-rendered
   * component. It is correct here specifically because this whole
   * component is loaded via dynamic(..., { ssr: false }): there is no
   * server render to mismatch against, and the first client render is the
   * first render there is.
   *
   * It also fixes a real, silent bug. Resolving this in useEffect meant
   * the first render returned the disabled branch, which does not attach
   * `sectionRef` -- so useScroll below initialised with a NULL target and
   * fell back to measuring whole-document scroll progress instead of this
   * section's. The dolly then ran off page scroll: at the point the
   * sequence should have finished (progress 1), measured progress was
   * 1620/2629 = 0.616, and the handoff never completed. Measured, not
   * guessed -- the graph's computed opacity read 0.04 where it should have
   * read 1. Resolving before first paint keeps the ref attached from the
   * very first render, so useScroll always has its real target.
   */
  const [enabled] = useState(() => canRenderSphere());

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

  // The graph's own data. Fetched exactly the way the real homepage
  // fetches it (useApi + getGraph("all")), so this preview exercises the
  // real payload rather than a fixture -- ~190 real reel nodes.
  const { data: graph } = useApi(() => getGraph("all"), EMPTY_GRAPH);

  // Graph scale/opacity as MotionValues -> applied via motion.div's style,
  // so scrolling drives them with zero React re-renders, same as the
  // sphere. useTransform clamps outside its input range by default, so
  // below HANDOFF_START these sit flat at their start values.
  const graphOpacity = useTransform(scrollYProgress, [HANDOFF_START, 1], [0, 1]);
  const graphScale = useTransform(scrollYProgress, [HANDOFF_START, 1], [GRAPH_START_SCALE, 1]);

  // The one thing that genuinely needs React state: whether the graph is
  // hit-testable. It flips at most twice per visit, so a state update is
  // the right tool here in a way it isn't for scale/opacity.
  const [interactive, setInteractive] = useState(false);
  useMotionValueEvent(scrollYProgress, "change", (v) => {
    const next = v >= INTERACTIVE_AT;
    setInteractive((prev) => (prev === next ? prev : next));
  });

  // A device that can't run the sphere shouldn't be scroll-jacked through
  // a 260vh track to watch nothing happen. Reduced-motion and low-power
  // devices get a plain, static, normal-height section instead -- same
  // answer canRenderSphere() gives the sphere itself, so the two can't
  // disagree about whether the sequence is running.
  // NOTE: the ref'd element below is rendered in BOTH branches, and the
  // early return is inside it rather than around it. useScroll resolves
  // its target once; handing it an element that only exists in one branch
  // is what caused the whole-document fallback described above, so the
  // tracked container stays mounted regardless of which branch runs.
  return (
    <div
      ref={sectionRef}
      style={{ height: enabled ? `${SCROLL_VH}vh` : undefined }}
      className="relative"
    >
      {!enabled ? (
        <section className="mx-auto flex h-[70vh] max-w-6xl items-center justify-center px-6">
          <p className="text-sm text-slate-400">
            Scroll sequence disabled on this device (reduced motion, narrow viewport, or
            low-power hardware).
          </p>
        </section>
      ) : (
        // The pinned frame. `sticky top-0` rather than a library pin: the
        // element stays put for exactly as long as its parent is scrolling
        // past, which is the same window useScroll is measuring, so the two
        // cannot drift out of sync the way a JS-pinned element and a
        // separately-computed progress value can.
        <div className="sticky top-0 h-screen w-full overflow-hidden">
        {/* Both layers fill the same box and are centred the same way, so
            the sphere's centre and the graph's centre are the SAME screen
            point. That is what makes the handoff read as one object
            growing rather than two objects swapping places -- the eye
            tracks a single centre through the whole move. */}
        <div className="absolute inset-0">
          <ScrollHeroSphere progressSource={() => scrollYProgress.get()} />
        </div>

        {/* The real force graph -- the actual react-force-graph-2d
            component the live site uses, unmodified, rendered here as a
            second instance. It mounts at progress 0 rather than being
            deferred to the handoff: its force simulation runs 400 warmup
            ticks synchronously on first render and then cools, so mounting
            it early means it is fully settled and STILL by the time it
            becomes visible. Deferring the mount would instead put a
            visibly jittering, self-arranging graph on screen at exactly
            the moment the transition hands over to it. It costs nothing
            during the dolly because a cooled simulation stops ticking. */}
        <motion.div
          style={{ opacity: graphOpacity, scale: graphScale }}
          className="absolute inset-0 flex items-center justify-center"
          // Not hit-testable until the transition has essentially landed,
          // so clicks during the fade cannot hit a translucent, moving
          // target. Restored in one step at INTERACTIVE_AT.
          aria-hidden={!interactive}
        >
          <div
            className="w-full max-w-6xl px-6"
            style={{ pointerEvents: interactive ? "auto" : "none" }}
          >
            <KnowledgeGraph initial={graph} />
          </div>
        </motion.div>
        </div>
      )}
    </div>
  );
}

export default HeroScrollSequence;
