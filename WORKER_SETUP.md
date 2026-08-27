# WORKER_SETUP.md — placeholder recovery worker on Windows Task Scheduler

The worker (`scripts/recover_placeholders.py`, launched via
`recover_placeholders.bat`) re-fetches every Notion row that is still a
placeholder (`Status = 📷 Photo — manual` OR `Title = "No caption or transcript
available."`) from your home IP, runs the real extraction, and updates the same
Notion row in place. It is resumable, spacing-safe, and stops cleanly on Gemini
quota — so running it every few hours is safe.

It must run from THIS machine (residential IP) — that's the whole point. It is
never deployed to Render.

## One-time setup

1. Confirm the venv + env are ready (they already are if the repo's other local
   scripts work): `.env` in the repo root has `NOTION_TOKEN`, `NOTION_DB_ID`,
   `GEMINI_API_KEY`; `venv\` exists.
2. Test the .bat manually first (from a normal terminal):

   ```
   C:\Users\garvb\reelbrain\recover_placeholders.bat
   ```

   Then check `recover_placeholders.log` in the repo root for output.

## Task Scheduler steps (exact)

1. Press **Win**, type `Task Scheduler`, open it.
2. Right panel → **Create Task…** (NOT "Create Basic Task" — we need two
   triggers on one task).
3. **General tab:**
   - Name: `ReelBrain placeholder recovery`
   - Select **"Run only when user is logged on"** (required — the fetch shares
     your interactive session's network + the venv, and this avoids needing to
     store your Windows password).
   - Leave "Run with highest privileges" UNCHECKED (not needed).
4. **Triggers tab → New…** (do this twice):
   - Trigger 1: Begin the task **On a schedule** → **Daily**, start today at a
     convenient time (e.g. 09:00). Under **Advanced settings**, check
     **"Repeat task every"** and pick **4 hours**, "for a duration of" →
     **Indefinitely**. OK.
   - Trigger 2: Begin the task **At log on** → "Specific user" (you). Under
     Advanced settings, optionally set **"Delay task for" 5 minutes** so it
     doesn't compete with startup. OK.
5. **Actions tab → New…**
   - Action: **Start a program**
   - Program/script: `C:\Users\garvb\reelbrain\recover_placeholders.bat`
   - Start in: `C:\Users\garvb\reelbrain`
6. **Conditions tab:**
   - UNCHECK "Start the task only if the computer is on AC power" if this is a
     laptop you use on battery (otherwise runs silently skip on battery).
   - CHECK "Start only if the following network connection is available: Any
     connection".
7. **Settings tab:**
   - CHECK "Allow task to be run on demand".
   - CHECK "If the task fails, restart every: 30 minutes", up to 2 times.
   - "If the task is already running": **Do not start a new instance** (the
     progress file makes overlap harmless, but there's no reason to overlap).
8. OK to save. Right-click the task → **Run** once to verify; check
   `recover_placeholders.log`.

## What "done" looks like

- Recovered rows flip in Notion to `📥 Inbox` (or `⏳ Awaiting DM` if the
  recovered caption revealed a comment gate) with a real Title/Topics/Priority.
- `recover_placeholders_progress.json` (repo root, local-only, gitignored)
  tracks attempts. Rows that failed **3** times are permanent no-caption cases
  and are skipped forever — clear their entry from the JSON to retry them.
- A Gemini quota (429) mid-run stops the run cleanly without burning any row's
  attempt count; the next scheduled run resumes where it left off.

---

# Nightly autonomous pass (`daily_runner.py`)

The recovery worker above handles ONE job. `scripts/daily_runner.py` handles
the whole backlog: it spends each day's Gemini quota across six pending-work
scripts in priority order, stops cleanly when the quota is gone, and resumes
tomorrow exactly where it left off. `nightly_autonomous.bat` is its launcher.

## Why it exists

Every remaining backlog item is quota-bound (the Gemini free tier is ~20
requests/day/model) and, until now, needed a human to run a script and remember
which one stopped where. This replaces that with one scheduled pass.

## Ollama check (`ensure_ollama.py`), first step in the .bat

Added 2026-08-21 after a pipeline health audit found Ollama wasn't running
when the nightly job fired, so the local-routed `plain_summary` step silently
processed 0 of 160 pending rows that night — every other step degrades cleanly
around a single missing local provider, which is exactly why it went
unnoticed. `nightly_autonomous.bat` now runs `ensure_ollama.py` first: it
checks `http://localhost:11434/api/tags`, starts Ollama if it's down, and
polls (up to 30s) for a real response before letting `daily_runner.py` run.
It **always exits 0** — if Ollama genuinely can't be started, `plain_summary`
degrades exactly as before (`ollama_stopped=True`) and every Gemini-routed
step still runs untouched; this only makes that outcome logged instead of
silent. Manual run: `python scripts\ensure_ollama.py`.

