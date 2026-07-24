# TAXONOMY_PROPOSAL.md — topic cleanup (Vault Librarian, Phase 6)

**Status: PROPOSAL ONLY — nothing has been applied.** Generated 2026-07-24 from
the live Notion Saves DB: 113 distinct topics across 129 rows, 71 of them used
exactly once. Approve (or edit) this file, then say so — the apply step will
merge tags in Notion, restructure Obsidian topic notes under the parent
categories, re-sync, and verify pre/post row counts match.

`near-duplicate` (12 rows) is an automation tag, not a topic — excluded from
all merges below and kept as-is.

## 1. Parent categories (12)

| Parent | Absorbs (child tags stay as tags; parent groups them in Obsidian) |
|---|---|
| **claude-ecosystem** | claude-ai, claude, claude-code, claude-mcp, claude-skills, mcp-servers, fable-5, agentic-os, anthropic-adjacent tags |
| **ai-tools** | ai-tools, ai-plugins, gemini-ai, midjourney, luma-dream-machine, arcads-ai, emergent-ai, notebooklm, local-ai |
| **ai-agents-automation** | ai-agents, automation, ai-automation, workflow-automation, business-automation, agentic-ai, ai-workflows |
| **developer-tools** | developer-tools, open-source, github-repositories, software-development, software-engineering, ai-coding, vibe-coding, free-tier, privacy-first, cybersecurity |
| **web-and-design** | web-design, web-development, ui-design, product-design, interactive-design, portfolio-design, 3d-design, scroll-animation, no-code, design-systems |
| **content-and-media** | content-creation, ai-video, ai-animation, video-editing, motion-graphics, image-generation, generative-ai, 3d-motion-design, animation-workflow, ai-generation, content-strategy |
| **sales-and-leads** | lead-generation, cold-outreach, cold-calling, email-outreach, sales-automation, sales-pipeline, sales-strategies, outreach-automation, client-acquisition, crm |
| **business-building** | startups, entrepreneurship, solopreneurship, business-launch, saas-ideas, saas-growth, saas-builder, app-ideas, funding, yc, investment, finance |
| **marketing-and-brand** | personal-branding, social-media-growth, social-media-strategy, instagram, meta-ads, ai-marketing, digital-advertising, seo-optimization, creator-economy, agency-growth |
| **productivity-knowledge** | productivity-hacks, prompt-engineering, ai-prompts, obsidian, second-brain, life-hacks, ai-tutorials, creative-framework, ai-research |
| **career** | career-advice, career-growth, job-hunting, interview-prep, resource-sharing |
| **income-and-products** | passive-income, digital-products, side-hustle, airbnb, property-management, cryptocurrency |

Unplaced one-offs to keep as loose tags (too specific/rare to force):
looksmaxxing, style-ai, gamification, wildlife-conservation, tech-news,
mobile-development, react-native, app-development.

## 2. Outright dedup/merge list (same concept, different spellings)

These are true duplicates — the left tag is REPLACED by the right tag on every
row (this is the part that actually edits Notion):

| Merge away | Into | Rows affected |
|---|---|---|
| claude | claude-ai | 1 |
| claude-code | claude-ai | 1 |
| claude-mcp | claude-ai | 1 |
| ai-workflow | ai-workflows | 1 |
| ai-automation | automation | 3 |
| workflow-automation | automation | 2 |
| business-automation | automation | 1 |
| artificial-intelligence | ai-tools | 7 |
| ai-generation | generative-ai | 1 |
| 3d-motion-design | motion-graphics | 1 |
| animation-workflow | ai-animation | 1 |
| software-development | developer-tools | 1 |
| software-engineering | developer-tools | 1 |
| github-repositories | open-source | 1 |
| social-media-strategy | social-media-growth | 1 |
| sales-strategies | sales-automation | 1 |
| outreach-automation | cold-outreach | 1 |
| career-advice | career-growth | 2 |
| saas-builder | saas-ideas | 1 |
| saas-growth | saas-ideas | 1 |

Net effect: 113 → ~93 tags, then the 12 parents give the Obsidian index its
top-level structure. Row count must be identical pre/post (merges edit tags,
never rows).

## 3. Impact check — compute_priority's CLAUDE substring matching

`compute_priority` marks a row High when ANY topic tag contains one of
`["claude", "claude-ai", "claude-code", "anthropic", "claude-skills", "mcp"]`
as a case-insensitive SUBSTRING. Consequences audited per merge:

- **Safe**: `claude`/`claude-code`/`claude-mcp` → `claude-ai` — target still
  contains "claude"; priority unchanged on all 3 rows.
- **DANGER — priority would silently drop**: merging `mcp-servers` into
  anything without "mcp"/"claude" in it (e.g. `developer-tools`) would demote
  those rows from High. **This proposal therefore does NOT merge
  `mcp-servers`** — it stays its own tag under the claude-ecosystem parent.
- **Deliberate non-change**: `fable-5` and `agentic-os` do NOT currently match
  any CLAUDE_KEYWORD (so those rows' High priority, if any, comes from
  value_score). Merging them into `claude-ai` would silently PROMOTE them to
  High — so they are grouped under the parent but NOT tag-merged.
- The 20 merges in §2 were checked pairwise: none adds or removes a
  CLAUDE_KEYWORD substring on any row.

## 4. Impact check — Obsidian topic pages

- `app/obsidian_sync.py` regenerates `topics/{slug}.md` from live tags, so
  merged-away tags stop being generated; their old topic notes become
  orphans. Apply step must DELETE the orphaned topic notes **after checking
  each for user-written content above/below the AUTO block** (the sync's
  upsert_auto_block deliberately preserves user notes — deleting without
  checking would destroy them).
- Parent categories are an Obsidian-index restructure (`_index.md` grouping
  topics under the 12 parents), not new Notion tags — Notion keeps the flat
  multi-select.
- Reel-note frontmatter `topics:` wikilinks self-heal on the next sync.

## 5. Apply plan (NOT executed — awaiting approval)

1. Snapshot: export current tag→rows mapping to a JSON for rollback.
2. For each §2 merge: Notion pages.update per affected row (remove old tag,
   add target if absent). Verify per-row tag counts.
3. Verify total row count and per-row Priority values unchanged (except none
   expected — §3 says zero priority changes).
4. Restructure Obsidian `_index.md` under the 12 parents; delete orphaned
   topic notes after the user-content check; full re-sync.
5. Mocked tests for the transform logic (merge mapping, priority-invariance
   assertion) land with the apply commit.
