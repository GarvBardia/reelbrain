"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";

import { EMPTY_STATS, getReels, getStats } from "@/lib/api";
import { useApi } from "@/lib/use-api";
import { BlurFade } from "@/components/magic/blur-fade";
import { ReelCard } from "@/components/reel-card";
import { LibraryFilters } from "@/components/library-filters";
import { Skeleton } from "@/components/skeleton";
import { Button } from "@/components/ui/button";

const EMPTY_REELS = { items: [], total: 0, page: 1, page_size: 24, total_pages: 1 };

export function LibraryClient() {
  // useSearchParams (not props) because this is now a fully client-rendered
  // page -- GitHub Pages has no server to read the request URL for us.
  const searchParams = useSearchParams();
  const q = searchParams.get("q") ?? "";
  const category = searchParams.get("category") ?? "";
  const page = Number(searchParams.get("page") ?? 1) || 1;
  const minValue = Number(searchParams.get("min_value") ?? 1) || 1;

  const { data: stats } = useApi(getStats, EMPTY_STATS, []);
  const { data, loading } = useApi(
    () => getReels({ q, category, page, min_value: minValue }),
    EMPTY_REELS,
    [q, category, page, minValue],
  );

  const qs = (over: Record<string, string | number | undefined>) => {
    const p = new URLSearchParams();
    const merged: Record<string, string | number | undefined> = {
      q,
      category,
      min_value: minValue > 1 ? minValue : undefined,
      ...over,
    };
    Object.entries(merged).forEach(([k, v]) => {
      if (v !== undefined && v !== "") p.set(k, String(v));
    });
    return `/library?${p}`;
  };

  return (
    <div className="mx-auto max-w-6xl px-6 py-16">
      <BlurFade>
        <h1 className="text-4xl font-semibold tracking-tight text-slate-900">Library</h1>
        <p className="mt-3 text-lg text-slate-600">
          {data.total.toLocaleString()}
          {q || category || minValue > 1 ? " matching " : " "}
          saves, newest and highest-value first.
        </p>
      </BlurFade>

      <BlurFade delay={0.05}>
        <LibraryFilters
          categories={stats.top_categories}
          allCategories={stats.top_categories}
          current={{ q, category, minValue }}
        />
      </BlurFade>

      {loading ? (
        <div className="mt-10 grid gap-5 sm:grid-cols-2 lg:grid-cols-3">
          {Array.from({ length: 6 }).map((_, i) => (
            <Skeleton key={i} className="h-56 w-full rounded-xl" />
          ))}
        </div>
      ) : data.items.length === 0 ? (
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
