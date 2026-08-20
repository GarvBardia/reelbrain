import type { GraphPayload, ReelPage, ScoutItem, Stats } from "./types";

/**
 * The Render-hosted FastAPI. Baked in at BUILD TIME (see
 * .github/workflows/deploy-pages.yml) since GitHub Pages serves static files
 * with no server to hold a runtime env var -- Next.js inlines any
 * NEXT_PUBLIC_ value into the JS bundle at build time regardless of hosting
 * target, so this has always been "public" in practice; GitHub Pages just
 * makes that explicit instead of implicit.
 */
// The default is the PRODUCTION backend, not localhost. This is deliberate:
// on a static export the value is baked in at build time, and if the deploy
// workflow ever dropped NEXT_PUBLIC_API_BASE, a localhost default would ship a
// site that silently fetches 127.0.0.1 and shows empty state to every visitor.
// Falling back to the real API means the worst case is "still works". Local
// development against a local backend sets NEXT_PUBLIC_API_BASE=http://127.0.0.1:8000
// in .env.local, which overrides this.
export const API_BASE = (
  process.env.NEXT_PUBLIC_API_BASE || "https://reelbrain.onrender.com"
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
/**
 * Render's free tier sleeps after ~15 minutes idle and takes ~30s to wake.
 * The browser's own fetch timeout is minutes long, so without this the page
 * would sit on a skeleton with no explanation. 45s is comfortably past a
 * normal cold start but short enough to fail visibly rather than hang.
 */
const FETCH_TIMEOUT_MS = 45_000;

/** Thrown so callers can tell "the request failed" apart from "the request
 *  succeeded and the answer was legitimately empty". Swallowing that
 *  distinction is what made the landing-page graph silently vanish: a failed
 *  fetch returned an empty node list, which renders as a blank canvas that
 *  looks identical to a healthy-but-empty graph. */
export class ApiError extends Error {
  constructor(message: string, readonly cause?: unknown) {
    super(message);
    this.name = "ApiError";
  }
}

async function getJSON<T>(path: string): Promise<T> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), FETCH_TIMEOUT_MS);
  try {
    const res = await fetch(`${API_BASE}${path}`, { signal: controller.signal });
    if (!res.ok) throw new ApiError(`${res.status} ${res.statusText}`);
    return (await res.json()) as T;
  } catch (err) {
    console.error(`[mycelium] GET ${path} failed:`, err);
    if (err instanceof ApiError) throw err;
    if (err instanceof DOMException && err.name === "AbortError") {
      throw new ApiError(
        "The API did not respond in time. It runs on a free tier that sleeps when idle — this usually clears on a retry.",
        err,
      );
    }
    // A TypeError here is fetch's opaque network/CORS failure. The browser
    // deliberately withholds the detail, so the message says what the reader
    // can actually act on rather than guessing at a cause.
    throw new ApiError("Could not reach the API. Check your connection and try again.", err);
  } finally {
    clearTimeout(timer);
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
  return getJSON<Stats>("/api/public/stats");
}

export function getGraph(expand?: string) {
  const qs = expand ? `?expand=${encodeURIComponent(expand)}` : "";
  return getJSON<GraphPayload>(`/api/public/graph${qs}`);
}

export function getScoutQueue(limit = 25) {
  return getJSON<{ items: ScoutItem[]; total_reels: number }>(
    `/api/public/scout-queue?limit=${limit}`,
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
  return getJSON<ReelPage>(`/api/public/reels?${qs}`);
}
