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
