"use client";

import { AlertTriangle, RefreshCw } from "lucide-react";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

/**
 * The visible failure state every API-backed view renders instead of
 * disappearing.
 *
 * The bug this exists to prevent: the landing-page graph used to fall back to
 * an empty node list on any fetch error, which draws an empty canvas --
 * indistinguishable from a healthy graph with nothing in it, and offering the
 * visitor no way to recover. Anything that can fail now says so and offers a
 * retry.
 */
export function ApiErrorState({
  message,
  onRetry,
  className,
  compact = false,
}: {
  message: string;
  onRetry: () => void;
  className?: string;
  compact?: boolean;
}) {
  return (
    <div
      className={cn(
        "flex flex-col items-center justify-center rounded-2xl border border-dashed border-slate-300 bg-slate-50/50 text-center",
        compact ? "gap-2 px-6 py-10" : "gap-3 px-6 py-16",
        className,
      )}
      role="alert"
    >
      <div className="flex h-10 w-10 items-center justify-center rounded-full bg-amber-50">
        <AlertTriangle className="h-5 w-5 text-amber-600" />
      </div>
      <p className={cn("font-medium text-slate-900", compact ? "text-sm" : "text-base")}>
        Couldn&apos;t load this
      </p>
      <p className="max-w-md text-sm leading-relaxed text-slate-500">{message}</p>
      <Button variant="outline" size="sm" onClick={onRetry} className="mt-1">
        <RefreshCw className="h-3.5 w-3.5" />
        Try again
      </Button>
    </div>
  );
}
