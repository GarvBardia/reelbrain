# Mycelium

**Mycelium turns scattered saved content into a self-organizing, self-improving
knowledge network.**

Instagram Reel → Notion knowledge-base capture pipeline, ₹0/month, with a public
Next.js frontend. Share a reel from the iOS share sheet → backend fetches it
(yt-dlp + burner cookies), transcribes and extracts structured takeaways, and a
Notion page appears with the main point, steps, quotes, transcript, topic tags,
related-saves links, and comment-gate handling.

> **Naming.** The product is **Mycelium**; the Python package, this directory,
> the Render service and the SQLite file are still `reelbrain`. That is the
> internal codename, deliberately kept — renaming it would touch nearly every
> file for no user-visible gain. Every string a visitor can read says Mycelium.
> See `app/public_api.py`'s module docstring.

Design docs: `BUILD_SPEC.md`, `CLAUDE.md`, `DATA_SCHEMA.md`, `OPEN_QUESTIONS.md`.

- **Phase 1:** `/capture`, `/retry`, fetch → transcribe+extract → Notion page, all fail-soft.
- **Phase 2:** iOS Shortcuts, `/attach` (comment-gate loop), nightly cleanup (`/nightly` + script).
- **Phase 3:** embeddings (sqlite-vec) + near-dup detection + related-saves, low-signal
  filter, creator "Core source" flag.
- **Frontend:** `web/` — Next.js static export on GitHub Pages, reading the
  read-only `/api/public/*` endpoints directly from the browser. See
  `web/README.md` and `FRONTEND_DEPLOY.md`.
- **Ops:** `/health`, `/ping` keep-alive, rate limiting, `render.yaml` — see
  `DEPLOYMENT.md` (Render setup) and `SCHEDULING.md` (keep-alive + nightly cron).

## The public API

Read-only, rate-limited, and redacted by an allow-list (never a deny-list), so a
new private Notion property cannot leak by being forgotten. Comment-gate
keywords, attached resource URLs, raw transcripts and private notes never appear.

| Endpoint | Purpose |
|---|---|
| `GET /api/public/graph` | Force-graph nodes+links. Defaults to the ~13 **category** nodes; `?expand=<slug>` adds that category's reels |
| `GET /api/public/stats` | Live aggregate counters for the landing page |
| `GET /api/public/reels` | Paginated/filterable library (`q`, `category`, `min_value`, `page`) |
| `GET /api/public/scout-queue` | Redacted implementation queue |
| `GET /api/public/categories` | Colour/label legend |

Admin equivalents (`/api/admin/*`) require the `CAPTURE_SECRET` in an
`x-admin-secret` header and return the **unredacted** data.

## Quickstart

```bash
# 1. install (Python 3.12; ffmpeg must be on PATH)
python -m venv .venv && source .venv/bin/activate   # .venv\Scripts\activate on Windows
pip install -r requirements.txt

# 2. configure
cp .env.example .env    # then fill it in — see "Configure" below

# 3. create the Notion databases (one-time)
python scripts/setup_notion.py    # paste the two printed IDs into .env

# 4. run
uvicorn app.main:app --reload

# 5. capture a reel
curl -X POST http://localhost:8000/capture \
  -H "Content-Type: application/json" \
  -d '{"url": "https://www.instagram.com/reel/XXXXXXXX/", "note": null, "secret": "<CAPTURE_SECRET>"}'
```

Everything in `scripts/` works both as `python scripts/x.py` (preferred, used throughout
these docs) and `python -m scripts.x` — each script bootstraps `sys.path` itself.

**Models:** defaults are `gemini-2.5-flash` (extraction+transcription) and
`gemini-embedding-001` pinned to 768-dim output (embeddings) — both free-tier, both
overridable via `GEMINI_MODEL` / `GEMINI_EMBEDDING_MODEL`. These defaults exist because
the originally-specced models died in practice: see Troubleshooting.

