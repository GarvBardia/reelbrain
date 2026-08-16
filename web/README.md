# Mycelium — public frontend

Next.js (App Router, static export) + Tailwind + shadcn/ui, deployed to
**GitHub Pages** via `.github/workflows/deploy-pages.yml`, reading the
read-only public API served by the FastAPI backend on Render.

## Naming

The product is **Mycelium**. The Python package, the repo directory, the Render
service and the SQLite file are still named `reelbrain` — that is the internal
codename, deliberately left alone (see `app/public_api.py`'s module docstring
for the reasoning). Nothing a visitor can read says "reelbrain".

## Why static export

GitHub Pages serves plain files — there is no Node server behind it. Two
consequences shape this codebase:

1. **Every data-driven page is a client component.** No Server Components
   fetching at request time, no ISR, no route handlers, no middleware —
   `next.config.mjs` sets `output: "export"`, which forbids all of those.
   Pages fetch `app/public_api.py`'s endpoints directly from the browser on
   mount (see `src/lib/use-api.ts`), which has a real upside: the numbers on
   the site are live on every visit, not "fresh as of the last deploy."
2. **The admin dashboard holds its own secret client-side.** With no server
   to proxy through, the usual "browser never sees the credential" pattern
   isn't achievable. See `src/lib/admin-auth.ts` for exactly what trade-off
   that is and why it's stated there rather than hidden.

## Component libraries

shadcn/ui, Magic UI and Aceternity UI are all **copy-the-source-in** libraries,
not npm packages. Their components therefore live in this repo as ordinary
files you own and can edit:

| Library       | Where                          | What                                    |
|---------------|---------------------------------|-----------------------------------------|
| shadcn/ui     | `src/components/ui/`           | Button, Card, Badge, Input              |
| Magic UI      | `src/components/magic/`        | BlurFade (scroll reveal), NumberTicker   |
| Aceternity UI | `src/components/aceternity/`   | Spotlight (used once, on the hero)      |
| obsidianui.dev | `src/components/obsidian/`    | LightLines (hero background), GlowingEffect (graph card border) |

### obsidianui components: adopted vs rejected

Pulled from `https://obsidianui.dev/r/<name>.json` (the same source
`shadcn add` reads). `shadcn init` was deliberately not run -- it rewrites
`globals.css` and `tailwind.config.ts`, both hand-tuned here, and the registry
JSON contains the identical source.

**Adopted:** `light-lines`, `glowing-effect`.

**Evaluated and rejected**, since these demos are overwhelmingly designed
against dark backgrounds and this site is white-first:

| Component | Why not |
|---|---|
| `sine-wave` | Not a background at all — it lays out an array of **images** (`items: {image}[]`, `next/image`) along a sine curve. Wrong tool for "animated background texture". |
| `perspective-grid` | Genuinely light-mode-first, but renders `gridSize²` divs (1600 at default) where the existing CSS dot-grid costs **zero** DOM nodes, on a hero already running a canvas simulation. Its 3D vanishing-point look also fights the graph for depth cues. |
| `mask-cursor-effect` | Reveals a *second* hidden layer through a cursor circle — requires inventing alternate hero content that serves no message. |
| `eagle-vision` | Hardcodes `white` ring borders (invisible on white) and a `#00ff00` HUD accent; the targeting-reticle aesthetic clashes with "premium and quiet". |
| `horizontal-scroll` | The category cards exist to show **breadth at a glance**; hiding most of them behind a horizontal gesture works against that. |
| `interactive-book` | The how-it-works page's vertical "hypha" strand *is* the mycelium metaphor — one continuous growing organism. A paginated book is a discrete, sequential metaphor that fights it, and it needs a cover image that doesn't exist. |
| `glitch-text` | A deliberately-corrupted look contradicts the product's core claim of honest, reliable extraction. |
| `flip-text` | Clean and light-safe, but every section already carries exactly one effect. Adding it would break the one-standout-effect-per-section rule for no gain. |

**Note for anyone adding more of these:** `LightLines` paints a full-bleed
`linear-gradient` container background from its `gradientFrom`/`gradientTo`
props — their *only* use. Left at the upstream blue defaults it covers the
entire hero and the white base disappears. They are set to `transparent` at
the call site for exactly that reason.

## Local development

```bash
cp .env.local.example .env.local   # then edit it if your API isn't on :8000
npm install
npm run dev
```

The backend must be running for real data. From the repo root:

```bash
python -m uvicorn app.main:app --port 8000
```

With no backend reachable, every page still renders — the fetch helpers in
`src/lib/api.ts` fall back to empty state rather than throwing, so a sleeping
free-tier Render instance degrades to zeroes instead of a broken page.

`npm run dev` serves at the plain root (`localhost:3000`); the GitHub Pages
`basePath` (`/reelbrain`) only applies to CI builds (`next.config.mjs` checks
`GITHUB_ACTIONS`), so local links and asset paths work without it.

## Environment variables

| Variable | Where | Purpose |
|---|---|---|
| `NEXT_PUBLIC_API_BASE` | `.env.local` (dev) / the GitHub Actions workflow (prod) | Base URL of the FastAPI backend, no trailing slash |

That's the only one. `NEXT_PUBLIC_`-prefixed values are always inlined into
the JS bundle at build time by Next — true on Vercel too, GitHub Pages just
makes the "this is public" fact explicit instead of implicit, since it's
literally baked into a static file anyone can view-source. There is nothing
sensitive to configure: the admin secret is entered by the operator into the
`/admin` form each session (see `src/lib/admin-auth.ts`), never stored in a
build artifact.

## Admin auth model

```
browser --(CAPTURE_SECRET, entered once)--> sessionStorage
browser --(secret in x-admin-secret header)--> FastAPI (Render), directly
```

No cookie, no server-side session, no proxy — there is no server on this side
to hold either. The backend's own constant-time secret comparison
(`app/main.py`'s `_check_secret`) is the actual gate; the frontend just
attaches whatever is in `sessionStorage` and reacts to a 401 by clearing it
and redirecting to `/admin`. Full reasoning, including what this trades away
versus a server-proxied design, is in `src/lib/admin-auth.ts`.

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
- Three pages (`how-it-works`, `library`, `scout`) split into a thin Server
  Component `page.tsx` (exports `metadata` for the `<title>`) wrapping a
  client `*-client.tsx` that does the actual fetching — Next requires
  `metadata` exports to come from a Server Component, so the split is
  structural, not stylistic. The landing page needs no override (the root
  layout's default title already fits it), so it has no such split.
