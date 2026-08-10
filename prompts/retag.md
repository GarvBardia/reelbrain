You are re-tagging an item that was already extracted for a personal knowledge base. You are NOT re-extracting anything — the summary below is final and correct. Your only job is to choose better `topic_tags` for it.

## Why this pass exists

The taxonomy collapsed: most items ended up with a unique, one-off tag combination instead of sharing a small set of reusable categories. This pass repairs that by re-picking tags for one item at a time against the current shared taxonomy.

## Rules (follow strictly)

- `topic_tags`: 3-6 lowercase kebab-case SUBJECT-MATTER CATEGORIES.
- Never use a person's name, an assistant/agent's given name, or a specific product/tool/company name as a tag — those are named entities, not topics. Bad topic_tags: "zoey", "jarvis", "elu".
- Strongly prefer the taxonomy candidates below whenever they genuinely fit — reuse a candidate's exact spelling rather than inventing a near-variant (e.g. reuse "startups", not "startup"; reuse "mcp-servers", not "mcp-server").
- Only introduce a brand-new tag if truly nothing in the candidate list fits this item's actual subject matter. Do not invent a new tag just because none of the candidates is a perfect match — pick the closest genuine fit instead.

## Content already extracted for this item

- Main point: {main_point}
- Supporting points: {supporting_points}
- Named entities: {named_entities}
- Content type: {content_type}

## Taxonomy candidates (most-used in this user's existing taxonomy)

{taxonomy}

## Output

Return only the structured JSON matching the enforced response schema. No markdown, no commentary, no code fences.
