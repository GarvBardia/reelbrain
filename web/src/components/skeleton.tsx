import { cn } from "@/lib/utils";

/** A shimmering placeholder block for the moment between mount and the first
 *  client-side fetch resolving -- unavoidable on a fully static site with no
 *  server-rendered data, so it is deliberately quick and unobtrusive rather
 *  than a full-page spinner.
 *
 * THE BUG (found 2026-09-08, chasing "the detail modal's loading state feels
 * stuck/slow"): this never actually animated. The inline `style.animation`
 * below and the `animate-pulse` Tailwind class both set the same CSS
 * property, and inline styles win the cascade -- so `animate-pulse` was dead
 * code, fully overridden every render. And the inline value it lost to
 * referenced a bare CSS keyframe name, `shimmer`, that was never registered
 * as a plain `@keyframes` rule anywhere; Tailwind only emits keyframes for
 * animations it sees requested via a class name (`animate-shimmer`), and
 * nothing in this codebase ever used that class. So the browser silently
 * dropped an animation pointing at a name that didn't exist, `animate-pulse`
 * was already shadowed, and every skeleton on the site has been rendering as
 * a static gradient block since the day this was written -- confirmed by
 * grepping the built output for `@keyframes shimmer` and finding nothing.
 * A frozen placeholder reads as "stuck," not "loading" -- which is exactly
 * the perception this was reported as, even though the real network fetch
 * behind it (see ReelDetail) is unrelated and already cached. Fixed by using
 * the real Tailwind utility (`animate-shimmer`, defined in
 * tailwind.config.ts's theme.extend.animation, which DOES get its keyframes
 * emitted because the class is now actually referenced) instead of a raw
 * inline value that was never going to resolve. */
export function Skeleton({ className }: { className?: string }) {
  return (
    <div
      className={cn(
        "animate-shimmer rounded-md bg-gradient-to-r from-slate-100 via-slate-50 to-slate-100 bg-[length:200%_100%]",
        className,
      )}
    />
  );
}
