# PROGRESS.md — hardening/deployment session log

## ⭐ PART 2 — gate-nudge live verification: root cause now CONFIRMED, still not reaching the phone

Triggered `/nightly` for real against the deployed app (not a diagnostic script)
after Part 1's deploy went live, then read Render's live logs for the actual
result — this is what the BUG A logging fix from earlier tonight was for, and
it worked exactly as intended:

```
ERROR:reelbrain.alerts:gate-nudge: ntfy push to https://ntfy.sh/reelbrain482 failed — status=429 body={"code":42908,"http":429,"error":"limit reached: daily message quota reached; increase your limits with a paid plan, see https://ntfy.sh","link":"https://ntfy.sh/docs/publish/#limitations"}
```

This is a precise, confirmed cause — not the generic "some 429" seen earlier —
ntfy.sh's **free/anonymous public server has hit its daily message quota** for
this topic. Same result on 3 consecutive `/nightly` triggers tonight (06:05,
06:06, 07:20 UTC), so this isn't transient — the daily quota is exhausted and
won't clear until ntfy.sh's own reset window passes (their docs don't publish
an exact time; historically UTC midnight for the public server).

**No code change can fix this** — it's an ntfy.sh account-tier limit, not a
malformed request or a bug in this repo. The logging fix (BUG A, earlier
tonight) did its job: the real cause is now visible instead of a silent
`ntfy_sent: false`. **The notification has not reached the phone tonight** —
this stays genuinely open, not silently assumed done. Options, none applied
without asking first:
1. Wait for ntfy.sh's daily quota to reset and re-trigger `/nightly` then.
2. Move to a paid ntfy.sh plan (higher/no anonymous daily cap).
3. Self-host ntfy, or switch to a different push channel (e.g. Pushover, a
   different ntfy topic/server) for just the gate-nudge + daily-digest pushes.

---

## ⭐ PART 1 — digests: single persistent page + real narrative content

**Problem:** both `/nightly`-adjacent digests (`/daily-digest`, `/weekly-digest`)
created a brand-new dated Notion page every run (`📬 Weekly digest — 2026-07-15`,
`🌙 Daily reflection — 2026-07-20`, ...) — accumulating endlessly. Content was
also a raw field dump: `- **title** — main_point — _topics_ — [Open reel](url)`.

**Fix 1 — single persistent page.** Added `notion_writer.find_child_page_by_title`
+ `notion_writer.upsert_named_page(parent_page_id, title, children)`: looks up a
direct child of the parent page by exact title; if found, replaces its body
blocks (the same delete-then-append pattern `update_page` already used for reel
notes); if not found, creates it once. `digest.create_notion_page` /
`create_daily_notion_page` now call this with FIXED titles —
`digest.WEEKLY_DIGEST_TITLE` ("📬 Weekly Digest") and `digest.DAILY_DIGEST_TITLE`
("🌙 Daily Reflection") — instead of a per-run dated title. The daily page's
title used to flip to "... (nothing saved)" on empty days; that's no longer
possible since the title is now the fixed lookup key, so "nothing saved" now
lives in the body text instead (it already did, as a fallback line).

**Known side effect, not touched:** old dated digest pages already sitting in
Notion from before this fix are NOT retroactively merged or deleted — only new
runs use the persistent-page pattern going forward. Didn't want to delete
historical pages without asking; flagging here rather than silently doing it.

**Fix 2 — content quality.** Both digests now open with a synthesis: a
deterministic stat line (`_synthesis_stat_line`, e.g. "12 reels saved today, 3
flagged High priority — common themes: claude-ai, mcp"), plus an OPTIONAL 2-3
sentence Gemini-written reflective paragraph in front of it (`try_ai_summary`
for weekly, new `try_ai_daily_summary` for daily — same fail-soft pattern:
returns None on any Gemini failure, deterministic line is always there).
Entries are grouped by priority tier (`## High/Medium/Low priority`, shared
`_group_by_priority`) and rendered as one natural sentence each
(`_format_entry`): the reel's already-well-written title + a light "filed
under {topics}" clause + the link — not a raw `**title** — main_point — _topics_`
field dump. Weekly also gets a compact "## Topics this week" index at the end
for browsing; the old "## By creator" section was dropped (mostly "(unknown)"
post-Notion-wipe anyway, and not part of the reflective-summary ask).

Tests: `tests/test_notion_writer.py` (new file) covers
`find_child_page_by_title`/`upsert_named_page` directly (create-once,
replace-on-second-call, pagination, 100-block cap) with a purpose-built fake
that actually models the parent/child-page relationship — the shared
`FakeClient` in `tests/test_pipeline.py` always returns empty block listings,
so it can't represent "the page already exists" and was left alone.
`tests/test_digest.py` updated: dropped by-creator assertions, rewrote content
assertions for the new priority-tier/natural-sentence format, added wiring
tests confirming `create_notion_page`/`create_daily_notion_page` call
`upsert_named_page` with a title that never changes across runs.

Pytest: 412 passed (401 + 6 new notion_writer tests + ~5 net new/changed digest tests).

---

## ⭐ Gate-nudge ntfy bugs (BUG A / BUG B) — investigated, one real fix, one already-fixed

**BUG A — silent ntfy send failure, now logged with real detail.** `/nightly`'s
response showed `gate_nudge: {nudged: [...rows...], ntfy_sent: false}` with no
visible cause. `alerts.send_gate_nudge` only logged a bare `"gate-nudge ntfy
push failed"` + traceback — technically present but not "visible" without
digging through a stack trace. Fixed: on `httpx.HTTPStatusError` it now logs
the actual `status=... body=...` on one line; on any other exception (connect
error, timeout) it logs `type(exc).__name__: exc`. Tests added:
`test_send_gate_nudge_logs_status_and_body_on_http_error`,
`test_send_gate_nudge_logs_exception_detail_on_network_error`.

**Root cause, confirmed via Render's live log API (not a guess):** the SAME
ntfy.sh topic (`reelbrain482`) got **429 Too Many Requests** from ntfy.sh
itself on 2026-07-20 and 2026-07-21, from the daily-digest's ntfy push (same
code pattern, same topic). This is ntfy.sh's own public-server rate limit on
that topic/IP — **not a malformed request**. The manual `Invoke-WebRequest`
test succeeded because it ran from a home IP, not Render's; Render's IP/topic
combination was already rate-limited when the app tried. No code change can
fix ntfy.sh's server-side rate limit — the logging fix makes this cause
visible next time instead of a silent `false`. If this becomes chronic,
options are: a paid/self-hosted ntfy instance, or an ntfy.sh account+token
(higher, non-anonymous limits) instead of the current anonymous public topic.

**BUG B — reported as "failed sends get marked nudged anyway," did not
reproduce.** `nightly.nudge_stale_gates()` already only calls
`already.update(...)` / persists `_GATE_NUDGED_KEY` inside `if sent:` — a
failed push leaves the row out of the persisted set, so it's re-attempted
next run. This exact behavior already has a passing regression test
(`test_nudge_failed_push_retries_next_night`, asserts the mocked send is
called twice across two runs when it keeps failing). The observed symptom
(`nudged: []` on both back-to-back runs) is consistent with **both** runs
hitting the same ntfy.sh 429 above, not with rows being wrongly marked done —
"nudged: []" means "not confirmed-delivered," not "skipped." No code change
made for BUG B; flagging this here in case the live retry still shows both
runs failing (also expected, if ntfy.sh's rate-limit window hasn't reset).

Pytest: 401 passed (399 + 2 new logging tests).

---

## ⭐ FINAL SUMMARY — Gemini retry + consistency check (read this first, supersedes the summary below)

Ran the 4-step follow-up while you were away ~45 min. Here's the true final state:

**True final numbers (fresh live Notion query, cross-checked 3 ways):**
- **93 rows** in Notion (was 81) — 93 distinct shortcodes, 0 duplicates.
- **REPORT.md**: 93 rows. **Obsidian vault**: 93 reel notes, 96 topics. **Processed flag**: 64 rows flagged true.
- All four numbers (Notion live / REPORT.md / Obsidian note count / Processed count) **agree with each other** — no discrepancy found (unlike the 69-vs-81 stale-cache incident last time).

**Step 1 — retry the 19 Gemini-degraded rows: partial success.**
- **12 of 19 succeeded** this pass (Gemini quota freed up) and were written to Notion normally.
- **7 still degraded**: `DXyXoVyMto8, DWY37MrhXJX, DanRTnJukuM, DagVhmZSgjt, DZu3ju6BBLt, Daf8iQknLD-, DadF0iqib3q`.
  Left unwritten, per instruction — no placeholder junk forced in. **This time the cause wasn't transient 503 overload — one error showed a 429 `RESOURCE_EXHAUSTED` with `quotaId: GenerateRequestsPerDayPerProjectPerModel-FreeTier, quotaValue: 20`**, i.e. the Gemini free tier's **daily** request cap, not a momentary capacity blip. Re-running again today may keep failing until the daily quota resets — worth knowing before re-running in a tight loop.

