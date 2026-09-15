"use client";

import { useState } from "react";
import dynamic from "next/dynamic";
import { ArrowRight, Sparkles } from "lucide-react";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import type { ParticleHeroVariant } from "@/components/particle-hero-background";

/**
 * LOCAL-ONLY COMPARISON ROUTE (2026-09-16) -- not linked from anywhere in
 * the site's nav, and explicitly NOT meant to ship. This exists purely so
 * the two ParticleHeroBackground variants can be viewed live via
 * `npm run dev` and compared side by side with a mocked-up hero, per direct
 * request ("don't push yet -- build both, let me look and pick one").
 *
 * Why a real route under app/ rather than something Next.js excludes from
 * routing (an `_`-prefixed folder): an excluded folder can't be served by
 * `npm run dev` either -- there would be nothing to point a browser at. A
 * real route is the only way to actually view this locally. The cost is
 * real too: `output: "export"` (next.config.mjs) means this WOULD become a
 * live, publicly-reachable page at /preview/particles/ if the static export
 * is ever built and deployed with this file still present. Delete this file
 * (and the /preview folder if it's left empty) once a variant is chosen and
 * wired into the real hero -- do not deploy with it in place.
 *
 * PAGE is white (2026-09-17, corrected from an earlier fully-dark version):
 * this preview's own outer background now matches the real site's actual
 * `--background: 0 0% 100%` (globals.css) -- confirmed against the live
 * page, not assumed. The dark field bloom needs (see
 * ParticleHeroBackground's own module docstring for why transparent doesn't
 * work) is still real, it is just no longer the WHOLE page: the hero is now
 * a bounded, rounded card sitting in an otherwise-white page, the same
 * relationship the real data graph card (knowledge-graph.tsx's own
 * rounded-[1.35rem] canvas, or AsciiHeroBackground's bounded top-640px
 * band) already has to the rest of that page. Text stays white/light INSIDE
 * the card, because it is still sitting on the card's own dark interior --
 * only the page AROUND the card changed. Headline copy is copied verbatim
 * from the real hero (src/app/page.tsx), so the comparison stays "would
 * this replace the hero visual" rather than an abstract demo.
 */

const ParticleHeroBackground = dynamic(
  () => import("@/components/particle-hero-background").then((m) => m.ParticleHeroBackground),
  { ssr: false },
);

/**
 * The scroll-driven sequence (2026-09-17) -- a SEPARATE component tree from
 * the Sun/Galaxy one above, deliberately: see scroll-hero-sphere.tsx's own
 * module docstring for why the existing, verified
 * particle-hero-background.tsx was built alongside rather than rewritten.
 * Both are rendered on this page so the original stays demonstrably
 * working next to the new one.
 */
const HeroScrollSequence = dynamic(
  () => import("@/components/hero-sphere/hero-scroll-sequence").then((m) => m.HeroScrollSequence),
  { ssr: false },
);

const VARIANTS: { value: ParticleHeroVariant; label: string }[] = [
  { value: "sun", label: "Sun" },
  { value: "galaxy", label: "Galaxy Nebula" },
];


