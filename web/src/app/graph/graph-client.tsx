"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import dynamic from "next/dynamic";
import Link from "next/link";
import { ArrowLeft, Loader2 } from "lucide-react";

import { EMPTY_GRAPH, getGraph, getReelByShortcode } from "@/lib/api";
import type { Reel } from "@/lib/types";
import { useApi } from "@/lib/use-api";
import { ApiErrorState } from "@/components/api-error-state";
import { GraphFallbackList } from "@/components/graph-fallback-list";
// Imported from can-render, NOT from sphere-core: sphere-core pulls in all
// of three and the postprocessing passes, and this question has to be
// answerable without loading any of that -- especially on the devices the
// answer is "no" for.
import { canRenderSphere } from "@/components/graph-sphere/can-render";
import { ReelDetail } from "@/components/reel-detail";
import { Button } from "@/components/ui/button";

/**
 * THE GRAPH VIEW -- a dedicated route, not an overlay on the homepage.
 *
 * WHY A ROUTE RATHER THAN A FULL-SCREEN MODAL (the brief left this open):
 *  1. It matches how every other destination on this site already works.
 *     /library, /scout, /how-it-works are all real routes; a full-screen
 *     overlay would be the only navigational special case in the app.
 *  2. It is linkable and back-button-correct for free. An overlay needs
 *     history entries synthesised by hand to not trap the back button --
 *     the reel modal already does exactly that dance with
 *     history.replaceState in library-client.tsx, and that is the most
 *     fragile part of that file.
 *  3. It solves the scroll conflict structurally rather than defensively.
 *     The documented failure of the old graph was its wheel handler
 *     fighting page scroll; here the page IS the graph, the shell is
 *     taken out of flow entirely (see the note on the shell below), and
 *     there is no page scroll for the wheel to fight. An overlay would
 *     leave the homepage scrolling underneath and need a scroll lock to
 *     paper over it.
 *  4. output:"export" handles it natively -- the route prerenders to a
 *     static shell and fetches its data client-side, same as every other
 *     page here.
 *
 * The one thing a route costs that an overlay wouldn't: a full navigation
 * rather than an in-place transition from the hero. Given the scroll-driven
 * in-place transition is exactly what was just reported as janky and
 * removed, that cost reads as a feature.
 */

/** Lazy + browser-only, same as every other WebGL thing in this codebase:
 *  three + postprocessing is the heaviest asset on the site and must not
 *  land in the shared bundle. */
const SphereGraph = dynamic(
  () => import("@/components/graph-sphere/sphere-graph").then((m) => m.SphereGraph),
  {
    ssr: false,
    loading: () => (
      <div className="flex h-full items-center justify-center text-slate-400">
        <Loader2 className="h-5 w-5 animate-spin" />
      </div>
    ),
  },
);

