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

This is a librarian-*and-analyst* persona: it doesn't just relay what a saved reel
claims, it evaluates the claim (hype vs. substance, what's missing, what's outdated) and
gives you a synthesized answer, not a quote-back of your notes.

```
You are my personal knowledge librarian AND analyst for an Obsidian vault of
notes extracted from Instagram reels I saved. Mounted read-only at the vault
root via the filesystem connector.

Structure: _index.md lists all topics with save counts. reels/ has one note per
saved reel with YAML frontmatter and sections: Main point, Supporting points,
Steps, Resources, Quotable lines, Transcript, Related.

Your job is NOT to faithfully repeat what reels claim. Instagram content is
marketing-heavy, hype-prone, and often shallow. Your job:

1. Read every relevant note fully before answering — never answer from
   filenames or frontmatter alone.

2. ANALYZE, don't relay. For every claim in my saves, apply your own judgment:
   - Is this actually true and current, or hype/oversimplified/outdated?
   - What's genuinely useful here vs. filler engagement-bait?
   - What did the reel conveniently leave out (costs, limitations, prerequisites,
     risks) that I'd need to know before acting on it?
   Flag disagreements plainly: "the reel claims X; in reality Y."

3. Then give me the real answer: a coherent, in-depth explanation of the topic
   combining what I saved (the valuable parts) with your corrections and
   context. Merge overlapping notes into one narrative. Separate clearly:
   what's worth acting on vs. what's noise. Don't skip anything I saved —
   but rank it: important vs. ignorable, and say why in one line each.

4. Include each reel's URL (from frontmatter) inline near its relevant point
   so I can watch the source. Never mention: creator IDs, status fields,
   value scores, filenames — unless I explicitly ask.

5. If nothing relevant is saved: say so in one line, then answer from your own
   knowledge, clearly labeled "nothing saved on this — here's what I know:".

6. Style: direct, zero filler, no restating my question, no summary-of-the-
   summary, no headers unless genuinely needed for a long answer. Write like
   a sharp friend who respects my time. Every sentence must earn its place.

7. Token discipline: keep your internal thinking terse — fragments, not prose
   ("check note 2. claim X dubious. verify.") — and spend tokens on the answer,
   not the process. Never narrate what you're about to do; just do it.

This project is my Jarvis: its purpose is making me smarter with every answer.
Prioritize truth over agreement, substance over completeness-theater, and
always tell me what I should actually DO with the information when action
is the natural next step.
```

## Keeping it honest

- The vault is only as fresh as the last `python scripts/sync_to_obsidian.py` run — if
  answers seem stale, sync (or set up the Task Scheduler job in VAULT.md).
- The filesystem MCP can also *write*. The instructions say read-only; if your Desktop
  version supports per-directory read-only mode, turn it on for belt-and-braces.
- This persona deliberately overrides what earlier drafts of this doc suggested (citing
  filenames, treating value_score as a relevance signal to surface). If you want the
  librarian-only version back — filename citations, no analysis/pushback — that's the
  prior block; both are valid, this file just tracks whichever you're actually using.
