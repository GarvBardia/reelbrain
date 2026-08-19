"use client";

import { ExternalLink } from "lucide-react";

import { getScoutQueue } from "@/lib/api";
import { useApi } from "@/lib/use-api";
import { ApiErrorState } from "@/components/api-error-state";
import { BlurFade } from "@/components/magic/blur-fade";
import { Skeleton } from "@/components/skeleton";

export function ScoutClient() {
  const { data, loading, error, retry } = useApi(() => getScoutQueue(40), {
    items: [],
    total_reels: 0,
  });
  const { items, total_reels } = data;

  return (
    <div className="mx-auto max-w-4xl px-6 py-16">
      <BlurFade>
        <p className="mb-3 text-sm font-medium uppercase tracking-widest text-slate-400">
          Implementation queue
        </p>
        <h1 className="text-balance text-4xl font-semibold tracking-tight text-slate-900">
          What&apos;s worth actually doing
        </h1>
        <p className="mt-4 max-w-2xl text-lg leading-relaxed text-slate-600">
          Not a reading list. These are the saves that scored highest on real value and
          carry a concrete next step — ranked, out of{" "}
          <span className="font-medium tabular-nums text-slate-900">
            {total_reels.toLocaleString()}
          </span>{" "}
          in the network.
        </p>
      </BlurFade>

      {loading ? (
        <div className="mt-12 space-y-4">
          {Array.from({ length: 5 }).map((_, i) => (
            <Skeleton key={i} className="h-32 w-full rounded-xl" />
          ))}
        </div>
      ) : error ? (
        <ApiErrorState message={error} onRetry={retry} className="mt-12" />
      ) : items.length === 0 ? (
        <BlurFade delay={0.1}>
          <div className="mt-12 rounded-2xl border border-dashed border-slate-200 py-20 text-center">
            <p className="text-lg font-medium text-slate-900">The queue is clear</p>
            <p className="mt-2 text-slate-500">
              Nothing is currently ranked high enough to surface here.
            </p>
          </div>
        </BlurFade>
      ) : (
        <ol className="mt-12 space-y-4">
          {items.map((item, i) => (
            <BlurFade key={item.shortcode} delay={Math.min(0.04 * i, 0.35)}>
              {/* Borderless with a shadow tinted to the item's category
                  colour -- same card language as the library grid and the
                  landing page, instead of the generic border+gray-shadow. */}
              <div
                className="flex gap-5 rounded-xl bg-white p-6 shadow-sm transition-all hover:-translate-y-0.5"
                style={{ boxShadow: `0 10px 28px -18px ${item.color}59` }}
              >
                  <div className="shrink-0">
                    <span className="flex h-9 w-9 items-center justify-center rounded-xl text-sm font-semibold tabular-nums text-white"
                      style={{ backgroundColor: item.color }}>
                      {i + 1}
                    </span>
                  </div>

                  <div className="min-w-0 flex-1">
                    <div className="mb-2 flex flex-wrap items-center gap-2">
                      <span
                        className="rounded-full px-2.5 py-0.5 text-xs font-medium"
                        style={{ backgroundColor: `${item.color}14`, color: item.color }}
                      >
                        {item.category_label}
                      </span>
                      {item.priority === "High" && (
                        <span className="rounded-full bg-amber-50 px-2.5 py-0.5 text-xs font-medium text-amber-700">
                          High priority
                        </span>
                      )}
                      <span className="text-xs tabular-nums text-slate-400">
                        value {item.value_score}/5
                      </span>
                    </div>

                    <h2 className="font-medium leading-snug text-slate-900">{item.title}</h2>

                    {item.plain_summary && (
                      <p className="mt-2 text-sm leading-relaxed text-slate-600">
                        {item.plain_summary}
                      </p>
                    )}

                    <div className="mt-4 rounded-lg bg-slate-50 px-4 py-2.5">
                      <p className="text-sm leading-snug text-slate-800">
                        <span className="font-semibold" style={{ color: item.color }}>
                          Next step:{" "}
                        </span>
                        {item.suggested_action}
                      </p>
                    </div>

                    <div className="mt-3 flex flex-wrap items-center gap-2">
                      {item.named_entities.slice(0, 4).map((e) => (
                        <span
                          key={e}
                          className="rounded-md bg-slate-100 px-2 py-0.5 text-xs text-slate-600"
                        >
                          {e}
                        </span>
                      ))}
                      {item.permalink && (
                        <a
                          href={item.permalink}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="ml-auto inline-flex items-center gap-1 text-xs text-slate-400 hover:text-slate-700"
                        >
                          Source
                          <ExternalLink className="h-3 w-3" />
                        </a>
                      )}
                    </div>
                  </div>
              </div>
            </BlurFade>
          ))}
        </ol>
      )}
    </div>
  );
}
