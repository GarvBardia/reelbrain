import type { GraphPayload, ReelPage, ScoutItem, Stats } from "./types";

/** The Render-hosted FastAPI. NEXT_PUBLIC_ so client components (the graph's
 *  expand-on-click) can call it too -- the base URL is not a secret, and the
 *  endpoints behind it are read-only and redacted by design. */
export const API_BASE = (
  process.env.NEXT_PUBLIC_API_BASE || "http://127.0.0.1:8000"
).replace(/\/$/, "");

/**
 * Every public page is server-rendered with ISR rather than fetched in the
 * browser, for three reasons that all point the same way:
 *   - the backend is a Render FREE instance that cold-starts after 15 minutes
 *     idle, so a client-side fetch would show a spinner for ~30s on the first
 *     visit of the day; a cached server render serves instantly,
 *   - it keeps the API off the critical path for SEO/first paint,
 *   - `revalidate` caps how hard Vercel can hit a free-tier backend no matter
 *     how much traffic the page gets.
 * 300s matches PUBLIC_CACHE_TTL_SECONDS on the API so the two layers do not
 * fight each other.
 */
const REVALIDATE_SECONDS = 300;

async function getJSON<T>(path: string, fallback: T): Promise<T> {
  try {
    const res = await fetch(`${API_BASE}${path}`, {
      next: { revalidate: REVALIDATE_SECONDS },
    });
    if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
    return (await res.json()) as T;
  } catch (err) {
    // A marketing page that renders with empty/zero state beats one that
    // 500s because a free-tier backend was briefly asleep. Every consumer
    // below is written to handle the empty shape.
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
