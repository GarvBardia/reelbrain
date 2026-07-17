# PROGRESS.md — hardening/deployment session log

## Obsidian sync: real auto-regenerated topic/creator indexes (fix)

**Problem:** topic/creator notes were create-once stubs (`# name\n\n## Notes\n`) that
never got the actual list of related reels — that only showed via Obsidian's Backlinks
panel, which isn't a real page and isn't obvious to someone new to Obsidian.

**Fix — `app/obsidian_sync.py`:**
- New `upsert_auto_block(path, default_header, generated_lines)`: rewrites *only* the
  content between `<!-- AUTO-GENERATED, DO NOT EDIT BELOW -->` / `<!-- END AUTO-GENERATED -->`
  markers. Everything above the start marker (a user's own `## Notes`, or anything else
  they wrote) and everything after the end marker is preserved byte-for-byte. If a file
  doesn't exist yet, or exists from before this feature with no markers at all, the whole
  existing content becomes the preserved prefix and the block is appended fresh below —
  so old-style bare stubs migrate cleanly on their next sync with zero data loss.
- New `write_stub_index()` replaces the old `ensure_stub()` (removed — fully superseded):
  every topic/creator note now gets a real `## Saved Reels` section, regenerated every
  sync, listing every tagged reel as a `[[wikilink]]` with its value score and one-line
  Main Point (pulled from the reel's own first callout block via new `extract_main_point()`),
  sorted by `value_score` descending then posted-date descending.
- `write_topics_index()` (`_index.md`) rewritten the same way: real per-topic previews
  (top 3 reel titles as wikilinks under each topic heading), not just a bare count, and
  now also wrapped in the same AUTO-GENERATED markers so a preamble note above it would
  survive too.
- `sync()` restructured to collect `topic_entries`/`creator_entries` (title, value_score,
  posted date, main_point, note stem) alongside the existing per-reel write pass, rather
  than just incrementing counts.

**Tests:** 11 new in `tests/test_obsidian_sync.py` (8 existing kept, all still pass):
`upsert_auto_block` unit tests (new file, no-markers migration, replace-between-markers,
preserve-content-after-end-marker), topic and creator notes both getting a real
`## Saved Reels` section, **the exact scenario asked for** — a real prior sync's output
hand-edited above the markers surviving a second sync — **and** a second sync adding a
new reel to an already-existing topic updating the block to show both (not just the new
one, not a stale copy of just the old one), sort-order verification (value desc, then
date desc across three reels), `_index.md` showing real reel-title previews, and
`_index.md`'s own preamble surviving a resync. **Full suite: 179 passed** (was 168). No
live calls.

**For your review:** `VAULT.md` updated to describe the new behavior; nothing else
downstream (VAULT_CLAUDE_SETUP.md's instructions, the Task Scheduler steps) needed
changes since they were already describing the vault at a level this doesn't affect.

---

## `scripts/bulk_import.py` — bulk URL import against the deployed endpoint

**Important discovery that shaped the design:** `/capture` returns 202 immediately and
runs the actual yt-dlp fetch in a background task (`run_pipeline`) — the daily
`MAX_FETCHES_PER_DAY` cap is enforced *inside* that background task
(`fetcher._enforce_rate_discipline`), not as a distinct HTTP-level rejection. There is no
special status code or error body for "daily cap reached" — a submission past the cap
still gets a plain 202, and only later, invisibly to any client, becomes a Notion page
with status `Failed — retry`. So a client genuinely cannot detect the cap from the
server's response. Built the only thing that actually works: the script self-throttles by
counting its own `processing`-status submissions recorded in the progress file for today,
stopping before it would exceed `MAX_FETCHES_PER_DAY` and printing the requested
"daily cap reached ... resume tomorrow" message. This is a ceiling on the script's own
contribution only — it can't see the iOS Shortcut or manual `/retry` calls also spending
the same server-side daily budget, so it's an approximation, not a hard guarantee.
Documented prominently in the script's own docstring and in the README, not buried.

**What it does:**
- `read_urls`: one URL per line, blank lines and `#`-comments skipped.
- `bulk_import_progress.json` (atomic write: temp file + `os.replace`) — one entry per
  URL: status (`processing`/`duplicate`/`error`/`rate_limited`/`auth_error`), http_status,
  detail, date, timestamp. **`processing`/`duplicate` are terminal** (skipped on rerun);
  **`error`/`rate_limited` are NOT** — a transient failure (network blip, cold start, a
  temporary rate limit) gets retried automatically next run instead of silently
  blacklisting that URL forever.
- Client-side spacing: `MIN_FETCH_SPACING_SECONDS` (default 20) + random jitter (0-5s)
  between submissions — skipped after a duplicate (no server-side fetch happened, nothing
  to space out) and after the last URL.
- `401` (bad `CAPTURE_SECRET`) stops the entire run immediately — no point hammering a
  misconfigured secret against every remaining URL.
- `429` (our own per-IP rate limiter, unrelated to the daily cap) sleeps 2x spacing and
  continues — should be essentially unreachable given 20s+ spacing vs. the server's
  30/min limit, but handled rather than treated as fatal.
- Request timeout is a generous 60s to tolerate Render's free-tier cold start (~30-50s)
  on the first hit after idle.
- Running per-URL count + final summary (`X captured, Y duplicates, Z errors`, plus
  skipped/total) printed as specified.

**Tests:** 24 new in `tests/test_bulk_import.py`, all logic-level — `run_bulk_import`
takes injectable `submit_fn`/`sleep_fn`/`jitter_fn`/`print_fn` so no test touches the
network, the clock, or stdout. Covers: URL/comment parsing, progress roundtrip + atomic
write, daily-submission counting (today only, `processing` only), the happy path,
duplicate/error mix, no-delay-after-duplicate, rerun skipping terminal entries, error
retry on a later run, the daily-cap stop (both "already at cap from a prior run" and
"hits cap mid-run"), that the cap only counts *today's* entries, auth-error full-stop,
rate-limit backoff-and-continue, `--dry-run` (zero submissions, no progress file
written), jitter addition, and `submit_capture`'s response-shape mapping for all five
outcomes (202/200-duplicate/401/429/400/network-exception) against a fake httpx response.
**Also added an autouse `httpx.post` block to conftest** (mirroring the existing
`httpx.get` block) — confirmed it doesn't interfere with FastAPI's `TestClient`, which
uses its own `httpx.Client` instance, not the module-level function.

**Full suite: 168 passed** (was 144). No live calls — did not run this script against the
real deployed endpoint myself, per the ground rules.

**For your review:** the daily-cap self-throttle is the one part of this that's a
deliberate approximation rather than a precise mirror of server truth — worth knowing
before running a large multi-day import alongside regular manual captures.

---

## SESSION SUMMARY — three workstreams (Notion cleanup / Obsidian vault / Claude setup)

**All three done, three separate commits, 144 tests passing, no live calls made.**

| WS | What landed | Commit |
|---|---|---|
| 1 | Gate regex fixed (quoted mixed-case keywords — the DajFASZODlj miss), NOTION_VIEWS.md manual view guide (API can't configure views), nightly auto-archive (score≤2, 30d untouched → 🗄 Archived) | `52913aa` |
| 2 | Obsidian sync: Notion→markdown notes with frontmatter wikilinks, embedding-based `## Related` links (reused, not recomputed), stubs, `_index.md`, idempotent, VAULT.md incl. Task Scheduler steps | `7205c22` |
| 3 | VAULT_CLAUDE_SETUP.md: Desktop filesystem-MCP setup scoped to the vault, paste-ready project instructions, limitation note + recommendation | (this commit) |

**Needs your review:**
- WS1: auto-archive uses `updated_at` as the "untouched" proxy — Notion-side My-note
  edits are invisible to it (details in the WS1 entry).
- WS2: reel-note edits don't survive re-sync (by design); stub notes do.
- WS1: `NOTION_VIEWS.md` is manual clicks (~3 min) since the API can't create views.
- The existing DajFASZODlj row needs a retry to pick up the gate fix (command below).

**Your next 3 commands:**
1. Re-run the missed gate row through the fixed pipeline (after Render shows Live):
   `curl -X POST https://<your-render-app>/retry/DajFASZODlj`
2. First real vault sync (local, read-only against Notion):
   `python scripts/sync_to_obsidian.py`
3. Open the result in Obsidian (then follow VAULT_CLAUDE_SETUP.md for the Claude project):
   open `C:\Users\garvb\ReelBrainVault` via Obsidian → "Open folder as vault"

Plus the 3 minutes of Notion view clicks from NOTION_VIEWS.md when you're next at a desk.

---

## WORKSTREAM 2 — Obsidian vault sync (the smart-memory layer)

**Built:** `app/obsidian_sync.py` (logic) + `scripts/sync_to_obsidian.py` (local-only
runner, sys.path bootstrap + .env, never deployed) + `VAULT.md` (usage, Obsidian intro,
Windows Task Scheduler steps) + `VAULT_PATH` in `.env.example`.

- **Notion → markdown:** paginated `data_sources.query` over the Saves DB; per page,
  `blocks.children.list` (toggles get a second fetch for their children). Block
  conversion mirrors our writer's layout: callout → `## Main point` blockquote, bullets →
  `## Supporting points`, numbered → `## Steps`, bookmarks/paragraphs → `## Resources`,
  quotes → `## Quotable lines`, toggles → `## <title>` (Transcript/Raw caption).
- **Frontmatter:** shortcode, `creator: "[[creators/x]]"`, status, value_score,
  `topics: ["[[topics/x]]"]`, url, posted. Filename `{posted-date}-{shortcode}.md`
  (created_time date as fallback). Creator username comes from local SQLite (the Notion
  property is a relation — resolving it would cost one extra API call per save; SQLite
  already has it).
- **Smart linking:** `store.get_embedding()` reads capture-time vectors back out of
  sqlite-vec (raw float32 blob, struct-unpacked — verified the roundtrip locally before
  writing code); top-3 similar other saves become real `[[reels/...]]` wikilinks in a
  `## Related` section. Nothing is recomputed; saves without a stored vector just get no
  Related section.
- **Stubs + index:** `topics/x.md` / `creators/x.md` auto-created once (never
  overwritten — your notes in them survive re-syncs); `_index.md` lists every topic with
  save counts, sorted by count.
- **Idempotent:** existing notes matched by the `shortcode:` in frontmatter and updated
  in place — verified by a test where the posted date changes between runs (computed
  filename would differ) and still exactly one file remains, at the original path.

**Tests:** 8 new in `tests/test_obsidian_sync.py` — full-note content (frontmatter,
every section, stub creation), Related from real sqlite-vec vectors (self-link excluded),
no-embedding → no section, idempotent rerun, pagination (page_size=2 walks 3 queries),
index counts, stub preservation, slugify. **Full suite: 144 passed.** No live calls.

**For your review / notes:**
- Reel-note edits do NOT survive re-sync (regenerated from Notion each run) — by design;
  put your own thinking in topic/creator stubs. Called out in VAULT.md.
- `_slugify` is ASCII-only ("désí créator" → "d-s-cr-ator") — ugly but stable; unicode
  slugs in filenames get risky across OSes. The note's own title keeps full unicode.
- The sync is read-only against Notion and reuses the existing pinned notion-client. Its
  first live run is on you (ground rules) — see the final summary for the exact command.

---

## WORKSTREAM 1 — Notion cleanup: gate-miss fix, mobile views, auto-archive

**1. The DajFASZODlj gate miss — root cause found and fixed.** Investigated the success
path first as asked: `_merge_comment_gate` IS correctly applied there (and on all degraded
paths since the earlier fix) — the wiring is fine. The actual bug: the regex backstop
`[A-Z]{2,12}` only matches ALL-CAPS keywords. The caption is
`Comment "International" for free Guide` — mixed case — so the regex returned None, and
since Gemini's own `comment_gate` field also missed it, there was nothing to merge.
Reproduced locally before fixing. New regex accepts a **quoted keyword in any case**
(straight or curly quotes — the creator quoting the word is itself the signal) while
keeping the ALL-CAPS requirement for unquoted words, so "comment your thoughts below"
still doesn't match. Tests: the exact failing caption, curly quotes, lowercase-quoted,
unquoted-ALL-CAPS, and the prose negative.
   - Note: the existing DajFASZODlj row won't self-heal — after redeploy, `POST /retry/DajFASZODlj`
     will rerun it through the fixed pipeline.

**2. Mobile-friendly Notion views → `NOTION_VIEWS.md` (manual steps, ~3 min).** The public
Notion API has NO endpoints for view creation/config (sorts, filters, visible properties
are UI-only), so a setup script is impossible — took the task's escape hatch. The doc
covers: a `📱 Triage` default view (Status priority-sorted via select-option order — the
only mechanism Notion has for custom status ordering — newest first within status, only
Title/Status/Topics/Creator visible, leftmost tab = default on mobile) and a `This Week`
gallery view on a rolling 7-day `Posted at` filter with an OR on `Saved at` so degraded
rows don't vanish. Plus a one-click filter to keep 🗄 Archived out of daily views.

**3. Auto-archive wired into the existing nightly job.** `store.get_archivable()`:
`value_score <= 2` (from `extraction_json` via `json_extract`) AND untouched 30+ days AND
status not `processing`/`awaiting_dm`/`archived` → flipped to new `archived` status
(`🗄 Archived` added to STATUS_LABELS; Notion select options auto-create on first write,
no schema migration needed). Runs as a third pass in `nightly.run()`, reported as
`marked_archived` in the /nightly response. 6 new tests: archives stale+low, spares
recent-low / stale-high / awaiting_dm / already-archived / no-extraction (failed) rows.
   - **Caveat for your review:** "no My note edits" is approximated by `updated_at` — we
     never read Notion pages back, so Notion-side edits are invisible to us. "Untouched"
     means no pipeline/API activity on the row for 30 days. If you edit a note in Notion
     to save it from archiving, the nightly job won't know. Good enough at this scale, but
     it's a proxy, not the literal spec.

**Tests: 136 passed** (was 127). No live calls.

---

## HOTFIX — yt-dlp prod failures: cookie resolution, fail-fast, OG fallback

Server-side fixes for `No video formats found` / `empty media response ... use --cookies`
on Render while the same code works locally. Mocked tests only, no live calls.

**Found while implementing — the cookie retry was unreachable.** `_looks_like_challenge()`
gated the cookie-backed retry on markers `login required / rate-limit / 429 / challenge /
checkpoint`. The errors actually seen in prod (`No video formats found`, `empty media
response ... use --cookies`) match **none** of them, so `fetch_reel` treated the
soft-block as a hard error and raised on the spot — **the cookie retry never fired**.
That alone could explain the prod-only failure. Added those messages (plus
`requested content is not available`) to `CHALLENGE_MARKERS`, with tests.

1. **Cookie path robustness** (`app/fetcher.py`): `resolve_cookies_file()` checks
   `BURNER_COOKIES_FILE` then `/etc/secrets/cookies.txt` (Render Secret Files mount), so
   local and Render both work with no env juggling. `fetch_reel` now **fails fast before
   any network call or rate-limit sleep** when neither exists — message names both paths.
   The cookie retry now uses the *resolved* path (it previously used the raw env value,
   which would have missed the Secret Files mount). `log_cookie_source()` runs in the
   FastAPI lifespan so the deploy log states which file was picked.
2. **/health** (`app/main.py`): added `"cookies_file": true|false` (presence only, never
   contents) — the most common prod-only breakage is now checkable from a browser.
3. **yt-dlp freshness**: `yt-dlp==2026.7.4` was **already the latest release** (verified via
   `pip index versions`), so the pin is unchanged — no bump was available. Added a
   DEPLOYMENT.md section on it being a cat-and-mouse dep: the symptom list that means
   "bump yt-dlp first", the bump/test procedure, and what it means if a fresh yt-dlp still
   fails only in prod.
4. **OG-tag fallback** (`app/fetcher.py`): when yt-dlp gives up, one anonymous `httpx.get`
   (browser UA, 10s timeout, **never cookies**) reads the page's `og:title` /
   `og:description` / `og:image` via regex. If it yields a caption, `fetch_reel` **returns
   caption-only ReelData instead of raising**, so the row is no longer failed — the gate
   regex and topic tags still run off the caption, and the transcript toggle honestly reads
   `(unavailable)`. Added `ReelData.thumbnail_url` to hold `og:image` (parsed as asked;
   not yet written to Notion). If OG yields nothing, `FetchDegraded` is raised as before.
5. **Notion error visibility** (`app/main.py`): `_note_with_failure_reason()` appends
   `⚠️ <reason>` (truncated to 300 chars) to **My note**, after the user's note, never
   replacing it. Only on failure paths; clean rows keep a clean note.

**Tests:** 34 new (`tests/test_fetch_hardening.py` + additions to test_pipeline /
test_capture_endpoint) covering cookie resolution order + dedupe, fail-fast (asserts
yt-dlp is never invoked and no fetch is burned against the daily cap), all five soft-block
markers, OG parsing (both meta attribute orders, HTML entities, `@handle` extraction),
OG request shape (one call, timeout set, no cookies kwarg), both OG fallback paths, and
note appending/truncation. **Also added a conftest autouse guard blocking module-level
`httpx.get` in all tests** — the new OG path would otherwise have hit instagram.com for
real from the test suite. Full suite: **127 passed**.

**⚠️ For your review:**
- **BUILD_SPEC 1.2 deviation:** the spec says "FREE-FIRST … try logged-out first". We still
  do (anonymous attempt first, cookie retry second), but the app now **refuses to run at
  all without a cookie file**, which narrows "free-first" to "free-first, given cookies
  exist". That's what you asked for and matches reality on a datacenter IP — flagging it
  since it's a spec change. If anonymous-first is now pure waste on Render (it burns a
  daily-cap slot to fail), consider going cookies-first; say the word.
- **OG-fallback rows land as normal saves, not failures** (`📥 Inbox`/`🗑 Low signal` per
  value score, or `⏳ Awaiting DM` if gated) — that's the "instead of failing the row
  entirely" intent, but it does mean a caption-only row can look like a full save at a
  glance. The tell is the `(unavailable)` transcript toggle. Say the word if you'd rather
  they carry a marker tag.
- Unverified live: whether Instagram serves useful `og:description` to a datacenter IP
  anonymously. The parse is tested against realistic HTML, but if IG returns a login wall
  to Render's IP, the fallback yields nothing and rows fail as before (no regression).

---

## HOTFIX — comment-gate regex fallback + pysqlite3 for Render sqlite-vec

Two mocked-test-only bug fixes (no live calls).

**Bug A — comment-gate detection skipped on Gemini failure.**
`app/gemini_pipe.py::run_extraction()` merged the comment-gate regex only on the success
path; all three degraded returns (no `video_path`, ffmpeg `CalledProcessError`, and the
final `except Exception: break` when the model call fails) returned `degraded_extraction()`
**without** the gate check. Since the gate is a pure caption regex needing no AI, a gated
reel whose Gemini call died would silently lose its `⏳ Awaiting DM` status and keyword.
Fix: added a `_degraded(caption)` helper that builds the degraded extraction **and** runs
`_merge_comment_gate` on it; all three degraded paths now go through it. Success path
unchanged.
- Tests (`tests/test_gemini_pipe.py`): gate still fires with (1) no video, (2) `_call_gemini`
  mocked to raise on every attempt, (3) ffmpeg mocked to raise `CalledProcessError` — each
  asserts `comment_gate.detected is True` and `keyword == "SEND"`.

**Bug B — sqlite-vec can't load on Render (Linux) — `enable_load_extension` missing.**
Some Linux Python builds compile the stdlib `sqlite3` without loadable-extension support,
so `Connection` has no `enable_load_extension` and sqlite-vec fails to load (works locally
on Windows, hence prod-only). Fix:
- `requirements.txt`: added `pysqlite3-binary==0.5.4 ; sys_platform == "linux"` (Linux-only
  marker — no Windows wheels exist, and local dev doesn't need it).
- `app/store.py`: at import, if `not hasattr(sqlite3.Connection, "enable_load_extension")`,
  swap in `pysqlite3.dbapi2 as sqlite3` (drop-in). No-op on Windows (stdlib already works),
  so local dev is unchanged. If pysqlite3 isn't importable either, we keep stdlib and the
  existing graceful-degrade (`sqlite-vec unavailable` warning, app runs without embeddings)
  is the final fallback — embeddings stay non-critical.
- `DEPLOYMENT.md`: new "sqlite-vec on Render" section documenting the symptom, the fix, and
  the /health verification.

**Tests:** full suite **99 passed**, 1 warning (starlette-internal). No live calls; the
sqlite3 swap is a no-op locally so existing embeddings-store tests still exercise the real
sqlite-vec load on Windows and pass.

**⚠️ Needs a live check (can't verify without deploy):** the pysqlite3 swap and the exact
`pysqlite3-binary==0.5.4` wheel are unverified on Render's actual sandbox. High confidence
it works and needs **no** paid plan tier — pysqlite3-binary bundles its own SQLite and
sqlite-vec loads its extension as an in-process shared lib from site-packages (not a system
`.so`, not a sandboxed syscall). But confirm post-deploy via `/health` → `sqlite_vec: true`;
if the wheel doesn't resolve for Render's Python 3.12, relaxing/bumping that pin is the fix,
and worst case the app still runs fine with embeddings disabled.

---

## HOTFIX — env-var whitespace stripping (Render trailing-newline token bug)

**Symptom:** Notion API calls on Render died with
`httpx.LocalProtocolError: Illegal header value ...\n` — the `NOTION_TOKEN` pasted into the
Render dashboard carried a trailing newline, which is illegal inside an HTTP
Authorization header.

**Fix:** every env var read as a credential / ID / username / URL / path is now `.strip()`-ed
at read time, so pasting is forgiving:
- `app/notion_writer.py`: `NOTION_TOKEN`, `NOTION_DB_ID`, `NOTION_CREATORS_DB_ID`
- `app/gemini_pipe.py`: `GEMINI_API_KEY`, `GEMINI_MODEL`, `GEMINI_EMBEDDING_MODEL`
- `app/main.py`: `CAPTURE_SECRET` — stripped at read **and** the inbound request secret is
  stripped in `_check_secret` before the constant-time compare, so a stored-stripped value
  still matches a request whose secret has stray whitespace.
- `app/fetcher.py`: `BURNER_COOKIES_FILE`, `BURNER_ACCOUNT_USERNAME`, `REAL_ACCOUNT_GUARD`
- `app/store.py`: `DB_PATH`
- `app/digest.py`: `NOTION_PARENT_PAGE_ID`
- `scripts/setup_notion.py`, `scripts/setup_notion_saves_only.py`: `NOTION_TOKEN`,
  `NOTION_PARENT_PAGE_ID`, `NOTION_CREATORS_DB_ID`
- Numeric config (`MAX_FETCHES_PER_DAY`, `MIN_FETCH_SPACING_SECONDS`, `PORT`) left as-is —
  already wrapped in `int()`, which tolerates surrounding whitespace.

**Test:** `tests/test_env_stripping.py` — sets env vars with trailing `\n`/`\r\n`/leading
spaces, reloads the module, asserts stripped; covers `NOTION_TOKEN` (+ the two Notion IDs),
`CAPTURE_SECRET` (including that a whitespace-carrying inbound secret still matches), and
`GEMINI_API_KEY`. Full suite: **96 passed** (was 93). No live calls.

**Deploy:** committed and pushed to `main`; Render auto-redeploys from the push.

---

## FINAL SUMMARY

**Tasks: 8 of 8 completed, none skipped. Tests: 93 passed, 0 failed, 1 warning**
(the warning is starlette-internal, not actionable). No live API calls were made at any
point; every test is mocked, and two autouse conftest guards now make a live Gemini call
from tests structurally impossible.

| # | Task | Outcome |
|---|---|---|
| 1 | Module invocation fix | ✅ sys.path bootstrap in all 5 scripts; both styles work |
| 2 | Render deployment pack | ✅ render.yaml, pinned requirements, /health, DEPLOYMENT.md |
| 3 | Deprecation cleanup | ✅ lifespan handler; tz-aware datetimes, format-identical; 153→1 warnings |
| 4 | Keep-alive + nightly scheduling | ✅ /ping, secret-protected /nightly, SCHEDULING.md |
| 5 | Input hardening | ✅ strict models, 4xx paths, per-IP rate limit, 11 rejection tests |
| 6 | Weekly digest (mocked) | ✅ app/digest.py + script; Gemini optional/fail-soft |
| 7 | Coverage gaps | ✅ 12 tests: igsh/utm URLs, unicode, empty+no-speech, /attach priority |
| 8 | README final pass | ✅ quickstart rewrite + real-errors troubleshooting table |

**Needs your review / a live test (can't be verified mocked):**
1. **render.yaml** — schema is eyeballed only; Render validates it at Blueprint apply.
   Also confirm `PYTHON_VERSION=3.12.0` matches what you want.
2. **`POST /nightly` + GH Actions workflows in SCHEDULING.md** — the yaml is untested by
   nature; do one manual `workflow_dispatch` run after setting the two repo secrets.
3. **Weekly digest live run** — `python scripts/weekly_digest.py` needs
   `NOTION_PARENT_PAGE_ID` set; the markdown→blocks converter and the page-under-page
   parent shape should be sanity-checked against the real Notion API once.
4. **Rate limiting behind Render's proxy** — the "IP" seen may be the proxy's; if you see
   spurious 429s in prod, we need to read X-Forwarded-For (noted in Task 5 entry).
5. **DEPLOYMENT.md's SQLite tradeoff decision** — I recommended accepting DB loss with
   Notion as source of truth (Option 2); rebuild-from-Notion script is listed as future
   work. Confirm you're okay with that before relying on the deploy.

---

## Task 1 — Fix module invocation ✅

**What changed:** Added a `sys.path.insert(0, <repo root>)` bootstrap at the top of all
five scripts in `scripts/` (`smoke.py`, `delete_row.py`, `run_nightly.py`,
`setup_notion.py`, `setup_notion_saves_only.py`), placed before any `app` import.
Documented in README (Install section) that both `python scripts/x.py` and
`python -m scripts.x` now work, with plain-path style as the preferred convention.

**Verified:** `py scripts/delete_row.py`, `py scripts/smoke.py`, and `py -m scripts.smoke`
all reach their usage message (i.e. imports resolve — the old failure was a
`ModuleNotFoundError` at import time, before usage printed). No live calls made: no-arg
invocations exit at the usage check. Full suite: **57 passed**.

**Unsure/notes:** the two `setup_notion*.py` scripts don't import `app` at all, so the
bootstrap is a no-op there today — added anyway for consistency and future-proofing.

## Task 2 — Render deployment pack ✅

**What changed:**
- `render.yaml`: free-tier web service Blueprint — build/start commands, `/health` as the
  health-check path, all env vars declared (`sync: false` for secrets so Render prompts
  for them at Blueprint apply; no values committed).
- `requirements.txt`: converted from `>=` ranges to exact pins matching the versions the
  suite passes against locally (fastapi 0.139.0, google-genai 2.11.0, notion-client 3.1.0,
  sqlite-vec 0.1.9, yt-dlp 2026.7.4, etc.). Cross-checked every import (including the
  lazy in-function ones: google.genai, yt_dlp, sqlite_vec) — all covered.
- `/health` endpoint in `app/main.py`: reports `status`, whether sqlite-vec is actually
  usable (probes the `save_vec` table, not just the not-yet-failed flag), and `DB_PATH`.
  Kept `/` as a plain ok response since the Shortcut docs never referenced it.
- `DEPLOYMENT.md`: click-by-click Blueprint + manual paths, where each env var value comes
  from, burner `cookies.txt` via Render Secret Files, and an honest SQLite-on-ephemeral-disk
  section: what a wipe costs (dedupe/embeddings/taxonomy/rate-counter — NOT the knowledge
  base, which lives in Notion), the paid-disk option and why it's not recommended under
  the ₹0 constraint, and `rebuild_from_notion.py` noted as future work.

**Verified:** new `/health` test asserts `sqlite_vec: true` in the test env. Full suite:
**58 passed**. render.yaml is YAML-eyeballed only — Render validates it at Blueprint
apply; I can't validate the schema locally.

**Unsure/notes:** pinned `PYTHON_VERSION=3.12.0` in render.yaml to match the local 3.12
interpreter the pins were resolved against. Worth your review.

## Task 3 — Deprecation cleanup ✅

**What changed:**
- `app/main.py`: `@app.on_event("startup")` → an `asynccontextmanager` lifespan handler
  passed to `FastAPI(lifespan=...)`.
- `app/store.py`: all `datetime.utcnow()` → new `_utc_naive_now()` helper =
  `datetime.now(timezone.utc).replace(tzinfo=None)`. The `.replace(tzinfo=None)` is the
  load-bearing part: an aware datetime's `isoformat()` appends `+00:00`, which would have
  changed the timestamp string format vs. every row already in the DB and skewed the
  nightly job's lexicographic `< cutoff` comparisons. Verified the output format is
  byte-identical to the old `utcnow().isoformat()`.
- `tests/test_nightly.py`: `_backdate` now uses the same helper (imported from store) so
  test timestamps stay format-identical too.

**Verified:** warnings went **153 → 1**; the survivor is inside starlette itself
(`httpx`→`httpx2` TestClient deprecation), not our code — not actionable without a dep
change. Full suite: **58 passed**.

## Task 4 — Keep-alive + nightly scheduling ✅

**What changed:**
- `GET /ping` (no auth, returns `{"pong": true}`) — keep-alive target.
- `POST /nightly` (secret-protected via the same `CAPTURE_SECRET`, strict pydantic body)
  wrapping `nightly.run()` — same code path as `scripts/run_nightly.py`, so Render's
  missing cron is covered by an external scheduler POSTing over HTTP.
- `SCHEDULING.md`: cron-job.org and GitHub Actions keep-alive recipes (with an honest note
  on the 750 instance-hours/month budget and why waking-hours-only pinging is smarter),
  plus the GH Actions nightly workflow yaml (repo secrets, manual-dispatch test path,
  note that the POST itself wakes a sleeping instance so nightly doesn't depend on ping).

**Verified:** 4 new endpoint tests — ping needs no auth, nightly 401s on bad secret,
nightly actually flips a backdated stuck row to failed over HTTP and reports it, empty
run returns empty lists. Full suite: **62 passed**.

## Task 5 — Input hardening ✅

**What changed:**
- `app/models.py`: all three request models now `extra="forbid"` with length bounds on
  every string field; `AttachRequest.resource_url` must be http(s) (rejects
  `javascript:` etc. with 422).
- `app/main.py`: per-IP in-memory rate limiter (30 req/min sliding window, `deque` per
  IP, `time.monotonic`) applied to `/capture` `/attach` `/retry` `/nightly` — NOT to
  `/ping` `/health`, which the keep-alive/Render health checks hit unauthenticated.
  `/retry/{shortcode}` now validates the path param (`[A-Za-z0-9_-]{1,30}`) → clear 400.
  Secret comparison was already `hmac.compare_digest` (constant-time) — kept, commented.
- `tests/conftest.py`: autouse fixture clears rate buckets between tests (all TestClient
  requests share one 'testclient' IP; without this the suite rate-limits itself).

**Verified:** 11 new rejection-path tests: unknown fields (422 on all three models),
oversized/empty URL, malformed-URL 400 with clear message, non-http resource_url,
malformed + overlong retry shortcode, rate limit trips at exactly the 31st request and
recovers after the window. Full suite: **73 passed**.

**Unsure/notes:** (1) Rate limiter is per-process memory — resets on redeploy/restart,
fine at this scale. (2) On Render the client IP seen may be Render's proxy unless we read
`X-Forwarded-For`; with one legit user + a shared secret this doesn't matter much, but if
you ever see spurious 429s in prod, that's the knob. (3) Discovered in passing:
URL-encoded traversal (`/retry/..%2F..`) is 404'd by starlette's router before our
handler — good, but it means the 400 path only covers junk that routes.

## Task 6 — Weekly digest (mocked only) ✅

**What changed:**
- `app/digest.py` (logic, testable) + `scripts/weekly_digest.py` (thin live runner, same
  pattern as nightly). `collect_week()` pulls past-7-day saves + tags from SQLite and
  groups by topic and creator; `render_markdown()` produces the digest (count, optional
  3-sentence AI section, by-topic with main points, by-creator counts);
  `create_notion_page()` writes it as a page directly under `NOTION_PARENT_PAGE_ID`
  (page_id parent, not a database) via a minimal markdown→Notion-blocks converter capped
  at Notion's 100-children limit.
- Gemini summary is genuinely optional: `try_ai_summary()` catches everything and returns
  None; the digest renders without the section. A Notion failure also fails soft — the
  markdown is still returned/printed.
- `store.py`: `get_saves_since()` + `get_tags_for_shortcodes()` helpers.
- **conftest hardening:** new autouse fixture blocks `google.genai.Client` construction
  in ALL tests (raises instead of any network call) — defense-in-depth beyond the
  existing embed_text guard, and it doubles as the real failure path for the digest's
  fail-soft test.

**Verified:** 8 new tests — grouping + old-row exclusion, empty week, with/without AI
summary rendering, fail-soft on blocked Gemini, full run() writing the fake Notion page
with correct parent/title/blocks despite Gemini failure, skip without parent ID, Notion
failure still yields markdown, 100-block cap. Full suite: **81 passed**. Not run live per
ground rules.

**Unsure/notes:** digest page goes under `NOTION_PARENT_PAGE_ID` (the same parent the
setup script used) — needs that env var set on whatever runs the weekly script.
Scheduling: run `python scripts/weekly_digest.py` wherever, or if you want it HTTP-
triggered like /nightly, that's a 5-line endpoint away — didn't add it unasked.

## Task 7 — Test coverage gaps ✅

**What changed:** `tests/test_coverage_gaps.py`, 12 new tests across the four named gaps:
- Real-world share URLs: the exact iOS share-sheet shape (`?igsh=...==` base64, also the
  percent-encoded `%3D%3D` variant), utm params, `/p/` + mixed params, and the share-text
  blob with emoji around the link.
- Unicode: Hindi + emoji caption/transcript/quote/note flow through the pipeline into
  Notion properties and blocks byte-intact; `_rich_text`'s 2000-char truncation is safe
  on an all-emoji string (Python slices by code point, not bytes).
- Empty caption + no speech end to end: transcript toggle says "(no speech detected)",
  caption toggle "(no caption)", value 1 → Low signal, nothing invented.
- `/attach` priority: exact-shortcode beats note-substring (adversarial row whose note
  contains another row's shortcode), note-match beats most-recent, no-match falls back to
  most-recent, and non-awaiting rows are invisible to matching even when their note matches.

**Verified:** full suite **93 passed**.

## Task 8 — README final pass ✅

**What changed:** top section rewritten as a true quickstart (5 commands, venv → capture),
with the current reality inline: `gemini-2.5-flash` + `gemini-embedding-001@768` model
defaults and why the specced ones died, Notion data_source_id API note, script invocation
conventions, pointers to DEPLOYMENT.md/SCHEDULING.md. Added the troubleshooting table of
every real error hit in development (ModuleNotFoundError, ffmpeg WinError 2, Gemini 429 on
2.0-flash, 404 on text-embedding-004, Notion properties-validation error, plus
`sqlite_vec: false` in /health). Replaced the stale manual Render section with a
DEPLOYMENT.md pointer and refreshed the repo-layout listing (digest, delete_row, new
endpoints, ops docs).

**Verified:** full suite after all edits: **93 passed**.

**Note:** your task description mentioned "gemini-flash-latest", but the code's actual
default (set during your live testing) is `gemini-2.5-flash` — I documented what the code
does. If you meant to switch the default to `gemini-flash-latest`, that's a one-line
change in `app/gemini_pipe.py`.