**Notion API:** `app/notion_writer.py` and the setup scripts target the 2025-09-03+
"data source" API (databases wrap data sources; pages are created under a
`data_source_id`, never a `database_id`; new DBs use the `initial_data_source` wrapper).
notion-client is pinned accordingly in requirements.txt.

**Deploying:** point Render at the repo — `render.yaml` does the rest. Click-by-click
steps, env-var placement, burner-cookie handling, and the SQLite-ephemeral-disk
tradeoffs are all in `DEPLOYMENT.md`.

## Validate yt-dlp first (before anything else)

Confirm the burner account works at all (OPEN_QUESTIONS.md item 1):

```bash
yt-dlp --cookies cookies.txt --dump-json <a_real_reel_url>
```

Try 3 reels. If it works locally but fails after deploying, that's an IG datacenter-IP
block — see `BUILD_SPEC.md` §1.2 fallbacks.

## Configure

- **Gemini:** free API key at aistudio.google.com → `GEMINI_API_KEY`.
- **Notion:** internal integration at notion.so/my-integrations → `NOTION_TOKEN`. Share a
  parent page with the integration, put its ID in `NOTION_PARENT_PAGE_ID`, run the setup
  script, paste `NOTION_DB_ID` / `NOTION_CREATORS_DB_ID` back into `.env`.
- **Burner account:** export `cookies.txt` (cookies.txt browser extension) →
  `BURNER_COOKIES_FILE`. Set `BURNER_ACCOUNT_USERNAME` (burner) and `REAL_ACCOUNT_GUARD`
  (your REAL username) — the app refuses to fetch if they ever match.
- **Capture secret:** any string → `CAPTURE_SECRET`; the iOS Shortcut sends it back.

## Test

```bash
pytest
```

Fully mocked — no network, no API keys needed. Live end-to-end run (real APIs):

```bash
python scripts/smoke.py https://www.instagram.com/reel/XXXXXXXX/
```

To re-run a smoke test cleanly, delete the Notion page by hand, then
`python scripts/delete_row.py <shortcode>`.

## Bulk importing a list of reels

```bash
python scripts/bulk_import.py urls.txt
```

One reel URL per line in `urls.txt` (blank lines and `#` comments ignored). POSTs each
to your deployed `/capture` (`REELBRAIN_URL` in `.env`), spaced `MIN_FETCH_SPACING_SECONDS`
+ jitter apart. Progress is tracked in `bulk_import_progress.json` — safely re-runnable;
already-submitted URLs are skipped, errored ones are retried automatically next run.

**Read before a large import:** `/capture` responds instantly and does the actual fetch
in the background, so the server's `MAX_FETCHES_PER_DAY` cap has no distinct HTTP error —
a submission past the cap still gets a normal response and only fails invisibly later.
The script works around this by self-throttling to the same cap using its own progress
file and stopping cleanly with "daily cap reached — resume tomorrow." This only accounts
for submissions the script itself made — it can't see fetches from the iOS Shortcut or
manual `/retry` calls consuming the same daily budget. `--dry-run` lists what would be
submitted without sending anything.

## Troubleshooting — errors we actually hit, and their fixes

