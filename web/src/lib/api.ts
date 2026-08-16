import type { GraphPayload, ReelPage, ScoutItem, Stats } from "./types";

/**
 * The Render-hosted FastAPI. Baked in at BUILD TIME (see
 * .github/workflows/deploy-pages.yml) since GitHub Pages serves static files
 * with no server to hold a runtime env var -- Next.js inlines any
 * NEXT_PUBLIC_ value into the JS bundle at build time regardless of hosting
 * target, so this has always been "public" in practice; GitHub Pages just
 * makes that explicit instead of implicit.
 */
export const API_BASE = (
  process.env.NEXT_PUBLIC_API_BASE || "http://127.0.0.1:8000"
).replace(/\/$/, "");

/**
 * Every data-driven page in this app is a CLIENT component fetching this
 * module directly (see the `"use client"` + useEffect pattern in
 * src/app/page.tsx and friends). That is not a stylistic choice: GitHub
 * Pages has no server, so there is no request to run a Server Component
 * against and no ISR to revalidate a cached render. Fetching from the
 * browser is the only way these pages can show live data at all -- and as a
 * side effect, the numbers are genuinely live on every visit, not "fresh as
 * of the last deploy."
 */
async function getJSON<T>(path: string, fallback: T): Promise<T> {
  try {
    const res = await fetch(`${API_BASE}${path}`);
    if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
    return (await res.json()) as T;
  } catch (err) {
    // A marketing page that renders with empty/zero state beats one that
    // throws because a free-tier backend was briefly asleep. Every consumer
    // is written to handle the empty shape (see the `EMPTY_*` constants).
    console.error(`[mycelium] GET ${path} failed:`, err);
    return fallback;
  }
}

export const EMPTY_STATS: Stats = {
  total_reels: 0,
  total_categories: 0,
  total_topics: 0,
  total_entities: 0,
  actionable_items: 0,
  high_priority: 0,
  top_categories: [],
  top_topics: [],
  top_entities: [],
};

export const EMPTY_GRAPH: GraphPayload = {
  nodes: [],
  links: [],
  level: "category",
  expanded: [],
  categories: [],
  total_reels: 0,
};

export function getStats() {
  return getJSON<Stats>("/api/public/stats", EMPTY_STATS);
}

export function getGraph() {
  return getJSON<GraphPayload>("/api/public/graph", EMPTY_GRAPH);
}

export function getScoutQueue(limit = 25) {
  return getJSON<{ items: ScoutItem[]; total_reels: number }>(
    `/api/public/scout-queue?limit=${limit}`,
    { items: [], total_reels: 0 },
  );
}

export function getReels(params: {
  q?: string;
  category?: string;
  page?: number;
  min_value?: number;
}) {
  const qs = new URLSearchParams();
  if (params.q) qs.set("q", params.q);
  if (params.category) qs.set("category", params.category);
  if (params.min_value && params.min_value > 1) qs.set("min_value", String(params.min_value));
  qs.set("page", String(params.page ?? 1));
  qs.set("page_size", "24");
  return getJSON<ReelPage>(`/api/public/reels?${qs}`, {
    items: [],
    total: 0,
    page: 1,
    page_size: 24,
    total_pages: 1,
  });
}
