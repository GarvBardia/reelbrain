# VAULT_CLAUDE_SETUP.md — "ask my Claude" over the ReelBrain vault

Goal: a dedicated Claude setup whose context is genuinely *this knowledge base* — the
Obsidian vault produced by `scripts/sync_to_obsidian.py` — not general chat history.

## The limitation, in one sentence

claude.ai Projects can't read local files directly (only files you upload or sync into
the Project), so the two real options for a live local vault are **(a) Claude Desktop
with the filesystem MCP connector pointed at the vault folder** or **(b) Claude Code run
from inside the vault folder**.

**Recommendation: (a) Claude Desktop + filesystem MCP.** "What do I know about X?" is a
conversational question, and Desktop gives you a persistent chat surface with project
instructions attached, always a click away — no terminal, no session startup ritual. It
also reads the vault *live*, so every sync is immediately queryable with zero upload
step, which a claude.ai Project can't do. Use (b) when you're already in a terminal
anyway or want to *transform* the vault (bulk-edit notes, build reports) rather than ask
questions — Claude Code is the better tool for writing, and you can simply `cd
C:\Users\garvb\ReelBrainVault && claude` with no setup at all.

## Setup (a): Claude Desktop + filesystem MCP, scoped to the vault only

1. Install/open **Claude Desktop** (not the browser app) and sign in.
2. **Settings → Extensions** (a.k.a. Connectors/MCP, naming shifts between versions) →
   find **Filesystem** → enable it.
3. When it asks which directories to allow, add **exactly one**:
   `C:\Users\garvb\ReelBrainVault`
   — and nothing else. The scoping *is* the security: Claude can then read (and only
   read/write within) the vault, never the rest of the disk. Don't add `C:\Users\garvb`.
   > If your Desktop version configures MCP via `claude_desktop_config.json` instead
   > (Settings → Developer → Edit Config), the entry is:
   > ```json
   > {
   >   "mcpServers": {
   >     "reelbrain-vault": {
   >       "command": "npx",
   >       "args": ["-y", "@modelcontextprotocol/server-filesystem",
   >                "C:\\Users\\garvb\\ReelBrainVault"]
   >     }
   >   }
   > }
   > ```
   > Restart Desktop after saving. The single path argument is the scope.
4. Create a **Project** in Desktop named e.g. `ReelBrain` and paste the instructions
   block below into the project's custom instructions. Have every vault conversation
   inside this project.
5. Smoke-test: ask *"List the files in reels/ and summarize _index.md."* You should see
   it read the actual files and name them.

## Project instructions block (paste as-is)

```
You are the librarian for my personal knowledge base: an Obsidian vault of notes
extracted from Instagram reels I saved. It is mounted read-only for you at the
vault root via the filesystem connector.

Structure: _index.md lists all topics with save counts. reels/ has one note per
saved reel ({date}-{shortcode}.md) with YAML frontmatter (topic/creator wikilinks,
value_score, status, url) and sections: Main point, Supporting points, Steps,
Resources, Quotable lines, Transcript, Related. topics/ and creators/ hold stub
notes whose backlinks ARE the index for that topic/creator.

Rules:
1. The vault is your ONLY source of truth for "what have I saved / what do I know
   about X". Search the files before answering — start from _index.md or the
   relevant topics/ note, then read the linked reels/ notes.
2. Cite the specific note file(s) you drew from in every answer, by path
   (e.g. reels/2026-07-01-AAA111.md), so I can jump to them in Obsidian.
3. If nothing relevant is saved, say exactly that — "nothing in the vault covers
   this" — and stop. Never fill the gap with general knowledge unless I explicitly
   ask "what do you think beyond my notes?"; when I do, label which part is which.
4. When asked for the reel itself, give the url from the note's frontmatter.
5. Treat value_score and Related links as relevance hints: prefer high-scoring
   notes and follow Related links one hop when synthesizing across saves.
```

## Keeping it honest

- The vault is only as fresh as the last `python scripts/sync_to_obsidian.py` run — if
  answers seem stale, sync (or set up the Task Scheduler job in VAULT.md).
- The filesystem MCP can also *write*. The instructions say read-only; if your Desktop
  version supports per-directory read-only mode, turn it on for belt-and-braces.
