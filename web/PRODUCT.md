# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

Primary: engineers and recruiters evaluating this project as a campus-placement
portfolio piece: a technical audience assessing build quality, taste, and depth of
execution, not just the surface pitch. The public site is more likely a recruiter's
first cold impression; the GitHub README is the developer-facing surface. Both must
come away informed and impressed, so neither is served a simplified version of the
other's page.

Secondary: the builder's own daily use of the pipeline as a real, working knowledge
base. This is not a demo: the data on the site is live and real.

## Product Purpose

Mycelium is a personal automation pipeline that runs every day. A reel shared from a
phone (Instagram Reels; photo and carousel posts take a separate image path) is
downloaded, transcribed and summarized by AI into structured notes (main point, steps,
tools named, value score, topic tags, and where warranted one suggested next step),
filed in Notion as the system of record, mirrored to a local Obsidian vault, and
published as a redacted, read-only view.

The public site is that view: a browsable graph, a searchable library, a Scout queue,
and a walkthrough of the pipeline. Success is a first-time visitor understanding what
it does, and that it is real, from the site alone; and an engineer being able to
verify every claim in the repository.

## Positioning

A live, working end-to-end system rather than a described one. What a neighbouring
portfolio project could not truthfully copy is the depth behind it: a pipeline run
daily on real content, engineered around free-tier limits (a Gemini quota that stops
near 17-20 calls a day, routed around with a local model and a quota-aware daily
runner), with health monitoring that stays silent when healthy, and a public API that
redacts by allow-list so a new private field cannot leak by being forgotten.

Evidence over assertion: the site shows real records (the How it works page follows
one real save through all six stages) instead of claiming capability. Copy is precise
and confident, never inflated; overclaiming reads worse to a technical audience than
specific, checkable statements do.

## Operating Context

- Frontend: Next.js 14 App Router, static export (`output: "export"`), Tailwind,
  framer-motion, Three.js. Deployed to GitHub Pages under a `/reelbrain` base path
  (`basePath`/`assetPrefix` are set only when `GITHUB_ACTIONS=true`, so local dev
  serves at the root). `trailingSlash: true`. No server at runtime: no ISR,
  middleware, or route handlers; every data-driven page is a client component that
  fetches the public API from the browser on mount. Deploys on any push to `main`
  that touches `web/**`.
- Backend: FastAPI on Render's free tier. `/api/public/*` (graph, stats, reels,
  reel detail, scout-queue, categories) is read-only, cached (300s) and rate-limited
  (120/min). `/api/admin/*` is unredacted and behind a shared-secret header. Both sit
  over the same Notion-backed store. CORS allows only the configured Pages origin plus
  `localhost:3000` and `127.0.0.1:3000`, so a local dev server on any other port cannot
  load data.
- The free tier sleeps after about 15 minutes idle; the first request can take 30-50s.
  Every data-driven surface therefore has real loading, error and retry states.
- Surfaces: `/` (static hero sphere and CTA, live totals, three mechanism cards, top
  categories and topics), `/graph`, `/how-it-works`, `/library` (search, category and
  minimum-value filters, reel detail modal, `?reel=<shortcode>` deep link), `/scout`,
  and an operator-only admin dashboard that is not linked from the public UI.
- Evaluated primarily as a live, working site, not a static mockup. Real behaviour
  (loading, error and empty states, live numbers, actual graph interaction) is part
  of what is judged.

## Capabilities and Constraints

- Data at time of writing (2026-09-21, live and changing): 201 reels, 13 categories,
  174 topic tags, 242 named tools/people/products, 36 saves with a suggested action.
  Personal scale, not web scale: design and interaction should assume that honestly
  rather than over-engineer for a scale that does not exist.
- Categories are derived from Topics in `app/public_api.py`; they are not a Notion
  property. Each has a label and colour (`/categories`), used in Library filters, cards
  and Scout. They are not colour-coded on the graph.
- **The graph (`/graph`) is a display arrangement, not a relationship graph.** It is
  a Three.js sphere with exactly one particle per real reel (1:1 with `total_reels`,
  deduped by shortcode; a guard warns on mismatch). Placement is a deterministic
  Fibonacci sphere, sorted by category, then value score, then shortcode, so each
  category occupies a contiguous band. There is no physics simulation and no links
  are drawn; where a point sits implies no relationship with its neighbours, and the
  page says so. Colour is a latitude gradient (pink to purple to blue) that marks
  nothing; particle size carries value score. About 6,500 decorative dust particles
  add density and are never interactive.
- Graph interaction: hover shows a short snippet label (DOM text, site typeface,
  hover-only); click opens the same `ReelDetail` modal the Library uses, fetched
  lazily by shortcode; wheel `deltaY` zooms and `deltaX` (a two-finger trackpad swipe)
  rotates. Ambient rotation pauses while a node is hovered. The view is a fixed,
  non-scrolling shell so the wheel belongs entirely to the graph. Hit-testing
  raycasts only the real-node layer, so dust can never register as a reel.
