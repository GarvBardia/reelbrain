"use client";

import { useEffect, useRef, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { ExternalLink, X } from "lucide-react";

import { getReelDetail } from "@/lib/api";
import type { Reel, ReelDetail as ReelDetailData } from "@/lib/types";
import { cn } from "@/lib/utils";

/**
 * The reel detail view. A modal rather than a dedicated /library/[shortcode]
 * route on purpose: this is a static export (output: "export"), so a dynamic
 * route would need generateStaticParams to enumerate every shortcode at BUILD
 * time -- and the data is live, so any reel captured after the last deploy
 * would 404.
 *
 * Two data sources now, not one (2026-09-04). `reel` is whatever's already in
 * hand from the list fetch -- title, summary, suggested action, topics, named
 * entities, all shown with no extra request, as before. Supporting points,
 * steps/framework and resources mentioned are DIFFERENT in kind: they live in
 * the reel's Notion page BODY, a separate fetch from the page properties
 * `reel` is built from (see app/public_api.py's load_reel_detail for why).
 * So this component now fetches GET /reels/{shortcode}/detail itself, lazily,
 * only once a visitor actually opens a reel -- the grid/list behind it pays
 * no extra cost for reels nobody expands.
 *
 * Quotable lines are deliberately NOT rendered here despite being returned by
 * the endpoint (and despite being one of the Obsidian vault's own sections):
 * they are short but genuinely VERBATIM excerpts of a creator's own spoken
 * words, not the paraphrased-and-cleaned main_point/supporting_points/steps
 * the rest of this view shows. Reproducing another creator's exact words at
 * scale across every reel's public detail page is a different kind of thing
 * than summarizing them, so this stays out of the UI pending an explicit
 * decision to show it.
 *
 * Everything the card truncates for the grid, this shows in full: the whole
 * title, the summary, the suggested action, and -- unlike the card -- every
 * topic, every named entity, the content type, the posted date, and a link
 * back to the original post.
 */
export function ReelDetail({ reel, onClose }: { reel: Reel | null; onClose: () => void }) {
  const closeRef = useRef<HTMLButtonElement>(null);

  // Fetched fresh per reel, reset to null (not a stale previous reel's data)
  // the instant the shortcode changes, and swallowed silently on failure --
  // this is genuinely optional enrichment of an otherwise-complete modal, so
  // a Notion hiccup here should never surface an error UI over content that
  // is already fully usable without it.
  const [detail, setDetail] = useState<ReelDetailData | null>(null);
  useEffect(() => {
    setDetail(null);
    if (!reel) return;
    let cancelled = false;
    getReelDetail(reel.shortcode)
      .then((result) => {
        if (!cancelled) setDetail(result);
      })
      .catch(() => {
        // Silent on purpose -- see the component docstring.
      });
    return () => {
      cancelled = true;
    };
    // Keyed on the shortcode itself, not the `reel` object -- an equivalent
    // reel re-derived from a fresh list fetch (new object, same shortcode)
    // should not re-trigger this.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [reel?.shortcode]);

  // Escape to close, and lock the background from scrolling while open. Both
  // are what makes a hand-rolled modal feel like a real one rather than a
  // floating div.
  useEffect(() => {
    if (!reel) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKey);
    const prevOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    // Move focus into the dialog so keyboard users aren't left behind on the
    // card, and so Escape is caught immediately.
    closeRef.current?.focus();
    return () => {
      document.removeEventListener("keydown", onKey);
      document.body.style.overflow = prevOverflow;
    };
  }, [reel, onClose]);

  const hasAction =
    reel?.suggested_action && reel.suggested_action !== "none — informational";
  const posted = reel?.posted_at
    ? new Date(reel.posted_at).toLocaleDateString(undefined, {
        year: "numeric",
        month: "long",
        day: "numeric",
      })
    : null;

  return (
    <AnimatePresence>
      {reel && (
        <motion.div
          className="fixed inset-0 z-[100] flex items-end justify-center sm:items-center"
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          transition={{ duration: 0.2 }}
        >
          {/* Backdrop. Tinted with the category colour rather than flat black,
              so the modal reads as belonging to this reel's cluster. */}
          <button
            aria-label="Close"
            onClick={onClose}
            className="absolute inset-0 cursor-default bg-slate-900/40 backdrop-blur-sm"
            style={{ backgroundColor: `${reel.color}22` }}
          />

          <motion.div
            role="dialog"
            aria-modal="true"
            aria-labelledby="reel-detail-title"
            // Bottom sheet on phones (slides up, rounded top only), centred
            // card on wider screens. dvh, not vh, so iOS Safari's collapsing
            // toolbar doesn't clip the bottom of the sheet.
            className="relative flex max-h-[92dvh] w-full max-w-xl flex-col overflow-hidden rounded-t-2xl bg-white shadow-2xl sm:max-h-[88dvh] sm:rounded-2xl"
            initial={{ y: 24, opacity: 0.6, scale: 0.98 }}
            animate={{ y: 0, opacity: 1, scale: 1 }}
            exit={{ y: 24, opacity: 0 }}
            transition={{ type: "spring", stiffness: 320, damping: 30 }}
          >
            {/* Accent rail so the modal carries the category colour the same
                way the cards and graph nodes do. */}
            <span
              className="absolute inset-x-0 top-0 h-1"
              style={{ backgroundColor: reel.color }}
            />

            <button
              ref={closeRef}
              onClick={onClose}
              aria-label="Close detail view"
              className="absolute right-4 top-4 z-10 flex h-8 w-8 items-center justify-center rounded-full text-slate-400 transition-colors hover:bg-slate-100 hover:text-slate-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-slate-300"
            >
              <X className="h-4 w-4" />
            </button>

            <div className="overflow-y-auto px-6 pb-6 pt-7 sm:px-8 sm:pb-8">
              {/* Meta row: category, value, priority, type */}
              <div className="flex flex-wrap items-center gap-2 pr-8">
                <span
                  className="inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-medium"
                  style={{ backgroundColor: `${reel.color}14`, color: reel.color }}
                >
                  <span className="h-1.5 w-1.5 rounded-full" style={{ backgroundColor: reel.color }} />
                  {reel.category_label}
                </span>
                <ValueDots score={reel.value_score} />
                {reel.priority && (
                  <span
                    className={cn(
                      "rounded-full px-2.5 py-1 text-xs font-medium",
                      reel.priority === "High"
                        ? "bg-rose-50 text-rose-600"
                        : reel.priority === "Medium"
                          ? "bg-amber-50 text-amber-700"
                          : "bg-slate-100 text-slate-500",
                    )}
                  >
                    {reel.priority} priority
                  </span>
                )}
                {reel.content_type && reel.content_type !== "unknown" && (
                  <span className="rounded-full bg-slate-100 px-2.5 py-1 text-xs font-medium capitalize text-slate-500">
                    {reel.content_type.replace(/_/g, " ")}
                  </span>
                )}
              </div>

              <h2
                id="reel-detail-title"
                className="mt-4 text-balance text-xl font-semibold leading-snug tracking-tight text-slate-900"
              >
                {reel.title}
              </h2>

              {reel.plain_summary && (
                <p className="mt-3 leading-relaxed text-slate-600">{reel.plain_summary}</p>
              )}

              {hasAction && (
                <div className="mt-5 rounded-xl bg-slate-50 px-4 py-3">
                  <p
                    className="text-xs font-medium uppercase tracking-wide"
                    style={{ color: reel.color }}
                  >
                    Suggested next step
                  </p>
                  <p className="mt-1 leading-snug text-slate-700">{reel.suggested_action}</p>
                </div>
              )}

              {/* Block-derived sections (2026-09-04) -- see the component
                  docstring. Each one is its own `length > 0` guard, not a
                  single "detail && ..." wrapper, because these are genuinely
                  independent: a reel can have steps but no listed resources,
                  or resources but no numbered steps, and each combination is
                  common in the real corpus. No loading indicator while
                  `detail` is still null -- the modal is already fully usable
                  on the fields it opened with, and these sections simply
                  appear a moment later rather than blocking anything. */}
              {detail && detail.supporting_points.length > 0 && (
                <Section label="Supporting points">
                  <ul className="w-full space-y-1.5">
                    {detail.supporting_points.map((point, i) => (
                      <li key={i} className="flex gap-2 text-sm leading-snug text-slate-700">
                        <span className="mt-2 h-1 w-1 shrink-0 rounded-full bg-slate-300" />
                        {point}
                      </li>
                    ))}
                  </ul>
                </Section>
              )}

              {detail && detail.steps_or_framework.length > 0 && (
                <Section label="Steps">
                  <ol className="w-full space-y-1.5">
                    {detail.steps_or_framework.map((step, i) => (
                      <li key={i} className="flex gap-2.5 text-sm leading-snug text-slate-700">
                        <span
                          className="mt-0.5 flex h-4 w-4 shrink-0 items-center justify-center rounded-full text-[10px] font-medium tabular-nums"
                          style={{ backgroundColor: `${reel.color}18`, color: reel.color }}
                        >
                          {i + 1}
                        </span>
                        {step}
                      </li>
                    ))}
                  </ol>
                </Section>
              )}

              {detail && detail.resources_mentioned.length > 0 && (
                <Section label="Resources mentioned">
                  {detail.resources_mentioned.map((resource, i) =>
                    resource.url ? (
                      <a
                        key={i}
                        href={resource.url}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="inline-flex items-center gap-1 rounded-md border border-slate-200 px-2 py-0.5 text-xs text-slate-600 transition-colors hover:border-slate-300 hover:text-slate-900"
                      >
                        {resource.name}
                        <ExternalLink className="h-3 w-3 text-slate-400" />
                      </a>
                    ) : (
                      <span
                        key={i}
                        className="rounded-md border border-slate-200 px-2 py-0.5 text-xs text-slate-600"
                      >
                        {resource.name}
                      </span>
                    ),
                  )}
                </Section>
              )}

              {reel.topics.length > 0 && (
                <Section label="Topics">
                  {reel.topics.map((t) => (
                    <span
                      key={t}
                      className="rounded-md bg-slate-100 px-2 py-0.5 text-xs text-slate-600"
                    >
                      {t}
                    </span>
                  ))}
                </Section>
              )}

              {reel.named_entities.length > 0 && (
                <Section label="Named entities">
                  {reel.named_entities.map((e) => (
                    <span
                      key={e}
                      className="rounded-md border border-slate-200 px-2 py-0.5 text-xs text-slate-600"
                    >
                      {e}
                    </span>
                  ))}
                </Section>
              )}

              <div className="mt-6 flex flex-wrap items-center justify-between gap-3 border-t border-slate-100 pt-5">
                {posted ? (
                  <span className="text-xs text-slate-400">Posted {posted}</span>
                ) : (
                  <span />
                )}
                {reel.permalink && (
                  <a
                    href={reel.permalink}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="inline-flex items-center gap-1.5 rounded-lg bg-slate-900 px-3.5 py-2 text-sm font-medium text-white transition-colors hover:bg-slate-700"
                  >
                    View original on Instagram
                    <ExternalLink className="h-3.5 w-3.5" />
                  </a>
                )}
              </div>
            </div>
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>
  );
}

function Section({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="mt-5">
      <p className="text-xs font-medium uppercase tracking-wide text-slate-400">{label}</p>
      <div className="mt-2 flex flex-wrap gap-1.5">{children}</div>
    </div>
  );
}

/** The same filled/empty dot language the card uses, kept in sync so the value
 *  score reads identically in both places, plus an explicit n/5 for clarity. */
function ValueDots({ score }: { score: number }) {
  return (
    <span className="inline-flex items-center gap-1.5 text-xs tabular-nums text-slate-400">
      <span aria-hidden>
        <span className="text-slate-500">{"●".repeat(score)}</span>
        <span className="text-slate-200">{"●".repeat(Math.max(0, 5 - score))}</span>
      </span>
      <span className="sr-only">Value score </span>
      {score}/5
    </span>
  );
}
