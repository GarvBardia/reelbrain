"use client";

import { useEffect, useRef, useState } from "react";
import { AnimatePresence, motion, useReducedMotion } from "framer-motion";
import {
  ArrowRight,
  Braces,
  Database,
  Download,
  FileText,
  Orbit,
  Share2,
} from "lucide-react";
import Link from "next/link";

import type { Reel } from "@/lib/types";
import { cn } from "@/lib/utils";

/**
 * THE PIPELINE WALKTHROUGH — the /how-it-works centrepiece.
 *
 * THESIS: one real save, followed the whole way down. This page refuses the
 * category default for a "how it works" page — four abstract capability
 * cards with an icon each, which is what the previous version was and which
 * proves nothing — and replaces it with evidence: a single reel that is
 * actually in the database, shown as it exists at each of the six stages.
 * The thing the visitor watches is not an illustration of a pipeline. It is
 * the pipeline's real output, changing shape.
 *
 * OWN-WORLD: the site's existing system, unchanged — white ground, Manrope,
 * slate text, the sphere palette as the only accent. The new material is a
 * single artifact panel with a mono-set data face, because the artifact is
 * data and should look like data, and a hairline progress rail that is the
 * only structural element this page adds.
 *
 * STORY: a recruiter watches a phone share become a filed, searchable note
 * and understands the product in six scrolls. An engineer reads real field
 * names, a real JSON shape, real model routing and real quota numbers, and
 * understands the system. Neither is served a simplified version of the
 * other's page.
 *
 * FIRST VIEWPORT: heading, one sentence, and the first stage already
 * showing the real reel's real permalink — the evidence starts above the
 * fold rather than being promised there.
 *
 * FORM: sticky artifact panel with a scrolling stage column (structure 3 of
 * an ordered list of 7, dealt by concept-seed key ffc6aaa2 alongside 6 and
 * 1, and fused with structure 1's "follow one real save" spine because the
 * morph needs something true to morph).
 *
 * MOTION: deliberately not scroll-jacked. The last scroll-driven thing
 * built for this site (the hero-to-graph camera dolly) was reported as
 * janky and deleted, so this page uses only two mechanisms: whileInView
 * reveals, and CSS `position: sticky`. Nothing hijacks the wheel, nothing
 * pins the viewport, and the whole thing degrades to a plain stacked
 * document under prefers-reduced-motion.
 */

/** One exponential ease-out, shared by every revealed child so the stagger
 *  reads as one movement instead of six unrelated ones. */
const REVEAL = {
  hidden: { opacity: 0, y: 16 },
  shown: { opacity: 1, y: 0, transition: { duration: 0.5, ease: [0.22, 1, 0.36, 1] } },
} as const;

export type Stage = {
  id: string;
  n: string;
  title: string;
  /** For a non-technical reader. No jargon, no hedging. */
  plain: string;
  /** For an engineer. Real component names, real numbers. */
  technical: string;
  icon: typeof Download;
  accent: string;
};

export const STAGES: Stage[] = [
  {
    id: "capture",
    n: "01",
    title: "Capture",
    plain:
      "Sharing a reel to the Mycelium shortcut is the whole interaction. No tag, no folder, no title — the share sheet closes and the pipeline takes it from there.",
    technical:
      "An iOS Shortcut POSTs the URL and a shared secret to /capture on the FastAPI backend. The endpoint validates, enqueues, and returns 202 immediately; everything below happens after that response.",
    icon: Share2,
    accent: "#e8118a",
  },
  {
    id: "fetch",
    n: "02",
    title: "Fetch",
    plain:
      "The backend downloads the reel itself, using a throwaway Instagram account that exists only for this. If the video can't be downloaded, it falls back to the post's caption rather than giving up.",
    technical:
      "yt-dlp with burner-account cookies, behind a per-day fetch cap and a minimum spacing between requests. A startup guard refuses to run at all if the cookie file belongs to the real account. Cookie expiry is detected after three consecutive auth failures and alerted, never auto-refreshed.",
    icon: Download,
    accent: "#c2149f",
  },
  {
    id: "extract",
    n: "03",
    title: "Extract",
    plain:
      "The audio is transcribed and read end to end, and the useful parts are pulled out: what the point actually is, the steps, the tools named, and how worth-your-time it is.",
    technical:
      "One Gemini Flash call takes inline audio plus the caption and returns JSON enforced by a response schema — no free-text parsing. Text-only backfill work is routed to a local llama3.1:8b through Ollama instead, because the Gemini free tier runs out around 17–20 calls a day.",
    icon: Braces,
    accent: "#9c15c4",
  },
  {
    id: "store",
    n: "04",
    title: "Store",
    plain:
      "A page appears in a Notion database, filled in. It is a normal Notion page — searchable, editable, and readable on a phone.",
    technical:
      "Pages are created under a data_source_id (Notion's 2025 API split databases into data sources). The same write links the creator record and fills Related from embedding similarity; embeddings live in SQLite via sqlite-vec, which also catches near-duplicate saves.",
    icon: Database,
    accent: "#7c16e8",
  },
  {
    id: "vault",
    n: "05",
    title: "Mirror",
    plain:
      "Every save is also written to a plain Markdown file on a local machine, so the knowledge base still exists if any hosted service goes away.",
    technical:
      "A scheduled local job syncs Notion into an Obsidian vault. A second pass reads the high-priority notes and ranks what is worth building, publishing only its top pick back to Notion — the vault is local-only, so Notion is the one store both sides can reach.",
    icon: FileText,
    accent: "#5a2ce4",
  },
  {
    id: "publish",
    n: "06",
    title: "Publish",
    plain:
      "Finally the save becomes a point on the public graph, and a card in the library — this site. Private fields never make the trip.",
    technical:
      "A read-only /api/public/* surface filters every field through an allow-list, never a block-list, so a new private Notion property cannot leak by being forgotten. It is cached and rate-limited; the site itself is a static Next.js export on GitHub Pages that calls it from the browser.",
    icon: Orbit,
    accent: "#1b45e0",
  },
];

