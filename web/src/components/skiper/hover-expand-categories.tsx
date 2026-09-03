"use client";

import { AnimatePresence, motion } from "framer-motion";
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
 * Touch: upstream already wires onClick alongside onHoverStart, so tap-to-
 * expand works on touch devices with no change. Click additionally pins the
 * category in the graph (see onSelect) -- hover only expands the tile.
 * Hover deliberately does NOT filter the graph: this component's active tile
 * is sticky (it never resets on mouse-leave, upstream behaviour we kept), so
 * driving the filter from hover would leave the graph stuck on whichever
 * category the cursor last crossed on its way somewhere else.
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
              <motion.button
                key={category.slug}
                type="button"
                aria-label={`${category.label}, ${category.count} saves`}
                aria-pressed={isPinned}
                className={cn(
                  "relative shrink-0 cursor-pointer overflow-hidden rounded-2xl",
                  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-slate-900/60",
                  isPinned && "ring-2 ring-slate-900/70",
                )}
                initial={{ width: "5rem", height: "8rem" }}
                animate={{ width: isActive ? "24rem" : "5rem", height: "8rem" }}
                transition={{ duration: 0.3, ease: "easeInOut" }}
                onClick={() => {
                  setActive(index);
                  onSelect(isPinned ? null : category.slug);
                }}
                onHoverStart={() => setActive(index)}
                onFocus={() => setActive(index)}
              >
                {/* Stands in for upstream's <img>: the category's own colour,
                    which is the same value the graph paints its nodes with. */}
                <div
                  className="size-full"
                  style={{
                    background: `linear-gradient(150deg, ${category.color} 0%, ${category.color}b0 55%, ${category.color}70 100%)`,
                  }}
                />

                <AnimatePresence>
                  {isActive && (
                    <motion.div
                      initial={{ opacity: 0 }}
                      animate={{ opacity: 1 }}
                      exit={{ opacity: 0 }}
                      className="absolute inset-0 h-full w-full bg-gradient-to-t from-black/60 to-transparent"
                    />
                  )}
                </AnimatePresence>

                <AnimatePresence>
                  {isActive && (
                    <motion.div
                      initial={{ opacity: 0 }}
                      animate={{ opacity: 1 }}
                      exit={{ opacity: 0 }}
                      className="absolute inset-0 flex h-full w-full flex-col items-start justify-end p-3"
                    >
                      <p className="truncate text-sm font-medium text-white">
                        {category.label}
                      </p>
                      <p className="text-xs tabular-nums text-white/70">
                        {category.count} saves
                      </p>
                    </motion.div>
                  )}
                </AnimatePresence>
              </motion.button>
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
