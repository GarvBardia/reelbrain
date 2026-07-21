# CHEAP_MODEL_GUIDE.md — extraction spec for a smaller/cheaper model

This is a **self-contained** instruction set for producing ReelBrain's structured
extraction from an Instagram reel's transcript + caption. A smaller model that
follows this exactly will produce output consistent with the main pipeline. No
outside reasoning required — everything you need is here.

Your job: read a reel's **transcript** (spoken audio, may be empty) and **caption**
(the post text), and return ONE JSON object matching the schema below. That's it.

---

## 1. Output schema (return EXACTLY this shape — valid JSON, no markdown, no prose)

```json
{
  "transcript": "string — verbatim transcription of the spoken audio, or \"\" if none",
  "has_speech": true,
  "main_point": "string, ONE sentence, MAX 200 chars",
  "supporting_points": ["string", "... 0 to 6 items"],
  "resources_mentioned": [
    {"name": "string", "type": "tool|book|site|person|course|other", "url_if_stated": "string or null"}
  ],
  "steps_or_framework": ["string — empty unless the reel teaches a concrete procedure"],
  "quotable_lines": ["string — verbatim from transcript only, 0 to 3 items"],
  "topic_tags": ["string — 3 to 6, lowercase-kebab-case"],
  "content_type": "tutorial|insight|resource_drop|motivation|news|entertainment|unknown",
  "comment_gate": {
    "detected": false,
    "keyword": "string or null",
    "promised_resource": "string or null"
  },
  "value_score": 3,
  "language": "en"
}
```

Field constraints (these are enforced — violating them fails validation):
- `main_point`: required, ≤200 chars, exactly one sentence.
- `supporting_points`: 0–6 items.
- `quotable_lines`: 0–3 items, each a **verbatim substring of the transcript** (never the caption, never paraphrased).
- `content_type`: must be one of the 7 listed values.
- `value_score`: integer 1–5.
- Do **NOT** output a `priority` field — it is computed afterward (see §4), not by you.

---

## 2. The extraction prompt (what to tell the model)

> You are transcribing and extracting takeaways from a single Instagram Reel for a
> personal knowledge base. Output must match the provided JSON schema exactly.
>
> **Anti-slop rules (follow strictly):**
> - `transcript` is a verbatim transcription of the spoken audio only. If there is no
>   speech (music, ambient, silence), set `transcript` to `""` and `has_speech` to `false`.
> - If `has_speech` is `false`: derive `main_point` from the caption only, and leave
>   `supporting_points`, `steps_or_framework`, and `quotable_lines` empty. Do not invent
>   spoken content that isn't there.
> - `quotable_lines` must be verbatim substrings of `transcript` only — never paraphrase
>   into a quote, never pull a quote from the caption.
> - Never pad a list to "fill out" the schema. Zero items is a correct answer when there
>   is nothing genuinely worth listing.
> - `steps_or_framework` stays empty unless the reel teaches a concrete procedure/method.
> - `value_score` of 1 is correct and expected for pure music/aesthetic/vibe reels with
>   no informational content — do not inflate it.
> - `topic_tags`: prefer the taxonomy candidates provided (most-used existing tags) when
>   they genuinely fit; only introduce a new tag if none fit. Lowercase, kebab-case, 3–6 tags.
> - `comment_gate`: set `detected: true` only if the creator is explicitly asking viewers
>   to comment a word/phrase to receive something in DM. Extract the exact `keyword` and
>   what was promised in `promised_resource`.
>
> Return only the structured JSON. No markdown, no commentary, no code fences.

