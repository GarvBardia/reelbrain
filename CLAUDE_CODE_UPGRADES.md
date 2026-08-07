# CLAUDE_CODE_UPGRADES.md — Claude Code capability scan of the saved corpus

Generated 2026-08-08 by reading all 209 Notion Saves rows for content specifically about
Claude Code, MCP servers/connectors, Claude skills/plugins, or GitHub repos meant to extend
Claude Code — not the general Implementation Scout queue. 21 rows matched a keyword filter
(`claude code|claude skill|mcp|model context protocol|claude plugin|claude extension|
claude connector|claude desktop|anthropic|claude agent|subagent`); the ones below are the
genuinely distinct, actionable finds after reading each one's full vault note. Duplicative
or too-vague-to-act-on rows are omitted.

**Nothing here was installed or auto-suggested for install.** Same principle as the
Implementation Scout: verify before installing. Star counts, "X,000 stars on GitHub" claims,
etc. are the REEL's own claim, not independently confirmed by me — check the repo yourself
before trusting them.

Cross-referenced against `ReelBrain-Installed.md` (the vault's own "what's already set up"
file) to flag likely overlap.

---

## Ranked by relevance to THIS project's actual development

The ranking asks: would this have helped with the bugs/work we've actually done this
project — the Gemini billing/quota/deprecation/truncation debugging, the multi-file audits,
the systematic bug-hunting sessions? None of these tools touch the Gemini API directly
(they're Claude Code extensions, a different product), so "relevance" here means: would it
have made *me*, working on ReelBrain inside Claude Code, faster or better at that work.

### Tier 1 — plausibly would have helped with work we actually did

**1. Context7 MCP** — live, up-to-date API documentation lookup
- Source: [reel](https://www.instagram.com/reel/Da8IIonEhGR/) — "A curated list of essential MCP servers," one of several recommended (alongside GitHub, Playwright, Filesystem, Brave Search, Sentry, Linear).
- What it'd add: on-demand live docs instead of relying on training-data knowledge or ad-hoc `WebFetch` calls. This session and prior ones needed to verify real Gemini API behavior multiple times (model deprecation status, `response.model_version`, token-budget behavior) via manual diagnostic scripts and `WebFetch` — a live-docs MCP is the kind of tool that class of research is for.
- Already have something similar? Not listed in `ReelBrain-Installed.md`. No overlap.
- Status: comment-gated (`"MCP"` → Awaiting DM), not yet attached.

**2. Headroom** — strips unnecessary context before it reaches the model, cutting token cost
- Source: [reel](https://www.instagram.com/reel/Dad9-qLhmMM/) — claims ~37,000 GitHub stars (unverified).
- Named entities: `Headroom`, `Claude Code`, `GitHub`, `Anthropic`.
- What it'd add: reduces Claude Code's own context/token overhead — directly relevant to exactly the kind of long, multi-file-reading audit session Phase A of this task was (reading dozens of files across `app/`, `scripts/`, `tests/`). Not a Gemini-API fix, but a genuine "Claude Code capability" improvement in the literal sense the task asked for.
- Already have something similar? No.
- Status: comment-gated (`"repo"` → Awaiting DM), not yet attached. No direct link captured — comment-gated only.

**3. "Everything Claude" (github.com/affaan-m/ECC)** — a bundled pack of subagents, skills, slash commands, rules, hooks, and MCP servers
- Source: [reel](https://www.instagram.com/reel/DZpySOnOCxI/) — direct link given in the raw caption, not gated: **https://github.com/affaan-m/ECC**
- Claims (per the reel, unverified): 28 subagents, 119 skills, 60 slash commands, 34 rules, 20 hooks, 14 MCP servers, built during an Anthropic hackathon, ~200,000 GitHub stars.
- What it'd add: broad enough that some of its subagents/skills are plausibly relevant to debugging, testing, or git workflows — the kind of work this project's sessions are made of. The star-count claim is implausibly high for most repos of this type; verify independently before trusting it.
- Already have something similar? The Claude Code environment already has `review:bug-hunter`, `review:code-reviewer`, `sdd:*`, `tdd:*` and other plugin-provided agents/skills (visible in this session's own skill listing) — there may be real overlap. Check what "Everything Claude" actually contains before adding it.
- Status: already has a direct link, no DM needed.

### Tier 2 — genuinely useful, but ReelBrain has no web frontend to apply them to

**4. Five Claude MCP servers/skills for web design**
- Source: [reel](https://www.instagram.com/reel/Da36Q7_vQPK/) (value_score 5)
- Named: **shadcn MCP** (UI component library), **Vercel's web design guidelines skill**, **front end design skill**, **Chrome DevTools MCP** (lets Claude see and auto-fix a live browser render), **Magic MCP by 21st.dev** (backend/styling add-ons).
- What it'd add: high-quality, well-documented web design tooling. Chrome DevTools MCP is the one with the broadest applicability outside pure web-design work (any future dashboard/UI debugging).
- Already have something similar? `ReelBrain-Installed.md` lists `ui-styling`, `ui-ux-pro-max`, `design`, `design-system` skills already — likely covers *some* of this ground, but not these exact named MCP servers (shadcn MCP, Chrome DevTools MCP, Magic MCP specifically).
- Status: comment-gated (`"stack"` → Awaiting DM).

**5. Higgsfield MCP content pipeline** — 19 "skills" for autonomous video-ad generation
- Source: [reel](https://www.instagram.com/reel/DbJl-7UyIhq/)
- Already have something similar? **Likely overlap** — `ReelBrain-Installed.md` already lists a `higgsfeild (Custom)` MCP connector. Verify whether this is the same integration before adding a second one.
- Status: comment-gated, GitHub link not in the caption.

### Tier 3 — real and well-documented, but off-topic for a data-pipeline project

**6. video-use** — open-source Claude Code skill, MIT license, automates video editing (transcription, cut removal, color grading, rendering) via sub-parallel editor agents (Remotion, Manim, Hyperframe)
- Source: [reel](https://www.instagram.com/reel/DYf-n9PguD5/) (value_score 4)
- Not directly relevant to ReelBrain's own work (no video-editing task in this project), but it is a genuine, concretely-named, well-documented example of exactly what the task asked for ("Claude Code skill"). Comment-gated (`"VIDEO"`), no direct link captured.

**7. Scroll World** (github.com/oso95/scroll-world) — Claude Code/Codex skill generating animated 3D scroll-scrubbed landing pages
- Source: [reel](https://www.instagram.com/reel/Da8ey0fscUF/) + its **attached resource note**, which has the real link and install command already captured: `/plugin marketplace add oso95/scroll-world` or `npx skills add oso95/scroll-world`.
- Requires: `ffmpeg`, `ffprobe`, Python 3 + Pillow, an authenticated Higgsfield CLI account.
- Not relevant to ReelBrain specifically (no 3D web generation task here), but this is the most independently-verifiable entry on this list — a real resource note with the actual repo already fetched and summarized, not just a comment-gate promise.

### Tier 4 — conceptual only, no single installable artifact, or too thin to act on

**8. "Build a system around Claude" (persistent memory + MCP + sub-agent orchestration)**
- Source: [reel](https://www.instagram.com/reel/DazUc-EJCBk/) (value_score 5)
- Describes exactly the pattern already in active use: a `CLAUDE.md`-style persistent memory file, MCP tool access, and sub-agent orchestration for parallel work. Nothing new to install — this project (and this very session, via the `Agent` tool and the memory system already active) already does this. Worth reading for the tactical tips (scope the filesystem MCP to one folder, start every tool read-only, sub-agents cost more tokens — use them for real jobs not quick questions) but not an "add this" item.

**9. Impeccable** — described as "one of the most impressive Claude Code plugins," catches "AI slop" (generic-looking AI-generated output) before it ships
- Source: [reel](https://www.instagram.com/reel/DbcSTKNthGN/) — thin evidence: the extraction is truncated/incomplete (caption cut off, transcript "unavailable"), so this is a weak signal. Named for completeness; would need re-extraction (or opening the reel directly) to know what it actually does or how to get it.

**10. MCP server for detecting user friction in vibe-coded apps, feeding self-improvement prompts**
- Source: [reel](https://www.instagram.com/reel/DXwo4xAxmsG/) (value_score 4) — genuinely interesting workflow concept (analyze user drop-off, generate fix prompts, feed to a coding agent) but no concrete MCP server name is given — it's described generically, comment-gated behind `"software"`. Not actionable without more info.

---

## Not included

Several other keyword-matched rows were excluded as too tangential: reels where "Claude
Code" or "MCP" appeared only as one tool among many in a broader AI-tools roundup (e.g.
"15 Free Google AI Tools," the "JARVIS" agent-building guide, "3 YouTube videos to master
Claude Code" — generic learning-resource pointers with no specific installable artifact),
and rows too thin/degraded to say anything concrete about (e.g. "Top 10 Claude skills for
Designers," currently `pending-extraction` with no real content yet).

## Summary table

| # | Name | Type | Link captured? | Overlaps existing setup? |
|---|---|---|---|---|
| 1 | Context7 MCP | MCP server | No (gated) | No |
| 2 | Headroom | Token-cost tool | No (gated) | No |
| 3 | Everything Claude (ECC) | Subagents/skills/MCP bundle | **Yes** — github.com/affaan-m/ECC | Possibly — check against existing plugin agents |
| 4 | shadcn / Chrome DevTools / Magic MCP / Vercel + front-end skills | MCP servers + skills | No (gated) | Partial — have adjacent design skills, not these |
| 5 | Higgsfield MCP content pipeline | MCP server | No (gated) | **Likely** — already have a Higgsfield MCP connector |
| 6 | video-use | Claude Code skill | No (gated) | No |
| 7 | Scroll World | Claude Code skill | **Yes** — github.com/oso95/scroll-world | No |
| 8 | (pattern, not a tool) | Concept | N/A | Already in active use |
| 9 | Impeccable | Claude Code plugin | No — thin data | Unknown |
| 10 | (unnamed) MCP for vibe-coding friction detection | MCP server | No — no name given | Unknown |