## Priority order

| # | Step | Cost/row | Why here |
|---|------|----------|----------|
| 1 | `backfill_named_entities` | 1 | **Highest.** Both the taxonomy work and the `/attach` accuracy remeasurement are blocked on it. |
| 2 | `recover_placeholders` | ~3 | Recovers content that exists nowhere else yet. Costs more because each row runs an extraction *plus* a research call per entity. |
| 3 | `enforce_topics` | **0 (free)** | Zero Gemini calls — runs **every day regardless of quota**, including after a 429. |
| 4 | `backfill_suggested_action` | 1 | |
| 5 | `backfill_plain_summary` | 1 | |
| 6 | `ingest_resources` | 1 | Last — the resources are already attached and readable; this only enriches the vault copy. |

Step 3 sits *after* step 1 deliberately: its fallback derives tags **from**
`named_entities`, so running it later in the order produces better tags. It
also only revisits rows stranded on `uncategorized` once they actually have
entities, so it can never churn a daily no-op write.

## Budget model

Two independent mechanisms, both needed:

- **The budget** (`DAILY_GEMINI_BUDGET`, default 20) is a *planning* device. It
  decides how work is spread across steps. Without it, step 1 would eat the
  entire day every day and steps 2–6 would never run.
- **The 429 is the hard stop.** The budget is only an estimate, so the real
  limit is whatever Google enforces. A watcher on the `reelbrain.gemini` logger
  catches it uniformly for every step — including `ingest_resources`, which
  (unlike the other five) does not self-report a quota stop.

## Run it by hand first

```
venv\Scripts\python.exe scripts\daily_runner.py --dry-run
```

Note: `--dry-run` skips all *writes*, but `ingest_resources` still fetches and
summarizes to show you a preview, so a dry run **does** consume some quota.

Then the real thing:

```
venv\Scripts\python.exe scripts\daily_runner.py
```

## Task Scheduler steps (exact)

1. Start → **Task Scheduler** → **Create Task…** (not "Basic Task").
2. **General tab:**
   - Name: `ReelBrain nightly autonomous`
   - Select **"Run only when user is logged on"**. This is required, not a
     preference: the scripts need your residential IP and your logged-on
     session — Instagram blocks datacenter IPs, which is why this runs at home
     instead of on Render.
   - Leave "Run with highest privileges" UNCHECKED.
3. **Triggers tab → New…:**
   - Begin the task: **On a schedule** → **Daily** → Recur every `1` days.
   - Start time: pick something you're reliably logged on for and not using the
     machine — **10:00 PM** is a good default.
   - CHECK "Enabled". Leave "Stop task if it runs longer than" at 3 days.