export default function ParticlePreviewPage() {
  const [variant, setVariant] = useState<ParticleHeroVariant>("sun");

  return (
    // bg-white, matching globals.css's real --background: 0 0% 100% --
    // this is the page background now, not the particle effect's. See the
    // component docstring above for why the dark field moved to a bounded
    // card instead of disappearing.
    <main className="min-h-screen bg-white">
      {/* Toggle bar -- fixed on top so it's reachable without scrolling past
          the hero card below it. Kept dark/semi-transparent regardless of
          page colour underneath -- it's dev-tool chrome floating above
          everything, not part of the page being previewed, and reads fine
          over either white or the hero card's own dark interior. */}
      <div className="fixed inset-x-0 top-0 z-50 flex items-center justify-center gap-2 border-b border-slate-200 bg-black/70 py-3 backdrop-blur-sm">
        <span className="mr-2 text-xs font-medium uppercase tracking-widest text-white/50">
          Preview
        </span>
        {VARIANTS.map((v) => (
          <button
            key={v.value}
            type="button"
            onClick={() => setVariant(v.value)}
            className={cn(
              "rounded-lg px-3.5 py-1.5 text-sm font-medium transition-colors",
              variant === v.value
                ? "bg-white text-black"
                : "text-white/70 hover:bg-white/10 hover:text-white",
            )}
          >
            {v.label}
          </button>
        ))}
      </div>

      {/* White space above, clearing the fixed toggle bar. */}
      <div className="pt-24" />

      {/* ---------- STAGES 1-2: white-background scroll sphere ----------
          The new pink/purple/blue sphere on the page's own white, with no
          dark card around it -- this is the one the scroll sequence will
          drive. Full-bleed white, no rounded container, because the whole
          point is that it sits ON the page rather than in a panel.

          The slider drives the SAME normalised progress value scroll will
          drive in Stage 3, which is the point of building the dolly
          scroll-agnostic: the camera move can be verified end to end here
          without any scroll machinery existing yet. */}
      <section className="mx-auto max-w-6xl px-6">
        <p className="text-xs font-medium uppercase tracking-widest text-slate-400">
          Scroll sphere · scroll down to dolly in
        </p>
      </section>

      {/* Stage 3: the real scroll-driven sequence. The Stage 2 debug slider
          is gone -- scroll is the progress source now. The dolly itself is
          unchanged and still scroll-agnostic; only what feeds it changed. */}
      <HeroScrollSequence />

      <div className="pt-24" />

      {/* Hero mockup, now a BOUNDED card (2026-09-17) rather than a
          full-viewport dark section -- same copy, structure and relative
          typography as the real page.tsx hero, still recoloured for the
          card's own dark interior. rounded-[1.35rem] matches
          knowledge-graph.tsx's own card radius, the closest real precedent
          on this site for "a dark, contained visual panel inside an
          otherwise-light page." */}
      <div className="mx-auto max-w-6xl px-6">
        <section className="relative flex h-[720px] items-center justify-center overflow-hidden rounded-[1.35rem]">
          <div className="absolute inset-0" key={variant}>
            <ParticleHeroBackground variant={variant} />
          </div>

          {/* Readability scrim (2026-09-16, added after the first screenshot
              showed the sun/galaxy's bright core sitting directly behind the
              headline and genuinely hurting legibility, even after the bloom
              re-tune below). Same PRINCIPLE the real hero already applies to
              its own background layers (opacity + mask-image fades on
              LightLines/AsciiHeroBackground) -- darken selectively where text
              sits, leave the rest of the effect at full intensity -- just
              shaped as a radial wash centred on the text column instead of a
              top-to-bottom fade, since the particle subject sits centred
              behind the text rather than above it. pointer-events-none and
              purely decorative, so it never affects hit-testing on the
              buttons above it. */}
          <div
            aria-hidden
            className="pointer-events-none absolute inset-0"
            style={{
              background:
                "radial-gradient(ellipse 650px 480px at 50% 48%, rgba(3,1,5,0.55) 0%, rgba(3,1,5,0.25) 55%, transparent 80%)",
            }}
          />

          <div className="relative mx-auto max-w-3xl px-6 text-center">
            <div className="mb-6 inline-flex items-center gap-2 rounded-full border border-white/15 bg-white/5 px-3.5 py-1.5 text-xs font-medium text-white/70 backdrop-blur-sm">
              <Sparkles className="h-3.5 w-3.5 text-indigo-300" />
              Live from a real, continuously-growing knowledge base
            </div>
            <h1 className="text-balance text-4xl font-semibold leading-[1.08] tracking-tight text-white sm:text-6xl">
              Mycelium turns scattered saved content into a{" "}
              <span className="bg-gradient-to-r from-indigo-300 via-violet-300 to-orange-300 bg-clip-text text-transparent">
                self-organizing, self-improving
              </span>{" "}
              knowledge network.
            </h1>
            <p className="mx-auto mt-6 max-w-2xl text-balance text-lg leading-relaxed text-white/70">
              Everything you save gets read, understood, categorized and connected —
              automatically. What comes out is not a folder of links. It is a map that
              knows what it contains, and tells you what to do next.
            </p>
            <div className="mt-9 flex flex-wrap items-center justify-center gap-3">
              <Button size="lg" className="bg-white text-black hover:bg-white/90">
                See how it works
                <ArrowRight className="h-4 w-4" />
              </Button>
              <Button size="lg" variant="outline" className="border-white/25 text-white hover:bg-white/10">
                Browse the library
              </Button>
            </div>
          </div>
        </section>
      </div>

      {/* White space below the card too -- proves the white background
          extends on both sides of the card, not just above it. */}
      <div className="pb-24" />
    </main>
  );
}