| Error | Cause | Fix |
|---|---|---|
| `ModuleNotFoundError: No module named 'app'` running a script | script run from outside repo root, or an old checkout without the sys.path bootstrap | run from repo root; both `python scripts/x.py` and `python -m scripts.x` work now |
| `FileNotFoundError: [WinError 2]` during extraction | ffmpeg not installed / not on PATH | install ffmpeg (`winget install ffmpeg` / `brew install ffmpeg`), reopen the shell |
| Gemini `429 RESOURCE_EXHAUSTED` immediately, on the very first call | `gemini-2.0-flash` no longer reliably on the free tier | default moved to `gemini-2.5-flash`; override via `GEMINI_MODEL` |
| Gemini `404 NOT_FOUND: models/text-embedding-004` | Google shut that model down Jan 14, 2026 | default moved to `gemini-embedding-001` with `output_dimensionality=768` (keeps the existing FLOAT[768] sqlite-vec schema) |
| Notion `body failed validation: body.properties should be defined` (or data_source errors) | Notion's 2025-09-03 API split databases into data sources | code migrated: pages are created under `data_source_id`, DBs created with `initial_data_source` — pinned notion-client handles it |
| `sqlite_vec: false` in `/health` | sqlite-vec extension failed to load in that environment | captures still work; embeddings/related-saves silently disabled — check install logs for the `sqlite-vec unavailable` warning |
| `cookies file not found at ... or ...` on every capture | no burner `cookies.txt` at `BURNER_COOKIES_FILE` or `/etc/secrets/cookies.txt` | upload it (Render: Environment → Secret Files, named `cookies.txt`) and restart; check `/health` → `cookies_file: true` |
| yt-dlp `No video formats found` / `empty media response ... use --cookies` — prod only | IG soft-blocking the datacenter IP, or a stale yt-dlp | bump yt-dlp first (see DEPLOYMENT.md); then refresh burner cookies. Rows still save caption-only via the OG-tag fallback |
| `cookie_health: "degraded"` in `/health`, or a `⚙️ System Alert` in Notion | 3+ consecutive cookie-backed auth failures — burner cookies have expired | see **COOKIES.md** — 2-minute refresh runbook |

## Cookie-health monitoring (no auto-refresh, on purpose)

`/health`'s `cookie_health` field goes `"degraded"` after `COOKIE_HEALTH_THRESHOLD`
(default 3) consecutive cookie-backed fetches fail with an auth-type error — the
strongest signal available that the burner session has expired, distinct from a merely
missing/private video. The nightly job checks this once a day and, while degraded, fires
at most one alert per day: a distinctly-titled `⚙️ System Alert` page in Notion (under
`NOTION_PARENT_PAGE_ID`, always wired up) and, if `NTFY_TOPIC` is set, a real push
notification via the free [ntfy.sh](https://ntfy.sh) service (no account needed on
either end). Detection and notification only — nothing here attempts to log back in or
refresh the session automatically, since that would mean storing a real password and
risking the burner account. See **COOKIES.md** for the manual fix once you're alerted.

## Phase 1 acceptance check

Paste 3 real reel URLs (1 comment-gated, ideally 1 music-only) through `/capture`:
- Each produces a correct Notion page within ~90s.
- Pasting a duplicate URL returns `{"status": "duplicate"}` with the existing page's URL
  instead of re-processing.
- Killing the fetch/Gemini step mid-run still produces a Notion page with status
  `⚠️ Failed — retry`, never a dropped capture.
- The music-only reel gets an honest "no speech detected" entry, not invented takeaways.

Retry a failed row:
```bash
curl -X POST http://localhost:8000/retry/<shortcode>
```

Attach a DM'd resource to a gated row (see Phase 2 below — redesigned; an
exact shortcode is the only value that commits immediately, everything else
returns candidates to confirm via a second call):
```bash
curl -X POST http://localhost:8000/attach \
  -H "Content-Type: application/json" \
  -d '{"shortcode_or_note": "Da8IIonEhGR", "resource_url": "https://instagram.com/direct/t/...", "secret": "change-me"}'
```

## Phase 2 acceptance check

- Sharing a comment-gated reel, waiting for the pipeline to finish, then re-sharing the
  same link returns `capture_status: "awaiting_dm"` and the real `gate_keyword`.
- `/attach`-ing a DM'd link flips that entry to `📥 Inbox` in Notion and records the link
  under **Gate resource**; the SQLite row shows `status: "done"`.
- Manually backdating a `processing` row's `updated_at` by >1h and running
  `scripts/run_nightly.py` flips it to `⚠️ Failed — retry` (and creates a Notion page for
  it if one somehow never existed). Backdating an `awaiting_dm` row by >7 days flips it to
  `🕳 Gate expired`.
