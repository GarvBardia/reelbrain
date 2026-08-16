# FRONTEND_DEPLOY.md — shipping Mycelium to Vercel

Click-by-click. Assumes the FastAPI backend is already live on Render (see
`DEPLOYMENT.md`); this covers only what the frontend adds.

---

## 0. What changes on each side

| | Render (API) | Vercel (frontend) |
|---|---|---|
| New env vars | `PUBLIC_CORS_ORIGINS`, optionally `PUBLIC_RATE_LIMIT_PER_MINUTE`, `PUBLIC_CACHE_TTL_SECONDS` | `NEXT_PUBLIC_API_BASE`, `ADMIN_PASSWORD`, `ADMIN_API_SECRET` |
| New endpoints | `/api/public/*` (read-only), `/api/admin/*` (secret-guarded) | — |
| Redeploy needed | yes | n/a (first deploy) |

---

## 1. Render — add the CORS allow-list

The browser will refuse to read API responses from a different origin unless
the API says it may. Until step 3 you do not know the Vercel URL, so this is a
two-pass step: deploy the frontend first, then come back and set this.

1. Render dashboard → your `reelbrain` service → **Environment**.
2. Add:

   | Key | Value |
   |---|---|
   | `PUBLIC_CORS_ORIGINS` | `https://<your-vercel-domain>` (comma-separate multiples) |

   Optional tuning, both with working defaults:

   | Key | Default | Purpose |
   |---|---|---|
   | `PUBLIC_RATE_LIMIT_PER_MINUTE` | `120` | Per-IP cap on the public read endpoints |
   | `PUBLIC_CACHE_TTL_SECONDS` | `300` | How long the API caches the Notion corpus |

3. **Save changes** → Render redeploys automatically.

`localhost:3000` and any `*.vercel.app` preview domain are allowed
unconditionally in code, so previews work without touching this variable.

---

## 2. Confirm the public API is live

```bash
curl https://<your-render-domain>/api/public/stats
```

Expect JSON with `total_reels`. If this 404s, the deploy predates the frontend
work — redeploy from the latest commit.

---

## 3. Vercel — import the project

1. Go to <https://vercel.com/new> and import the GitHub repo.
2. **Root Directory** — click *Edit* and set it to **`web`**. This is the one
   setting people miss; the repo root is a Python project and the build fails
   without it.
3. Framework preset auto-detects as **Next.js**. Leave build/output commands
   at their defaults.
4. Add environment variables (apply to Production, Preview and Development):

   | Key | Value | Notes |
   |---|---|---|
   | `NEXT_PUBLIC_API_BASE` | `https://<your-render-domain>` | **No trailing slash** |
   | `ADMIN_PASSWORD` | a long random passphrase | Gates the `/admin` login form |
   | `ADMIN_API_SECRET` | the *same value* as `CAPTURE_SECRET` on Render | Never sent to the browser |

   Generate a password with:

   ```bash
   python -c "import secrets; print(secrets.token_urlsafe(32))"
   ```

5. **Deploy**.

---

## 4. Close the CORS loop

Vercel now shows the production domain. Go back to Render, set
`PUBLIC_CORS_ORIGINS` to that `https://…` origin, and save.

Verify from the browser console on the live site:

```js
fetch(`${location.origin}`) // sanity
fetch("https://<your-render-domain>/api/public/stats").then(r => r.json()).then(console.log)
```

A CORS error here means the origin string does not match exactly — check for a
trailing slash or `http` vs `https`.

---

## 5. Verify the deployment

| Check | Expected |
|---|---|
| `/` | Hero, graph with coloured category nodes, non-zero live numbers |
| Click a graph node | Expands into that category's reels |
| `/library` | Cards; search and category filters change the URL and the results |
| `/scout` | Ranked list, each with a "Next step" |
| `/admin` | Login form |
| `/admin/dashboard` while logged out | Redirects to `/admin` |
| `/api/admin/overview` while logged out | `{"error":"unauthorized"}` |
| `/admin/dashboard` after login | Health panel, counters, unredacted queue |

---

## 6. Free-tier behaviour worth knowing

**Render sleeps after ~15 minutes idle.** The first request then takes ~30s.
This is largely hidden because every public page is server-rendered with ISR
(`revalidate = 300`) — visitors get cached HTML while Vercel refreshes in the
background. Two consequences:

- If Render happens to be asleep during a Vercel build, pages bake with empty
  state and self-heal at the next revalidation (≤5 min). Redeploy if you want
  it immediate.
- The existing `/ping` keep-alive job (see `SCHEDULING.md`) already mitigates
  this. Keep it running.

**Vercel free tier** is ample here: five routes, no images, no server-side
compute beyond ISR regeneration and the admin proxy handlers.

---

## 7. Custom domains (optional)

- **Frontend**: Vercel → Project → Settings → Domains.
- **API**: Render → Settings → Custom Domain, e.g.
  `api.mycelium.<yourdomain>`. Then update `NEXT_PUBLIC_API_BASE` on Vercel and
  `PUBLIC_CORS_ORIGINS` on Render. This is also the only way to stop the
  internal `reelbrain` codename appearing in network requests.

---

## 8. Rollback

Vercel keeps every deployment. Project → Deployments → the last good one →
**Promote to Production**. No rebuild, effectively instant.
