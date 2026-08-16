import type { Metadata } from "next";
import Link from "next/link";

import { getReels, getStats } from "@/lib/api";
import { BlurFade } from "@/components/magic/blur-fade";
import { ReelCard } from "@/components/reel-card";
import { LibraryFilters } from "@/components/library-filters";
import { Button } from "@/components/ui/button";

// Regenerated at most every 5 minutes (matches PUBLIC_CACHE_TTL_SECONDS on
// the API, so the two cache layers do not fight). Declared explicitly rather
// than inferred from the fetch options so the rendering mode of this route is
// obvious from the file itself.
export const revalidate = 300;


export const metadata: Metadata = {
  title: "Library",
  description: "Browse and search everything in the Mycelium knowledge network.",
};

export default async function LibraryPage({
  searchParams,
}: {
  searchParams: { q?: string; category?: string; page?: string; min_value?: string };
}) {
  const page = Number(searchParams.page ?? 1) || 1;
  const minValue = Number(searchParams.min_value ?? 1) || 1;

  const [data, stats] = await Promise.all([
    getReels({ q: searchParams.q, category: searchParams.category, page, min_value: minValue }),
    getStats(),
  ]);

  // Preserved across pagination so "next page" does not silently drop the
  // filters the visitor just set.
  const qs = (over: Record<string, string | number | undefined>) => {
    const p = new URLSearchParams();
    const merged = {
      q: searchParams.q,
      category: searchParams.category,
      min_value: minValue > 1 ? String(minValue) : undefined,
      ...over,
    };
    Object.entries(merged).forEach(([k, v]) => {
      if (v !== undefined && v !== "" && v !== null) p.set(k, String(v));
    });
    return `/library?${p}`;
  };

  return (
    <div className="mx-auto max-w-6xl px-6 py-16">
      <BlurFade>
        <h1 className="text-4xl font-semibold tracking-tight text-slate-900">Library</h1>
        <p className="mt-3 text-lg text-slate-600">
          {data.total.toLocaleString()}{" "}
          {searchParams.q || searchParams.category || minValue > 1 ? "matching " : ""}
          saves, newest and highest-value first.
        </p>
      </BlurFade>

      <BlurFade delay={0.05}>
        <LibraryFilters
          categories={stats.top_categories.length ? stats.top_categories : []}
          allCategories={stats.top_categories}
          current={{
            q: searchParams.q ?? "",
            category: searchParams.category ?? "",
            minValue,
          }}
        />
      </BlurFade>

      {data.items.length === 0 ? (
        <BlurFade delay={0.1}>
          <div className="mt-16 rounded-2xl border border-dashed border-slate-200 py-20 text-center">
            <p className="text-lg font-medium text-slate-900">Nothing matches that yet</p>
            <p className="mt-2 text-slate-500">Try a broader search or clear the filters.</p>
            <Link href="/library" className="mt-6 inline-block">
              <Button variant="outline">Clear filters</Button>
            </Link>
          </div>
        </BlurFade>
      ) : (
        <div className="mt-10 grid gap-5 sm:grid-cols-2 lg:grid-cols-3">
          {data.items.map((reel, i) => (
            <BlurFade key={reel.shortcode} delay={Math.min(0.03 * i, 0.3)}>
              <ReelCard reel={reel} />
            </BlurFade>
          ))}
        </div>
      )}

      {data.total_pages > 1 && (
        <div className="mt-14 flex items-center justify-center gap-3">
          {page > 1 && (
            <Link href={qs({ page: page - 1 })}>
              <Button variant="outline">Previous</Button>
            </Link>
          )}
          <span className="px-2 text-sm tabular-nums text-slate-500">
            Page {page} of {data.total_pages}
          </span>
          {page < data.total_pages && (
            <Link href={qs({ page: page + 1 })}>
              <Button variant="outline">Next</Button>
            </Link>
          )}
        </div>
      )}
    </div>
  );
}
