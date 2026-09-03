"use client";

import { AnimatePresence, motion } from "framer-motion";
import { ArrowUpRight } from "lucide-react";
import Link from "next/link";
import { useState } from "react";

import type { CategoryInfo } from "@/lib/types";
import { cn } from "@/lib/utils";

/**
 * Skiper UI "skiper52" (HoverExpand_001), adapted from an image gallery to
 * category tiles.
 *
 * Installed by hand rather than via `npx shadcn add @skiper-ui/skiper52`:
 * this project has no components.json, and `shadcn init` would rewrite the
 * Tailwind config, globals.css and path aliases of an already heavily
 * customised app to add one component. The registry source was fetched from
 * https://skiper-ui.com/r/skiper52.json and adapted here instead.
 *
 * What changed from upstream, and why:
 *   - The `images` array is gone. Tiles are driven by the SAME
 *     `data.categories` the graph already renders (slug/label/colour/count),
 *     passed in as a prop -- no second API call.
 *   - `<img src={image.src}>` became a div carrying the category's real hex
 *     colour as a gradient. We have no per-category artwork, and inventing
 *     stock imagery would misrepresent the data.
 *   - `image.code` ("# 23" placeholder) became the real label and save count.
 *   - Upstream imports "swiper/css" and three more Swiper stylesheets but
 *     never renders a Swiper. They are vestigial copy-paste; swiper is not a
 *     dependency of this project, so importing them would fail the build for
 *     no benefit. Dropped.
 *   - Height is 8rem rather than upstream's 24rem. This sits under the graph
 *     as a browsing strip, not as a hero gallery -- a 384px-tall band would
 *     out-weigh the 480px graph it belongs to. The WIDTH interaction the
 *     brief asked to preserve (5rem collapsed -> 24rem expanded, 0.3s
 *     easeInOut, gradient overlay + label fade on the active tile) is
 *     untouched.
 *   - Wrapped in overflow-x-auto. 13 tiles at 5rem + one at 24rem is ~1392px,
 *     which overflows even a 1440px container, and obviously so on a phone.
 *     Scrolling is the brief's own suggested degradation and costs nothing on
 *     desktop, where it never triggers.
 *
 * TWO SEPARATE ACTIONS live on an expanded tile (2026-09-03), and they are two
 * real sibling elements rather than one handler branching on event.target:
 *   - a full-bleed <button> covering the tile, which focuses that category on
 *     the graph and scrolls the graph into view;
 *   - a small "View in Library" <Link> stacked above it, which instead routes
 *     to /library?category=<slug> and does NOT touch the graph.
 * Siblings, deliberately not nested. An <a> inside a <button> is invalid HTML
 * and browsers disagree about which one a click activates, so the wrapper is a
 * plain div and the two targets sit side by side in the stacking order. That
 * removes the conflict structurally rather than papering over it; the link
 * still calls stopPropagation as a guard for the wrapper's own handlers.
 *
 * Touch: upstream already wires onClick alongside onHoverStart, so tap-to-
 * expand works on touch devices with no change. Hover only expands the tile;
 * pinning the category on the graph is a click.
 */