- The graph deliberately has no category-level nodes, category anchors or category
  labels. That was an accepted tradeoff of replacing the earlier force-directed
  graph. Category shows only through spatial banding, the hover label and the modal.
- The homepage hero is the same sphere, static and decorative: dust only, no data,
  no scroll coupling, slow ambient rotation. A scroll-driven camera dolly into the
  graph was built, reported as janky, and deleted; do not reintroduce scroll-jacking
  or pinned-viewport scrub.
- Device gate: the sphere renders only at 768px or wider, without
  `prefers-reduced-motion`, and on more than 4 cores and more than 4GB reported memory.
  Otherwise the hero omits it (it is decoration) and `/graph` serves the tappable
  category-then-reel list instead, which opens the same modal. Any change to the graph
  surface must keep both paths working; the gate is not just a width check.
- Scout queue: a rule, not a ranking model. A save qualifies with a value score of 4
  or higher and a real suggested action, sorted by score then High priority. It is
  distinct from the Claude-ranked Implementation Scout that runs locally over the
  Obsidian vault; the site does not show that.
- Library search covers title, summary, topics and named entities. Results are sorted
  by value score, then posted date, both descending.
- Comment-gate content ("comment X for the link") is detected and handled privately;
  gate keywords, gate resources, private notes and raw transcripts never appear on
  the public site.
- Undecided: whether the admin dashboard's visual treatment matters for the portfolio
  evaluation at all, given it is not part of the audience-facing tour.

## Brand Commitments

- Name: **Mycelium** (public-facing). The backend, repo and Render service keep the
  original working codename `reelbrain` deliberately; nothing a visitor reads says
  "reelbrain".
- Voice: the live site is neutral and product-register, not first person. The README
  is first person, which suits a developer reading the repo. No hype, no vague
  superlatives, and no claim the code does not back. The earlier "self-organizing,
  self-improving knowledge network" framing was retired in the 2026-09 copy pass, and
  the site's copy no longer uses the mycelium metaphor as a device; the name and the
  sphere remain.
- Accent: the graph sphere's palette (pink `#e8118a`, purple `#7c16e8`, blue `#1b45e0`;
  defined in `tailwind.config.ts` as `sphere.*` and in
  `src/components/graph-sphere/sphere-core.ts`) is a restrained site-wide accent so the
  product feels like one thing. Purple is the working accent (focus, active nav, hover,
  faint tag tints); pink and blue appear only alongside it. It never touches body text
  or main backgrounds (which stay white or light) and never turns a neutral button
  into a colour block. When in doubt, use it more sparingly.
- Typeface: Manrope.

## Evidence on Hand

- The live backend and its real data (`https://reelbrain.onrender.com/api/public/*`):
  real counts, categories and content, not placeholder copy.
- Deployed site: `https://garvbardia.github.io/reelbrain/`. Source:
  `https://github.com/GarvBardia/reelbrain`.
- README with real screenshots (`docs/screenshots/`), a Mermaid architecture diagram,
  the confirmed Notion schema and a known-gotchas section; a 1,100+ test backend
  suite (fully mocked); detailed runbooks in the repo root (`DEPLOYMENT.md`,
  `SCHEDULING.md`, `WORKER_SETUP.md`, `COOKIES.md`).
- No testimonials, press, case studies or usage metrics exist and none should be
  fabricated. This is a solo portfolio project, not a company with customers to quote.

## Product Principles

- Judged by engineers and recruiters: craft and correctness are part of the pitch,
  not just the copy. Visible loading and error states, live real numbers and working
  interaction matter as much as visual polish.
- Honest about scale: a personal-scale knowledge base, not a SaaS product. Design
  should read as confident at its real size, not padded to imply scale that is not
  there.
- Show real records rather than describing capability, and never invent a sample to
  fill a gap: an empty slot beats a plausible fake.
- The particle sphere and its pink-to-purple-to-blue palette are the visual signature.
  A generic redesign that trades it for a default SaaS purple gradient would be a
  regression, not an upgrade.
- Public surfaces (home, graph, how-it-works, library, scout) are the evaluation
  surface; admin is operator tooling and secondary.

## Accessibility & Inclusion

No product-specific standard has been set beyond ordinary web standards. Established
behaviour to preserve: small screens get a tappable list instead of the canvas, and
`prefers-reduced-motion` is honoured by the sphere (no hero sphere, `/graph` falls back
to the list), the ASCII hero background, and the How it works walkthrough (reveals
off, plain stacked document).

Known gaps, recorded so they are not mistaken for settled behaviour:
- On desktop the sphere is pointer-only. The canvas has no keyboard or screen-reader
  path and its hover label is `aria-hidden`. The Library is the accessible route to
  the same reels, and the list fallback is reachable only through the device gate above.
- The shared `BlurFade` entrance used across most pages and `NumberTicker` do not
  check `prefers-reduced-motion`, and there is no site-wide `MotionConfig`.
