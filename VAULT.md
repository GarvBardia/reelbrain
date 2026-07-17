# VAULT.md — the local Obsidian knowledge layer

The Notion database is the capture UI; the Obsidian vault is the *thinking* layer — a
folder of plain markdown files with real links between related saves, browsable as a
graph, readable by any tool (including a Claude project — see VAULT_CLAUDE_SETUP.md).

## What the sync produces

```
C:\Users\garvb\ReelBrainVault\          (or wherever VAULT_PATH points)
  _index.md                 ← start here: every topic, real reel previews, not just counts
  reels/
    2026-07-01-AAA111.md    ← one note per save: {posted-date}-{shortcode}.md
  topics/
    sleep.md                ← your notes at the top + an auto-regenerated reel index
  creators/
    janedoe.md              ← same, per creator
```

Each reel note has YAML frontmatter (shortcode, creator, status, value_score, topics,
url, posted) followed by the same sections as the Notion page — Main point, Supporting
points, Steps, Resources, Quotable lines, Transcript — plus a **## Related** section
linking the 3 most similar past saves. Related links come from the embeddings computed
at capture time (sqlite-vec), so they surface connections Notion's Related property
missed, and they're real `[[wikilinks]]` so Obsidian's graph view renders them.

**Topic and creator notes are a real index, not a bare stub.** Every sync rewrites a
`## Saved Reels` section listing every reel tagged with that topic (or by that creator),
each as `[[wikilink]]` — title, value score, and one-line Main Point — sorted by value
score descending, then date descending. This is the actual reason to be in Obsidian
instead of Notion: you get a real page per topic, not just Obsidian's Backlinks panel
(which works, but isn't a page you can read top to bottom). The generated part is wrapped
between `<!-- AUTO-GENERATED, DO NOT EDIT BELOW -->` / `<!-- END AUTO-GENERATED -->`
markers — anything you write above (or below) those markers, in any of these notes
including `_index.md`, survives every re-sync untouched.

## Running the sync

```bash
python scripts/sync_to_obsidian.py                 # VAULT_PATH from .env
python scripts/sync_to_obsidian.py D:\SomeVault    # or explicit path
```

Local-only — it reads your `.env` (Notion token, read-only; local SQLite for creators
and embeddings). Never deploy it to Render. It's idempotent: existing notes are matched
by the `shortcode:` in their frontmatter and updated in place, so re-run it as often as
you like. Edits *inside a reel note* do NOT survive (the whole note is regenerated from
Notion each time) — put your thinking in topic/creator notes instead, above the
AUTO-GENERATED markers, where it's preserved on every sync.

**How often:** manually, whenever you feel like browsing, is fine — nothing breaks if
it's stale. To make it fully hands-off, schedule it daily:

### Windows Task Scheduler (daily, hands-off)

1. Start menu → **Task Scheduler** → **Create Basic Task…**
2. Name: `ReelBrain vault sync` → **Next**.
3. Trigger: **Daily**, pick a time you're usually online (e.g. 21:00) → **Next**.
4. Action: **Start a program** → **Next**:
   - Program/script: `py`
   - Add arguments: `scripts\sync_to_obsidian.py`
   - Start in: `C:\Users\garvb\reelbrain`
5. **Finish.** Then open the task's Properties → check **"Run task as soon as possible
   after a scheduled start is missed"** (laptop asleep at 21:00 = still syncs later).
6. Test once: right-click the task → **Run**, then check the vault folder.

## New to Obsidian? The 3 things that matter here

1. **Open the vault:** install Obsidian (free) → "Open folder as vault" → pick your
   `ReelBrainVault` folder. That's it — it's just reading the markdown files.
2. **`[[wikilinks]]`** are Obsidian's links between notes. Click one to jump; hover to
   preview. Every topic, creator, and Related save in these notes is one — including the
   `## Saved Reels` list in every topic/creator note, so `topics/sleep.md` shows every
   sleep reel directly on the page (the Backlinks panel, right sidebar, shows the same
   thing automatically too, as a bonus, not the only way to see it).
3. **Graph view** (Ctrl+G): every note is a dot, every wikilink an edge. Clusters form
   around topics and creators automatically, and the Related links draw the
   similarity connections between reels. Local graph (on a single note) is often more
   useful than the global one — it shows just that note's neighborhood.

Start at `_index.md`, click a topic, read its backlinks. That's the whole workflow.
