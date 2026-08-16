"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { Search, X } from "lucide-react";

import type { CategoryInfo } from "@/lib/types";
import { cn } from "@/lib/utils";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";

/**
 * Filters drive the URL, not local state. That keeps every view shareable and
 * back-button-correct, and it lets the page stay a server component that reads
 * searchParams -- only this small control strip needs to be a client component.
 */
export function LibraryFilters({
  allCategories,
  current,
}: {
  categories: CategoryInfo[];
  allCategories: CategoryInfo[];
  current: { q: string; category: string; minValue: number };
}) {
  const router = useRouter();
  const [q, setQ] = useState(current.q);

  const push = (over: Record<string, string | number | undefined>) => {
    const merged: Record<string, string | number | undefined> = {
      q: current.q,
      category: current.category,
      min_value: current.minValue,
      ...over,
    };

    const p = new URLSearchParams();
    Object.entries(merged).forEach(([key, value]) => {
      if (value === undefined || value === "") return;
      // min_value 1 means "no minimum" — the default, so it stays out of the
      // URL rather than showing up as noise in every shared link.
      if (key === "min_value" && Number(value) <= 1) return;
      p.set(key, String(value));
    });
    router.push(`/library?${p}`);
  };

  const hasFilters = current.q || current.category || current.minValue > 1;

  return (
    <div className="mt-8 space-y-4">
      <form
        onSubmit={(e) => {
          e.preventDefault();
          push({ q, page: undefined });
        }}
        className="flex gap-2"
      >
        <div className="relative flex-1">
          <Search className="pointer-events-none absolute left-3.5 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400" />
          <Input
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="Search titles, summaries, topics, tools…"
            className="pl-10"
          />
        </div>
        <Button type="submit">Search</Button>
        {hasFilters && (
          <Button
            type="button"
            variant="ghost"
            onClick={() => {
              setQ("");
              router.push("/library");
            }}
          >
            <X className="h-4 w-4" />
            Clear
          </Button>
        )}
      </form>

      <div className="flex flex-wrap items-center gap-2">
        <button
          onClick={() => push({ category: undefined, page: undefined })}
          className={cn(
            "rounded-full border px-3 py-1.5 text-sm transition-colors",
            !current.category
              ? "border-slate-900 bg-slate-900 text-white"
              : "border-slate-200 text-slate-600 hover:border-slate-300",
          )}
        >
          All
        </button>
        {allCategories.map((c) => {
          const active = current.category === c.slug;
          return (
            <button
              key={c.slug}
              onClick={() => push({ category: active ? undefined : c.slug, page: undefined })}
              className={cn(
                "inline-flex items-center gap-1.5 rounded-full border px-3 py-1.5 text-sm transition-colors",
                active ? "text-white" : "border-slate-200 text-slate-600 hover:border-slate-300",
              )}
              style={
                active
                  ? { backgroundColor: c.color, borderColor: c.color }
                  : undefined
              }
            >
              {!active && (
                <span className="h-1.5 w-1.5 rounded-full" style={{ backgroundColor: c.color }} />
              )}
              {c.label}
            </button>
          );
        })}

        <div className="ml-auto flex items-center gap-1.5 text-sm text-slate-500">
          <span>Min value</span>
          {[1, 3, 4, 5].map((v) => (
            <button
              key={v}
              onClick={() => push({ min_value: v, page: undefined })}
              className={cn(
                "h-7 w-7 rounded-md border text-xs tabular-nums transition-colors",
                current.minValue === v
                  ? "border-slate-900 bg-slate-900 text-white"
                  : "border-slate-200 hover:border-slate-300",
              )}
            >
              {v === 1 ? "All" : v}
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}
