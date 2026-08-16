# Mycelium — public frontend

Next.js (App Router) + Tailwind + shadcn/ui, deployed to Vercel, reading the
read-only public API served by the FastAPI backend on Render.

## Naming

The product is **Mycelium**. The Python package, the repo directory, the Render
service and the SQLite file are still named `reelbrain` — that is the internal
codename, deliberately left alone (see `app/public_api.py`'s module docstring
for the reasoning). Nothing a visitor can read says "reelbrain".

The one place the old name remains visible to a curious visitor is the API
hostname in network requests (`reelbrain.onrender.com`). If that matters,
attach a custom domain such as `api.mycelium.<yourdomain>` to the Render
service and point `NEXT_PUBLIC_API_BASE` at it — no code change needed.

## Component libraries

shadcn/ui, Magic UI and Aceternity UI are all **copy-the-source-in** libraries,
not npm packages. Their components therefore live in this repo as ordinary
files you own and can edit:

| Library       | Where                          | What                                    |
|---------------|--------------------------------|-----------------------------------------|
| shadcn/ui     | `src/components/ui/`           | Button, Card, Badge, Input              |
| Magic UI      | `src/components/magic/`        | BlurFade (scroll reveal), NumberTicker   |
| Aceternity UI | `src/components/aceternity/`   | Spotlight (used once, on the hero)      |

## Local development

```bash
cp .env.local.example .env.local   # then edit it
npm install
npm run dev
```

The backend must be running for real data. From the repo root:

```bash
python -m uvicorn app.main:app --port 8000
```

With no backend reachable, every page still renders — the fetch helpers in
`src/lib/api.ts` fall back to empty state rather than throwing, so a sleeping
free-tier Render instance degrades to zeroes instead of a 500.

## Environment variables

| Variable | Where | Public? | Purpose |
|---|---|---|---|
| `NEXT_PUBLIC_API_BASE` | Vercel | yes (in bundle) | Base URL of the FastAPI backend, no trailing slash |
| `ADMIN_PASSWORD` | Vercel | **no** | Password for the `/admin` login form |
| `ADMIN_API_SECRET` | Vercel | **no** | Must equal `CAPTURE_SECRET` on Render |

`ADMIN_API_SECRET` is deliberately **not** prefixed `NEXT_PUBLIC_` — that prefix
is what inlines a value into the client bundle. It is read only inside route
handlers (`src/lib/admin-proxy.ts`), attached to server-to-server requests, and
never included in a response body.

## Admin auth model

```
browser --(password)------> /api/admin/login      (Next.js server)
        <--(httpOnly cookie)--
browser --(cookie)--------> /api/admin/*          (Next.js server)
                            + ADMIN_API_SECRET header/body
                            ---------------------> FastAPI (Render)
```

The browser never holds the backend secret. The session cookie is an HMAC of a
fixed payload keyed by `ADMIN_PASSWORD`, so it is unforgeable without the
password and needs no session store. `src/middleware.ts` gates the dashboard
route; each `/api/admin/*` handler **re-checks** the session itself rather than
trusting middleware, because a handler that forwards a real credential should
not delegate its own authorization.

## Notes

- `next@14.2.35` is pinned. `npm audit` reports a transitive `postcss`
  advisory inside Next's own dependency tree; it concerns parsing
  attacker-controlled CSS at **build time**, and all CSS here is authored in
  this repo. The only "fix" npm offers is Next 16, a major breaking change.
  Next 15 does not resolve it either — it merely relocates the advisory to
  `sharp` (image optimization, unused here, since the site ships no images).
- The graph (`react-force-graph-2d`) is dynamically imported with `ssr: false`
  — it touches `window` and a canvas at module scope.
- Below 768px the graph renders as a tappable list instead of a canvas. See
  `src/components/graph-fallback-list.tsx` for why.
