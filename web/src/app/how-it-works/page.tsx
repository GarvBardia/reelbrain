import type { Metadata } from "next";

import { HowItWorksClient } from "./how-it-works-client";

// A plain Server Component wrapper purely so `metadata` can be exported --
// static export still prerenders this shell's <title>/<meta> tags at build
// time; the data-fetching child below is the client component. Next.js
// requires metadata exports to come from a Server Component, so the split
// is structural, not stylistic.
export const metadata: Metadata = {
  title: "How it works",
  description:
    "Capture, Extract, Organize, Suggest — the four stages that turn a saved link into part of a knowledge network.",
};

export default function HowItWorksPage() {
  return <HowItWorksClient />;
}