**Step 2 — since new rows landed, all three follow-ups ran:**
- `flag_processed.py`: flagged the 12 newly-written rows Processed (64 total now flagged, 29 left unflagged — placeholders/degraded).
- `generate_report.py`: regenerated against a fresh live query — 93 rows (not cached).
- `sync_to_obsidian.py`: failed once with a transient `httpx.RemoteProtocolError` (Notion server disconnected mid-request) — retried once, succeeded cleanly (93 notes, 96 topics).

**Step 3 — consistency check: clean, no discrepancy.** All four counts (Notion/REPORT.md/Obsidian/Processed-eligible) matched on this pass.

**Step 4 — test suite: 399 passed, 0 failed, 1 pre-existing unrelated deprecation warning (starlette/httpx).**

**Flagged for your review:**
- The 7 still-degraded rows above — likely blocked on Gemini's free-tier **daily** quota rather than a transient overload, so an immediate re-run may not help; consider waiting for the daily reset before retrying.
- Nothing was deleted, no Awaiting-DM statuses touched, no live-app code changed.

---

## ⭐ FINAL SUMMARY — autonomous Stages 3–7 (superseded by the summary above — kept for history)

All stages ran to completion while you were away. 399 tests green. Per-stage detail
is in the running log below; here's the headline for your review:

| Stage | Result |
|---|---|
| **3 — dedupe** | ✅ Archived the 1 duplicate (`DabVtQoCI2p`, kept original). Full re-scan: **no other duplicates.** |
| **3.5 — retry 19 degraded** | ⚠️ **PENDING — Gemini still 503-overloaded.** All 19 fetched fine but couldn't be extracted; **left unwritten (no junk)**. Re-run `python scripts/bulk_ingest_local.py --from-file urls.txt` later — only these 19 will process. |
| **4 — master report** | ✅ `REPORT.md` written (81 rows by topic + pending resources). |
| **5 — Obsidian** | ✅ Re-synced (81 notes, 92 topics, priority-grouped index, no stale artifacts). |
| **6 — cheap-model guide** | ✅ `CHEAP_MODEL_GUIDE.md` written. |
| **7 — Processed marker** | ✅ Added `Processed` checkbox; flagged **52 real extractions**, left **29 unflagged** (placeholders + degraded caption-title rows). |

**Judgment calls you should sanity-check:**
1. **Row count: it's 81, not the "69" I reported mid-run.** My earlier counts came from a
   **stale Notion-connector cache**; the live DB is 81 rows, verified by two independent
   queries, **81 distinct / zero duplicates**. No data lost — the "missing" 12 were always
   there, just not in the cached view. Stage 3's dedup conclusion still holds at full scale.
