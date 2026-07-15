# OPEN_QUESTIONS.md — remaining decisions (most are now locked)

## LOCKED (from your answers)
- **Hosting:** Render free tier (no card, simple deploy). Cold starts (~30–50s after idle) accepted as a trade-off.
- **Extraction + transcription:** Gemini 2.x Flash free tier, one call, schema-enforced JSON.
- **Embeddings:** Gemini embedding free tier (768-dim).
- **Fetch:** yt-dlp + burner cookies (free), safety caps enforced.
- **Volume:** <20/day — comfortably inside every free-tier limit.
- **Storage:** Notion (free plan).
- **Total recurring cost: ₹0.**

## STILL OPEN — answer before build
1. **Validate yt-dlp FIRST (15 min, before any code).** Create a burner IG account, export cookies (cookies.txt browser extension), then test the fetch. Render's free tier doesn't give you SSH shell access the way a VM does, so test locally on your laptop first with `yt-dlp --cookies cookies.txt --dump-json <reel_url>` on 3 reels — this validates the cookies/burner account work at all. The real test happens once Phase 1 is deployed to Render (its outbound IP is what matters for IG's scrutiny) — if fetches fail only in production, that's a datacenter-IP block, and the fallback is the paid data-API (still nearly free) or hybrid mode (fetch step runs from your laptop as a small worker instead).
2. **Oracle Cloud signup friction.** Requires a card for identity verification (not charged). OK with that? If not → Render free tier and accept cold starts.
3. **Gemini API key.** Create at aistudio.google.com — free, 2 minutes. Confirm free tier is available on your Google account/region.
4. **Notion databases.** Create manually from DATA_SCHEMA §1, or let the build script create them via API on first run? (Recommend: script creates them — one less manual step.)
5. **Capture secret vs Cloudflare Tunnel.** Shared secret in the Shortcut is the simple plan. Fine, or want Tunnel+Access hardening?
6. **Tag seed list.** Give ~15 starting tags matching how you think (ai-workflows, dsa, fitness, real-estate-marketing, content-creation, music, skincare, finance…) so the taxonomy converges from day one.
7. **Creator account later?** Phase 4's automatic DM capture needs converting your IG to Creator + Meta app review (free, but days–weeks). Decide only when Phase 3 is done.
