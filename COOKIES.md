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

1. **Log into the burner account** in a normal browser (the one you use for the burner,
   not your real account — don't mix sessions). instagram.com → log in as usual.

2. **Export fresh cookies** with the same browser extension you used originally (the
   "cookies.txt" / "Get cookies.txt LOCALLY" extension). Export for `instagram.com`,
   save as `cookies.txt`.

3. **Sanity-check locally first** (catches a bad export before it goes to prod):
   ```bash
   yt-dlp --cookies cookies.txt --dump-json https://www.instagram.com/reel/<any_public_reel>/
   ```
   Should print JSON metadata, not an error. If it errors, the export didn't work —
   re-check you're logged in as the burner and re-export.

4. **Update the Render Secret File:**
   - Render dashboard → your service → **Environment** tab → **Secret Files**.
   - Find the existing `cookies.txt` entry → **Edit** → paste the new file's full
     contents, replacing the old ones → **Save**.
   - (First time setting this up instead of refreshing it? Same screen: **Add Secret
     File**, filename `cookies.txt`, paste contents. See DEPLOYMENT.md's "Burner
     cookies on Render" section.)

5. **Render auto-restarts the service** when a Secret File changes — no manual redeploy
   needed. Give it ~1 minute.

6. **Verify the fix:**
   ```bash
   curl https://<your-service>.onrender.com/health
   ```
   `cookie_health` should read `"ok"` again. It won't flip back automatically until the
   *next* successful cookie-backed fetch resets the counter (see below) — if it's still
   `"degraded"` right after restart, that's expected; it clears on the next real capture.

7. **Optional immediate confirmation:** trigger one capture (share a reel, or
   `POST /retry/<some-shortcode>` on a previously-failed row) and check it lands in
   Notion normally instead of `⚠️ Failed — retry`.

## Why this is manual, permanently — not a one-time limitation

An earlier version of this doc briefly documented a `scripts/refresh_cookies.py` tool
that read cookies straight out of a local Chrome profile (via the `browser_cookie3`
library) and pushed them to Render automatically. **That approach was removed —
it's fundamentally unworkable, not a bug that got fixed.** Starting with Chrome 127
(mid-2024), Google introduced **App-Bound Encryption**, which ties a Chrome profile's
saved cookies to the specific Windows app that encrypted them. It exists specifically
to stop exactly this technique — tools reading a browser's cookie store from outside
the browser itself, the same method infostealer malware uses to hijack sessions.
`browser_cookie3` (and every library like it) cannot decrypt Chrome's cookies on a
current Windows install without either downgrading Chrome (not a reasonable
security tradeoff for the convenience) or extracting decryption keys some other way
(exactly the kind of thing App-Bound Encryption is designed to prevent). Firefox does
not have this protection, so switching the burner account to Firefox specifically is
a real option if this is ever worth revisiting — but for now, the 7-step manual export
above is the sustainable method, and it isn't going away.

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
