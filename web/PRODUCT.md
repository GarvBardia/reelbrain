# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

Primary: engineers and recruiters evaluating this project as a campus-placement
portfolio piece — a technical audience assessing build quality, taste, and depth of
execution, not just the surface pitch. Secondary: the builder's own ongoing use of
the tool as a real, working knowledge base (this is not a fake demo product — the
data on the site is live and real).

## Product Purpose

Mycelium turns saved Instagram content (Reels, photo/carousel posts) into a
self-organizing, browsable knowledge network. Every save is read, categorized
against a shared taxonomy, linked to related saves, and (where relevant) given a
concrete next step — instead of accumulating as a dead bookmarks folder.

## Positioning

Not a bookmarking tool with tags bolted on. The categorization and connections are
generated automatically from content understanding, the taxonomy converges rather
than sprawling per-user, and the highest-value items are surfaced into an
actionable queue (Scout) rather than left to be rediscovered by scrolling.

## Operating Context

- Frontend: Next.js 14 App Router, static export (`output: "export"`), deployed to
  GitHub Pages. No server on this side — every data-driven page fetches a read-only
  public API directly from the browser on mount.
- Backend: FastAPI on Render, serving `/api/public/*` (redacted/public-safe) and
  `/api/admin/*` (unredacted, behind a shared-secret header) over the same
  underlying Notion-backed data store.
- Surfaces: landing page (graph as centerpiece), how-it-works, a searchable/filterable
  library, a Scout queue, and an admin dashboard (operator-only, not part of the
  public-facing evaluation surface).
- Evaluated primarily as a live, working site — not a static mockup — so real
  behavior (loading/error/empty states, live numbers, actual graph interaction)
  is part of what's being judged.

## Capabilities and Constraints

- Confirmed: real live data (157+ saves, 13 categories at time of writing), a
  category-level co-occurrence graph (`react-force-graph-2d`), category → reel
  drill-down, comment-gate detection/routing for "comment X for DM" content.
  Data volume is modest (double-to-low-triple-digit save counts), not
  web-scale — design and interaction choices should assume that scale honestly
  rather than over-engineer for one that doesn't exist yet.
- Constraint: no server at runtime (GitHub Pages static hosting) — no ISR,
  middleware, or route handlers; all data-driven pages are client components.
- Constraint: mobile viewports (<768px) cannot run the force-graph canvas
  acceptably — it currently degrades to a tappable list there. Any redesign of
  the graph surface must account for this split rather than assume one visual
  treatment covers both.
- Undecided: whether the admin dashboard's visual treatment matters for the
  portfolio evaluation at all, given it's not part of the audience-facing tour.

## Brand Commitments

- Name: **Mycelium** (public-facing). The backend/repo/service internals keep the
  original working codename `reelbrain` deliberately — nothing a visitor reads says
  "reelbrain."
- The mycelium/network metaphor is an existing, intentional thread (network graph
  as the literal centerpiece, "hypha" language on the how-it-works page) — treat it
  as evidence of a committed identity, not merely a name to reinterpret freely.

## Evidence on Hand

- The live backend and its real data (`https://reelbrain.onrender.com/api/public/*`)
  — real counts, categories, and content, not placeholder copy.
- Deployed site: `https://garvbardia.github.io/reelbrain/`.
- No user testimonials, press, or case studies exist or should be fabricated — this
  is a solo portfolio project, not a company with customers to quote.

## Product Principles

- Judged by engineers/recruiters: craft and correctness are part of the pitch, not
  just the copy — visible loading/error states, live real numbers, and working
  interaction matter as much as visual polish.
- Honest about scale: this is a personal-scale knowledge base, not a SaaS product
  with thousands of users — design should read as confident at its real size, not
  padded to imply a scale that isn't there.
- The network/mycelium metaphor is the product's one real point of differentiation
  visually — a generic redesign that discards it in favor of a trend (e.g. default
  SaaS-purple-gradient) would be a regression, not an upgrade.
- Public surfaces (landing, how-it-works, library, scout) are the evaluation
  surface; admin is operator tooling and secondary.

## Accessibility & Inclusion

No product-specific accessibility requirement has been established beyond
ordinary web standards (the graph already ships a `nodeLabel` tooltip and a
mobile list fallback partly for this reason).
