# FRONTEND_DEPLOY.md — shipping Mycelium to GitHub Pages

Click-by-click. Assumes the FastAPI backend is already live on Render (see
`DEPLOYMENT.md`); this covers only what the frontend adds.

---

## 0. What changes, and the trade-off this hosting choice makes

| | Render (API) | GitHub Pages (frontend) |
|---|---|---|
| New env vars | `PUBLIC_CORS_ORIGINS`, optionally `PUBLIC_RATE_LIMIT_PER_MINUTE`, `PUBLIC_CACHE_TTL_SECONDS` | none — `NEXT_PUBLIC_API_BASE` is baked in by the GitHub Actions workflow |
| New endpoints | `/api/public/*` (read-only), `/api/admin/*` (secret-guarded) | — |
| Deploy trigger | manual env var change + redeploy | automatic on every push to `web/**` |

**GitHub Pages serves plain static files — there is no server.** That rules
out the usual "browser never holds the secret" pattern (a server-side proxy
that attaches a credential). The admin dashboard here instead asks for the
`CAPTURE_SECRET` once per browser session, keeps it in `sessionStorage`
(cleared when the tab closes, never written to a file or the JS bundle), and
sends it directly to the FastAPI backend on every admin call. The backend's
own constant-time secret check is what actually gates access — the frontend
never pretends otherwise. See `web/src/lib/admin-auth.ts` for the full
reasoning. If that trade-off is unacceptable for your threat model, host the
admin dashboard on something with a real server (Vercel, a small VPS, etc.)
and keep only the public marketing pages on GitHub Pages.

Every data-driven page (landing, library, scout queue) fetches the public API
directly from the browser, for the same reason: there is no server to
prerender against. This has an upside — the numbers are live on every visit,
not "fresh as of the last build."

---

## 1. Enable GitHub Pages on the repo

1. Repo → **Settings** → **Pages**.
2. Under **Build and deployment → Source**, choose **GitHub Actions** (not
   "Deploy from a branch" — the workflow here uses the official Pages Actions).
3. Save. Nothing deploys yet; that happens in step 3.

---

## 2. Render — add the CORS allow-list

The browser refuses to read API responses from a different origin unless the
API explicitly allows it.

1. Render dashboard → your `reelbrain` service → **Environment**.
2. Add:

   | Key | Value |
   |---|---|
   | `PUBLIC_CORS_ORIGINS` | `https://<your-github-username>.github.io` |

   Origin only — no path, even though the site itself is served under
   `/reelbrain/`. Comma-separate if you ever add another origin.

   Optional tuning, both with working defaults:

   | Key | Default | Purpose |
   |---|---|---|
   | `PUBLIC_RATE_LIMIT_PER_MINUTE` | `120` | Per-IP cap on the public read endpoints |
   | `PUBLIC_CACHE_TTL_SECONDS` | `300` | How long the API caches the Notion corpus |

3. **Save changes** → Render redeploys automatically.

---

## 3. Confirm the public API is live

```bash
curl https://<your-render-domain>/api/public/stats
```

Expect JSON with `total_reels`. If this 404s, the deploy predates the frontend
work — redeploy from the latest commit.

---

## 4. Deploy the frontend

Already automatic: `.github/workflows/deploy-pages.yml` runs on every push to
`main` that touches `web/**`, and can also be run by hand from the **Actions**
tab → *Deploy Mycelium to GitHub Pages* → **Run workflow**.

If your Render service has a different name than `reelbrain.onrender.com`,
edit the `NEXT_PUBLIC_API_BASE` line near the top of the workflow file before
the first run.

Watch the run in the **Actions** tab. On success the site is live at:

```
https://<your-github-username>.github.io/reelbrain/
```

(A trailing slash matters for the base path — `next.config.mjs` sets
`basePath: "/reelbrain"` specifically for this project-Pages URL shape.)

---

## 5. Verify the deployment

| Check | Expected |
|---|---|
| `/` | Hero, graph with coloured category nodes (loads a beat after the page, since it's a client fetch), non-zero live numbers |
| Click a graph node | Expands into that category's reels |
| `/library/` | Cards; search and category filters update the URL and results |
| `/scout/` | Ranked list, each with a "Next step" |
| `/admin/` | Secret-entry form |
| `/admin/dashboard/` with no secret in sessionStorage | Redirects to `/admin/` |
| `/admin/dashboard/` after entering the correct `CAPTURE_SECRET` | Health panel, counters, unredacted queue |
| `/admin/dashboard/` after entering a wrong secret | "Incorrect secret." on the login form, never reaches the dashboard |

---

## 6. Free-tier behaviour worth knowing

**Render sleeps after ~15 minutes idle.** The first request then takes ~30s.
Because every page here fetches client-side, a visitor hitting a sleeping
backend sees the loading skeleton for that ~30s rather than an instant (but
stale) render — a direct consequence of static hosting having no server-side
cache layer to hide the cold start behind. The existing `/ping` keep-alive job
(`SCHEDULING.md`) mitigates this; keep it running.

**GitHub Pages** itself has no meaningful free-tier limits for a project this
size (soft caps around 100GB bandwidth/month, 1GB site size — this site is a
few hundred KB of JS).

---

## 7. Custom domains (optional)

- **Frontend**: repo → Settings → Pages → **Custom domain**. Update
  `PUBLIC_CORS_ORIGINS` on Render to match.
- **API**: Render → Settings → Custom Domain, e.g.
  `api.mycelium.<yourdomain>`. Then update `NEXT_PUBLIC_API_BASE` in
  `.github/workflows/deploy-pages.yml` and `PUBLIC_CORS_ORIGINS` on Render.
  This is also the only way to stop the internal `reelbrain` codename
  appearing in network requests.

---

## 8. Rollback

Actions tab → find the last known-good *Deploy Mycelium to GitHub Pages* run →
**Re-run all jobs**. This rebuilds and republishes that commit's `web/`
directory as-is.
