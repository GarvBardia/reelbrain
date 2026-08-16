import Link from "next/link";
import { ArrowRight, Compass, Layers, Sparkles } from "lucide-react";

import { getGraph, getStats } from "@/lib/api";
import { KnowledgeGraph } from "@/components/knowledge-graph";
import { BlurFade } from "@/components/magic/blur-fade";
import { NumberTicker } from "@/components/magic/number-ticker";
import { Spotlight } from "@/components/aceternity/spotlight";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";

// Regenerated at most every 5 minutes (matches PUBLIC_CACHE_TTL_SECONDS on
// the API, so the two cache layers do not fight). Declared explicitly rather
// than inferred from the fetch options so the rendering mode of this route is
// obvious from the file itself.
export const revalidate = 300;


export default async function LandingPage() {
  // Both fetched server-side in parallel: one round trip each, cached by ISR,
  // so the graph is already in the HTML when the page paints.
  const [stats, graph] = await Promise.all([getStats(), getGraph()]);

  return (
    <>
      {/* ---------------- HERO + GRAPH ---------------- */}
      <section className="relative overflow-hidden">
        {/* The single Aceternity spotlight on the entire site. */}
        <Spotlight className="-top-40 left-0 text-indigo-500 md:-top-20 md:left-60" />
        <div className="pointer-events-none absolute inset-0 bg-dot-grid mask-radial-fade" />

        <div className="relative mx-auto max-w-6xl px-6 pb-16 pt-20 md:pt-28">
          <BlurFade className="mx-auto max-w-3xl text-center">
            <div className="mb-6 inline-flex items-center gap-2 rounded-full border border-slate-200 bg-white/70 px-3.5 py-1.5 text-xs font-medium text-slate-600 shadow-sm">
              <Sparkles className="h-3.5 w-3.5 text-indigo-500" />
              Live from a real, continuously-growing knowledge base
            </div>
            <h1 className="text-balance text-4xl font-semibold leading-[1.08] tracking-tight text-slate-900 sm:text-6xl">
              Mycelium turns scattered saved content into a{" "}
              <span className="bg-gradient-to-r from-indigo-600 via-violet-600 to-orange-500 bg-clip-text text-transparent">
                self-organizing, self-improving
              </span>{" "}
              knowledge network.
            </h1>
            <p className="mx-auto mt-6 max-w-2xl text-balance text-lg leading-relaxed text-slate-600">
              Everything you save gets read, understood, categorized and connected —
              automatically. What comes out is not a folder of links. It is a map that
              knows what it contains, and tells you what to do next.
            </p>
            <div className="mt-9 flex flex-wrap items-center justify-center gap-3">
              <Link href="/how-it-works">
                <Button size="lg">
                  See how it works
                  <ArrowRight className="h-4 w-4" />
                </Button>
              </Link>
              <Link href="/library">
                <Button size="lg" variant="outline">
                  Browse the library
                </Button>
              </Link>
            </div>
          </BlurFade>

          {/* THE CENTREPIECE. */}
          <BlurFade delay={0.15} className="mt-14">
            <KnowledgeGraph initial={graph} />
          </BlurFade>
        </div>
      </section>

      {/* ---------------- LIVE NUMBERS ---------------- */}
      <section className="border-y border-slate-200/70 bg-slate-50/60">
        <div className="mx-auto max-w-6xl px-6 py-14">
          <BlurFade>
            <p className="mb-8 text-center text-sm font-medium uppercase tracking-widest text-slate-400">
              The network right now
            </p>
          </BlurFade>
          <dl className="grid grid-cols-2 gap-y-10 md:grid-cols-4">
            {[
              { label: "Saves organized", value: stats.total_reels },
              { label: "Distinct topics", value: stats.total_topics },
              { label: "Tools & entities named", value: stats.total_entities },
              { label: "Actionable next steps", value: stats.actionable_items },
            ].map((stat, i) => (
              <BlurFade key={stat.label} delay={0.06 * i}>
                <div className="text-center">
                  <dd className="text-4xl font-semibold tracking-tight text-slate-900 sm:text-5xl">
                    <NumberTicker value={stat.value} />
                  </dd>
                  <dt className="mt-2 text-sm text-slate-500">{stat.label}</dt>
                </div>
              </BlurFade>
            ))}
          </dl>
        </div>
      </section>

      {/* ---------------- WHAT MAKES IT DIFFERENT ---------------- */}
      <section className="mx-auto max-w-6xl px-6 py-24">
        <BlurFade className="mx-auto max-w-2xl text-center">
          <h2 className="text-balance text-3xl font-semibold tracking-tight text-slate-900 sm:text-4xl">
            A saved link is a dead end. A network is a map.
          </h2>
          <p className="mt-4 text-lg leading-relaxed text-slate-600">
            Three things happen automatically that a bookmarks folder will never do.
          </p>
        </BlurFade>

        <div className="mt-14 grid gap-6 md:grid-cols-3">
          {[
            {
              icon: Layers,
              title: "It organizes itself",
              body: "Every save is read and filed against a shared vocabulary — not whatever tag came to mind that day. The taxonomy converges instead of sprawling.",
              tint: "text-orange-500",
            },
            {
              icon: Sparkles,
              title: "It improves itself",
              body: "New saves are matched against everything already there. Near-duplicates get flagged, related items get linked, and thin entries get re-processed later.",
              tint: "text-violet-600",
            },
            {
              icon: Compass,
              title: "It tells you what to do",
              body: "Each item carries one concrete next step. The highest-value ones get promoted into a queue, so the network hands you work instead of a reading list.",
              tint: "text-blue-600",
            },
          ].map((f, i) => (
            <BlurFade key={f.title} delay={0.08 * i}>
              <Card className="h-full border-slate-200/80 transition-shadow hover:shadow-md">
                <CardContent className="p-7">
                  <f.icon className={`h-6 w-6 ${f.tint}`} />
                  <h3 className="mt-4 text-lg font-semibold text-slate-900">{f.title}</h3>
                  <p className="mt-2.5 leading-relaxed text-slate-600">{f.body}</p>
                </CardContent>
              </Card>
            </BlurFade>
          ))}
        </div>
      </section>

      {/* ---------------- WHAT'S INSIDE ---------------- */}
      {stats.top_categories.length > 0 && (
        <section className="border-t border-slate-200/70 bg-slate-50/60">
          <div className="mx-auto max-w-6xl px-6 py-24">
            <BlurFade className="mx-auto max-w-2xl text-center">
              <h2 className="text-balance text-3xl font-semibold tracking-tight text-slate-900 sm:text-4xl">
                What&apos;s actually in there
              </h2>
              <p className="mt-4 text-lg text-slate-600">
                The biggest clusters in the network today — every number is live.
              </p>
            </BlurFade>

            <div className="mt-12 grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
              {stats.top_categories.map((c, i) => (
                <BlurFade key={c.slug} delay={0.05 * i}>
                  <Link href={`/library?category=${c.slug}`}>
                    <Card className="group h-full border-slate-200/80 transition-all hover:-translate-y-0.5 hover:shadow-md">
                      <CardContent className="flex items-center gap-4 p-6">
                        <span
                          className="h-10 w-1.5 shrink-0 rounded-full"
                          style={{ backgroundColor: c.color }}
                        />
                        <div className="min-w-0 flex-1">
                          <p className="truncate font-medium text-slate-900">{c.label}</p>
                          <p className="text-sm text-slate-500">{c.count} saves</p>
                        </div>
                        <ArrowRight className="h-4 w-4 shrink-0 text-slate-300 transition-transform group-hover:translate-x-0.5 group-hover:text-slate-500" />
                      </CardContent>
                    </Card>
                  </Link>
                </BlurFade>
              ))}
            </div>

            {stats.top_topics.length > 0 && (
              <BlurFade delay={0.1}>
                <div className="mt-12 flex flex-wrap justify-center gap-2">
                  {stats.top_topics.slice(0, 14).map((t) => (
                    <Link key={t.topic} href={`/library?q=${encodeURIComponent(t.topic)}`}>
                      <span className="inline-flex items-center gap-1.5 rounded-full border border-slate-200 bg-white px-3 py-1.5 text-sm text-slate-600 transition-colors hover:border-slate-300 hover:text-slate-900">
                        {t.topic}
                        <span className="tabular-nums text-slate-400">{t.count}</span>
                      </span>
                    </Link>
                  ))}
                </div>
              </BlurFade>
            )}
          </div>
        </section>
      )}

      {/* ---------------- CLOSING CTA ---------------- */}
      <section className="mx-auto max-w-6xl px-6 py-24">
        <BlurFade>
          <div className="relative overflow-hidden rounded-3xl border border-slate-200 bg-slate-900 px-8 py-16 text-center">
            <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(ellipse_60%_60%_at_50%_0%,rgba(99,102,241,0.35),transparent)]" />
            <div className="relative">
              <h2 className="text-balance text-3xl font-semibold tracking-tight text-white sm:text-4xl">
                {stats.actionable_items > 0
                  ? `${stats.actionable_items} things worth doing, already sorted.`
                  : "The network sorts itself. You just read the top."}
              </h2>
              <p className="mx-auto mt-4 max-w-xl text-balance text-lg text-slate-300">
                The Scout queue surfaces the highest-value saves with a concrete next step
                attached — so the pile becomes a plan.
              </p>
              <Link href="/scout" className="mt-8 inline-block">
                <Button size="lg" className="bg-white text-slate-900 hover:bg-slate-100">
                  Open the Scout queue
                  <ArrowRight className="h-4 w-4" />
                </Button>
              </Link>
            </div>
          </div>
        </BlurFade>
      </section>
    </>
  );
}
