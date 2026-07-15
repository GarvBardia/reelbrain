# PROGRESS.md — hardening/deployment session log

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
