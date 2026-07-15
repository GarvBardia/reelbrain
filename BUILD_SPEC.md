# BUILD_SPEC.md — ReelBrain

## Phase 1 — MVP capture pipeline (weekend scope)

### 1.1 `POST /capture`
- Body: `{ "url": str, "note": str|null, "secret": str }`
- Validate secret. Normalize URL → extract shortcode (`/reel/{shortcode}/`, `/p/{shortcode}/`, share-URL variants with query params stripped).
- Dedupe: if shortcode exists in SQLite → return existing Notion page URL, HTTP 200 with `{"status": "duplicate"}` — do not re-process.
- Insert row with status `processing`, enqueue pipeline (FastAPI `BackgroundTasks` is fine — no Celery).
- Respond immediately (`202`) so the iOS Shortcut never times out.

### 1.2 `fetcher.py` (FREE-FIRST)
- `fetch_reel(shortcode) -> ReelData{video_path, caption, creator_username, creator_fullname, taken_at, like_count, permalink}`
- **Primary: yt-dlp.** Try logged-out first; if blocked, retry once with `BURNER_COOKIES_FILE` (burner account only — see CLAUDE.md burner rules). Extract caption/creator from yt-dlp metadata.
- Rate discipline: max 25 fetches/day, ≥20s spacing, exponential backoff on 429/challenge. Persist a daily counter in SQLite.
- **Fallback (config-gated, off by default): paid data-API provider** behind a thin interface (`DATA_API_PROVIDER`) — enable later only if yt-dlp breaks for >48h. Download any CDN `video_url` immediately (links expire).
- On total failure: return partial ReelData (permalink only) and raise `FetchDegraded` — pipeline continues in degraded mode; status message should say whether cookies need refreshing.

### 1.3 + 1.4 `gemini_pipe.py` — ONE call, transcript + extraction together
- ffmpeg: strip audio → 16kHz mono m4a (reels ≤90s → ~200–400KB upload).
- Single Gemini 2.x Flash free-tier call: inline audio bytes + caption + creator + user note + **current tag taxonomy** (top ~40 tags from SQLite as candidates).
- **`response_schema` enforced** — the DATA_SCHEMA §3 JSON extended with `"transcript": "string — verbatim transcription, empty if no speech"` and `"has_speech": bool`. Structured output mode means no markdown fences, no parsing slop.
- Pydantic-validate. On failure: ONE retry with validation errors appended; then degrade to `{main_point: caption[:200], content_type: "unknown", has_speech: null}` and status `⚠️ Failed — retry`.
- **Anti-slop rules in the prompt:** quotes verbatim-only from the transcript; if `has_speech` is false, main_point comes from caption only and supporting_points stays empty; never pad lists to fill the schema; value_score 1 for pure music/aesthetic reels.
- Comment-gate detection: regex pre-check on caption (`comment\s+["']?([A-Z]{2,12})["']?`) merged with the model's `comment_gate` field — either positive → gated.
- Rate handling: free tier gives ~10–15 RPM — a per-process semaphore(2) is plenty at <20/day.

### 1.5 `notion_writer.py`
- Create page in the Saves DB (schema: DATA_SCHEMA.md §1). Page body: main point (callout) → supporting points → steps → resources (bookmarks) → quotes → transcript in a toggle.
- Status: `📥 Inbox` normally, `⏳ Awaiting DM` if gated, `⚠️ Failed — retry` if degraded.
- Store returned Notion page_id in SQLite.

### 1.6 `POST /retry/{shortcode}` — re-runs pipeline for failed rows.

**Phase 1 acceptance:** paste 3 real reel URLs (1 comment-gated) → 3 correct Notion pages in <90s each; pasting a duplicate returns the existing page; killing the data API mid-run still yields a Failed-status page.

## Phase 2 — Capture UX + comment-gate loop

### 2.1 iOS Shortcut (documented in README, not code)
Share Sheet input (URL or IG share text) → extract URL → `POST /capture` with secret → show result notification. Include the exact Shortcut recipe steps in README.md.

### 2.2 Comment-gate assist
- If gated: response/notification includes the keyword; Shortcut puts keyword on clipboard and offers "Open reel" (permalink deep link).
- `POST /attach` `{ "shortcode_or_note": str, "resource_url": str, "secret": str }` — user shares the DM'd link back; attaches URL to the pending entry's Resources, flips status `⏳ Awaiting DM → 📥 Inbox`. Match by most-recent `Awaiting DM` entry if shortcode omitted.
- Second Shortcut: "Attach to ReelBrain" for sharing the DM link.

### 2.3 Nightly job
- Rows stuck `processing` > 1h → mark failed. `Awaiting DM` > 7 days → status `🕳 Gate expired`.

## Phase 3 — Intelligence layer

### 3.1 Embeddings (`store.py`)
- After extraction: embed `main_point + "\n" + joined(supporting_points)` via **Gemini embedding free tier (768-dim)**. Store in sqlite-vec keyed by shortcode. (Free-tier embed limits are far above our volume; on quota error just skip — embeddings are enhancement, not critical path.)
- Near-dup: top-1 cosine > 0.92 → set `Related` relation on the Notion page + tag `near-duplicate`, still save.
- Related saves: top-3 neighbors (>0.75) → Notion `Related` relation.

### 3.2 Tag convergence
- `GET taxonomy` from SQLite tag frequency; inject top-40 into extraction prompt as preferred candidates (already wired in 1.4 — this phase makes it dynamic).

### 3.3 Value filter + creator stats
- `value_score <= 2` → status `🗑 Low signal` instead of Inbox.
- Creator save-count ≥ 5 → set `Core source` checkbox on the Creators DB row.

## Phase 4 (later, optional)
- Convert IG account to Creator; Meta app + Messaging API webhook `POST /ig-webhook` to auto-capture gate DMs (replaces manual `/attach`). Requires Meta app review — see OPEN_QUESTIONS.md.
- Weekly digest: cron → Claude summarizes week's saves → Notion page or email.

## Testing
- Unit: URL normalization (6 URL shapes), gate regex, schema validation, dedupe.
- Integration: mocked fetcher fixture (3 canned reels) → full pipeline → assert Notion payload shape (mock Notion client).
- One live smoke-test script `scripts/smoke.py URL` hitting real APIs.