/** Tracks which stage is currently under the reader, for the sticky panel.
 *  IntersectionObserver rather than scroll maths: no listener on the scroll
 *  event, nothing to throttle, and it stays correct when the stages are
 *  different heights (they are — the copy is not padded to match). */
function useActiveStage(count: number) {
  const [active, setActive] = useState(0);
  const refs = useRef<(HTMLDivElement | null)[]>([]);

  useEffect(() => {
    const els = refs.current.filter(Boolean) as HTMLDivElement[];
    if (els.length === 0) return;
    const io = new IntersectionObserver(
      (entries) => {
        // The entry closest to the top of the reading band wins, so passing
        // a stage hands over cleanly instead of flickering between two.
        const visible = entries
          .filter((e) => e.isIntersecting)
          .sort((a, b) => a.boundingClientRect.top - b.boundingClientRect.top);
        if (visible.length === 0) return;
        const idx = els.indexOf(visible[0].target as HTMLDivElement);
        if (idx >= 0) setActive(idx);
      },
      { rootMargin: "-45% 0px -45% 0px", threshold: 0 },
    );
    els.forEach((el) => io.observe(el));
    return () => io.disconnect();
  }, [count]);

  return { active, refs };
}

/** Shared chrome for the artifact: a small window with a label, so each
 *  stage's payload reads as "a thing the system produced" rather than a
 *  decorative card. */
function Artifact({
  label,
  accent,
  children,
}: {
  label: string;
  accent: string;
  children: React.ReactNode;
}) {
  return (
    <div className="overflow-hidden rounded-xl border border-slate-200 bg-white shadow-[0_18px_50px_-32px_rgba(15,23,42,0.45)]">
      <div className="flex items-center gap-2 border-b border-slate-100 px-4 py-2.5">
        <span className="h-1.5 w-1.5 rounded-full" style={{ backgroundColor: accent }} />
        <span className="font-mono text-[11px] uppercase tracking-widest text-slate-400">
          {label}
        </span>
      </div>
      <div className="p-4">{children}</div>
    </div>
  );
}

const MONO = "font-mono text-[12px] leading-relaxed";

/** A value that isn't loaded yet. Never a fake sample: the panel shows the
 *  real shape with an empty slot rather than inventing plausible content,
 *  which is the same rule the extraction itself follows. */
function Pending({ w = "w-40" }: { w?: string }) {
  return <span className={cn("inline-block h-3 animate-shimmer rounded bg-slate-100", w)} />;
}