- A row just reset via `/retry` is *not* touched by the nightly job even if its original
  `created_at` is old.

## Phase 3 acceptance check

- Saving two similar reels back to back: the second gets a **Related** relation to the
  first in Notion (visible on both pages — it's a two-way relation).
- Saving a near-duplicate (basically the same takeaway restated) tags the entry
  `near-duplicate` in **Topics**, in addition to the Related relation.
- A reel Gemini scores `value_score <= 2` (and isn't comment-gated) lands in
  `🗑 Low signal`, not `📥 Inbox`.
- A low-value *and* comment-gated reel still lands in `⏳ Awaiting DM` — the gate takes
  priority, since you still need to act on (or knowingly skip) it.
- After your 5th save from the same creator, that creator's row in the Creators DB gets
  **Core source** checked.
- Pulling your Gemini API key or hitting a quota error doesn't break capture — the reel
  still saves normally, just without a Related relation or near-dup tag for that one.

## Deploying (Render free tier)

See **DEPLOYMENT.md** for the full click-by-click setup (`render.yaml` Blueprint, env-var
placement, burner cookies via Secret Files, SQLite persistence tradeoffs) and
**SCHEDULING.md** for the keep-alive ping + nightly cron recipes. Expect ~30-50s cold
starts after ~15min idle unless the keep-alive is on — acceptable at <20 captures/day.

## Phase 2 — iOS Shortcuts, comment-gate assist, nightly cleanup

### Shortcut 1: "Save to Mycelium" (BUILD_SPEC §2.1)

Trigger: Share Sheet, accepts URLs and Instagram's share text.

1. **Receive input** from Share Sheet (`Reel`, `URL`, or `Instagram` as accepted types).
2. **Get URLs from Input** — pulls the link out whether you shared a raw URL or IG's
   share-text blob (e.g. "Check this out! https://instagram.com/reel/XXXX/?igsh=...").
3. **Get Contents of URL**:
   - Method: `POST`
   - URL: `https://<your-render-app>/capture`
   - Headers: `Content-Type: application/json`
   - Request body (JSON): `{"url": <the URL from step 2>, "note": null, "secret": "<CAPTURE_SECRET>"}`
4. **Get Dictionary from Input** on the response, then **Show Notification** with a
   text built from the dictionary's `status` field:
   - `processing` → "Saving reel..." (fetch+extraction is still running in the background)
   - `duplicate` → "Already saved" — see the comment-gate assist step below for what
     else this response carries once the pipeline has finished.

Optionally add a "Note" text-entry step before the API call and pass its value instead
of `null`, if you want to attach a quick note at capture time.

### Comment-gate assist (BUILD_SPEC §2.2)

`/capture` responds in under a second (`202 processing`) — long before fetch+extraction
(which can take up to ~90s) has actually figured out whether the reel is comment-gated.
So there's no keyword to show on the *first* share. Instead:

- **Re-share the same link** (same Shortcut, same Share Sheet action) once you'd expect
  the pipeline to have finished — that hits `/capture`'s dedupe path, which now returns:
  ```json
  {
    "status": "duplicate",
    "url": "<notion page url>",
    "capture_status": "awaiting_dm",
    "gate_keyword": "SEND",
    "permalink": "https://www.instagram.com/reel/XXXX/"
  }
  ```
- Extend the Shortcut: if `capture_status == "awaiting_dm"`, **Copy to Clipboard** the
  `gate_keyword`, then show a notification with an **Open reel** button that opens
  `permalink` (deep-links into the Instagram app if it's installed) — comment the
  keyword (already on your clipboard) and wait for the creator's DM.
- If `capture_status` is anything else (`done`, `failed`, etc.), just show that instead.

### Shortcut 2: "Attach to Mycelium" (BUILD_SPEC §2.2 — REDESIGNED)

For once the creator's DM arrives with the promised link. Trigger: Share Sheet from the
Instagram DM (or Messages/any app), accepting URLs.

**Why this changed:** the old design let `shortcode_or_note: null` fall back to
"whichever reel is the sole Awaiting DM entry", auto-committing with no way to verify
it was the right one. That caused a real cross-attachment — a resource meant for one
reel landed on a different, coincidentally-similar-sounding one, reported as a genuine
"success" with no error anywhere (see PROGRESS.md for the full incident writeup). The
fallback tier is gone. **An exact shortcode is now the only value that commits
immediately — everything else returns candidates for you to pick from, never an
auto-commit.**

#### `POST /attach`

Request body (JSON):
```json
{"shortcode_or_note": "Da8IIonEhGR", "resource_url": "https://...", "secret": "<CAPTURE_SECRET>"}
```
- `shortcode_or_note`: pass the **exact shortcode** if you know it (from the reel's
  Instagram URL, e.g. `instagram.com/reel/Da8IIonEhGR/` → `Da8IIonEhGR`). `null`/omitted
  is accepted but no longer does a "note substring" or "sole pending row" guess — it goes
  straight to candidate scoring (see below).

**Response shape (simplified further — this is the whole reason Shortcuts kept
breaking):** iOS Shortcuts has no reliable way to loop through an array of dictionaries or
navigate nested conditions client-side — every earlier attempt at that (including the
first "flat" response shape, which still shipped a `candidates` array of objects) broke
in practice. So now the server does **all** formatting, looping, and truncation, and the
response is always exactly **three keys**, no nesting, no arrays-of-dictionaries:

```json
{"action": "NOTIFY" | "MENU", "message": "<string>", "menu_items": ["<string>", ...]}
```

The Shortcut only ever needs to check **one field** — `action` — and either show a
notification (`message`) or a native **"Choose from List"** built directly from
`menu_items` (already plain strings, zero further parsing needed to display them). Every
internal business outcome collapses into one of these two, and only a genuine server
error (the Notion write itself failing) is still a real non-`200` status — see below.
`menu_items` is always `[]` when `action` is `"NOTIFY"`.

Here's the exact JSON for all four internal outcomes (server-side logs/audit still record
which of these four actually happened — see below — but the client never sees these names):

1. **`attached`** — committed immediately (only when `shortcode_or_note` was an exact,
   real shortcode that's currently open — Awaiting DM, or Inbox with an unfulfilled
   gate keyword):
   ```json
   {
     "action": "NOTIFY",
     "message": "✅ Attached to Da8IIonEhGR: Higgsfield is offering 24 hours of free access.",
     "menu_items": []
   }
   ```
2. **`not_found`** — the exact shortcode you gave exists but isn't open for a gate
   right now (already attached, or never gated at all — check the Notion row directly):
   ```json
   {
     "action": "NOTIFY",
     "message": "⚠️ No match found — that shortcode isn't awaiting a DM resource right now. Attach manually in Notion.",
     "menu_items": []
   }
   ```
3. **`unresolved`** — no exact shortcode was given (or it isn't a real shortcode), and
   nothing among the currently-open gates scored as a confident match against the
   resource URL's own page title/description:
   ```json
   {
     "action": "NOTIFY",
     "message": "⚠️ No match found — nothing scored a confident match. Attach manually in Notion.",
     "menu_items": []
   }
   ```
4. **`needs_confirmation`** — no exact shortcode, but 1-3 candidates scored above the
   confidence threshold. Each `menu_items` entry is pre-formatted server-side as
   `"<main_point> | <shortcode>"` — **main_point first and prominent** (that's how you
   actually recognize which reel it is), shortcode reduced to a trailing suffix purely so
   the Shortcut can recover it after you pick:
   ```json
   {
     "action": "MENU",
     "message": "Which reel does this belong to?",
     "menu_items": [
       "Higgsfield is offering 24 hours of free access. | DbFDY3yTwlI",
       "An app built with Higgsfield AI applies filters. | Da8A4axznbT"
     ]
   }
   ```
   Feed `menu_items` directly into a native **"Choose from List"** step — no looping, no
   dictionary access. Once the user picks one, **split the chosen string on `"|"` and take
   the LAST segment** to recover the shortcode, then call `/attach/confirm` with it.

**`5xx` (502) — genuine write failure, NOT folded into the three-key shape.** The row was
correctly identified, but the durable Notion write itself failed (network hiccup, etc.) —
the attach was **NOT** recorded anywhere. This is a real error, not a business outcome, so
it keeps FastAPI's normal `{"detail": {"status": "failed", ...}}` shape and a non-200
status. Retry; never treat this as success.

#### `POST /attach/confirm`

Commits a specific candidate you chose from a prior `needs_confirmation`/`MENU` response.

Request body (JSON):
```json
{"shortcode": "DbFDY3yTwlI", "resource_url": "https://...", "secret": "<CAPTURE_SECRET>"}
```
- `shortcode`: **required** — the shortcode you split off the end of whichever
  `menu_items` string you picked (see above). This endpoint never guesses either.

Response: the same three-key shape as `/attach` — in practice always `action: "NOTIFY"`
here, since confirming never produces a menu:
```json
{"action": "NOTIFY", "message": "✅ Attached to DbFDY3yTwlI: Higgsfield is offering 24 hours of free access.", "menu_items": []}
```
or, if the shortcode isn't a pending attach target:
```json
{"action": "NOTIFY", "message": "⚠️ No match found — DbFDY3yTwlI is not a pending attach target.", "menu_items": []}
```
A genuine write failure is still a real `502`, same shape as `/attach`'s.

Successfully attaching (via either endpoint) flips the entry `⏳ Awaiting DM → 📥 Inbox`
and records the DM'd link on the Notion page's **Gate resource** field. Every attempt —
resolved instantly, resolved via a confirmed candidate, or unresolved — is logged to a
persistent "🔍 Attach Audit Log" page under your `NOTION_PARENT_PAGE_ID`, so a future
mismatch can be looked up directly instead of reconstructed after the fact. Since the
client-visible `action` field can no longer distinguish the four internal outcomes (three
of the four are all `NOTIFY`), each one is still logged server-side as `"attach resolved:
<attached|not_found|unresolved|needs_confirmation>"` for anyone checking Render's logs.

**Shortcut rebuild note (yours to do, not server-side):** the whole point of this shape is
that the Shortcut needs **zero loops and zero nested-dictionary navigation** — get the
JSON, check `Dictionary Value for action`:
- `"NOTIFY"` → show `message` in a "Show Notification" (or "Show Result") step. Done.
- `"MENU"` → feed `menu_items` straight into "Choose from List", `message` as the prompt.
  Take whatever the user picks, split on `"|"`, take the last piece, call
  `/attach/confirm` with it as `shortcode`.

Only a genuine `5xx` means something actually went wrong — everything else is a normal,
expected outcome.

### Nightly cleanup job (BUILD_SPEC §2.3)

`scripts/run_nightly.py` marks rows stuck in `processing` for over an hour as failed, and
expires `Awaiting DM` rows nobody ever attached a resource to after 7 days — both still
get a Notion status update, never a silent drop:

```bash
python scripts/run_nightly.py
```

Schedule it once a day, however's easiest for you:
- **Render Cron Job** (separate from the web service) running the same command against
  the same env vars.
- **GitHub Actions** on a `schedule:` cron trigger, checking out the repo and running the
  script (needs the same secrets configured as repo secrets).
- A plain OS cron entry / Task Scheduler job if you'd rather run it from your own machine.

## Phase 3 — embeddings, near-dup, related saves, low-signal filter, Core source

All of this happens automatically inside the existing `/capture` → `run_pipeline` flow —
nothing new to trigger by hand.

### Embeddings + near-duplicate + related saves (BUILD_SPEC §3.1)

After extraction, `main_point + "\n" + joined(supporting_points)` gets embedded via
Gemini's embedding free tier (768-dim, `GEMINI_EMBEDDING_MODEL` in `.env`) and stored in
a `sqlite-vec` virtual table keyed by shortcode. Before storing the new save's own vector,
it's used to look up nearest neighbors:

- **Top-3 neighbors with cosine similarity > 0.75** become a two-way **Related** relation
  in Notion (it's a `dual_property` self-relation — set it on the new page and Notion
  automatically shows the reverse link on the older page too, no extra write needed).
- **If the single nearest neighbor is > 0.92 similar**, the save is also tagged
  `near-duplicate` in Topics — still saved in full, just flagged.
- This is an enhancement, not the critical path: a quota error, network failure, or
  sqlite-vec being unavailable in some environment just skips embeddings for that row
  (logged as a warning) — capture still succeeds normally.
- A neighbor that never got its own Notion page (e.g. it failed outright) is silently
  excluded from Related rather than producing a broken relation reference.

### Tag taxonomy convergence (BUILD_SPEC §3.2)

Already fully wired since Phase 1 — every extraction call injects the top-40 most-used
tags from SQLite (`store.get_taxonomy()`) as preferred candidates, so the taxonomy
naturally converges as you save more. Nothing new needed for Phase 3 here.

### Low-signal filter + Core source (BUILD_SPEC §3.3)

- `value_score <= 2` (and not comment-gated) → status `🗑 Low signal` instead of
  `📥 Inbox`. A comment-gated reel always lands in `⏳ Awaiting DM` regardless of its
  value score — the gate is a required action, not a triage signal.
- Once a creator has **5 or more saves**, their row in the Creators DB gets **Core
  source** checked. This only ever gets set, never unset (nothing in this system deletes
  saves, so the count only grows).

## Repo layout

```
app/main.py               FastAPI: /capture /retry /attach /nightly /ping /health
app/fetcher.py            yt-dlp burner fetch + safety caps
app/gemini_pipe.py        audio+caption -> Gemini -> validated JSON (transcript + extraction)
app/notion_writer.py      Notion page creation/update (data_source_id API)
app/store.py              SQLite: saves, tags, embeddings (sqlite-vec), dedupe, rate counter
app/nightly.py            stuck-row + expired-gate cleanup + cookie-health alert check
app/alerts.py             cookie-health alerting: Notion System Alert page + ntfy.sh push
app/digest.py             weekly digest: group, render markdown, Notion page
app/models.py             pydantic schemas (strict request models)
prompts/extraction.md     system prompt (versioned, editable)
scripts/setup_notion.py   one-time Notion DB creation
scripts/smoke.py          live end-to-end smoke test
scripts/delete_row.py     remove a local row so a smoke test can re-run
scripts/run_nightly.py    runs the nightly cleanup once (same code as POST /nightly)
scripts/weekly_digest.py  builds + posts the weekly digest
scripts/bulk_import.py    bulk-POST a list of reel URLs to the deployed /capture
scripts/sync_to_obsidian.py  Notion -> local Obsidian vault markdown sync
tests/                    unit + integration tests (fully mocked, no network)
render.yaml               Render free-tier Blueprint
DEPLOYMENT.md             Render setup, click by click
SCHEDULING.md             keep-alive ping + nightly cron recipes
VAULT.md                  Obsidian vault sync usage + scheduling
VAULT_CLAUDE_SETUP.md     Claude Desktop filesystem-MCP setup for the vault
NOTION_VIEWS.md           manual Notion view setup (mobile-friendly, This Week)
COOKIES.md                cookie-expiry alert runbook (2-minute manual refresh)
```
