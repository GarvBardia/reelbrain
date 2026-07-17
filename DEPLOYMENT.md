# DEPLOYMENT.md — Render free-tier setup, click by click

The repo ships a `render.yaml` Blueprint, so most of this is automated. Two paths below —
Blueprint (recommended) or manual. Either way, read the **SQLite persistence** section at
the bottom before your first real capture: it affects whether your local database survives
deploys.

## Prerequisites

- The repo pushed to GitHub (Render deploys from a connected repo).
- Your `.env` values handy locally — you'll paste the secret ones into Render's dashboard.
- `cookies.txt` (burner account) — see "Burner cookies on Render" below; do NOT commit it.

## Path A — Blueprint deploy (recommended)

1. Go to https://dashboard.render.com → **New** → **Blueprint**.
2. Click **Connect a repository** and pick the ReelBrain repo (authorize GitHub access if
   prompted). Render detects `render.yaml` automatically.
3. Render shows the `reelbrain` web service it's about to create, with a list of env vars.
   The ones marked `sync: false` in the yaml will prompt you for values right here:
   - `GEMINI_API_KEY` — from aistudio.google.com → "Get API key".
   - `NOTION_TOKEN` — from notion.so/my-integrations → your integration → "Internal
     Integration Secret".
   - `NOTION_DB_ID` / `NOTION_CREATORS_DB_ID` — the two IDs `scripts/setup_notion.py`
     printed when you created the databases.
   - `NOTION_PARENT_PAGE_ID` — the same parent page ID from setup. Needed at runtime too,
     not just for the one-time setup script: the weekly digest and cookie-health alert
     both create pages directly under it.
   - `BURNER_ACCOUNT_USERNAME` — the burner IG account's username.
   - `REAL_ACCOUNT_GUARD` — your REAL IG username (the safety interlock; the app refuses
     to fetch if it ever matches the burner name).
   - `CAPTURE_SECRET` — the same string your iOS Shortcut sends.
   - `NTFY_TOPIC` — optional; an unguessable topic name for a free ntfy.sh push
     notification when cookies expire. See COOKIES.md. Leave blank to skip — the Notion
     alert fires regardless.
4. Click **Apply**. First build takes a few minutes.
5. When it's live, open `https://<your-service>.onrender.com/health` — you should see
   `{"status": "ok", "sqlite_vec": true, "cookie_health": "ok", ...}`. If `sqlite_vec` is
   `false`, embeddings are disabled but everything else still works (see README Phase 3
   notes). If `cookie_health` ever flips to `"degraded"`, see COOKIES.md.

## Path B — manual (no Blueprint)

1. Dashboard → **New** → **Web Service** → connect the repo.
2. Settings:
   - **Runtime:** Python
   - **Build command:** `pip install -r requirements.txt`
   - **Start command:** `uvicorn app.main:app --host 0.0.0.0 --port $PORT`
   - **Plan:** Free
   - **Health check path:** `/health`
3. Under **Environment** → **Add Environment Variable**, add every var from
   `.env.example`, pasting the same secret values listed in Path A step 3.
4. **Create Web Service** and wait for the first deploy, then check `/health` as above.

## Burner cookies on Render

`cookies.txt` must not be committed to the repo. Options, best first:

1. **Render "Secret Files"** (Environment tab → Secret Files): create a secret file named
   `cookies.txt` with the file's contents. Render mounts Secret Files at
   **`/etc/secrets/<filename>`**, which `app/fetcher.py` checks automatically as a
   fallback — so you do NOT need to change `BURNER_COOKIES_FILE`. This is the way.
2. Base64 the file into an env var and write it out in a start wrapper — works but ugly;
   only if Secret Files is unavailable for some reason.

**Cookie resolution order** (`fetcher.resolve_cookies_file`): `BURNER_COOKIES_FILE`
(default `./cookies.txt`, i.e. local dev) → `/etc/secrets/cookies.txt` (Render). The
startup log line says which one this process picked (`burner cookies: using <path>`).

**No cookies = fail fast, deliberately.** If neither path exists, fetches fail immediately
with `cookies file not found at ... or ...` and the row lands in `⚠️ Failed — retry` with
that reason written onto the Notion page's **My note**. This is on purpose: an anonymous
fetch from a datacenter IP gets soft-blocked by Instagram and returns an inscrutable
"no video formats found", while burning a slot against the 25/day cap. A missing cookie
file is a deploy problem, and it should say so.

**Verify from a browser:** `/health` reports `"cookies_file": true|false` — no log access
needed.

When IG challenges get frequent, refresh the burner cookies locally (cookies.txt browser
extension) and update the Secret File — no redeploy of code needed, just a service restart.

## When Instagram fetches start failing: bump yt-dlp first

**yt-dlp is a cat-and-mouse dependency.** Instagram changes its internals regularly and
yt-dlp ships fixes within days; a pin that worked last month can break with no code change
on our side. It's pinned in `requirements.txt` for reproducibility, which means it goes
stale on purpose — bumping it is a routine maintenance chore, not an emergency.

Symptoms that mean "bump yt-dlp before debugging anything else":
`No video formats found`, `empty media response ... use --cookies`,
`requested content is not available`, or any extractor error naming Instagram.