**Photo/carousel posts (caption only, no video):** use the same schema, but tell the
model there is NO audio — `transcript` must be `""`, `has_speech` `false`, and
`main_point`/topics come from the caption alone. If the caption is thin/vague, say so
honestly in `main_point` rather than inventing detail. (Do not attempt extraction at all
if the caption is under ~10 words — return the placeholder "No caption or transcript
available." instead.)

---

## 3. value_score rubric (1–5)

- **1** — pure entertainment/aesthetic/vibe, no informational content.
- **2** — mildly interesting but little actionable takeaway.
- **3** — solid, a real point worth remembering (this is the neutral default).
- **4** — genuinely useful: a concrete tool, method, or insight you'd act on.
- **5** — high-signal: a complete workflow, framework, or resource worth revisiting.

---

## 4. Priority (computed AFTER extraction — do not output it, but this is the rule)

`priority` is derived from `topic_tags` + `value_score`, plain text `"High"|"Medium"|"Low"`:

```
CLAUDE_KEYWORDS = ["claude", "claude-ai", "claude-code", "anthropic", "claude-skills", "mcp"]

priority = "High"    if ANY topic_tag contains any CLAUDE_KEYWORD as a
                        case-insensitive substring  OR  value_score >= 4
         = "Medium"  if value_score == 3
         = "Low"     otherwise
```

(Substring match: topic `"claude-code-tips"` matches `"claude"` and `"claude-code"`.)

---

## 5. comment-gate rules (the "comment X for the link in DM" pattern)

Set `comment_gate.detected = true` ONLY when the creator explicitly asks viewers to
comment a word/phrase to get something DM'd. Extract:
- `keyword` — the exact magic word (e.g. `SEND`, `GUIDE`, `International`).
- `promised_resource` — what they'll DM (e.g. "AI workflow doc", "install guide").

A deterministic regex backstop also runs; the model's own judgment is merged with it —
whichever detects a gate wins. The backstop matches:
1. **Quoted keyword, any case:** `Comment "International" for free Guide` → keyword `International`.
2. **Unquoted ALL-CAPS word:** `comment SEND below` → keyword `SEND`. (Unquoted lowercase
   like "comment your thoughts" must NOT match — that's ordinary prose.)
3. **Emoji-drop-for-DM:** `Drop your 🔥 emoji to grab all in ur dms` → keyword `🔥`.
   (Requires verb "drop" + emoji token + the literal word "emoji" + "dm/dms" nearby —
   ordinary "drop a 🔥 if you agree" must NOT match.)

**INVARIANT (must never be violated):** if `keyword` is set, `detected` MUST be `true`.
The two can never disagree. If you set a keyword, set detected true.

---

## 6. Notion field mapping (how the JSON becomes a Saves row)

| Notion property | Type | Source |
|---|---|---|
| Title | title | `main_point` (truncated to 100 chars) |
| Status | select | routed — see §7 |
| Content type | select | `content_type` |
| Topics | multi-select | `topic_tags` |
| Value score | select | `str(value_score)` — "1".."5" |
| Priority | select | computed (§4) — "High"/"Medium"/"Low" |
| Comment gate | **checkbox** | `comment_gate.detected` (boolean — NOT a string) |
| Gate keyword | rich_text | `comment_gate.keyword` |
| Gate resource | url | attached later by /attach (the DM'd link) |
| Reel URL | url | the permalink |
| Shortcode | rich_text | the reel shortcode |
| My note | rich_text | user's capture-time note |
| Posted at | date | post timestamp, if known |
| Creator | relation | creator page |

Page body blocks: a callout with `main_point`; bulleted `supporting_points`; numbered
`steps_or_framework`; bookmark/paragraph per `resources_mentioned`; quote blocks for
`quotable_lines`; a "Transcript" toggle; a "Raw caption" toggle.

Note on "Comment gate": it is a genuine **Checkbox** property. Some Notion tools RENDER
checkbox values as the literal strings `__YES__`/`__NO__` — that is a display convention
of those tools, NOT the stored value. Always write a real boolean.

---

## 7. Status routing (which Status the row gets), in priority order

```
if photo/carousel post with no video  -> "📷 Photo — manual"
elif comment_gate.detected            -> "⏳ Awaiting DM"      (user must do the DM step)
elif value_score <= 2                 -> "🗑 Low signal"
else                                  -> "📥 Inbox"
```

Other statuses exist but are set by workflows, not extraction: `⚠️ Failed — retry`
(fetch failed), `🕳 Gate expired` (Awaiting DM >7 days, never attached), `🗄 Archived`
(auto-archived low-value after 30 days), `✅ Processed/Reviewed` (manual).

---

## 8. Worked example

**Input** — caption: *"Comment 'STACK' and I'll DM you the 5 Claude MCP servers every web
designer needs."*; transcript: *"These five MCP servers completely change how Claude
builds websites. First, ..."*

**Output:**
```json
{
  "transcript": "These five MCP servers completely change how Claude builds websites. First, ...",
  "has_speech": true,
  "main_point": "Five essential Claude MCP servers that web designers should install to improve AI-generated sites.",
  "supporting_points": ["Each server adds a specific capability to Claude's web workflow"],
  "resources_mentioned": [],
  "steps_or_framework": [],
  "quotable_lines": ["These five MCP servers completely change how Claude builds websites."],
  "topic_tags": ["claude-ai", "mcp-servers", "web-design", "developer-tools"],
  "content_type": "resource_drop",
  "comment_gate": {"detected": true, "keyword": "STACK", "promised_resource": "5 Claude MCP servers list"},
  "value_score": 5,
  "language": "en"
}
```
Then computed: `priority = "High"` (topic `claude-ai` matches a CLAUDE_KEYWORD, and
value_score 5 ≥ 4). Status: `⏳ Awaiting DM` (comment gate detected).
