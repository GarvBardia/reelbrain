import { ExternalLink } from "lucide-react";

import type { Reel } from "@/lib/types";
import { Card, CardContent } from "@/components/ui/card";

export function ReelCard({ reel }: { reel: Reel }) {
  return (
    <Card className="group flex h-full flex-col border-slate-200/80 transition-all hover:-translate-y-0.5 hover:shadow-md">
      <CardContent className="flex flex-1 flex-col p-6">
        <div className="mb-3 flex items-center gap-2">
          <span
            className="inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-medium"
            style={{ backgroundColor: `${reel.color}14`, color: reel.color }}
          >
            <span className="h-1.5 w-1.5 rounded-full" style={{ backgroundColor: reel.color }} />
            {reel.category_label}
          </span>
          <span className="ml-auto text-xs tabular-nums text-slate-400" title="Value score">
            {"●".repeat(reel.value_score)}
            <span className="text-slate-200">{"●".repeat(5 - reel.value_score)}</span>
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
              className="ml-auto inline-flex items-center gap-1 text-xs text-slate-400 hover:text-slate-700"
            >
              Source
              <ExternalLink className="h-3 w-3" />
            </a>
          )}
        </div>
      </CardContent>
    </Card>
  );
}