```bash
py -m pip index versions yt-dlp     # find the newest release
# bump the pin in requirements.txt, then:
py -m pip install -r requirements.txt
py -m pytest                        # mocked suite — proves nothing broke structurally
python scripts/smoke.py <reel_url>  # the real check: one live reel
```

Then commit the pin bump and let Render redeploy. If a fresh yt-dlp *and* valid cookies
still fail only in production, that's the datacenter-IP block — see BUILD_SPEC.md §1.2 for
the fallback options (paid data-API provider, or a laptop-side fetch worker).

Note the pipeline no longer fails the row outright when yt-dlp dies: it falls back to
reading the reel page's public Open Graph tags (one anonymous request, never cookies) and
continues caption-only, so the comment-gate keyword and topic tags still land. You'll see
those rows in Notion with a transcript toggle reading `(unavailable)`.

## sqlite-vec on Render (the `enable_load_extension` fix)

On some Linux Python builds — including what Render provisions — the standard library's
`sqlite3` is compiled **without** loadable-extension support. When that's the case,
`sqlite3.Connection` has no `enable_load_extension` method, and loading sqlite-vec fails
with:

```
AttributeError: 'sqlite3.Connection' object has no attribute 'enable_load_extension'
```

This works fine on local Windows/macOS dev (their `sqlite3` is built with the support),
so it only ever bites in production. The fix, already in the repo:

- `requirements.txt` includes `pysqlite3-binary` (Linux-only via a `sys_platform == "linux"`
  marker — there are no Windows wheels, and local dev doesn't need it). It bundles a SQLite
  compiled **with** extension loading.
- `app/store.py` detects the missing capability (`hasattr(sqlite3.Connection,
  "enable_load_extension")`) and, only then, swaps in `pysqlite3.dbapi2` as a drop-in
  `sqlite3` replacement. On dev where the stdlib already works, this is a no-op.
- If even `pysqlite3-binary` isn't present/usable, the existing graceful-degrade path
  still applies: the app logs `sqlite-vec unavailable` and runs fine without embeddings —
  captures, extraction, and Notion writes are unaffected; only near-dup/related-saves are
  disabled. Embeddings are never a hard dependency.

**Verify after deploy:** hit `/health` — `sqlite_vec` should now be `true`. If it's still
`false`, check the build log for whether the `pysqlite3-binary` wheel installed, and the
runtime log for the `sqlite-vec unavailable` warning's traceback.

**Plan tier:** this needs no paid plan or special Render setting — `pysqlite3-binary`
carries its own SQLite and `sqlite-vec` ships the extension as a normal in-process shared
library the app loads from its own site-packages (not a system `.so`, not a syscall Render
sandboxes). It should work on the free tier. That said, it has **not** been verified on a
live Render deploy — see the caveat the assistant flagged when landing this.

## SQLite persistence — read this before relying on the deploy

**Render's free tier has an ephemeral disk.** Every deploy, restart, or free-tier instance
recycle wipes the filesystem — which means `data/reelbrain.db` is lost. What that actually
costs you, and your options:

**What's lost when the DB resets:**
- **Dedupe memory** — a re-shared reel gets re-processed as new (duplicate Notion page).
- **Embeddings** — near-dup/related-saves links only consider reels saved since the reset.
- **Tag taxonomy** — the top-40 tag candidates reset, so tag convergence starts over.
- **Daily fetch counter** — resets, which slightly weakens the 25/day rate cap.
- **NOT lost:** the actual knowledge base. Every save's full content lives in Notion,
  which is the real source of truth. A DB wipe costs intelligence-layer continuity, not data.

**Option 1 — Render persistent disk (paid add-on, ~$0.25/GB/mo, 1GB min).** Attach a disk
at e.g. `/data` and set `DB_PATH=/data/reelbrain.db`. Cheapest true fix, but it breaks the
₹0/month constraint (barely) and disks on free-plan services aren't supported — you'd need
the Starter plan. Not recommended given the constraint.

**Option 2 — accept the wipe (recommended for now).** Keep Notion as the source of truth
and treat the SQLite layer as a rebuildable cache. Deploys are rare for a personal tool,
and the failure mode (an occasional duplicate page, temporarily weaker related-links) is
cosmetic. This is what the current setup assumes.

**Future work (noted, not built):** a `scripts/rebuild_from_notion.py` that pages through
the Notion Saves DB and repopulates `saves` + `tags` (and optionally re-embeds) after a
wipe. The `Shortcode` property on every Notion page exists precisely so this join is
possible. Re-embedding ~hundreds of saves stays comfortably inside the Gemini free tier.

## Cold starts

Free-tier services spin down after ~15 min idle; the next request takes ~30–50s. At
<20 captures/day this is acceptable (the iOS Shortcut gets its 202 as soon as the app
wakes). To reduce it, set up the free keep-alive ping — see SCHEDULING.md.

## Verifying the deployed service

```bash
curl https://<your-service>.onrender.com/health
curl -X POST https://<your-service>.onrender.com/capture \
  -H "Content-Type: application/json" \
  -d '{"url": "https://www.instagram.com/reel/XXXXXXXX/", "note": null, "secret": "<CAPTURE_SECRET>"}'
```

Remember OPEN_QUESTIONS.md item 1: the fetch step is the part that behaves differently on
Render's datacenter IPs than on your laptop. If captures consistently land as
`⚠️ Failed — retry` only in production, that's an IG datacenter-IP block — fallback
options are in BUILD_SPEC.md §1.2.