export function HoverExpandCategories({
  categories,
  selected,
  onSelect,
  className,
}: {
  categories: CategoryInfo[];
  /** Slug currently pinned in the graph, or null. Drives the ring, not the expansion. */
  selected: string | null;
  onSelect: (slug: string | null) => void;
  className?: string;
}) {
  const [active, setActive] = useState<number | null>(0);

  if (!categories.length) return null;

  return (
    <motion.div
      initial={{ opacity: 0, translateY: 20 }}
      animate={{ opacity: 1, translateY: 0 }}
      transition={{ duration: 0.3, delay: 0.5 }}
      className={cn("relative w-full", className)}
    >
      <div className="w-full overflow-x-auto pb-1 [scrollbar-width:thin]">
        <div className="flex w-max items-center justify-start gap-1">
          {categories.map((category, index) => {
            const isActive = active === index;
            const isPinned = selected === category.slug;
            return (
              <motion.div
                key={category.slug}
                className={cn(
                  "relative shrink-0 overflow-hidden rounded-2xl",
                  isPinned && "ring-2 ring-slate-900/70",
                )}
                initial={{ width: "5rem", height: "8rem" }}
                animate={{ width: isActive ? "24rem" : "5rem", height: "8rem" }}
                transition={{ duration: 0.3, ease: "easeInOut" }}
                onHoverStart={() => setActive(index)}
              >
                {/* Stands in for upstream's <img>: the category's own colour,
                    which is the same value the graph paints its nodes with. */}
                <div
                  className="size-full"
                  style={{
                    background: `linear-gradient(150deg, ${category.color} 0%, ${category.color}b0 55%, ${category.color}70 100%)`,
                  }}
                />

                {/* ACTION 1 -- the primary target, covering the whole tile:
                    focus this category on the graph and scroll it into view.
                    Sits above the gradient, below the (inert) overlay and the
                    Library link. */}
                <button
                  type="button"
                  aria-label={`Show ${category.label} on the graph, ${category.count} saves`}
                  aria-pressed={isPinned}
                  className="absolute inset-0 z-10 cursor-pointer focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-white/80"
                  onClick={() => {
                    setActive(index);
                    onSelect(isPinned ? null : category.slug);
                  }}
                  onFocus={() => setActive(index)}
                />

                {/* Overlay and label are pointer-events-none so they never
                    intercept a click meant for the button underneath them. */}
                <AnimatePresence>
                  {isActive && (
                    <motion.div
                      initial={{ opacity: 0 }}
                      animate={{ opacity: 1 }}
                      exit={{ opacity: 0 }}
                      className="pointer-events-none absolute inset-0 z-20 h-full w-full bg-gradient-to-t from-black/60 to-transparent"
                    />
                  )}
                </AnimatePresence>

                <AnimatePresence>
                  {isActive && (
                    <motion.div
                      initial={{ opacity: 0 }}
                      animate={{ opacity: 1 }}
                      exit={{ opacity: 0 }}
                      className="pointer-events-none absolute inset-0 z-20 flex h-full w-full flex-col items-start justify-end p-3"
                    >
                      <p className="truncate text-sm font-medium text-white">{category.label}</p>
                      <p className="text-xs tabular-nums text-white/70">
                        {category.count} saves
                      </p>
                    </motion.div>
                  )}
                </AnimatePresence>

                {/* ACTION 2 -- deliberately secondary: small, cornered and
                    quiet beside a tile-sized primary target. Routes straight
                    to the pre-filtered Library and does not focus the graph.
                    z-30 puts it above the primary button so the click lands
                    here instead. */}
                <AnimatePresence>
                  {isActive && (
                    <motion.div
                      initial={{ opacity: 0 }}
                      animate={{ opacity: 1 }}
                      exit={{ opacity: 0 }}
                      className="absolute bottom-3 right-3 z-30"
                    >
                      <Link
                        href={`/library?category=${encodeURIComponent(category.slug)}`}
                        onClick={(event) => event.stopPropagation()}
                        className="inline-flex items-center gap-1 rounded-md bg-white/15 px-2 py-1 text-[11px] font-medium text-white/85 backdrop-blur-sm transition-colors hover:bg-white/25 hover:text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-white/80"
                      >
                        View in Library
                        <ArrowUpRight className="h-3 w-3" />
                      </Link>
                    </motion.div>
                  )}
                </AnimatePresence>
              </motion.div>
            );
          })}
        </div>
      </div>
    </motion.div>
  );
}

/**
 * Skiper 52 HoverExpand_001 — React + Framer Motion
 *
 * License & Usage:
 * - Free to use and modify in both personal and commercial projects.
 * - Attribution to Skiper UI is required when using the free version.
 * - No attribution required with Skiper UI Pro.
 *
 * Author: @gurvinder-singh02
 * Website: https://gxuri.me
 * Twitter: https://x.com/Gur__vi
 */
