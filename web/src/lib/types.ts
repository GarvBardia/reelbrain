// Mirrors app/public_api.py's response shapes. Kept hand-written rather than
// generated: the API is small, and a generator would be one more build step
// to keep alive on Vercel for very little gain.

export type GraphNode = {
  id: string;
  label: string;
  type: "category" | "reel";
  category: string;
  color: string;
  val: number;
  count?: number;
  value_score?: number;
  shortcode?: string;
};

export type GraphLink = {
  source: string | GraphNode;
  target: string | GraphNode;
  value: number;
  type: "co-occurrence" | "membership";
};

export type CategoryInfo = {
  slug: string;
  label: string;
  color: string;
  count: number;
};

export type GraphPayload = {
  nodes: GraphNode[];
  links: GraphLink[];
  level: "category" | "expanded";
  expanded: string[];
  categories: CategoryInfo[];
  total_reels: number;
};

export type Stats = {
  total_reels: number;
  total_categories: number;
  total_topics: number;
  total_entities: number;
  actionable_items: number;
  high_priority: number;
  top_categories: CategoryInfo[];
  top_topics: { topic: string; count: number }[];
  top_entities: { entity: string; count: number }[];
};

export type Reel = {
  shortcode: string;
  title: string;
  plain_summary: string;
  suggested_action: string;
  topics: string[];
  category: string;
  category_label: string;
  color: string;
  value_score: number;
  priority: string;
  content_type: string;
  named_entities: string[];
  permalink: string;
  posted_at: string;
};

export type ReelPage = {
  items: Reel[];
  total: number;
  page: number;
  page_size: number;
  total_pages: number;
};

export type ResourceMentioned = {
  name: string;
  type: "tool" | "book" | "site" | "person" | "course" | "other";
  /** null for a resource named but not linked in the reel's own content. */
  url: string | null;
};

/**
 * The block-derived sections /reels doesn't carry -- see app/public_api.py's
 * load_reel_detail. A separate type (not fields bolted onto Reel) because
 * this is fetched separately too: GET /reels/{shortcode}/detail, lazily,
 * only when a visitor opens a reel's detail view. Every array here can
 * legitimately be empty (most reels have some but not all of these), which
 * the UI must treat as "omit the section", not "show it empty".
 */
export type ReelDetail = {
  shortcode: string;
  supporting_points: string[];
  steps_or_framework: string[];
  resources_mentioned: ResourceMentioned[];
  quotable_lines: string[];
};

export type ScoutItem = Reel & { suggested_action: string };
