# SCHEDULING.md — keep-alive ping + nightly job, both free

Render's free tier gives you neither always-on instances nor built-in cron, so both
recurring needs are handled by free external schedulers hitting two endpoints:

- `GET /ping` — no auth, does nothing; its only job is to keep the instance warm.
- `POST /nightly` — secret-protected (same `CAPTURE_SECRET` as capture); runs the
  stuck-row + expired-gate cleanup (identical code path to `python scripts/run_nightly.py`).

## Keep-alive ping (reduces cold starts)

Render free-tier services spin down after ~15 minutes idle; the next request pays a
~30–50s cold start. A ping every ~10 minutes keeps it warm during hours you care about.

**Heads-up before enabling this:** Render free tier includes 750 instance-hours/month —
enough to keep ONE service warm 24/7, but pinging round-the-clock spends all of it on
this service. Pinging only during waking hours (the cron below) leaves headroom.

### Option A — cron-job.org (simplest)

1. Create a free account at https://cron-job.org.
2. **Create cronjob** →
   - URL: `https://<your-service>.onrender.com/ping`
   - Schedule: every 10 minutes, and restrict to the hours you actually capture reels
     (e.g. 08:00–24:00 in your timezone) to conserve instance-hours.
   - Request method: GET. No headers or body needed.
3. Save. Check its execution history once after ~30 min to confirm 200s.

### Option B — GitHub Actions

`.github/workflows/keepalive.yml`:

```yaml
name: keepalive
on:
  schedule:
    - cron: "*/10 2-18 * * *"   # every 10 min, 02:00–18:59 UTC = 07:30–00:29 IST
jobs:
  ping:
    runs-on: ubuntu-latest
    steps:
      - run: curl -sf https://<your-service>.onrender.com/ping
```

Note: GitHub schedules are UTC and best-effort (can lag minutes on busy runners) — fine
for keep-alive, since an occasional missed ping just means one cold start.

## Nightly cleanup job

### GitHub Actions (recommended)

1. In the repo: **Settings → Secrets and variables → Actions → New repository secret**:
   - `RENDER_URL` = `https://<your-service>.onrender.com`
   - `CAPTURE_SECRET` = the same value as the app's `CAPTURE_SECRET` env var
2. Add `.github/workflows/nightly.yml`:

```yaml
name: nightly-cleanup
on:
  schedule:
    - cron: "30 21 * * *"   # 21:30 UTC = 03:00 IST
  workflow_dispatch: {}      # allows manual "Run workflow" for testing
jobs:
  nightly:
    runs-on: ubuntu-latest
    steps:
      - name: Trigger nightly cleanup
        run: |
          curl -sf -X POST "$RENDER_URL/nightly" \
            -H "Content-Type: application/json" \
            -d "{\"secret\": \"$CAPTURE_SECRET\"}"
        env:
          RENDER_URL: ${{ secrets.RENDER_URL }}
          CAPTURE_SECRET: ${{ secrets.CAPTURE_SECRET }}
```

3. Test it once via the **Actions** tab → nightly-cleanup → **Run workflow**. The response
   body lists which shortcodes were marked failed / gate-expired (empty lists = nothing to
   clean, which is the normal case).

The POST wakes the service if it's asleep, so the nightly job doesn't depend on the
keep-alive ping being active at 3am — expect that one request to take the cold-start
~30–50s, which is fine for a cron caller.

### Alternative — cron-job.org

Same as the keep-alive recipe but: method POST, header `Content-Type: application/json`,
body `{"secret": "<CAPTURE_SECRET>"}`, schedule once daily. Works, but your secret then
lives in cron-job.org's dashboard — GitHub Actions secrets are the tidier home for it.
