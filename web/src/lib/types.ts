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

export type ScoutItem = Reel & { suggested_action: string };
