"use client";

import { useRef } from "react";
import { motion, useInView } from "framer-motion";
import { Download, ScanText, Network, Lightbulb } from "lucide-react";

const STAGES = [
  {
    n: "01",
    title: "Capture",
    icon: Download,
    color: "#FF5A1F",
    body: "Share a reel from your phone and you are done. No tagging, no choosing a folder, no deciding where it belongs. The network takes it from there.",
    detail: "One tap from the share sheet.",
  },
  {
    n: "02",
    title: "Extract",
    icon: ScanText,
    color: "#7C3AED",
    body: "The audio is transcribed and the content read end to end — the actual point, the steps, the tools named, and one plain-language explanation written for someone who has never heard of any of them.",
    detail: "Transcript, main point, entities, plain summary.",
  },
  {
    n: "03",
    title: "Organize",
    icon: Network,
    color: "#2563EB",
    body: "Each item is filed against the vocabulary that already exists, then matched against everything saved before it. Near-duplicates are flagged, related items are linked, and the map redraws itself.",
    detail: "Shared taxonomy, similarity links, duplicate detection.",
  },
  {
    n: "04",
    title: "Suggest",
    icon: Lightbulb,
    color: "#059669",
    body: "Every item ends with one concrete next step. The highest-value ones rise into a queue, so what you get back is not a reading list — it is a short, ranked set of things worth doing.",
    detail: "One action per item, ranked into a queue.",
  },
];

export function PipelineStages({
  totalReels,
  totalTopics,
  actionable,
}: {
  totalReels: number;
  totalTopics: number;
  actionable: number;
}) {
  const ref = useRef(null);
  const inView = useInView(ref, { once: true, margin: "-100px" });

  const counters = [totalReels, totalReels, totalTopics, actionable];

  return (
    <section ref={ref} className="mx-auto max-w-4xl px-6 py-12">
      <div className="relative">
        {/* The hypha: one continuous strand that grows down through all four
            stages as they come into view. This is the mycelium metaphor doing
            actual work -- it visually asserts that the stages are one
            connected organism rather than four separate boxes. */}
        <motion.div
          className="absolute left-[27px] top-4 w-px origin-top bg-gradient-to-b from-orange-400 via-violet-500 to-emerald-500 md:left-[31px]"
          initial={{ scaleY: 0 }}
          animate={inView ? { scaleY: 1 } : {}}
          transition={{ duration: 1.6, ease: "easeInOut" }}
          style={{ height: "calc(100% - 2rem)" }}
        />

        <ol className="space-y-12">
          {STAGES.map((stage, i) => (
            <motion.li
              key={stage.n}
              className="relative flex gap-6 md:gap-8"
              initial={{ opacity: 0, x: -14 }}
              animate={inView ? { opacity: 1, x: 0 } : {}}
              transition={{ duration: 0.5, delay: 0.28 * i + 0.15 }}
            >
              {/* Node on the strand. */}
              <div className="relative z-10 shrink-0">
                <motion.div
                  className="flex h-14 w-14 items-center justify-center rounded-2xl border-2 bg-white md:h-16 md:w-16"
                  style={{ borderColor: stage.color }}
                  initial={{ scale: 0.6 }}
                  animate={inView ? { scale: 1 } : {}}
                  transition={{
                    duration: 0.45,
                    delay: 0.28 * i + 0.15,
                    type: "spring",
                    stiffness: 220,
                  }}
                >
                  <stage.icon className="h-6 w-6 md:h-7 md:w-7" style={{ color: stage.color }} />
                </motion.div>
              </div>

              <div className="min-w-0 flex-1 pb-2 pt-1.5">
                <div className="flex items-baseline gap-3">
                  <span
                    className="text-xs font-semibold tabular-nums tracking-widest"
                    style={{ color: stage.color }}
                  >
                    {stage.n}
                  </span>
                  <h3 className="text-2xl font-semibold tracking-tight text-slate-900">
                    {stage.title}
                  </h3>
                </div>
                <p className="mt-3 max-w-xl leading-relaxed text-slate-600">{stage.body}</p>
                <p className="mt-3 text-sm text-slate-400">{stage.detail}</p>
                {counters[i] > 0 && (
                  <p className="mt-4 inline-flex items-center gap-2 rounded-lg bg-slate-50 px-3 py-1.5 text-sm text-slate-600">
                    <span
                      className="h-1.5 w-1.5 rounded-full"
                      style={{ backgroundColor: stage.color }}
                    />
                    <span className="font-medium tabular-nums text-slate-900">
                      {counters[i].toLocaleString()}
                    </span>
                    {["captured", "extracted", "topics mapped", "actions queued"][i]}
                  </p>
                )}
              </div>
            </motion.li>
          ))}
        </ol>
      </div>
    </section>
  );
}