4. **Actions tab → New…:**
   - Action: **Start a program**
   - Program/script: `C:\Users\garvb\reelbrain\nightly_autonomous.bat`
   - Start in: `C:\Users\garvb\reelbrain`
     (Set this. Without it the relative venv path in the .bat won't resolve.)
5. **Conditions tab:**
   - UNCHECK "Start the task only if the computer is on AC power".
   - CHECK "Start only if the following network connection is available: Any
     connection".
   - UNCHECK "Wake the computer to run this task" — "run only when logged on"
     means a wake would fail anyway.
6. **Settings tab:**
   - CHECK "Allow task to be run on demand".
   - CHECK "Run task as soon as possible after a scheduled start is missed"
     — the whole point is that a skipped day is a lost day of quota.
   - "If the task is already running": **Do not start a new instance.**
   - Do NOT check "If the task fails, restart every…". A quota stop is a
     *normal* outcome here, and retrying it just burns nothing usefully.
7. OK to save. Right-click the task → **Run** once to verify.

## Reading the log

Each run appends ONE paragraph to `daily_runner.log` (gitignored). It states
what ran, how much quota was spent, what's still pending, and — the number to
watch — how many rows remain until the `named_entities` backfill is complete,
with an ETA in days at the current rate:

```
[2026-07-27 12:52 UTC] Ran: named_entities (20/122), enforce_topics (0/0),
ingest_resources (4/27). Quota: 20/20 calls used, STOPPED on a 429. Still
pending — plain_summary: 125 pending. named_entities countdown: 102 rows left
(~6 more day(s) at 20 calls/day).
```

When that countdown reaches 0 the log says so explicitly, and the taxonomy
work and `/attach` remeasurement are unblocked.

`nightly_autonomous.log` (also gitignored) captures raw stdout/stderr from the
.bat for when you need to debug a run rather than read its summary.

---

# Monthly: Vault Librarian (`vault_librarian.py`)

A separate MONTHLY maintenance pass (not nightly). It reconnects orphaned
notes, enforces topics on any topic-less rows, detects tag drift since the
Phase 0 merge, reconciles Notion vs vault counts, and re-syncs.

- Report only, safe: `python scripts\vault_librarian.py`
- Remediate + re-sync: `python scripts\vault_librarian.py --apply`

Drift findings are written to `TAXONOMY_DRIFT.md` — **the librarian never
merges tags itself**. To act on a finding, add it to `app/taxonomy.MERGES` and
re-run `apply_taxonomy.py`, which re-checks priority invariance before writing.

## Task Scheduler steps (monthly)

Identical to the nightly entry above, with two differences:

1. **General tab** — Name: `ReelBrain monthly librarian`. Same "Run only when
   user is logged on" (needs your session + residential IP for the re-sync).
2. **Triggers tab → New…** — Begin: **On a schedule** → **Monthly** → Months:
   select **all 12** → Days: **1**. Start time: a time you're logged on, offset
   from the nightly run (e.g. **11:00 PM**) so they don't overlap.
3. **Actions tab → New…** — Program/script:
   `C:\Users\garvb\reelbrain\venv\Scripts\python.exe`
   Add arguments: `scripts\vault_librarian.py --apply`
   Start in: `C:\Users\garvb\reelbrain`
4. **Conditions / Settings** — same as the nightly entry (uncheck AC-power
   requirement, "Do not start a new instance", allow run-on-demand).

Right-click → **Run** once, then read the console tail and `TAXONOMY_DRIFT.md`.

---

# Health Watchdog (`health_watchdog.py`) — already in the nightly .bat

Runs six daily checks and sends ONE ntfy push only if something failed —
**silence means healthy**. It's already wired into `nightly_autonomous.bat`
after `daily_runner.py`, so the existing nightly Task Scheduler entry runs it;
there is no separate schedule to create.

Checks: `/health` (cookie_health + sqlite_vec), Daily Reflection page edited
within 26h, vault count == Notion count, all 4 GitHub Actions last-runs green,
daily_runner not stuck on quota (3+ zero-progress 429 days), and zero
empty-Topics rows.

Needs, in `.env` (all optional — a missing one degrades that check, never the
whole run): `REELBRAIN_URL` (default the Render URL), `GITHUB_REPO` (default
`GarvBardia/reelbrain`), and `NTFY_TOPIC` for the push.

**The GitHub Actions check needs `GITHUB_TOKEN`** — the repo is private, so an
unauthenticated read can't see it. Create a fine-grained PAT scoped to this repo
with **Actions: Read-only** and put it in `.env` as `GITHUB_TOKEN=...`. Without
it, that one check is skipped (reported, but never a false alarm) — every other
check still runs.

Manual run (never pushes): `python scripts\health_watchdog.py --dry-run`

# Daily Capture Report (`daily_capture_report.py`) — already in the nightly .bat

Tracks the one number that actually matters here: is the extraction backlog
growing or shrinking? Capture has never been the bottleneck — Gemini quota is —
so this reports capture-vs-processing rate, not a raw snapshot. It's already
wired into `nightly_autonomous.bat` **after** `daily_runner.py` and
`health_watchdog.py`, so the existing nightly Task Scheduler entry runs it too;
there is no separate schedule to create.

**Zero Gemini calls.** Pure Notion + vault + local-file read — it must never
compete with the extraction backlog for the quota it's reporting on.

Each run:
- counts new rows captured in the last 24h, and how many of those already
  finished extraction vs are still waiting;
- computes the total backlog (`content_type=unknown` OR a `uncategorized` /
  `pending-extraction` topic marker);
- persists one line/day to `backlog_history.json` (gitignored) so it can say
  the backlog **grew by 6** or **shrank by 12** vs the last recorded day;
- estimates days-to-clear at the recent observed Gemini pace (read from
  `daily_runner.log`), 3 calls/row for the multimodal recovery work;
- confirms the Obsidian vault is still 1:1 with Notion (same check
  `health_watchdog` uses).

Output: one plain-English paragraph appended to `daily_capture_report.log`
(gitignored), same voice as `daily_runner.log`. A single ntfy push fires **only
when the backlog grew** — a shrinking or steady backlog stays silent, the same
noise-means-look-now discipline as the watchdog. Uses the existing `NTFY_TOPIC`;
no new config.

Manual run (never pushes, never writes history): `python scripts\daily_capture_report.py --dry-run`

# Pipeline Health (`pipeline_health.py`) — the ONE "is everything OK" check

Built 2026-08-27 after a real 6-day silent outage: Task Scheduler refused to
even start the Nightly Runner task (Win32 error 4320, "The operator or
administrator has refused the request" — this machine's task has
`DisallowStartIfOnBatteries`/`StopIfGoingOnBatteries` set, and it's a laptop),
and `health_watchdog.py` — which would normally catch staleness — runs
*inside that same nightly job*, so it never got a chance to raise the alarm.
Consolidates every check from that incident's manual audit into one script so
the audit never has to happen by hand again: backend `/health`, Task
Scheduler's own last-result (decoded, not a bare number), Ollama reachability,
the Notion backlog trend (reuses `daily_capture_report`'s logic directly, not
re-implemented), Obsidian vault/Notion count match, Gemini quota remaining,
and **ntfy delivery verified end-to-end** — it reads ntfy.sh's own HTTP
response when it actually sends an alert, not just "our code didn't raise."

**Zero Gemini calls.** Every check is a read against already-existing state
or a live service ping.

**Runs on its own Task Scheduler entry, "ReelBrain Pipeline Health" — deliberately
separate from `nightly_autonomous.bat`/"ReelBrain Nightly Runner."** It does
NOT run inside `nightly_autonomous.bat` (it briefly did, for one commit, before
this was created — removed again 2026-08-27) precisely because that's the task
Task Scheduler refused outright during the incident. If this check lived inside
the same task, a repeat of that refusal would silence it too, defeating the
entire point. Its own task has:

- Every 4 hours, indefinitely.
- **No battery restriction** — `DisallowStartIfOnBatteries` and
  `StopIfGoingOnBatteries` both `False` (this is a handful of lightweight
  reads and pings, not a real workload, so there's no real battery cost to
  running it unplugged).
- `StartWhenAvailable` on, so a missed run (machine asleep/off) fires as soon
  as it's back, rather than silently skipping to the next slot.

Created via (PowerShell, run once — already done on this machine 2026-08-27):

```powershell
$action = New-ScheduledTaskAction -Execute "C:\Users\garvb\reelbrain\pipeline_health.bat"
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date) `
    -RepetitionInterval (New-TimeSpan -Hours 4) `
    -RepetitionDuration (New-TimeSpan -Days 3650)   # ~10 years; [TimeSpan]::MaxValue
                                                      # overflows the task XML's duration format
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Hours 1)
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited

Register-ScheduledTask -TaskName "ReelBrain Pipeline Health" `
    -Action $action -Trigger $trigger -Settings $settings -Principal $principal `
    -Description "Consolidated pipeline health check ... runs every 4h, independent of the Nightly Runner task, with no battery restriction." `
    -Force
```

Note the parameter names: it's `-AllowStartIfOnBatteries` / `-DontStopIfGoingOnBatteries`
on `New-ScheduledTaskSettingsSet` — **not** `-DisallowStartIfOnBatteries:$false` /
`-StopIfGoingOnBatteries:$false`, which don't exist on this cmdlet and fail with
"A parameter cannot be found." After creating any task like this, verify the
settings actually landed rather than trusting the switches blindly:

```powershell
(Get-ScheduledTask -TaskName "ReelBrain Pipeline Health").Settings |
    Select-Object DisallowStartIfOnBatteries, StopIfGoingOnBatteries
# both must read False
```

Trigger it on demand any time: `Start-ScheduledTask -TaskName "ReelBrain Pipeline Health"`.
Raw stdout/stderr from each run goes to `pipeline_health_task.log` (gitignored,
via the `pipeline_health.bat` launcher) — separate from `pipeline_health.log`,
which is the script's own structured report.

**Output:** one consolidated block — `ALL GREEN — 7/7 checks passing.` when
healthy, or an itemized `[OK]`/`[WARN]`/`[FAIL]` list naming exactly what
needs attention. Appended to `pipeline_health.log` (gitignored). A single
ntfy push fires if *anything* is degraded or broken (not failure-only like
`health_watchdog` — backlog growth alone counts here too). **Backup channel
that doesn't depend on ntfy being healthy:** whenever something needs
attention, it also writes a dated `PIPELINE_ALERT_<date>.txt` in the repo
root (gitignored, auto-cleaned after 14 days) — so a broken ntfy path is
never the *only* way this gets noticed, which is exactly the failure mode
that hid the original outage's ntfy problem too.

Manual run (real alert if something's wrong): `python scripts\pipeline_health.py`
Dry run (prints the report, never pushes, never writes files):
`python scripts\pipeline_health.py --dry-run`
