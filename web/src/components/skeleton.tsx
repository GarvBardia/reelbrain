import { cn } from "@/lib/utils";

/** A shimmering placeholder block for the moment between mount and the first
 *  client-side fetch resolving -- unavoidable on a fully static site with no
 *  server-rendered data, so it is deliberately quick and unobtrusive rather
 *  than a full-page spinner. */
export function Skeleton({ className }: { className?: string }) {
  return (
    <div
      className={cn(
        "animate-pulse rounded-md bg-gradient-to-r from-slate-100 via-slate-50 to-slate-100 bg-[length:200%_100%]",
        className,
      )}
      style={{ animation: "shimmer 1.6s linear infinite" }}
    />
  );
}
