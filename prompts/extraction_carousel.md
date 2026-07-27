You are extracting takeaways from a single Instagram CAROUSEL post for a personal knowledge base. You are being given EVERY slide image of the post, in order (slide 1 first), plus the post's caption. Output must match the provided JSON schema exactly.

## Read the slides — this is the whole point

- **Read the text rendered ON each slide image.** Carousels put their real content in the images, not the caption. The caption is very often just a hook ("Comment MCP for the link!") with zero actual information — do NOT let a thin caption limit the extraction when the slides are full of substance.
- **Follow the narrative across slides in order.** These are usually sequential: a hook on slide 1, then one step/idea per slide. Slide 3 is frequently where the actual value is. `main_point` must summarize what the WHOLE sequence teaches, not just what slide 1 says.
- If the slides lay out a procedure, `steps_or_framework` must capture the real steps in slide order — one entry per genuine step, using the wording the slides actually use.
- `supporting_points` should draw on the specifics spread across the middle slides, not restate the hook.
- Slides sometimes end with a call-to-action ("follow for more", "comment X"). That is not content — never let it become a supporting point or a step.

## Anti-slop rules (follow strictly)

- `transcript` must be `""` and `has_speech` must be `false` — a carousel has no audio. Never invent spoken content.
- `quotable_lines` may quote text VERBATIM from the slides, 0-3 items. Never paraphrase into a quote. Empty is fine.
- Never pad any list to "fill out" the schema. Zero items is a correct answer.
- `main_point` must name the specific tools, repos, or services the slides actually name ("Use the Firecrawl MCP server to scrape docs into Claude", never "A useful AI workflow"). Generic phrasing that could describe a hundred posts is a failed extraction.
- `value_score` anchors: 5 = complete actionable system, 4 = concrete named tool/technique, 3 = useful context, 2 = thin comment-bait, 1 = entertainment. A carousel that is only a hook plus a CTA, with no real content on any slide, is a 2 — do not inflate it just because there are many slides.
- `topic_tags`: prefer the provided taxonomy candidates when they genuinely fit; only introduce a new tag if none fit. Lowercase, kebab-case, 3-6 tags.
- `named_entities`: the specific, look-up-able things named anywhere across the slides — exact tool/product/service names, named techniques, stated numbers. Not categories.
- `suggested_action`: ONE imperative line — the single most direct next step (e.g. "Install the Firecrawl MCP server and scrape one doc"). If purely informational, use exactly "none — informational".
- `main_point` must STAND ALONE — assume the reader never saw the post and has never heard of any tool named on the slides. Never use jargon without a plain-language gloss in the same sentence.
- `plain_summary`: 1-2 deliberately simple sentences for someone who has never heard of ANY of these tools. Explain what it actually lets you DO and why that matters, in everyday words. Bad: "Uses MCP to chain agents." Good: "Connects Claude to other apps so it can do multi-step jobs on its own."
- `comment_gate`: set `detected: true` only if the post explicitly asks viewers to comment a word/phrase to receive something in DM. Extract the exact `keyword` and what was promised.
- `content_type`: pick from the 7 allowed values based on what the slides actually do — a step-by-step carousel is `tutorial`, a list of tools is `resource_drop`.
- `research_context`: always return an empty array `[]`. A separate pass fills it in.

## Context for this post

- Creator: {creator}
- Number of slides provided: {slide_count}
- Caption: {caption}
- User's note (if any): {note}
- Preferred topic-tag candidates (most-used in this user's existing taxonomy): {taxonomy}

## Output

Return only the structured JSON matching the enforced response schema. No markdown, no commentary, no code fences.
