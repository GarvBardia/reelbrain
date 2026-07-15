# ReelBrain — Instagram Reel → Notion Knowledge Layer (₹0/month build)

## What this is
Personal knowledge-capture pipeline. Garv shares an Instagram reel (iOS share sheet or pasted link) → backend fetches media + caption → **one Gemini free-tier call transcribes the audio AND extracts structured takeaways** → writes a Notion database page. SQLite handles dedupe, embeddings, tag suggestions, and related-save lookups. Notion is the entire UI — there is **no frontend**.

## Hard requirements from the user
- **₹0/month.** Every component uses a free tier or open source. No paid APIs anywhere.
- **Fast:** respond 202 to the Shortcut instantly; full Notion page within ~30–60s.
- **Accurate, no slop:** schema-enforced structured output only (Gemini `response_schema`), pydantic-validated, one retry with validation errors, degrade gracefully — never invent content. Quotes must be verbatim from transcript. If audio is music-only, say so; don't hallucinate takeaways from nothing.

## Non-negotiable constraints
1. **NEVER automate Instagram actions with the user's account.** No auto-commenting, no main-account cookies, no instagrapi/private-API libraries. Comment-gated reels are human-in-the-loop.
2. Fetch uses yt-dlp with a BURNER account's cookies only. Enforce: refuse to start if cookie username matches the real-account guard env var; max 25 fetches/day (SQLite counter); ≥20s spacing; exponential backoff on 429/challenge; on repeated challenges surface "refresh burner cookies" — never retry-loop.
3. Fail soft: if fetch/extraction fails, still create the Notion entry with whatever exists (URL, caption) and status `⚠️ Failed — retry`. Never silently drop a capture.
4. Datacenter IPs get extra scrutiny from IG — keep volume low and spacing generous. This is the flakiest link; design every error path around it.

## Stack (all free)
- Python 3.11+, FastAPI, uvicorn, SQLite + sqlite-vec
- **Fetch:** yt-dlp (logged-out first, burner cookies retry) — free
- **ffmpeg:** strip audio to 16kHz mono m4a (small upload) — free
- **Transcription + extraction:** ONE call to **Gemini 2.x Flash free tier** with inline audio + caption + tag taxonomy, `response_schema`-enforced JSON (schema in DATA_SCHEMA.md §3, extended with a `transcript` field). Free tier limits (~1.5k req/day) are 75x our <20/day volume.
- **Embeddings:** Gemini embedding model free tier (768-dim) — no local model, no RAM pressure
- **Notion API** (official SDK) — free plan
- **Hosting:** Render free tier (no card required, simple GitHub-connected deploy). Accepts ~30–50s cold start on first capture after ~15min idle — acceptable at <20 captures/day. Ping-based keep-alive optional in Phase 2 if cold starts annoy you.

## Repo layout
```
reelbrain/
  app/main.py            # FastAPI: /capture /attach /retry
  app/fetcher.py         # yt-dlp burner fetch + safety caps
  app/gemini_pipe.py     # audio+caption → Gemini → validated JSON (transcript + extraction)
  app/store.py           # SQLite: saves, tags, embeddings, dedupe, related
  app/notion_writer.py   # Notion page creation/update
  app/models.py          # pydantic schemas
  prompts/extraction.md  # system prompt (versioned, editable)
  data/reelbrain.db
  .env.example
```

## Env vars
`GEMINI_API_KEY, NOTION_TOKEN, NOTION_DB_ID, BURNER_COOKIES_FILE, REAL_ACCOUNT_GUARD, CAPTURE_SECRET`

## Build order
Follow BUILD_SPEC.md phases strictly. Phase 1 end-to-end before any intelligence. Test with 3 real reels including one comment-gated and one music-only reel (must produce an honest "no spoken content" entry, not slop).

## Style
Small typed modules, boring and debuggable. Personal tool, not a product.
