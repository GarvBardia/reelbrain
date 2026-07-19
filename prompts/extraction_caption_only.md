You are analyzing a single Instagram photo/carousel post's CAPTION ONLY for a personal knowledge base. This post has no video — Instagram photo/carousel posts can never be auto-transcribed — so there is no audio and nothing else available to you, only the caption text below. Output must match the provided JSON schema exactly.

## Anti-slop rules (follow strictly)

- `transcript` must be `""` and `has_speech` must be `false` — there is no audio for this post at all. Never invent spoken content.
- `main_point` must be derived ONLY from the caption text below. If the caption is thin or vague, say so honestly in `main_point` rather than inventing detail that isn't there.
- `supporting_points`, `steps_or_framework`, and `quotable_lines` must stay empty unless the caption itself genuinely contains that content — never invent or infer beyond what's written in the caption.
- `quotable_lines`, if used at all, must be verbatim substrings of the caption only.
- Never pad any list to "fill out" the schema. Zero items is a correct answer when the caption doesn't support more.
- `value_score` of 1 is correct and expected when the caption carries little informational content — do not inflate it just because something must be scored.
- `topic_tags`: prefer the provided taxonomy candidates below when they genuinely fit; only introduce a new tag if none of the candidates fit. Lowercase, kebab-case, 3-6 tags.
- `comment_gate`: set `detected: true` only if the caption is explicitly asking viewers to comment a word/phrase to receive something in DM. Extract the exact `keyword` if stated, and what was promised in `promised_resource`.

## Context for this post

- Creator: {creator}
- Caption: {caption}
- User's note (if any): {note}
- Preferred topic-tag candidates (most-used in this user's existing taxonomy): {taxonomy}

## Output

Return only the structured JSON matching the enforced response schema. No markdown, no commentary, no code fences.
