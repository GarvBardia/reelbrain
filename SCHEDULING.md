# SCHEDULING.md — four scheduled jobs, all free, all genuinely wired

Render's free tier gives you neither always-on instances nor built-in cron, so all
recurring needs are handled by GitHub Actions (free, scheduled) hitting endpoints on
the deployed service. **All four workflow files below are committed in this repo**
(`.github/workflows/`) — nothing here is documentation-only anymore; you only need to
add the two repository secrets once (step 0) and everything runs itself.

| Job | Workflow file | Schedule (UTC / IST) | Endpoint |
|---|---|---|---|
| Keep-alive ping | `.github/workflows/keepalive.yml` | every 10 min, 02:00–18:59 UTC | `GET /ping` |
| Nightly cleanup | `.github/workflows/nightly.yml` | 21:30 UTC = 03:00 IST daily | `POST /nightly` |
| Daily reflection digest | `.github/workflows/daily-digest.yml` | 16:00 UTC = 21:30 IST daily | `POST /daily-digest` |
| Weekly digest | `.github/workflows/weekly-digest.yml` | 15:30 UTC Sun = 21:00 IST Sunday | `POST /weekly-digest` |

## Step 0 — one-time repo secrets (needed by all four)

**Settings → Secrets and variables → Actions → New repository secret:**
- `RENDER_URL` = `https://<your-service>.onrender.com`
- `CAPTURE_SECRET` = the same value as the app's `CAPTURE_SECRET` env var (used by
  everything except keepalive, which needs no auth)

Without these, every workflow run fails at the `curl` step — that's the only setup
step left; the workflow files themselves are already committed and scheduled.

## Keep-alive ping (reduces cold starts)

Render free-tier services spin down after ~15 minutes idle; the next request pays a
~30–50s cold start. `keepalive.yml` pings every ~10 minutes during waking hours only
(02:00–18:59 UTC = 07:30–00:29 IST) to keep it warm without spending the entire
750 free instance-hours/month on round-the-clock pinging.

GitHub schedules are UTC and best-effort (can lag minutes on busy runners) — fine for
keep-alive, since an occasional missed ping just means one cold start.

**Alternative, if you'd rather not use GitHub Actions for this one:** a free
[cron-job.org](https://cron-job.org) account, GET `https://<your-service>.onrender.com/ping`
every 10 minutes.

## Nightly cleanup job

`nightly.yml` runs the stuck-row + expired-gate + auto-archive cleanup (identical
code path to `python scripts/run_nightly.py`), plus the cookie-health alert check.
Test it manually anytime via the **Actions** tab → nightly-cleanup → **Run workflow**
(the `workflow_dispatch` trigger is there for exactly this). The response body lists
which shortcodes were marked failed/gate-expired/archived (empty lists = nothing to
clean, the normal case).

The POST wakes the service if it's asleep, so this doesn't depend on the keep-alive
ping being active at 3am — expect that one request to take the cold-start ~30–50s.

## Daily reflection digest (evening)

`daily-digest.yml` runs `app.digest.run_daily()` — the past 24 hours of saves,
grouped by Priority, with a synthesis line. Test manually via **Actions** → 
daily-digest → **Run workflow**. Check for a new Notion page titled
`🌙 Daily reflection — <today's date>` under your parent page (or
`🌙 Daily reflection (nothing saved) — <date>` if nothing was captured that day), and
a phone push if `NTFY_TOPIC` is set (same topic as the cookie-health alert — see
COOKIES.md).

## Weekly digest

`weekly-digest.yml` runs `app.digest.run()` — the past 7 days of saves, grouped by
topic and creator (see `scripts/weekly_digest.py` for the local-run equivalent).
**Schedule: Sunday 15:30 UTC = 21:00 IST** — a natural week-wrap-up slot, chosen 30
minutes before the daily digest's 21:30 IST so the two never land in the same GitHub
Actions minute on the Sunday they both fire. (This was previously undocumented as
"the same way as nightly" but never actually built — a real `POST /weekly-digest`
endpoint and this workflow file are what actually deliver on that now.) Test manually
via **Actions** → weekly-digest → **Run workflow**; check for a Notion page titled
`📬 Weekly digest — <week-ending date>`.

## All four are independent

Each runs on its own schedule and none replace each other — a failure or manual
disable of one doesn't affect the others. All four POST/GET the deployed Render
service directly, so none of them depend on your local machine being on.