function ArtifactBody({ stage, reel }: { stage: Stage; reel: Reel | null }) {
  const accent = stage.accent;
  const url = reel?.permalink ?? "";
  const code = reel?.shortcode ?? "";

  if (stage.id === "capture") {
    return (
      <Artifact label="POST /capture" accent={accent}>
        <pre className={cn(MONO, "overflow-x-auto text-slate-600")}>
          <span className="text-slate-400">{"{"}</span>
          {"\n  "}
          <span className="text-slate-500">&quot;url&quot;</span>:{" "}
          <span style={{ color: accent }}>
            {url ? `"${url}"` : ""}
            {!url && <Pending w="w-48" />}
          </span>
          ,{"\n  "}
          <span className="text-slate-500">&quot;note&quot;</span>: null,{"\n  "}
          <span className="text-slate-500">&quot;secret&quot;</span>:{" "}
          <span className="text-slate-400">&quot;••••••••&quot;</span>
          {"\n"}
          <span className="text-slate-400">{"}"}</span>
        </pre>
        <p className="mt-3 border-t border-slate-100 pt-3 font-mono text-[11px] text-slate-400">
          → 202 Accepted
        </p>
      </Artifact>
    );
  }

  if (stage.id === "fetch") {
    return (
      <Artifact label="yt-dlp" accent={accent}>
        <p className={cn(MONO, "break-all text-slate-500")}>
          <span className="text-slate-400">$</span> yt-dlp --cookies cookies.txt{" "}
          {code ? `.../reel/${code}/` : <Pending w="w-28" />}
        </p>
        {/* Illustrative, and labelled as such below: the audio itself is
            never published, so this is a shape, not this reel's waveform. */}
        <div className="mt-4 flex h-12 items-end gap-[3px]" aria-hidden>
          {Array.from({ length: 48 }).map((_, i) => (
            <span
              key={i}
              className="flex-1 rounded-sm"
              style={{
                // Rounded, not raw floats: server and client can disagree
                // on the last binary digit of a Math.sin result, and React
                // reports that as a hydration mismatch on the style prop.
                height: `${(18 + Math.abs(Math.sin(i * 1.7)) * 78).toFixed(2)}%`,
                backgroundColor: accent,
                opacity: Number((0.18 + Math.abs(Math.cos(i * 0.9)) * 0.5).toFixed(3)),
              }}
            />
          ))}
        </div>
        <p className="mt-3 border-t border-slate-100 pt-3 font-mono text-[11px] text-slate-400">
          audio + caption extracted · waveform illustrative
        </p>
      </Artifact>
    );
  }

  if (stage.id === "extract") {
    return (
      <Artifact label="Gemini → schema-validated JSON" accent={accent}>
        <pre className={cn(MONO, "overflow-x-auto whitespace-pre-wrap text-slate-600")}>
          <span className="text-slate-500">&quot;main_point&quot;</span>:{" "}
          {reel ? (
            <span className="text-slate-700">&quot;{reel.title.slice(0, 96)}…&quot;</span>
          ) : (
            <Pending w="w-52" />
          )}
          {"\n"}
          <span className="text-slate-500">&quot;content_type&quot;</span>:{" "}
          {reel ? (
            <span style={{ color: accent }}>&quot;{reel.content_type}&quot;</span>
          ) : (
            <Pending w="w-20" />
          )}
          {"\n"}
          <span className="text-slate-500">&quot;value_score&quot;</span>:{" "}
          {reel ? <span style={{ color: accent }}>{reel.value_score}</span> : <Pending w="w-6" />}
          {"\n"}
          <span className="text-slate-500">&quot;priority&quot;</span>:{" "}
          {reel ? (
            <span style={{ color: accent }}>&quot;{reel.priority}&quot;</span>
          ) : (
            <Pending w="w-14" />
          )}
          {"\n"}
          <span className="text-slate-500">&quot;named_entities&quot;</span>:{" "}
          {reel ? (
            <span className="text-slate-700">
              [{reel.named_entities.slice(0, 3).map((e) => `"${e}"`).join(", ")}]
            </span>
          ) : (
            <Pending w="w-36" />
          )}
          {"\n"}
          <span className="text-slate-500">&quot;suggested_action&quot;</span>:{" "}
          {reel ? (
            <span className="text-slate-700">
              &quot;{reel.suggested_action.slice(0, 72)}…&quot;
            </span>
          ) : (
            <Pending w="w-48" />
          )}
        </pre>
      </Artifact>
    );
  }

  if (stage.id === "store") {
    const rows: [string, React.ReactNode][] = [
      ["Category", reel ? reel.category_label : <Pending w="w-24" />],
      ["Value score", reel ? `${reel.value_score} / 5` : <Pending w="w-10" />],
      ["Priority", reel ? reel.priority : <Pending w="w-12" />],
      [
        "Topics",
        reel ? (
          <span className="flex flex-wrap gap-1">
            {reel.topics.slice(0, 3).map((t) => (
              <span
                key={t}
                className="rounded bg-sphere-purple/[0.07] px-1.5 py-0.5 text-[11px] text-sphere-purple"
              >
                {t}
              </span>
            ))}
          </span>
        ) : (
          <Pending w="w-32" />
        ),
      ],
      ["Status", <span key="s">✅ Processed/Reviewed</span>],
    ];
    return (
      <Artifact label="Notion · Saves" accent={accent}>
        <dl className="divide-y divide-slate-100">
          {rows.map(([k, v]) => (
            <div key={k} className="flex items-start gap-4 py-2 first:pt-0 last:pb-0">
              <dt className="w-24 shrink-0 text-[12px] text-slate-400">{k}</dt>
              <dd className="min-w-0 text-[13px] text-slate-700">{v}</dd>
            </div>
          ))}
        </dl>
      </Artifact>
    );
  }

  if (stage.id === "vault") {
    return (
      <Artifact label={code ? `vault/reels/${code}.md` : "vault/reels/….md"} accent={accent}>
        <pre className={cn(MONO, "overflow-x-auto whitespace-pre-wrap text-slate-600")}>
          <span className="text-slate-400">---</span>
          {"\n"}
          <span className="text-slate-500">shortcode:</span>{" "}
          {code || <Pending w="w-24" />}
          {"\n"}
          <span className="text-slate-500">priority:</span>{" "}
          {reel ? reel.priority : <Pending w="w-12" />}
          {"\n"}
          <span className="text-slate-500">topics:</span>{" "}
          {reel ? `[${reel.topics.slice(0, 2).join(", ")}]` : <Pending w="w-28" />}
          {"\n"}
          <span className="text-slate-400">---</span>
          {"\n\n"}
          <span style={{ color: accent }}>## Next step</span>
          {"\n"}
          {reel ? reel.suggested_action.slice(0, 88) + "…" : <Pending w="w-44" />}
        </pre>
      </Artifact>
    );
  }

  return (
    <Artifact label="GET /api/public/graph" accent={accent}>
      {/* A flat field, deliberately not a second 3D sphere: this page's
          visual language stays simpler than the hero's. */}
      <svg viewBox="0 0 240 120" className="w-full" role="img" aria-label="The save joins the graph">
        {Array.from({ length: 84 }).map((_, i) => {
          const a = i * 2.399963;
          const r = 8 + Math.sqrt(i) * 5.4;
          return (
            <circle
              key={i}
              cx={Number((120 + Math.cos(a) * r).toFixed(2))}
              cy={Number((60 + Math.sin(a) * r * 0.62).toFixed(2))}
              r={1.5 + (i % 5) * 0.28}
              fill={i % 3 === 0 ? "#7c16e8" : i % 3 === 1 ? "#e8118a" : "#1b45e0"}
              opacity={0.16 + (i % 7) * 0.035}
            />
          );
        })}
        <circle cx={168} cy={38} r={5.5} fill={reel?.color ?? accent} />
        <circle cx={168} cy={38} r={11} fill="none" stroke={reel?.color ?? accent} strokeWidth={1} opacity={0.45} />
      </svg>
      <p className="mt-2 border-t border-slate-100 pt-3 font-mono text-[11px] text-slate-400">
        {reel ? `1 of ${reel.category_label} · public fields only` : "public fields only"}
      </p>
    </Artifact>
  );
}