2. **"Processed" criterion:** I flagged a row Processed only if it has a synthesized title
   AND ≥1 real topic. This deliberately EXCLUDES the raw-caption-title rows (e.g. "comment
   AGENTS for the guide") that have a caption-ish title but no real extraction — they read
   as done but aren't, so they stay unflagged. If you'd rather those count as processed,
   say so and I'll adjust.
3. **DabVtQoCI2p dedup:** both copies were identical placeholders, so "keep the richer one"
   was a wash — I kept the older by creation time. Archived (recoverable), not deleted.

**Still needs you / still pending (nothing silently assumed done):**
- The **19 Gemini-503 rows** (Stage 3.5) — one later re-run when Gemini recovers.
- The **12 placeholder photo rows** with no caption at all — not recoverable via caption
  (some are true no-caption carousels); `scripts/recover_photo_captions.py` can retry OG
  captions but those specific ones returned nothing.
- 39 rows are **Awaiting-DM / gated with no resource attached yet** — that's your manual
  DM step (listed in REPORT.md's "Resources still pending").

Nothing in this session deleted data, changed Awaiting-DM statuses, or touched the live
app code path. All changes were: Notion content (adds/archive/flag) + new local-only
scripts + docs.

---

## AUTONOMOUS SESSION (Stages 3–7) — running log for your review

Running unattended while you're away (~1h). Conservative rules applied: archive not
delete, flag not guess, log everything. Per-stage results below; a FINAL SUMMARY is
appended at the very end once all stages finish.

### Stage 3 — Notion cleanup ✅ DONE
- Full re-scan for duplicate shortcodes across all 70 rows: **only ONE duplicate
  found** — the known `DabVtQoCI2p` pair. No others.
- Both `DabVtQoCI2p` rows were identical placeholders ("No caption or transcript
  available.", Photo — manual, value 3). Neither was genuinely "richer" — the only
  difference was a meaningless `near-duplicate` tag on the later copy. **Judgment
  call:** kept the ORIGINAL (created 2026-07-19 04:17Z), **archived** the later
  redundant copy (08:09Z, page ...ccff353) — standard keep-oldest dedup convention.
  Archived, NOT hard-deleted (restorable from Notion Trash).
- Result: Notion now **69 rows / 69 distinct shortcodes — zero duplicates.**
- Did NOT touch any Awaiting DM statuses, per your instruction.

### Stage 3.5 — retry the 19 Gemini-503 rows ⚠️ STILL PENDING (Gemini still overloaded)
- Re-ran `bulk_ingest_local.py`; progress file correctly processed ONLY the 19
  pending (22 skipped as already-done). **Gemini's free tier is STILL 503-overloaded**
  — all 19 fetched fine again (captions/video came down) but every extraction
  degraded. **0 written — correctly left pending, no junk forced into Notion** per
  your rule.
- **These 19 still need a later re-run when Gemini recovers** (just run
  `python scripts/bulk_ingest_local.py --from-file urls.txt` again — only these 19
  will process):
  `Da0i1JcjXRr, Da0jSm-tDsI, DaxsXR-APbZ, DayP5WwtYM5, Dax8uqyzVWv, DaxrltihiEM,
  DawD8vcNJC7, DZuHZfSDj2-, DaacoWejUai, DatHa0gOR4l, DXupkURBCz5, DXyXoVyMto8,
  DWY37MrhXJX, DanRTnJukuM, DagVhmZSgjt, DaiWZTfs3x9, DZu3ju6BBLt, Daf8iQknLD-,
  DadF0iqib3q`
- This is a transient external-service outage, not a code or data problem. Their
  captions ARE recoverable (seen in the run log) — just gated on Gemini capacity.

### Stage 4 — master report ✅ DONE (`REPORT.md`)
- Built `scripts/generate_report.py` (reproducible) and wrote **REPORT.md**: overview
  stats, every row grouped by topic (92 topic sections) with one-line summary + value
  score + `[R]`/`[P]` resource state, plus an actionable "resources still pending" list.
- **⚠️ IMPORTANT ROW-COUNT RECONCILIATION (a judgment call worth your eyes):** my
  earlier Stage 2/3 log said "69 rows". That was a **stale cached read** from the
  Notion connector — the LIVE database is actually **81 rows**. I verified both the
  app's direct Notion API and a fresh connector query now agree: **81 rows, 81
  distinct shortcodes, ZERO duplicates.** So Stage 3's conclusion still holds at full
  scale (the DabVtQoCI2p archive was the only dup; nothing else is duplicated across
  all 81). The extra 12 rows were always there — not new captures — just hidden by the
  connector's cache when I first counted. No data was lost or mis-handled; the earlier
  "69" figures in this log are simply superseded by the verified 81.
- REPORT.md snapshot: 81 rows = 69 real AI extractions + 12 placeholders; 14 resources
  attached, 39 pending (Awaiting DM / gated); Priority High 47 / Medium 30 / Low 4.

### Stage 6 — cheap-model guide ✅ DONE (`CHEAP_MODEL_GUIDE.md`)
- Self-contained extraction spec: JSON schema + constraints, the extraction prompt,
  value_score rubric, the exact priority formula (CLAUDE_KEYWORDS + value thresholds),
  comment-gate rules incl. the keyword↔detected invariant, the full Notion field
  mapping, status routing, and a worked example. (Written out of order — it's
  Notion-independent — while the Stage 3.5 retry ran; committed with Stage 6.)

### Stage 5 — Obsidian re-sync ✅ DONE
- `sync_to_obsidian.py`: **81 notes written, 92 topics.** `_index.md` came out clean —
  just `# Topics Index` + the AUTO-GENERATED priority-grouped block, NO stale
  above-marker artifacts (last session's cleanup held). Spot-checked a newly-ingested
  row (Da0vM9ZPVmz): full frontmatter, priority High, value 5, real topics. Vault is a
  separate folder (not git-tracked), so only this log entry is committed.

### Stage 7 — Processed marker ✅ DONE
- Added a `Processed` **checkbox** property to the Saves data source (idempotent).
- Flagged **52 rows** Processed = real extraction (synthesized title AND ≥1 real topic).
  **29 left unflagged**: the 12 placeholder photo rows + the raw-caption-title degraded
  rows (title but no topics) + a couple gated-caption rows with no real extraction.
  Only ever SET True on real rows — never unchecked anything (placeholders stay
  unflagged by default). Verified live: 52 Processed / 29 unflagged / 81 total.
- `scripts/flag_processed.py` is re-runnable + has a `--dry-run`; new captures can be
  swept later the same way.

---

## Bulk local ingest — home IP fetched 32/32; Gemini 503 is the only bottleneck

Stage 2 of the reellist ingest ran live via `scripts/bulk_ingest_local.py` (home IP +
burner cookies). **The fetch worked for ALL 32 new URLs — 32/32, zero blocked.** The
home-IP approach is now proven at scale, not just on a single probe. Every URL got
either a video download (full transcript extraction) or, for the true carousels, an
OG-caption recovery via the bot UA.

Result: **13 written to Notion, 9 skipped as already-present, 19 still degraded.** The
19 degraded are PURELY Gemini free-tier 503s — its capacity was sustained-overloaded
tonight, so even the built-in 60s retry pass couldn't clear them (503 was longer than
one retry window, not a brief spike). Crucially: **the 19 are NOT written** —
`probe_one` never persists a degraded extraction, so no caption-as-title junk polluted
Notion. They're all tracked in `bulk_ingest_progress.json`; the 13 written + 9 dupes
are terminal, so **re-running `python scripts/bulk_ingest_local.py --from-file urls.txt`
when Gemini recovers will process ONLY the 19** (no re-fetching, no duplicates). The
still-degraded shortcodes: Da0i1JcjXRr, Da0jSm-tDsI, DaxsXR-APbZ, DayP5WwtYM5,
Dax8uqyzVWv, DaxrltihiEM, DawD8vcNJC7, DZuHZfSDj2-, DaacoWejUai, DatHa0gOR4l,
DXupkURBCz5, DXyXoVyMto8, DWY37MrhXJX, DanRTnJukuM, DagVhmZSgjt, DaiWZTfs3x9,
DZu3ju6BBLt, Daf8iQknLD-, DadF0iqib3q.

The 13 written are all high quality (real synthesized titles, value 1-5, correct gate
keywords, statuses routed properly: Awaiting DM / Inbox / Photo — manual). Note
`Da0vM9ZPVmz` — which was named as "already there" — was genuinely NOT in Notion and
got ingested (value 5, "Build a 4-agent Claude AI council…", gate "ROAST").

Notion now: 70 rows / 69 distinct shortcodes — the 1 remaining duplicate is the
pre-existing DabVtQoCI2p pair, which Stage 3 will clean.

---

## Photo/carousel recovery: VIABLE from home IP — the audit's biggest gap is solvable

**The live test result, definitively:** Instagram DOES serve OG caption tags to a
residential IP — but only to link-preview bot user-agents (`facebookexternalhit`,
`Twitterbot`). The browser UA gets a tag-less page even from home, so the block was
never purely IP-based; it's IP+UA. Proven on real placeholder row `Dak-1xRnPXL`:
recovered its actual caption ("Comment "agent" to get my free Claude code guides &
skills", creator chase.h.ai). **The carousel problem is NOT a dead end.**

`scripts/recover_photo_captions.py` (LOCAL-ONLY, never deployed) is built and
dry-run-verified: **14 candidate rows**. It fetches each placeholder row's caption
with the bot UA, runs the normal caption-only extraction, and updates the Notion row
(title/topics/value/priority/gate fields + rebuilt body); Status honestly stays
`📷 Photo — manual`. **Run it with: `python scripts/recover_photo_captions.py`**
(~3 min for 14 rows at 10s spacing; safely re-runnable).

Live end-to-end caveat, reported honestly: during the one-row full test, Gemini's
free tier was in a sustained 503 spike, so the final extraction step couldn't be
verified end-to-end tonight — the OG fetch and Notion write mechanics are proven,
and the script was hardened from that exact failure (a degraded extraction is never
written and stays retryable; the one row written before that guard gets re-picked).
If your run shows rows erroring with "extraction degraded", just re-run later —
that's Gemini load, not the script.

**Also fixed (deployed):** `/capture` dedupe now falls back to Notion — the
`DabVtQoCI2p` duplicate can't recur. (That existing duplicate pair still needs one
page archived by hand, or ask and I'll do it via the API.)

---

## Batch-retry script for pre-FIX-1 raw-caption rows — ready for YOU to run

`python scripts/batch_retry_stale_titles.py --dry-run` (verified live against real
Notion data) found **11 candidates** — rows whose Title is still the raw caption
dump and would benefit from FIX 1's caption-only extraction: DaS3SALCYAo,
Da7iYBuiSEO, DZrQ7dMRx0m, DaQIJuYCMkv, DaloTsYCBGo, DaYwajdCPOX, DZKfop6R30d,
DZVsGZGt8dd, DaRbl6qJxRM, DaxA0CdxSkW, DajFASZODlj. (Not 13: DZSFkNppVW_ was
already fixed live tonight, and Dap3IoNo4Kt / DatJq40lVD_ don't use "comment …"
phrasing so the agreed heuristic deliberately skips them — retry those two by
hand if wanted.) 📷 Photo — manual rows are excluded by design: no caption exists
for them, a retry can never improve them. Drop the `--dry-run` flag to run for
real — same spacing/daily-cap/progress-file discipline as bulk_import.py, safely
re-runnable, ~4 min for 11 rows at 20s spacing. **After running it, worth
spot-checking a few of the 11 for quality, same as tonight's DZSFkNppVW_
before/after.**

---

## Four fixes, each verified against real live outcomes (not just HTTP 200s)

All four deployed (commits `548b348`, `2c6f886`, `364c415` + Notion-side cleanup),
360 tests passing. Per tonight's lesson from the digest env-var bug, every claim
below is a verified live outcome — a real Notion row/page inspected after the fact.

### FIX 1 — no-video path now produces real extractions ✅ VERIFIED LIVE, big improvement

The main complaint. `run_extraction`'s no-video branch went straight to the bare
placeholder even though `run_caption_only_extraction` already worked; it now routes
through it whenever a caption exists (placeholder only when there's genuinely no/thin
caption). **Live before/after on `DZSFkNppVW_` (retried after deploy):**

| | BEFORE | AFTER |
|---|---|---|
| Title | raw caption dump: `comment "ads" for the full install guide 📈 skill 1. /spy 🔍…` | synthesized: *"You can run your entire Meta ads workflow inside Claude using five custom skills to automate spy res…"* |
| Topics | *(none)* | claude-ai, meta-ads, ai-marketing, digital-advertising |
| Value score | 3 (flat default) | 5 |
| Priority | Medium | High |
| Status | 📥 Inbox | ⏳ Awaiting DM (gate keyword "ads" correctly re-detected) |

Yes — this genuinely improved, exactly the way the complaint asked. Caveat for the
remaining stuck rows: a `/retry` re-runs the whole fetch, so rows whose OG caption
scrape gets login-walled from Render's IP (most `📷 Photo — manual` ones with
"(no caption)") still can't produce a summary — no caption ever reaches the
extractor. Rows that DO have caption text (the 14 raw-caption-title rows found in
the audit) are the ones worth re-running.

### FIX 2 — digests read from Notion, not ephemeral SQLite ✅ VERIFIED LIVE with real data

`collect_day`/`collect_week` are now Notion-primary (`find_saves_pages_since`,
created_time window, paginated) with local SQLite as the fallback on Notion errors.
**Live proof, run immediately after a redeploy wiped SQLite** (the exact condition
that previously produced false "nothing saved" digests): daily digest returned
**18 saves / 6 High priority** with real synthesized entries; weekly returned
**53 saves** including a live Gemini "week in three sentences". Both wrote real
Notion pages (inspected). Known limitation: weekly per-creator names show
"(unknown)" after a wipe (Creator is a Notion relation, not cheaply resolvable).

### FIX 3 — Awaiting-DM nudge in the nightly job ✅ (mocked tests; fires on next nightly)

Rows in Awaiting DM >24h (read from Notion, durable) trigger ONE ntfy push listing
title + gate keyword each — no repeats (app_state dedup; a failed push retries next
night rather than burning the row's one notification). Deliberately NOT DM
automation — bot-DMing risks the burner account; the human does the actual step.
Requires `NTFY_TOPIC` set on Render (currently NOT set there — the nudge silently
skips until you add it; same for the daily digest's phone push).
Note: with ~20 rows currently Awaiting DM >24h, the FIRST nudge after deploy will
be one long list — that's the backlog clearing, not a bug.

### FIX 4 — the two verification-artifact pages archived ✅ DONE (Notion-side, no code)

`🌙 Daily reflection (nothing saved) — 2026-07-19` and `📬 Weekly digest —
2026-07-19` (both created during last session's testing, both misleadingly saying
"nothing saved") were verified by title and archived via the Notion API — they're
in Trash, restorable. Nothing else was touched.

---

## ffmpeg exit-1 root cause: yt-dlp was downloading video-only files (no audio)

**ROOT CAUSE (of the three candidates investigated, this was the real one — #2):**
`fetcher._run_ytdlp` used `format: "best"`. For Instagram posts that only expose DASH
streams (no single progressive file), yt-dlp's `best` falls through to a **video-only**
stream — so the downloaded file genuinely had no audio track, and ffmpeg's `-vn` audio
extraction then failed with exit 1 on a file that had downloaded to 100% and opened
fine. This was never a truncation/timing problem (that earlier lock fix was a real but
*separate* bug); this is the download grabbing the wrong stream. Mocked tests only,
345 tests passing.

**Fix #2 (the actual fix — get audio into the download):** `fetcher.YTDLP_FORMAT` is
now `"bestvideo*+bestaudio/best"` with `merge_output_format: "mp4"` — prefer best video
+ best audio *merged* (so the file actually contains audio), with `/best` as a
last-resort fallback for the rare post that only has one combined format. `_video_path`
now resolves from `requested_downloads[0].filepath` (the actual post-merge path) rather
than a name derived from the pre-merge format, so the merged `.mp4` is found correctly.

**Safety net #1 (graceful handling if audio is genuinely absent):**
`gemini_pipe._has_audio_stream()` runs `ffprobe -select_streams a` *before* ffmpeg. If
a video genuinely has no audio (returns `False`), `run_extraction` skips ffmpeg
entirely and routes to `run_caption_only_extraction`, setting a distinct note on the
reel — **"no audio track in source video — summarized from caption only, no
transcript"** — which main.py surfaces on the Notion row. This is deliberately distinct
from the generic degrade, and from the photo/carousel note, so a no-audio video is
identifiable at a glance. If ffprobe itself can't run (missing/unreadable), it returns
`None` and the code falls through to attempting ffmpeg anyway — never wrongly skipping a
fetchable transcript just because the probe failed.

**Diagnostic #3 (make any remaining failure obvious):** when ffmpeg extraction *does*
still fail, the log line now appends `ffprobe streams: <layout>` —
`gemini_pipe._ffprobe_streams()` lists each stream's `codec_type`/`codec_name` — so a
future failure shows immediately whether the file has audio and in what codec, instead
of only an opaque `CalledProcessError`.

**Why #2 is the fix and #1/#3 are the net, not the other way round:** working around
missing audio downstream alone would have masked the real problem — every affected reel
would silently become caption-only forever, losing a transcript it *could* have had.
Fixing the format string means those reels now download *with* audio and get a real
transcript; #1 only ever triggers for videos that truly have no audio track at all
(some screen-recordings, silent clips), and #3 exists so the next surprise is
diagnosable in one log line.

---

## Timing bug: ffmpeg reading a truncated file — real mechanism found and fixed

**The evidence:** ffmpeg's `CalledProcessError` fired at the exact Render-log moment
a download's progress showed 63.9%, with that same download's progress continuing to
100% afterward. This looked like ffmpeg reading a still-being-written file. Mocked
tests only, 336 tests passing.

**What I checked first, and ruled out:** `app/fetcher.py`'s `_run_ytdlp` — read the
actual code, not assumed. It registers no `progress_hooks`, no `postprocessors`, spawns
no thread, awaits nothing. `yt_dlp.YoutubeDL.extract_info(url, download=True)` is a
synchronous, blocking call with this exact `opts` dict — there is no mechanism inside
`_run_ytdlp` that could hand control back to the caller before the file is completely
written. (A structural test now asserts this directly — `test_run_ytdlp_registers_
no_hooks_or_threading` in `tests/test_fetch_hardening.py` — not just a comment claim.)

**The real mechanism:** `fetch_reel` had **no lock**. FastAPI's `BackgroundTasks` runs
sync functions (like `run_pipeline`) in a threadpool, so two overlapping `/capture`
calls — or a `/retry` landing while a fresh capture was still processing — could run
`fetch_reel` **concurrently in two different threads**. Nothing enforced the intended
"≥20s spacing" safety rule against a second *concurrent* call, only against sequential
ones sharing a single thread — `_enforce_rate_discipline`'s check-then-act on
`get_last_fetch_at()` has a real TOCTOU gap with no lock guarding it.

Two concurrent yt-dlp/ffmpeg runs write their log lines into Render's **single combined
log stream**, interleaved. That's what made it look like ffmpeg was reading THIS reel's
still-downloading file: the "63.9%... ffmpeg error... 100%" sequence most likely
belonged to **two different, unrelated reels** being processed at the same time, not
one reel's download racing its own audio extraction. `_extract_audio` is only ever
called from `gemini_pipe.run_extraction`, which only runs *after* `fetch_reel` has
already returned within the *same* thread — there's no path for one reel's own
ffmpeg call to run before its own download finished. The confusion was across reels,
not within one.

**Fixed:** `_FETCH_LOCK` (a `threading.Lock`) now wraps the entire `fetch_reel` body
(rate-discipline check through the final fallback, including its backoff sleeps) —
this is a deliberate, full serialization: at <25 fetches/day this costs nothing, and
it's exactly the guarantee the burner-account safety rule already assumed existed.
`test_fetch_reel_serializes_concurrent_calls` proves three concurrent calls' internal
intervals never overlap.

**Defensive check added regardless (per instruction — never rely on the workaround
alone):** `fetcher._expected_download_size()` reads yt-dlp's own reported file size
(`requested_downloads[0].filesize`/`filesize_approx`, falling back to the top-level
info dict) and threads it through a new `ReelData.expected_video_size` field.
`gemini_pipe._check_video_file_size()` compares the actual file on disk against it
**before** `_extract_audio` is ever called — a truncated/missing file now degrades
with an explicit, actionable log line (`"...looks truncated: N bytes on disk vs M
expected..."`) instead of surfacing as an opaque `CalledProcessError`. This is a
safety net for ANY future truncation cause (disk issues, a container restart
mid-download), not a substitute for the lock fix above.

---

## All four scheduled jobs now genuinely wired (supersedes the "not automatic yet" note below)

The entry directly below this one ("Daily reflection digest added") said the daily
digest workflow file didn't exist yet and you'd need to add it yourself. **That's no
longer true** — all four GitHub Actions workflow files are now committed:
`.github/workflows/{keepalive,nightly,daily-digest,weekly-digest}.yml`. Mocked tests
only, 323 tests passing.

**What changed:**
- Added `POST /weekly-digest` (secret-protected, mirrors `/nightly` and
  `/daily-digest` exactly) — the weekly digest's own docstring always claimed it'd be
  "scheduled the same way as nightly," but until now that was never actually built.
  It is now.
- Committed all four workflow files — previously only `nightly.yml`'s and
  `daily-digest.yml`'s YAML existed as copy-paste blocks *inside* SCHEDULING.md, never
  as real files in `.github/workflows/`.
- `keepalive.yml` deliberately deviates slightly from SCHEDULING.md's old
  Option-B snippet: instead of a hardcoded placeholder URL baked into the committed
  file, it uses the same `RENDER_URL` repo secret as the other three — one less place
  to remember to edit, and consistent with everything else.
- Picked **Sunday 21:00 IST (15:30 UTC)** for the new weekly-digest workflow — a
  natural week-wrap-up slot, offset 30 minutes from the daily digest's 21:30 IST so
  the two don't land in the same Actions-runner minute on the Sunday they both fire.

**⚠️ The one remaining manual step, same as before, just smaller:** add the two
repository secrets once — `RENDER_URL` and `CAPTURE_SECRET` — under **Settings →
Secrets and variables → Actions**. Every workflow's `curl` step fails without them;
nothing else needs adding. SCHEDULING.md has the full table of what fires when.

---

## Daily reflection digest added — here's exactly when/where to look for it

New: a daily digest alongside the existing weekly one (`app/digest.py`'s `run_daily()`),
grouped by Priority (High first), with a synthesis line up top ("X reels saved today,
Y flagged High priority, common themes: ..."). Mocked tests only, 320 tests passing.

**⏰ When it fires, once you add the GitHub Actions workflow (see below):**
`.github/workflows/daily-digest.yml`'s cron is `0 16 * * *` = **16:00 UTC = 21:30 IST,
every evening.** GitHub's schedule is best-effort (can lag a few minutes on busy
runners) but that's the target time.

**📍 Where to look:**
- **Notion**, under your parent page: a new page titled
  **`🌙 Daily reflection — <today's date>`** (or **`🌙 Daily reflection (nothing saved)
  — <date>`** if nothing was captured that day — see the zero-saves decision below).
- **Your phone**, if `NTFY_TOPIC` is set: a push titled `ReelBrain: N saved today` (or
  `ReelBrain: nothing saved today`) — same ntfy topic as the cookie-health alert, no
  new setup needed if you already did that.

**⚠️ REQUIRED MANUAL SETUP before it actually fires — this is not automatic yet:**
add `.github/workflows/daily-digest.yml` yourself (exact YAML in SCHEDULING.md's new
"Daily reflection digest" section) using the same `RENDER_URL`/`CAPTURE_SECRET`
repository secrets nightly already uses. Until that file exists, the only way to run
it is manually: `python scripts/daily_digest.py`, or `POST /daily-digest` directly.

**Honest finding while investigating the trigger mechanism (asked to confirm this,
not assume it):** the weekly digest's own docstring claims it should be "scheduled
weekly... the same way as the nightly job," but that was never actually built —
there's no `/weekly-digest` endpoint and no `.github/workflows/*.yml` committed
anywhere (nightly's workflow file isn't committed either, per SCHEDULING.md's own
instructions — you add that one yourself too). Today, weekly digest is a
manual-only `python scripts/weekly_digest.py` run. The **daily** digest, by
contrast, gets a real `POST /daily-digest` endpoint (secret-protected, mirrors
`/nightly` exactly) so it CAN be automated the moment you add the workflow file.
Same pattern would work for weekly if you want that automated too — not done here
since it wasn't what was asked.

**Zero-saves choice (asked to note, not just decide silently):** sends a short
"Nothing saved today" note rather than skipping delivery entirely. Reasoning: a
day with nothing saved is itself informative (the habit lapsed, or you were busy),
and silently skipping would look identical to the job having failed or never
fired — you'd have no way to tell "nothing happened" from "it's broken" without
checking logs. A short note costs nothing and removes that ambiguity.

**Also new:** `store.get_saves_since_hours(hours=24)` — a dedicated, exactly-24-hour
window (separate from the weekly digest's `get_saves_since(days=N)`, not just
`days=1`, so the boundary is directly testable rather than an assumption).

---

## URGENT: silent extraction failures now logged — root cause still open

**Reported:** reels with a successfully-downloaded video (confirmed via Render's
own "[download] 100% of X MiB" log line) were landing in Notion as
degraded/caption-only saves — no Topics, flat `value_score=3`, caption used as
the title — with **zero error logged anywhere**. Suspected tied to tonight's
photo/carousel and Priority changes.

**Fixed immediately, deployed regardless of root cause (per instruction):**
every silent degradation point in `app/gemini_pipe.py`'s `run_extraction` and
`run_caption_only_extraction` now logs the actual exception before falling
back — `logger.exception(...)` with ffmpeg's real `stderr` for
`subprocess.CalledProcessError`, the real exception for a failed Gemini call,
and the validation error text for a schema-validation failure. Previously all
of these were caught and silently swallowed with no log line at all. 5 new
regression tests in `tests/test_gemini_pipe.py` assert the actual error text
lands in the log (via `caplog`) for each degrade path.

**Investigated whether tonight's changes caused this — inconclusive, reported
honestly rather than guessed:**
- Diffed tonight's three commits (photo/carousel `484b650`, Priority `429fe69`,
  comment-gate `04f4c99`) against the prior baseline. **`_extract_audio` and
  `run_extraction`'s entire try/except skeleton are byte-for-byte unchanged**
  by any of them — ffmpeg's invocation, args, and the surrounding exception
  handling are exactly what they were before tonight.
- The one real structural change in the call chain: `main.py`'s `run_pipeline`
  now routes `reel.is_photo_or_carousel=True` reels to the new
  `run_caption_only_extraction` instead of `run_extraction`. But
  `is_photo_or_carousel` is set **only** by `fetcher.py`, and only on a
  **failed** yt-dlp fetch — never on a successful download. A reel whose video
  genuinely downloaded (video_path set, `is_photo_or_carousel=False`) cannot be
  misrouted into the caption-only path by tonight's change; it still goes
  through the unchanged `run_extraction`.
- Most likely explanation: `run_extraction`'s silent `except
  subprocess.CalledProcessError` / `except Exception` catches were **already
  silent before tonight** — this specific bug isn't new, it just went
  unnoticed until now (it never crashes anything, it just produces a
  degraded-looking-but-plausible Notion row). Something is making ffmpeg or
  the Gemini call actually fail on these specific reels, and that "something"
  is still unknown — genuinely can't tell without live logs, which this
  mocked-only session can't produce.
- **The next real capture that degrades will now show the actual error in
  Render logs** — that's what will actually answer this. Report back what it
  says and I can fix the real cause directly instead of theorizing further.

---

## Cookie refresh automation removed — Chrome's App-Bound Encryption blocks it

The `scripts/refresh_cookies.py` tool from a previous pass (reading Chrome cookies via
`browser_cookie3` and pushing them to Render's API automatically) has been **deleted
entirely**, along with `requirements-local.txt`, its test file, the
`RENDER_API_KEY`/`RENDER_SERVICE_ID` entries in `.env.example`, and every mention of it
in COOKIES.md. This isn't a bug fix or a deferred improvement — it's confirmed
fundamentally unworkable on Windows: Chrome 127+'s **App-Bound Encryption** (mid-2024)
deliberately ties a profile's saved cookies to the specific app that encrypted them,
specifically to stop tools like `browser_cookie3` from reading a browser's cookie
store from outside the browser — the same technique infostealer malware uses. No
library workaround changes that; the realistic options are downgrading Chrome (not a
reasonable security tradeoff) or moving the burner account to Firefox (which doesn't
have this protection — a real option if this is ever worth revisiting, not attempted
now). COOKIES.md documents this reasoning directly and goes back to the manual
export → paste-into-Render-dashboard process as the only, permanent method.

**What actually solves the practical problem here isn't automating the refresh — it's
already-built and still fully in place: the cookie-health alert system** (`app/fetcher.py`'s
consecutive-auth-failure counter, `/health`'s `cookie_health` field, and `app/alerts.py`'s
daily Notion + optional ntfy.sh push). That's what answers the real question, "when do I
need to do this 2-minute manual step," so expiry is never discovered by noticing failed
captures days later. The manual export itself was never the bottleneck worth automating —
knowing *when* to do it was, and that part was solved before this cookie-refresh-tool
detour even started.

---

## Photo/carousel posts get a real summary instead of a bare caption placeholder

Photo/carousel posts (yt-dlp can never fetch these — video-only) landed with
just "no transcript/caption" placeholder text even when a real caption WAS
recoverable via the OG-tag fallback. Mocked tests only — no live calls. 295
tests passing.

**What changed:**
- `app/fetcher.py`: `_og_fallback_or_degrade` now tags `is_photo_or_carousel`
  (and sets a distinguishing `fetch_note`) even when the OG-tag scrape
  succeeds and recovers a caption — previously that tag only fired when the OG
  scrape ALSO failed, so a photo/carousel post with a recoverable caption was
  silently treated as an ordinary caption-only capture with no distinguishing
  status/note at all.
- `app/gemini_pipe.py`: new `run_caption_only_extraction()` — the SAME
  structured-output Gemini call as a normal video reel (`response_schema=
  Extraction`), just without any audio/video upload (`_call_gemini_text_only`,
  a new prompt template `prompts/extraction_caption_only.md` that doesn't
  frame the task as transcription since there's no audio at all). A caption
  under `MIN_CAPTION_WORDS_FOR_EXTRACTION` (10) words falls back to the
  existing honest placeholder rather than risking Gemini hallucinating content
  from almost nothing.
- `app/main.py`: `run_pipeline` branches to `run_caption_only_extraction` when
  `reel.is_photo_or_carousel`, instead of `run_extraction` — an addition for
  the fallback case only; `run_extraction` itself, and normal video reel
  processing, are completely untouched.
- Notion: these rows now get a real Title/Main Point, Topics, Value score, and
  Priority exactly like a video capture, plus the note "photo/carousel post —
  summarized from caption only, no video transcript available" so the row is
  honest about its source rather than indistinguishable from a full
  video-based save. Status stays `📷 Photo — manual` either way (unchanged) —
  only the underlying content richness changed.

---

## Priority system — computed field replacing decorative-only value_score

`value_score` existed but nothing acted on it. **Priority now drives real
action:** a new computed field, a Notion Select property, a dedicated
"go do this" view, and an Obsidian vault reorganized around it. Mocked tests
only — no live calls. 285 tests passing.

**What's new:**
- `app/gemini_pipe.py`: `compute_priority(topic_tags, value_score)` — `"High"`
  if a topic matches `CLAUDE_KEYWORDS` (`claude`, `claude-ai`, `claude-code`,
  `anthropic`, `claude-skills`, `mcp` — case-insensitive substring match, easy
  to extend) OR `value_score >= 4`; `"Medium"` if `value_score == 3`; `"Low"`
  otherwise. Plain text values only, computed at the same finalization point
  as `comment_gate` (both the success path and the degraded path), so it's
  never left at the model default.
- `app/models.py`: `Extraction.priority: Literal["High", "Medium", "Low"]`.
- `app/notion_writer.py`: writes a `"Priority"` Select property on every
  capture/retry, alongside the existing properties.
- **⚠️ NEEDS YOUR ACTION before this deploys:** run
  `python scripts/add_priority_property.py` once — Notion's API auto-creates
  new *option values* on an existing Select property, but not a brand-new
  *property* that doesn't exist in the schema yet. Without this, writes to
  the new "Priority" property will likely fail against your live database.
- **⚠️ NEEDS YOUR ACTION, manual, ~1 minute:** add the "🎯 Action Needed" view
  (Priority=High AND Status≠Archived, sorted Created time desc) — see the new
  section in `NOTION_VIEWS.md`. Notion's public API has no endpoint for
  creating views (confirmed precedent: the existing Triage/This Week views in
  the same file), so this is a UI-only step, same as those.
- Obsidian (`app/obsidian_sync.py`): reel notes now carry plain-text
  `Priority: High` / `Score: 4` lines in frontmatter and body (no emoji
  anywhere); topic/creator auto-generated listings show the same plain text
  (replacing the old "value N" phrasing); `_index.md` is restructured to
  group by Priority tier first (`## High Priority` / `## Medium Priority` /
  `## Low Priority`), each listing topics with a count — so opening the vault
  immediately shows what needs attention, not an alphabetical/by-volume dump.

**This does NOT retroactively fix existing rows.** Every save already in
Notion has no Priority value at all — Obsidian sync treats those as Low
Priority (so they don't vanish from the index), but their Notion rows have no
`Priority` property set until you `/retry` them or edit manually. **A
one-time backfill script (read existing Value score + Topics off every row,
compute and write Priority) would need writing separately if you want old
saves reclassified — let me know if you want that built.**

---

## Three live-testing bugs — fixed vs. needs your manual Notion correction

Three bugs reported from live testing. Mocked tests only in this pass — no live
Instagram/Notion/Gemini calls were made by Claude. Three commits, one per bug:
`34258de` (BUG 3), `04f4c99` (BUG 2), `84ba60a` (BUG 1).

### ✅ FIXED — BUG 3 (CRITICAL): `/attach` could attach to the wrong row
An explicit `shortcode_or_note` (e.g. `DZSFkNppVW_`) landed on an unrelated row
(`Dap3IoNo4Kt`) instead. Root cause: `find_pending_gate`'s exact-shortcode check
was scoped to rows already `Awaiting DM`; if the requested row wasn't in that
set (e.g. after an ephemeral-disk wipe), the exact match silently found nothing
and fell through to the "single remaining row" auto-pick meant only for an
*omitted* shortcode — substituting a completely different row. Fixed: exact
shortcode is now resolved across the full local table (any status), then
directly against Notion, before any substring/fallback logic runs. A shortcode
that exists but isn't pending now returns 404 — never a substitute. 6 new
regression tests prove the exact incident shape can't recur. **Nothing for you
to do** — this was a pure code bug, now closed.

### ✅ FIXED — BUG 2, part 2: emoji-drop-for-DM gates now detected
`Dap3IoNo4Kt` ("Drop your 🔥 emoji to grab all in ur dms") wasn't recognized as
a gate — no literal "comment" for the existing regex to match. Added a
narrowly-scoped pattern (verb "drop" + emoji-shaped token + literal "emoji" +
"dm" nearby) specifically to avoid false-positiving on ordinary "drop a fire
emoji if you agree" captions. **Nothing for you to do** for future captures;
already-processed rows with this phrasing won't retroactively update unless
you `/retry` them.

### ✅ FIXED — BUG 2, part 1: `comment_gate`/`gate_keyword` invariant enforced
`DajFASZODlj` (`gate_keyword="International"`, `comment_gate=False`) and
`DaQIJHnP6zn` (`gate_keyword="CODING"`, same mismatch) — Gemini's own model
output could set a keyword while independently leaving `detected=False`, and
when the regex also missed the caption's phrasing, nothing corrected it.
`_merge_comment_gate` now forces `detected=True` whenever a keyword is present,
with an assertion guaranteeing the two fields can never disagree again.

**⚠️ NEEDS YOUR MANUAL NOTION CORRECTION:** the two specific rows above still
have the stale, disagreeing values in Notion right now — this fix only
prevents new mismatches, it doesn't rewrite old rows. Either edit `DajFASZODlj`
and `DaQIJHnP6zn` directly in Notion (check the "Comment gate" box), or run
`POST /retry/DajFASZODlj` and `POST /retry/DaQIJHnP6zn` to regenerate them
under the fixed code.

**Investigated, no code bug found — the "`__YES__`/`__NO__` string" report:**
the live "Comment gate" property is correctly declared as a Checkbox
(`scripts/setup_notion.py`), and the write code has always sent a real Python
boolean (`{"checkbox": extraction.comment_gate.detected}` in
`notion_writer.py`) — no such string literal appears anywhere in this
codebase's current code or git history. **This needs you to check directly in
Notion**: open the "Comment gate" column header → "Edit property" and confirm
it still says "Checkbox". If it does, and you're still seeing literal
`__YES__`/`__NO__` text somewhere, tell me exactly which row/view you're
looking at — my best guess is these are stale values from a much earlier
iteration of the pipeline (predating this session's codebase), or you're
looking at a different property/view than "Comment gate" itself.

### ⚠️ ALREADY FIXED, PRE-DATES THIS SESSION — BUG 1: photo/carousel vanish
`DaNiWoBzdja` ("No video formats found") reportedly vanished entirely. Verified
the existing fix (commit `ef2cbf0`, an earlier session) is fully intact:
`fetcher.py` correctly classifies this error on both the immediate-failure and
backoff-exhausted paths, and `main.py`'s `run_pipeline` checks
`is_photo_or_carousel` before any other status logic, always writing a
"📷 Photo — manual" row. This was already covered by an end-to-end test; added
one more tied to the literal reported shortcode for direct confirmation. **No
code change was needed. If `DaNiWoBzdja` is still missing from Notion**, that
capture most likely ran before `ef2cbf0` was deployed — run
`POST /retry/DaNiWoBzdja` to regenerate it under the current code.

### 📋 Gate-resource audit — report only, nothing changed in Notion
Built `scripts/audit_gate_resources.py` — a **read-only** script (run it
yourself with your own Notion credentials; Claude made no live calls this
session) that lists every Saves row's Gate resource URL next to its
title/topics for a manual eyeball-check, plus automated checks for
`gate_keyword`-without-`comment_gate`, `comment_gate`-without-`gate_keyword`,
and a `Gate resource` set while `Status` is still `Awaiting DM`. Run:
```
python scripts/audit_gate_resources.py
```
Share the output (or the specific mismatches you spot) and I can help
interpret/fix from there — I won't write anything to Notion without you
confirming first.

---

## URGENT SAFETY FIX — /attach refuses to guess instead of best-guessing

**Tonight's real incident:** with many rows simultaneously `Awaiting DM`, `/attach`'s
note/title substring fallback was too loose and attached a DM'd resource to the WRONG
pending entry. **This replaces the previous "best guess" behavior with "refuse to
guess" across every fallback path in `find_pending_gate`.** Nothing here silently picks
a candidate anymore unless there is genuinely only one possible match.

**The rule, everywhere in `store.find_pending_gate` and its Notion-fallback twin
`_find_pending_gate_via_notion`:**
- An **exact shortcode match** is always safe — shortcode is a primary key, so it can
  never be ambiguous — and always wins outright over any substring match.
- A **substring match** (against note OR title — title-matching locally is new: it's
  derived from `extraction_json`'s `main_point`, since SQLite has no separate title
  column) that hits **2+** rows now raises `store.AmbiguousGateMatch` instead of
  returning the first/most-recent one.
- The **omitted-shortcode fallback** ("just attach to whatever's pending") now only
  auto-picks when there is **exactly one** row `Awaiting DM`. 2+ rows → same exception.
- `/attach` (`app/main.py`) catches `AmbiguousGateMatch` and returns **HTTP 409** with
  `{"detail": {"message": ..., "candidates": [...]}}` — every matching shortcode, so
  the caller (the iOS Shortcut, or you by hand) retries with the exact one instead of
  the ambiguous word. A 409 has **no side effects** — every candidate row is left
  exactly as it was.

**Six new direct unit tests in `tests/test_store.py`** against `store.find_pending_gate`
itself (the two the task named exactly — 3 rows with omitted shortcode, and 2 rows
sharing a word in their note — plus the title-substring equivalent, proof that an exact
shortcode match is never ambiguous no matter how many other rows exist, proof that a
substring match unique among many unrelated rows still safely auto-picks — ambiguity is
about the **match** count, not the row count — and the single-row auto-pick case).
**Plus 5 endpoint-level tests** in `tests/test_attach_endpoint.py` and
`tests/test_coverage_gaps.py` proving the same guarantees through the actual HTTP
`/attach` call (409 status code, `candidates` list, zero side effects on the rows), and
2 more in `tests/test_notion_fallback.py` covering the same rule in the Notion-fallback
path. Updated 3 pre-existing tests that had explicitly asserted the OLD "silently pick
most-recent" behavior — that was the exact bug, so they were updated to assert the new
409 instead, not reverted. **Full suite: 257 passed** (was 246). No live calls.

**Also updated:** README's "Attach to ReelBrain" Shortcut section now documents the 409
response shape and explains why it exists, so future-you (or anyone reading the setup
doc) understands this isn't a bug when it happens.

---

## Known limitations (plain English)

Three things worth knowing about how this app behaves day-to-day — none of these are
bugs, they're tradeoffs of the ₹0/free-tier constraint, documented so they're not a
surprise later.

**(a) Photo and carousel posts capture the URL only, no transcript.** yt-dlp only
understands video, so a photo or carousel post always fails to fetch — that's expected,
not broken. Normally the app falls back to reading the post's public preview data
(Open Graph tags) to still get a caption; but from Render's datacenter IP, Instagram
often blocks that anonymous read too. When both fail, the reel still gets captured — it
lands in Notion as **📷 Photo — manual**, with the link saved and a note explaining
there's no auto-summary. You'll need to open the link yourself to see what it is.

**(b) The burner account's login cookies need periodic manual refresh — this is not
automated on purpose.** Instagram sessions expire; when that happens, fetches start
failing with an auth-type error. The app never tries to log back in by itself (that
would mean storing a real password and risking the burner account being flagged). You
get a **cookie-health alert** (a Notion page, and a phone push if you set up ntfy.sh)
after 3 consecutive auth failures. Fix is a 2-minute manual step — see COOKIES.md.

**(c) Render's free tier wipes the local database on every redeploy — Notion is the
real source of truth, not SQLite.** The local database exists to make things faster
(fewer Notion round-trips) and to power dedupe detection ("have I saved this already?")
and the intelligence layer (embeddings, related-saves, tag suggestions). When it gets
wiped: dedupe detection resets (re-sharing an old link may re-process it as new),
related-saves/tag-suggestion history resets, but **nothing captured is ever lost** —
every save's actual content lives in Notion. `/attach` and `/retry` specifically fall
back to reading Notion directly if the local database doesn't have what they need
(see below), so those two commonly-used actions keep working across a redeploy even
before the database catches back up.

---

## Fix 2 — verified: the ephemeral-SQLite Notion fallback is correctly deployed

Re-read `store.py`'s `find_pending_gate`/`get_by_shortcode_or_notion` and `main.py`'s
`/retry`/`/attach` line by line, as asked, rather than assuming last session's fix was
intact. It is — fully applied, nothing missing or half-wired:

- `POST /retry/{shortcode}` (`app/main.py`) calls `store.get_by_shortcode_or_notion`,
  not the plain `get_by_shortcode` — confirmed at the call site.
- `POST /attach` calls `store.find_pending_gate`, which itself falls back to
  `_find_pending_gate_via_notion` once every local attempt (exact shortcode, note
  substring, most-recent awaiting_dm) comes up empty — confirmed the fallback branch
  is actually reached at the end of the function, not just present but unreachable.
- Both fallbacks route through `_persist_notion_page` → `upsert_from_notion`, so a
  Notion-recovered row is written back to SQLite as a real row, not a fake stand-in.
- All 25 existing tests in `tests/test_notion_fallback.py` still pass unmodified,
  including the exact "SQLite miss → Notion hit" case for both endpoints and the
  "both miss → still a real 404" case for both — re-ran them fresh rather than
  trusting a memory of them passing before.

**No code changes were needed for this fix** — it shipped correctly in commit `8ef0730`
from the previous session. No separate commit for this entry; it's documented here
alongside the "known limitations" section as this session's verification record.

**One related gap noticed while re-reading, out of scope for this task (not fixed,
flagging only):** `POST /capture`'s duplicate-detection check (`store.get_by_shortcode`,
`app/main.py` line ~282) does NOT have the same Notion fallback. If SQLite gets wiped
and someone re-shares a URL that was already captured before the wipe, `/capture` won't
recognize it as a duplicate locally and will re-process it — potentially creating a
second Notion page for the same reel. Lower-stakes than `/attach`/`/retry` failing
outright (worst case is a duplicate page, not a lost/broken capture), but worth a
follow-up if it turns out to matter in practice.

---

## Fix 1 — photo/carousel posts no longer silently vanish

**Root cause, found via testing (not assumed):** yt-dlp is video-only, so a photo or
carousel post always fails with "no video formats found" — expected, not a bug. The
OG-tag fallback exists for exactly this, but Instagram login-walls the anonymous HTML
scrape from Render's datacenter IP, so it returns nothing there. Architecturally this
already produced a Notion row (`⚠️ Failed — retry`, via the existing `FetchDegraded` →
`run_pipeline` catch path) rather than a true silent drop — but "Failed — retry" is
actively misleading for a photo post: retrying can never succeed, since it's
structurally not a video. That's the real bug: a row stuck in a status implying
"try again" that will never self-resolve, functionally indistinguishable from vanished.

**Found while testing, a second real bug:** `fetch_reel`'s final fallback (after the
cookie-backed retry loop exhausts `BACKOFF_SECONDS`) discards the actual yt-dlp error
and substitutes a generic "repeated challenges... refresh burner cookies" string. Since
a genuine photo/carousel post reports "no video formats found" identically on *every*
retry attempt (cookies don't change what kind of post it is), this generic string was
the ONLY reason ever reaching `_og_fallback_or_degrade` for the realistic case — my
photo/carousel detection would never have fired against real traffic without this fix.
Now the last real exception's text rides along inside the generic message.

**The fix:**
- `app/models.py`: `ReelData` gains `is_photo_or_carousel: bool` and `fetch_note:
  Optional[str]` — a fetcher-supplied annotation surfaced on the Notion row's My note
  regardless of success/failure (generalizing the existing failure-reason mechanism).
- `app/fetcher.py`: new `PHOTO_OR_CAROUSEL_MARKERS = ("no video formats found",)` and
  `_is_photo_or_carousel()`. When the OG-tag fallback ALSO fails and the original error
  matches this signature, `_og_fallback_or_degrade` returns a URL-only `ReelData`
  (`is_photo_or_carousel=True`) instead of raising `FetchDegraded`. Any OTHER kind of
  total failure still raises as before — those may genuinely be worth a human retry,
  unlike a post that will never become fetchable no matter how many times you try.
- `app/notion_writer.py`: new status `"photo_manual"` → `"📷 Photo — manual"`. Auto-
  creates as a Notion select option on first write, same as the earlier `archived`
  addition — no manual schema migration needed.
- `app/main.py`: `run_pipeline`'s status decision checks `reel.is_photo_or_carousel`
  first, ahead of the comment-gate/value-score logic (harmless either way here, since a
  photo/carousel `ReelData` never has a caption for the gate regex to match — but
  semantically the most specific classification should win). `notion_note` now folds in
  `reel.fetch_note` alongside the existing `failure_reason`, so the row lands with
  `⚠️ photo/carousel post — no auto-transcript, open the reel URL to view` appended
  after the user's own note, never replacing it.

**Tests:** 8 new in `tests/test_fetch_hardening.py` (marker matching, the fetch-level
fix in isolation, a regression guard proving an unrelated hard failure with a failed OG
scrape still raises `FetchDegraded` as before, and `fetch_reel` end-to-end never
raising for the realistic repeated-"no video formats found" case) plus one full
pipeline-level integration test in `tests/test_pipeline.py` — running the REAL
`fetcher.fetch_reel` (only its yt-dlp/OG internals mocked, not the whole function) through
`run_pipeline`, asserting a genuine Notion page gets created with `Status = 📷 Photo —
manual`, the correct `Reel URL`, the user's original note preserved plus the appended
explanation, and a local SQLite row — this is the task's own literal ask: "a 'No video
formats found' error with a failed OG fallback still yields a persisted row with the
permalink, not a dropped capture." Two pre-existing tests (`test_cookie_retry_uses_
resolved_path`, `test_non_auth_challenge_does_not_increment_counter`) used "no video
formats found" purely as an arbitrary example CHALLENGE_MARKER for unrelated
assertions (cookie-path resolution, auth-counter behavior) — swapped their example
error strings to non-photo markers so each test still isolates the thing it's actually
about. **Full suite: 246 passed** (was 238). No live calls.

---

## /attach and /retry survive ephemeral-disk wipes (Notion fallback)

**Fixes the specific failure mode we hit twice today: `DajFASZODlj` on `/retry` and
`DZSFkNppVW_` on `/attach`, both 404/"no matching entry" purely because Render's
free-tier ephemeral disk got wiped on redeploy and took local SQLite with it — while
the actual Notion page (the real source of truth for everything else in this system)
was sitting there the whole time.**

**1. `app/notion_writer.py` — new read-back helpers** (this file previously only
*wrote* to Notion; reading properties back needed new code):
- `_rt_text()`: reads a rich_text/title array back to plain text (mirrors
  `obsidian_sync.py`'s reader of the same API shape, kept separate rather than
  cross-imported since obsidian_sync is a local-only vault-sync module).
- `extract_saves_fields(page)`: pulls shortcode, permalink, note, status label, gate
  keyword, title, page id/url out of a raw Saves-DB page object.
- `status_label_from_notion(label)`: reverse of `STATUS_LABELS`; unmapped (the
  manual-only `✅ Processed/Reviewed`) returns `None`.
- `find_page_by_shortcode(shortcode)`: exact match, for `/retry`.
- `find_awaiting_dm_pages()`: every `⏳ Awaiting DM` page, most-recently-edited
  first, for `/attach`.

**2. `app/store.py` — the fallback logic + persistence:**
- `upsert_from_notion()`: the "re-insert as a byproduct" step — `INSERT ... ON
  CONFLICT(shortcode) DO UPDATE`, so it works whether the local row is genuinely
  absent or (rare race) present under a different status, and never clobbers an
  existing note (`COALESCE`). Returns a real `sqlite3.Row` via a fresh SELECT — no
  fake row-like object needed, subsequent code just sees a normal local row.
- `get_by_shortcode_or_notion()`: `get_by_shortcode`, then on a miss queries
  `find_page_by_shortcode` and persists what it finds. Used by `/retry`.
- `find_pending_gate()`: after ALL local attempts miss (exact shortcode, note
  substring, most-recent-awaiting_dm), now falls back to
  `_find_pending_gate_via_notion()`, which applies the **identical priority order**
  against Notion's awaiting-DM pages: exact shortcode → note substring → title
  substring (an addition — the task asked for Title-or-My-note; local search only
  ever checked note, so Notion fallback is slightly more capable here) → most
  recent. Used by `/attach`.
- Both fallbacks catch any Notion error and return `None` rather than raising —
  a Notion outage degrades to the pre-existing 404, it doesn't crash the endpoint.

**3. `app/main.py`:** `/retry` swapped `store.get_by_shortcode` for
`store.get_by_shortcode_or_notion`. `/attach` needed no change — it already calls
`store.find_pending_gate`, which now carries the fallback internally.

**What only got backfilled, not fully reconstructed:** the fallback-recovered row is
deliberately minimal — shortcode, permalink, note, status, notion_page_id/url, gate
keyword. Fields like `creator`, `transcript`, `extraction_json`, `taken_at` stay
`NULL` until a real pipeline run backfills them (which `/retry` triggers
immediately; `/attach` doesn't need them at all for what it does). Good enough for
both endpoints' actual needs — confirmed by reading exactly what each one accesses
off `row` before writing any reconstruction code, per the task's own instruction.

**Tests:** 25 new in `tests/test_notion_fallback.py` — notion_writer's readers
(`_rt_text` both shapes, status mapping known/unmapped, field extraction complete
and with missing-optional-properties, both query functions' exact filter/sort
shapes); store.py's fallback (upsert creates + round-trips, ON CONFLICT preserves an
existing note, local-hit short-circuits before ever touching Notion — proved with a
`data_sources.query` that raises if called at all — fallback-hit persists and a
*second* lookup then also short-circuits, both-miss returns `None`, Notion errors
degrade to `None` rather than raising, and `find_pending_gate`'s full priority chain:
shortcode / note / title / most-recent, each independently); and the two endpoints
end-to-end through `TestClient` reproducing the exact failure modes named above —
`/retry` and `/attach` both succeeding via the Notion fallback with a mocked pipeline,
both still correctly 404ing when Notion has nothing either, and a round-trip check
that the locally-persisted row after a fallback `/attach` is indistinguishable from
what a normal local hit would have produced. **Full suite: 238 passed** (was 213).
No live Notion calls anywhere — every test drives a fake `data_sources.query`.

---

## Cookie-expiry monitoring + alerting

**Goal:** stop discovering expired burner cookies by noticing failed captures.
Detection + notification only — explicitly no auto-login/refresh (that would mean
storing a real password and risking the burner account).

**1. Failure signal (`app/fetcher.py`, `app/store.py`):**
- New `app_state` KV table in SQLite (`store.get_state`/`set_state`) — a generic small
  persistent-value store, reused by the alert dedup below too.
- `AUTH_FAILURE_MARKERS`: a deliberate SUBSET of the existing `CHALLENGE_MARKERS` —
  `login required`, `empty media response`, `challenge`, `checkpoint`, `use --cookies`.
  Excluded on purpose: `no video formats found` / `429` / `rate-limit` / `requested
  content is not available` — those happen for reasons unrelated to cookie validity
  (a private/deleted video, a passing rate limit) and would make the counter noisy.
- Only checked on the **cookie-backed** retry attempts in `fetch_reel`, never the
  initial anonymous one — an anonymous attempt hitting "login required" is expected
  and says nothing about whether *our* cookies are still good.
- `record_cookie_auth_failure()` / `record_cookie_auth_success()` /
  `get_consecutive_auth_failures()`: increment on a cookie-backed auth-type failure,
  reset to 0 on any successful cookie-backed fetch. Persisted (survives a Render
  restart mid-degradation, unlike in-process state).

**2. `/health` (`app/main.py`):** new `cookie_health` field, `"ok"` / `"degraded"` at
`AUTH_FAILURE_THRESHOLD` (default 3, `COOKIE_HEALTH_THRESHOLD` env var) consecutive
cookie-backed auth failures. Distinct from the existing `cookies_file` field — a file
can exist and still hold expired cookies.

**3. Alerting (`app/alerts.py`, new module, wired into `nightly.run()`):**
- **Notion:** a distinctly-titled `⚙️ System Alert — cookies likely expired — <date>`
  page created directly under `NOTION_PARENT_PAGE_ID` (same pattern as the weekly
  digest — no new database needed for a "dedicated area"). Wired up unconditionally
  per the task's "at minimum."
- **ntfy.sh:** recommended as the simpler channel — genuinely zero-config (no account,
  no signup, one `httpx.post` to a topic URL), and unlike Notion it's an actual push to
  the phone rather than something you have to go open. Wired up too since it was easy,
  gated behind an optional `NTFY_TOPIC` env var (blank = skipped, no error).
- Both are best-effort: any failure is caught and logged, never raised — a Notion or
  ntfy outage must not block or crash the nightly job.
- **Dedup:** at most one alert per calendar day while degraded (`app_state` again),
  so a nightly job re-run (manual `/nightly` POST, GH Actions retry) doesn't spam
  either channel — but a still-broken cookie file gets a fresh nudge the next day.
- `render.yaml` gained `NOTION_PARENT_PAGE_ID` (was actually missing before — the
  weekly digest needed it too and had no way to configure it via the Blueprint; fixed
  in passing) and the two new vars.

**4. `COOKIES.md`:** the 2-minute manual runbook — browser login as the burner →
export cookies.txt → paste into Render's Secret File → Render auto-restarts on Secret
File change (no redeploy needed) → verify via `/health`. Plus ntfy.sh setup steps and
a short "how the detection actually works" section for context.

**Tests:** 32 new — `tests/test_cookie_health.py` (counter mechanics, persistence via
`app_state`, ok/degraded at custom thresholds, auth-vs-non-auth marker classification
including the two cases that matter most: "no video formats found" is a challenge but
NOT an auth failure, and a hard error unrelated to auth touches neither; four
integration tests against the real `fetch_reel` retry loop — success resets, cookie-
backed auth failure increments, non-auth challenge doesn't touch the counter, anonymous-
only failure never reaches the counter at all) and `tests/test_alerts.py` (no alert
when healthy, alert fires when degraded, once-per-day dedup and reset on a new day,
Notion page shape, Notion failure doesn't block dedup or crash, ntfy request shape,
ntfy skip without a topic, ntfy network-error survival, both channels firing together).
Plus 2 new `/health` tests and fixes to 3 existing nightly-response-shape assertions
that needed the new `cookie_alert` key. **Full suite: 213 passed** (was 179). No live
calls — conftest's existing `httpx.post`/`httpx.get` blocks cover the new ntfy path too.

**For your review:** the `AUTH_FAILURE_MARKERS` subset (which errors count as "cookies
are the problem" vs. generic/unrelated) is my judgment call, documented inline in
`fetcher.py` — worth a skim if your real-world failure messages don't match what I
guessed. Also: pick an actually-unguessable `NTFY_TOPIC` if you turn it on — anyone who
knows the topic name can post to or read it, per ntfy's whole no-account design.

---

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
