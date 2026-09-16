"use client";

import dynamic from "next/dynamic";
import Link from "next/link";
import { ArrowLeft, Loader2 } from "lucide-react";

import { EMPTY_GRAPH, getGraph } from "@/lib/api";
import { useApi } from "@/lib/use-api";
import { ApiErrorState } from "@/components/api-error-state";
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
 *     h-screen with overflow hidden, and there is no page scroll for the
 *     wheel to fight. An overlay would leave the homepage scrolling
 *     underneath and need a scroll lock to paper over it.
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

  return (
    // h-screen + overflow-hidden is load-bearing, not cosmetic: it is what
    // makes "the wheel belongs entirely to the graph" true rather than
    // merely intended. With nothing to scroll, the graph's own wheel
    // handling cannot be in conflict with the page, which is the specific
    // failure this view was split out to avoid.
    <div className="relative h-screen w-screen overflow-hidden bg-white">
      {/* Back out of the view. Deliberately a real link to "/" rather than
          router.back(): this route is linkable and can be arrived at
          directly, in which case there is no back to go to. */}
      <div className="absolute left-0 right-0 top-0 z-20 flex items-center justify-between px-6 py-5">
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

      {loading ? (
        <div className="flex h-full items-center justify-center text-slate-400">
          <Loader2 className="h-5 w-5 animate-spin" />
        </div>
      ) : error ? (
        <div className="flex h-full items-center justify-center px-6">
          <ApiErrorState message={error} onRetry={retry} />
        </div>
      ) : (
        <SphereGraph data={data} />
      )}
    </div>
  );
}

export default GraphClient;
