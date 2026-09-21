"use client";

import Link from "next/link";
import { ArrowRight } from "lucide-react";

import { EMPTY_STATS, getReelByShortcode, getScoutQueue, getStats } from "@/lib/api";
import type { Reel, ScoutItem } from "@/lib/types";
import { useApi } from "@/lib/use-api";
import { BlurFade } from "@/components/magic/blur-fade";
import { PipelineWalkthrough } from "@/components/pipeline-walkthrough";
import { Button } from "@/components/ui/button";

/**
 * /how-it-works — rebuilt (2026-09-20).
 *
 * What it replaced and why: four capability cards ("Capture, Extract,
 * Organize, Suggest"), each an icon and two sentences. Three problems, all
 * factual rather than aesthetic. It claimed four stages when the pipeline
 * has six — the Instagram fetch, the local vault mirror and the public API
 * were simply missing. It asserted instead of showing, so an engineer
 * learned nothing checkable and a recruiter saw no evidence. And it opened
 * on a mycelium metaphor rather than on the product.
 *
 * The replacement follows ONE REAL SAVE through all six stages and shows
 * what that save actually looked like at each one. See the contract at the
 * top of pipeline-walkthrough.tsx.
 */

const EMPTY_SCOUT: { items: ScoutItem[]; total_reels: number } = { items: [], total_reels: 0 };

export function HowItWorksClient() {
  const { data: stats } = useApi(getStats, EMPTY_STATS);

  /**
   * The subject of the walkthrough, chosen by the data rather than by hand:
   * the top of the Scout queue is by definition a save the pipeline rated
   * 5/5 with a real suggested action, so it is guaranteed to have every
   * field the stages below display. Hardcoding a favourite shortcode would
   * have been one more thing to rot.
   *
   * A failure here is not an error state: the walkthrough renders its real
   * shapes with empty slots (see Pending), because the page still explains
   * the pipeline correctly without the example, and inventing a plausible
   * sample would break the one promise this page makes.
   */
  const { data: scout } = useApi(() => getScoutQueue(1), EMPTY_SCOUT);
  const shortcode = scout.items[0]?.shortcode ?? null;

  /**
   * Two requests on purpose. /scout-queue is the right way to CHOOSE the
   * example (it is the one endpoint that guarantees a real suggested
   * action), but it returns a deliberately small projection -- no topics,
   * no content_type. The stages display both, so the full record is fetched
   * by shortcode once the choice is made. Both responses are cached server
   * side, and the page renders correctly from the moment the first lands.
   */
  const { data: reel } = useApi<Reel | null>(
    () => (shortcode ? getReelByShortcode(shortcode) : Promise.resolve(null)),
    null,
    [shortcode],
  );

  return (
    <>
      <section className="mx-auto max-w-6xl px-6 pb-16 pt-20">
        <BlurFade>
          {/* No eyebrow above this heading, unlike the other pages here: a
              kicker is a label doing the heading's job, and this heading
              carries itself. The stage count moved into the sentence below,
              where it is information rather than decoration. */}
          <h1 className="max-w-3xl text-balance text-4xl font-semibold leading-[1.1] tracking-tight text-slate-900 sm:text-5xl">
            One shared reel, followed all the way to this site.
          </h1>
          <p className="mt-6 max-w-2xl text-lg leading-relaxed text-slate-600">
            Six stages, and everything below is one real save from the database, shown as it
            existed at each one — the request that started it, the JSON the model returned, the
            Notion page, the Markdown mirror, and the point it became on the graph. Nothing
            here is a mock-up.
          </p>
        </BlurFade>
      </section>

      <PipelineWalkthrough reel={reel} />

      {/* The constraints section, kept from the previous page because it was
          the one part doing real work -- but rewritten against what the code
          does, not what reads well. Two of the four claims here were
          unverifiable as previously worded. */}
      <section className="mt-28 border-t border-slate-200/70 bg-slate-50/60">
        <div className="mx-auto max-w-6xl px-6 py-20">
          <BlurFade>
            <h2 className="text-balance text-3xl font-semibold tracking-tight text-slate-900">
              Where it deliberately stops
            </h2>
            <p className="mt-4 max-w-2xl text-lg text-slate-600">
              Each of these is a constraint that was chosen, and each one costs something.
            </p>
          </BlurFade>
          <dl className="mt-12 grid gap-x-12 gap-y-9 md:grid-cols-2">
            {[
              {
                q: "It never invents a summary.",
                a: "A save with no readable content is marked as needing another pass instead of being given a plausible description. The cost is visible gaps in the library; the alternative is a knowledge base that lies quietly.",
              },
              {
                q: "It never publishes a gated link.",
                a: "When a creator asks you to comment a word to receive a link, that link is what they are trading for engagement. It is stored in a private field and filtered out of every public response by an allow-list.",
              },
              {
                q: "It never logs in for you.",
                a: "Instagram sessions expire. Detecting that is automatic — three consecutive auth failures raises an alert — but refreshing the cookies is a manual two-minute job, because automating it would mean storing a real password.",
              },
              {
                q: "It never pays for inference.",
                a: "Extraction runs on the Gemini free tier, which in practice stops at 17–20 calls a day. Text-only backfill work is routed to a local model instead, and a daily runner spends the remaining quota in priority order and resumes tomorrow.",
              },
            ].map((item, i) => (
              <BlurFade key={item.q} delay={0.05 * i}>
                <div className="border-l border-sphere-purple/30 pl-5">
                  <dt className="font-semibold text-slate-900">{item.q}</dt>
                  <dd className="mt-2 leading-relaxed text-slate-600">{item.a}</dd>
                </div>
              </BlurFade>
            ))}
          </dl>
        </div>
      </section>

      <section className="mx-auto max-w-4xl px-6 py-24 text-center">
        <BlurFade>
          <h2 className="text-balance text-3xl font-semibold tracking-tight text-slate-900">
            {stats.total_reels > 0
              ? `${stats.total_reels.toLocaleString()} saves have been through all six stages.`
              : "See the output"}
          </h2>
          <p className="mx-auto mt-4 max-w-xl text-lg text-slate-600">
            The graph is every one of them at once. The library is the same set, searchable.
          </p>
          <div className="mt-8 flex flex-wrap justify-center gap-3">
            <Link href="/graph">
              <Button size="lg">
                Explore the graph
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
      </section>
    </>
  );
}

export default HowItWorksClient;
