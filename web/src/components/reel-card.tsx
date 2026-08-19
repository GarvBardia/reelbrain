import { ExternalLink } from "lucide-react";

import type { Reel } from "@/lib/types";

/**
 * A library grid card. Clicking anywhere on it opens the full detail view
 * (onSelect) -- so the title, which the API stores truncated and the grid
 * clamps further, is always reachable in full. The Source link is a real
 * anchor nested inside, so it stops propagation to open Instagram directly
 * rather than the modal.
 *
 * Borderless with a shadow tinted to the category colour, matching the
 * landing page's feature and category cards -- one card language across the
 * whole site instead of the generic border+gray-shadow shadcn default here
 * and the tinted treatment there.
 */
export function ReelCard({ reel, onSelect }: { reel: Reel; onSelect?: (reel: Reel) => void }) {
  return (
    <button
      type="button"
      onClick={() => onSelect?.(reel)}
      aria-label={`Open details for: ${reel.title}`}
      className="group flex h-full w-full flex-col rounded-xl bg-white p-6 text-left shadow-sm transition-all hover:-translate-y-0.5 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-slate-300 focus-visible:ring-offset-2"
      style={{ boxShadow: `0 10px 28px -18px ${reel.color}59` }}
    >
      <div className="mb-3 flex items-center gap-2">
        <span
          className="inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-medium"
          style={{ backgroundColor: `${reel.color}14`, color: reel.color }}
        >
          <span className="h-1.5 w-1.5 rounded-full" style={{ backgroundColor: reel.color }} />
          {reel.category_label}
        </span>
        <span className="ml-auto shrink-0 text-xs tabular-nums text-slate-400" title="Value score">
          {"●".repeat(reel.value_score)}
          <span className="text-slate-200">{"●".repeat(Math.max(0, 5 - reel.value_score))}</span>
        </span>
      </div>

      <h3 className="line-clamp-3 font-medium leading-snug text-slate-900">{reel.title}</h3>

      {reel.plain_summary && (
        <p className="mt-2.5 line-clamp-3 text-sm leading-relaxed text-slate-600">
          {reel.plain_summary}
        </p>
      )}

      {reel.suggested_action && reel.suggested_action !== "none — informational" && (
        <p className="mt-4 rounded-lg bg-slate-50 px-3 py-2 text-sm leading-snug text-slate-700">
          <span className="font-medium text-slate-900">Next: </span>
          {reel.suggested_action}
        </p>
      )}

      <div className="mt-auto flex flex-wrap items-center gap-1.5 pt-4">
        {reel.topics.slice(0, 3).map((t) => (
          <span key={t} className="rounded-md bg-slate-100 px-2 py-0.5 text-xs text-slate-500">
            {t}
          </span>
        ))}
        {reel.permalink && (
          <a
            href={reel.permalink}
            target="_blank"
            rel="noopener noreferrer"
            onClick={(e) => e.stopPropagation()}
            className="ml-auto inline-flex items-center gap-1 text-xs text-slate-400 transition-colors hover:text-slate-700"
          >
            Source
            <ExternalLink className="h-3 w-3" />
          </a>
        )}
      </div>
    </button>
  );
}