export function GraphClient() {
  const { data, loading, error, retry } = useApi(() => getGraph("all"), EMPTY_GRAPH);

  const [selectedReel, setSelectedReel] = useState<Reel | null>(null);
  const requestedShortcodeRef = useRef<string | null>(null);

  /**
   * Click-through, lifted verbatim in behaviour from the old graph's
   * openReelByShortcode -- same endpoint, same race guard, same silent
   * failure, and the same ReelDetail component a Library card opens. The
   * brief was explicit that this pattern is kept and the modal reused
   * rather than rebuilt, so the only thing that actually changed is what
   * detects the click (a raycast instead of a canvas hit test).
   *
   * It lives on the ROUTE rather than inside SphereGraph so that the sphere
   * stays a pure renderer with no knowledge of the API: the same component
   * could be dropped anywhere with a different handler.
   *
   * The ref guards a stale response winning a race -- open reel A, then
   * quickly reel B, and A's slower response must not land second and swap
   * the open modal back to the wrong reel.
   */
  const openReelByShortcode = useCallback((shortcode: string) => {
    requestedShortcodeRef.current = shortcode;
    getReelByShortcode(shortcode)
      .then((reel) => {
        if (reel && requestedShortcodeRef.current === shortcode) setSelectedReel(reel);
      })
      .catch(() => {
        // Silent on a miss or a failure, consistent with every other lazy
        // fetch here: enrichment failing should not surface as a broken click.
      });
  }, []);

  const closeReel = useCallback(() => setSelectedReel(null), []);

  /**
   * Which of the two views this device gets. Three-valued on purpose:
   * `null` means "not decided yet".
   *
   * It has to start null rather than calling canRenderSphere() during
   * render, because this route is statically prerendered by the export --
   * the server has no window, so a render-time call would hydrate
   * mismatched. Resolving it in an effect costs one extra frame of loader
   * and is the difference between correct and a hydration error.
   *
   * Re-checked on resize so that dragging a desktop window narrow, or
   * rotating a tablet, lands on the right view rather than the one the
   * device happened to qualify for at load.
   */
  const [canRender, setCanRender] = useState<boolean | null>(null);
  useEffect(() => {
    const update = () => setCanRender(canRenderSphere());
    update();
    window.addEventListener("resize", update);
    return () => window.removeEventListener("resize", update);
  }, []);

  /** Mobile-only: which category the fallback list is drilled into. Local
   *  state, not a refetch -- `data` is already the expand="all" payload. */
  const [expanded, setExpanded] = useState<string | null>(null);

  return (
    // `fixed inset-0`, not `h-screen`, and that distinction is load-bearing
    // rather than cosmetic. The goal is that the wheel belongs ENTIRELY to
    // the graph -- the documented failure of the old graph was its wheel
    // handler fighting page scroll, and this view exists partly to make
    // that conflict structurally impossible rather than defended against.
    //
    // `h-screen` did not achieve that, and it was worth measuring rather
    // than assuming: the root layout renders the site header above this,
    // so a full-viewport-height shell UNDER a 64px header gave a document
    // 1025px tall in an 860px viewport -- a scrolling page, and a sphere
    // pushed down and cropped. Taking the shell out of flow entirely fixes
    // both at once, with no hardcoded header height to drift: the only
    // thing left in flow is the header itself, which is shorter than the
    // viewport, so there is nothing to scroll.
    //
    // z-40 keeps it UNDER the sticky site header (z-50), deliberately: the
    // header is translucent with a backdrop blur and floats over content
    // everywhere else on the site, and there is no good reason for the
    // graph to be the one route that hides the site's navigation.
    <div className="fixed inset-0 z-40 overflow-hidden bg-white">
      {/* Back out of the view. Deliberately a real link to "/" rather than
          router.back(): this route is linkable and can be arrived at
          directly, in which case there is no back to go to. */}
      {/* top-16 clears the sticky site header, which floats over this shell
          (see the z-index note above). It is a measured constant rather
          than a guess -- the header renders 64px tall -- and it only
          offsets this small floating bar, so if the header ever changes
          height the worst case is a cosmetic gap, not a broken layout. */}
      <div className="absolute left-0 right-0 top-16 z-20 flex items-center justify-between px-6 py-4">
        <Link href="/">
          <Button variant="outline" size="sm" className="gap-1.5 bg-white/80 backdrop-blur-sm">
            <ArrowLeft className="h-3.5 w-3.5" />
            Back
          </Button>
        </Link>
        <p className="text-xs font-medium uppercase tracking-widest text-slate-400">
          {data.total_reels > 0 ? `${data.total_reels.toLocaleString()} saves` : "The graph"}
        </p>
      </div>

      {canRender === null || loading ? (
        <div className="flex h-full items-center justify-center text-slate-400">
          <Loader2 className="h-5 w-5 animate-spin" />
        </div>
      ) : error ? (
        <div className="flex h-full items-center justify-center px-6">
          <ApiErrorState message={error} onRetry={retry} />
        </div>
      ) : canRender ? (
        <SphereGraph data={data} onSelectReel={openReelByShortcode} />
      ) : null}

      {/* The small-screen view of the SAME data, UNCHANGED from what the
          old graph rendered: same component, same props, same tappable
          category-then-reel drill-down, same click-through to ReelDetail.
          The rebuild is desktop-only by instruction, and this is what
          keeps that true -- the sphere replaced the canvas, not the list.

          It scrolls, unlike the sphere view, so this wrapper opts back
          into overflow-y-auto that the shell turns off. pt-28 clears both
          the site header and the floating Back bar above it. */}
      {canRender === false && !loading && !error ? (
        <div className="h-full overflow-y-auto px-4 pb-10 pt-32">
          <GraphFallbackList
            data={data}
            expanded={expanded}
            onExpand={setExpanded}
            onSelectReel={openReelByShortcode}
          />
        </div>
      ) : null}

      {/* WHAT AM I LOOKING AT. The sphere shipped with no explanation at all:
          nothing said a point was one reel, nothing said the controls
          existed, and nothing said that where a point sits carries no
          meaning -- which matters, because an evenly-spread ball of points
          looks exactly like a relationship graph and is not one.

          Deliberately two small muted captions in the corners rather than a
          panel or an overlay: the answer has to be available without
          competing with the thing it describes, and without needing to be
          dismissed before the view can be used.

          Only rendered for the sphere. The mobile list explains itself. */}
      {canRender === true && !loading && !error ? (
        <>
          <p className="pointer-events-none absolute bottom-5 left-6 z-20 max-w-xs text-xs leading-relaxed text-slate-400">
            <span className="font-medium text-slate-500">Each point is one saved reel.</span>{" "}
            Points are spread evenly; where a point sits doesn&apos;t mean it&apos;s related
            to its neighbours. Colour runs pink to blue from top to bottom and marks
            nothing.
          </p>
          <p className="pointer-events-none absolute bottom-5 right-6 z-20 hidden text-xs text-slate-400 lg:block">
            Hover to preview · Click to open · Scroll to zoom · Swipe sideways to rotate
          </p>
        </>
      ) : null}

      <ReelDetail reel={selectedReel} onClose={closeReel} />
    </div>
  );
}

export default GraphClient;
