# COOKIES.md — refreshing expired burner cookies (2-minute runbook)

You'll land here from one of:
- A Notion page titled `⚙️ System Alert — cookies likely expired — <date>`.
- An ntfy.sh push notification "ReelBrain: cookies likely expired" (if you set that up).
- `/health` showing `"cookie_health": "degraded"`.

All three mean the same thing: 3+ consecutive cookie-backed Instagram fetches failed
with an auth-type error (login required, empty media response, etc.) — the burner
account's session has expired. **This is expected and normal** — IG sessions don't
last forever. No auto-refresh happens on purpose (that would mean storing a real
password somewhere and risking the burner account); this is always a manual, 2-minute
fix.

## The fix

**Keep Chrome logged into the burner account** (its default profile — no separate
profile needed), then run one command:

```bash
python scripts/refresh_cookies.py
```

That's it. It reads fresh `instagram.com` cookies straight out of Chrome's local
cookie store, exports them in the Netscape format yt-dlp expects, pushes them to
Render's `cookies.txt` Secret File via Render's API (same effect as pasting into the
dashboard by hand — Render auto-restarts the service on any Secret File change), and
polls `/health` a few times afterward to confirm `cookies_file: true` post-restart,
printing a clear SUCCESS/COULD NOT CONFIRM message.

**One-time setup**, before the first run:
1. `pip install -r requirements-local.txt` (this pulls in `browser_cookie3`, a
   local-only dependency — never installed on Render, see that file's header comment).
2. In `.env`, set `RENDER_API_KEY` (Render dashboard → Account Settings → API Keys →
   Create API key — treat it like a password) and `RENDER_SERVICE_ID` (Render
   dashboard → your service → Settings, or the `srv-...` segment of the service's
   dashboard URL). See `.env.example`.

**Useful flags:**
- `--browser edge` — if the burner session is in Edge instead of Chrome.
- `--dry-run` — writes `cookies.txt` locally and stops before touching Render, so you
  can sanity-check it first:
  ```bash
  yt-dlp --cookies cookies.txt --dump-json https://www.instagram.com/reel/<any_public_reel>/
  ```
  Should print JSON metadata, not an error. If it errors, you're likely not logged
  into the burner account in that browser.

**Why this doesn't increase Instagram-side risk.** This script never logs into
Instagram, never authenticates, never does anything IG's bot-detection would notice —
it only *reads* cookies that already exist in Chrome's local cookie store, because you
logged into the burner account normally, through a real browser, at some point before
running it. It automates exactly one thing: the copy-cookies-and-paste-into-Render
step, which was always just moving bytes from one place you already trust (your own
logged-in browser) to another (Render's Secret File). Same trust boundary as the
manual extension-export process it replaces — nothing new is exposed to Instagram.

**If it doesn't work:** the fallback is still the manual path — Render dashboard →
your service → **Environment** tab → **Secret Files** → edit `cookies.txt` directly,
using any cookie-export extension of your choice. This script is a convenience layer
over that same mechanism, not a replacement dependency.

## Why this matters / what happens if you ignore the alert

Every fetch keeps failing the same way, degrading straight to the OG-tag fallback
(caption-only, `(unavailable)` transcript) or `⚠️ Failed — retry` outright, until you
refresh the cookies. The alert exists specifically so you don't have to notice this by
eyeballing Notion — do the 7 steps above and it stops.

## How the detection actually works (for context, not needed to fix it)

- `app/fetcher.py` tracks consecutive cookie-backed auth failures in a small SQLite
  key/value counter (`app_state` table), reset to 0 on any successful cookie-backed fetch.
- `/health`'s `cookie_health` flips to `"degraded"` at 3 consecutive failures
  (override via `COOKIE_HEALTH_THRESHOLD` if that's too twitchy or too lax for you).
- The nightly job (`app/alerts.py`) checks this once a day and fires at most one alert
  per calendar day — you won't get spammed by every nightly run while it's still broken,
  but you will get reminded once/day until it's fixed.

## Setting up the ntfy.sh push channel (optional, recommended — genuinely the simplest)

ntfy.sh needs no account and no signup on either end:

1. Pick a topic name only you know — something unguessable, e.g.
   `reelbrain-cookies-<random-string>` (anyone who knows the topic name can post to it
   or subscribe to it, so don't use something guessable like `reelbrain`).
2. On your phone: install the **ntfy** app (iOS App Store / Google Play, free) →
   subscribe to that topic name.
3. Set `NTFY_TOPIC=<your-topic-name>` in Render's environment variables (and locally in
   `.env` if you want to test).
4. That's it — no further setup. The nightly job POSTs to `https://ntfy.sh/<topic>` when
   degraded, which becomes a real push notification on your phone.

This is the simpler of the two channels — a Notion alert only shows up if you happen to
open Notion, whereas ntfy is an actual push notification. The Notion channel is wired up
unconditionally (it's already configured — `NOTION_PARENT_PAGE_ID`), so you get it either
way; ntfy is free to add on top for the real "phone buzzes" experience.