export function PipelineWalkthrough({ reel }: { reel: Reel | null }) {
  const { active, refs } = useActiveStage(STAGES.length);
  const reduce = useReducedMotion();

  return (
    <div className="mx-auto max-w-6xl px-6">
      {/* Which reel is being followed, stated plainly and linked, so the
          claim "this is real" is checkable rather than asserted. */}
      <div className="mb-12 flex flex-wrap items-center gap-x-2 gap-y-1 border-l border-sphere-purple/40 pl-4 text-sm text-slate-500">
        <span>Following one real save through the pipeline:</span>
        {reel ? (
          <Link
            href={`/library?reel=${reel.shortcode}`}
            className="font-medium text-sphere-purple underline-offset-4 hover:underline"
          >
            {reel.title.slice(0, 56)}…
          </Link>
        ) : (
          <Pending w="w-40" />
        )}
      </div>

      <div className="grid gap-12 lg:grid-cols-[1fr_minmax(0,26rem)] lg:gap-16">
        {/* The stage column. */}
        <div>
          {STAGES.map((stage, i) => {
            const Icon = stage.icon;
            return (
              <div
                key={stage.id}
                ref={(el) => {
                  refs.current[i] = el;
                }}
                className="relative pb-20 pl-14 last:pb-0"
              >
                {/* Rail: one hairline the whole column long, with the
                    reached portion inked in. */}
                <span
                  aria-hidden
                  className={cn(
                    "absolute left-[17px] top-9 -bottom-1 w-px transition-colors duration-500",
                    i <= active ? "bg-sphere-purple/30" : "bg-slate-200",
                    i === STAGES.length - 1 && "hidden",
                  )}
                />
                <span
                  aria-hidden
                  className={cn(
                    "absolute left-0 top-0 flex h-9 w-9 items-center justify-center rounded-full border transition-all duration-500",
                    i <= active
                      ? "border-transparent text-white"
                      : "border-slate-200 bg-white text-slate-300",
                  )}
                  style={i <= active ? { backgroundColor: stage.accent } : undefined}
                >
                  <Icon className="h-4 w-4" />
                </span>

                {/* One orchestrated entrance rather than the same blanket
                    fade on six identical blocks: the number and title lead,
                    the two paragraphs follow a beat later, so a stage reads
                    as arriving in order rather than as a section sliding in
                    whole. staggerChildren does the timing; every child
                    shares one exponential ease-out. */}
                <motion.div
                  initial={reduce ? false : "hidden"}
                  whileInView={reduce ? undefined : "shown"}
                  viewport={{ once: true, margin: "-80px" }}
                  variants={{
                    hidden: {},
                    shown: { transition: { staggerChildren: 0.07 } },
                  }}
                >
                  <motion.p variants={REVEAL} className="font-mono text-xs tracking-widest text-slate-400">
                    {stage.n}
                  </motion.p>
                  <motion.h3
                    variants={REVEAL}
                    className="mt-1 text-2xl font-semibold tracking-tight text-slate-900"
                  >
                    {stage.title}
                  </motion.h3>
                  <motion.p
                    variants={REVEAL}
                    className="mt-3 max-w-xl text-[17px] leading-relaxed text-slate-600"
                  >
                    {stage.plain}
                  </motion.p>

                  {/* The two audiences, stacked rather than toggled: a
                      recruiter reads the paragraph above and stops; an
                      engineer keeps going. Nobody has to find a tab. */}
                  <motion.p
                    variants={REVEAL}
                    className="mt-4 max-w-xl border-l border-slate-200 pl-4 text-sm leading-relaxed text-slate-500"
                  >
                    {stage.technical}
                  </motion.p>

                  {/* On narrow screens the artifact belongs inline, because
                      a sticky panel beside nothing is just a panel that
                      scrolls away. Same renderer, different position. */}
                  <motion.div variants={REVEAL} className="mt-6 lg:hidden">
                    <ArtifactBody stage={stage} reel={reel} />
                  </motion.div>
                </motion.div>
              </div>
            );
          })}
        </div>

        {/* The artifact panel. Sticky, desktop only. */}
        <div className="hidden lg:block">
          <div className="sticky top-28">
            <AnimatePresence mode="wait">
              <motion.div
                key={STAGES[active].id}
                initial={reduce ? false : { opacity: 0, y: 10 }}
                animate={{ opacity: 1, y: 0 }}
                exit={reduce ? undefined : { opacity: 0, y: -10 }}
                transition={{ duration: 0.28, ease: "easeOut" }}
              >
                <ArtifactBody stage={STAGES[active]} reel={reel} />
              </motion.div>
            </AnimatePresence>

            <div className="mt-5 flex items-center gap-2">
              {STAGES.map((s, i) => (
                <span
                  key={s.id}
                  aria-hidden
                  className={cn(
                    "h-1 flex-1 rounded-full transition-colors duration-500",
                    i <= active ? "bg-sphere-purple/40" : "bg-slate-200",
                  )}
                />
              ))}
            </div>
            {/* Sans, not mono: the window labels above are literal
                identifiers (an endpoint, a filename) and earn a mono face.
                This is a caption about the page, and setting it in mono too
                would be mono worn as a costume for "technical". */}
            <p className="mt-3 text-xs text-slate-400">
              Stage {STAGES[active].n} of {STAGES.length} · {STAGES[active].title}
            </p>
          </div>
        </div>
      </div>

      <div className="mt-16 flex justify-center lg:justify-start lg:pl-14">
        <Link
          href="/graph"
          className="inline-flex items-center gap-1.5 text-sm font-medium text-sphere-purple underline-offset-4 hover:underline"
        >
          See where it ends up
          <ArrowRight className="h-3.5 w-3.5" />
        </Link>
      </div>
    </div>
  );
}

export default PipelineWalkthrough;
