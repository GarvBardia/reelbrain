# DATA_SCHEMA.md — ReelBrain

## 1. Notion schema (recommended)

### DB: `📼 Saves` (main database)
| Property | Type | Notes |
|---|---|---|
| Title | title | main_point (truncated 100 chars) |
| Status | select | 📥 Inbox · ⏳ Awaiting DM · ✅ Processed/Reviewed · ⚠️ Failed — retry · 🗑 Low signal · 🕳 Gate expired |
| Content type | select | tutorial / insight / resource_drop / motivation / news / entertainment / unknown |
| Topics | multi-select | tags from extraction (converging taxonomy) |
| Creator | relation → Creators | |
| Reel URL | url | permalink |
| Saved at | created_time | |
| Posted at | date | taken_at from metadata |
| Value score | select | 1–5 |
| Comment gate | checkbox | |
| Gate keyword | rich_text | |
| Gate resource | url | attached DM link |
| Related | relation → Saves (self) | filled by embedding neighbors |
| My note | rich_text | note passed at capture time |
| Shortcode | rich_text | join key with SQLite — do not edit |

**Page body layout:** callout (main point) → bulleted supporting points → numbered steps (if any) → bookmark blocks for resources → quote blocks → toggle("Transcript") → toggle("Raw caption").

**Views:** Inbox (Status=Inbox, sort Saved at desc) · Awaiting DM · By Topic (group Topics) · By Creator · High value (score≥4) · Low signal.

### DB: `🎤 Creators`
Username (title) · Full name · Save count (rollup) · Core source (checkbox) · Profile URL · Primary topics (rollup of Topics).

## 2. Obsidian alternative (for comparison — not building unless chosen)

```
vault/
  reels/2026/07/{shortcode}-{slug}.md
  creators/{username}.md
  tags via frontmatter, MOC notes per topic
```

Frontmatter per reel note:
```yaml
---
shortcode: C9xAbC
creator: "[[creators/hormozi]]"
url: https://instagram.com/reel/C9xAbC/
saved: 2026-07-13
type: tutorial
topics: [ai-workflows, automation]
value: 4
gate: {detected: true, keyword: SEND, resource: https://...}
status: inbox
---
```
Body mirrors the Notion layout; related saves as `[[wikilinks]]` appended by the backend. **Requirement:** vault must live in a folder the server can write to (Git repo pushed by backend, or server-side folder synced via Syncthing/iCloud). This sync requirement is the main reason Notion was recommended.

## 3. Extraction JSON schema (Claude output, pydantic-validated)

```json
{
  "transcript": "string — verbatim transcription of spoken audio, empty string if none",
  "has_speech": true,
  "main_point": "string, one sentence, <=200 chars",
  "supporting_points": ["string", "... 0-6 items"],
  "resources_mentioned": [
    {"name": "string", "type": "tool|book|site|person|course|other", "url_if_stated": "string|null"}
  ],
  "steps_or_framework": ["string — empty unless the reel teaches a procedure"],
  "quotable_lines": ["string — verbatim from transcript only, 0-3 items"],
  "topic_tags": ["string — 3-6, lowercase-kebab, prefer provided taxonomy candidates"],
  "content_type": "tutorial|insight|resource_drop|motivation|news|entertainment",
  "comment_gate": {
    "detected": false,
    "keyword": "string|null",
    "promised_resource": "string|null"
  },
  "value_score": 3,
  "language": "en"
}
```

## 4. SQLite schema (the brain)

```sql
CREATE TABLE saves (
  shortcode TEXT PRIMARY KEY,
  permalink TEXT NOT NULL,
  creator TEXT,
  caption TEXT,
  transcript TEXT,
  extraction_json TEXT,          -- full validated JSON
  notion_page_id TEXT,
  status TEXT NOT NULL,          -- processing|done|awaiting_dm|failed|low_signal|gate_expired
  gate_keyword TEXT,
  gate_resource_url TEXT,
  created_at TEXT DEFAULT (datetime('now')),
  updated_at TEXT
);

CREATE TABLE tags (tag TEXT, shortcode TEXT, PRIMARY KEY (tag, shortcode));

-- sqlite-vec virtual table
CREATE VIRTUAL TABLE save_vec USING vec0(
  shortcode TEXT PRIMARY KEY,
  embedding FLOAT[768]           -- Gemini embedding free tier
);
```

Taxonomy query for prompt injection: `SELECT tag, COUNT(*) c FROM tags GROUP BY tag ORDER BY c DESC LIMIT 40;`
