"use client";

import Link from "next/link";
import { ArrowRight } from "lucide-react";

import { EMPTY_STATS, getStats } from "@/lib/api";
import { useApi } from "@/lib/use-api";
import { BlurFade } from "@/components/magic/blur-fade";
import { PipelineStages } from "@/components/pipeline-stages";
import { Button } from "@/components/ui/button";

export function HowItWorksClient() {
  const { data: stats } = useApi(getStats, EMPTY_STATS);

  return (
    <>
      <section className="mx-auto max-w-4xl px-6 pb-8 pt-20 text-center">
        <BlurFade>
          <p className="mb-4 text-sm font-medium uppercase tracking-widest text-slate-400">
            The pipeline
          </p>
          <h1 className="text-balance text-4xl font-semibold leading-[1.1] tracking-tight text-slate-900 sm:text-5xl">
            Four stages. No filing, no folders, no upkeep.
          </h1>
          <p className="mx-auto mt-6 max-w-2xl text-balance text-lg leading-relaxed text-slate-600">
            A mycelial network grows toward what it finds and links it back to everything
            already connected. This works the same way: every save extends the network and
            makes the rest of it a little more useful.
          </p>
        </BlurFade>
      </section>

      <PipelineStages
        totalReels={stats.total_reels}
        totalTopics={stats.total_topics}
        actionable={stats.actionable_items}
      />

      {/* The honest part: what it deliberately does NOT do. */}
      <section className="border-t border-slate-200/70 bg-slate-50/60">
        <div className="mx-auto max-w-4xl px-6 py-20">
          <BlurFade>
            <h2 className="text-balance text-3xl font-semibold tracking-tight text-slate-900">
              What it deliberately doesn&apos;t do
            </h2>
            <p className="mt-4 text-lg text-slate-600">
              The constraints are the design, not omissions.
            </p>
          </BlurFade>
          <dl className="mt-10 space-y-7">
            {[
              {
                q: "It never invents a summary.",
                a: "If a save has no readable content, it is marked as needing another pass rather than being given a plausible-sounding description. An honest gap beats a confident guess.",
              },
              {
                q: "It never publishes what was gated.",
                a: "When a creator asks you to comment a word to get a link, that link is the thing they are trading for engagement. It is stored privately and never appears in any public view.",
              },
              {
                q: "It never lets the vocabulary sprawl.",
                a: "Tags are matched against the categories that already exist before a new one is allowed. Spelling drift is normalized on write, so the map keeps converging instead of fragmenting.",
              },
              {
                q: "It never needs a subscription to keep thinking.",
                a: "The text-only work runs on a local model. Only the parts that genuinely need to watch or listen to a video call out to a hosted one.",
              },
            ].map((item, i) => (
              <BlurFade key={item.q} delay={0.05 * i}>
                <div className="border-l-2 border-slate-200 pl-6">
                  <dt className="font-semibold text-slate-900">{item.q}</dt>
                  <dd className="mt-1.5 leading-relaxed text-slate-600">{item.a}</dd>
                </div>
              </BlurFade>
            ))}
          </dl>
        </div>
      </section>

      <section className="mx-auto max-w-4xl px-6 py-20 text-center">
        <BlurFade>
          <h2 className="text-balance text-3xl font-semibold tracking-tight text-slate-900">
            See the result
          </h2>
          <p className="mx-auto mt-4 max-w-lg text-lg text-slate-600">
            {stats.total_reels > 0
              ? `${stats.total_reels} saves have already been through all four stages.`
              : "Every save that goes through comes out mapped and actionable."}
          </p>
          <div className="mt-8 flex flex-wrap justify-center gap-3">
            <Link href="/library">
              <Button size="lg">
                Browse the library
                <ArrowRight className="h-4 w-4" />
              </Button>
            </Link>
            <Link href="/scout">
              <Button size="lg" variant="outline">
                Open the Scout queue
              </Button>
            </Link>
          </div>
        </BlurFade>
      </section>
    </>
  );
}
