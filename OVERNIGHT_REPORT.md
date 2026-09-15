# Overnight report — scroll-driven hero transition

Branch: `overnight/hero-particle-transition`. Nothing pushed. Nothing deployed.

---

## 1. Summary

`/preview/particles` now opens on a pink→purple→blue instanced particle sphere on a white page; scrolling dollies the camera into it, and from 60% through the scroll the particles rush outward and dissolve while the real `react-force-graph-2d` graph scales up and fades in from the same screen centre, ending fully interactive (node click → detail modal verified with a real browser click). All six stages are complete and committed; mobile and `prefers-reduced-motion` skip the whole sequence and render the graph directly. The live homepage, `knowledge-graph.tsx`, and last session's `particle-hero-background.tsx` are untouched — the only changed files are the preview route and two new components under `components/hero-sphere/`.

---

## 2. Stages completed

| Stage | Commit | What verification actually showed |
|---|---|---|
| 0 — baseline | `4b40a24` | Empty marker commit. `/preview/particles` already existed from the last session; verified rather than assumed — `npm run build` passed (11 routes), tsc clean, route served with no console errors. |
| 1 — white bg + gradient + bloom | `28a0caa` | See below. |
| 2 — camera dolly | `7798c57` | See below. |
| 3 — scroll wiring | `a6f9e23` | See below. |
| 4 — graph handoff | `3b84647` | See below. |
| 5 — fallbacks | `66c14f1` | See below. |
| 6a — dolly easing | `e74bf18` | `pow(progress, 1.7)` on camera z only. Build + tsc clean. |
| 6b — idle drift | `3a2405e` | Two non-harmonic sines on camera x/y, amplitude 0.14 world units, scaled by `(1 - progress)`. Renders correctly at rest. **Not** claimed as screenshot-verified motion — see §7. |

**Stage 1 detail.** Bloom ended at `strength 0.35 / radius 0.4 / threshold 0.75` (vs `0.9–1.0 / 0.45 / 0.30–0.36` on the dark-background component). The gradient took three passes, each caught on a screenshot, not predicted:
1. Colouring by curved radius with the dark version's `2.4` density exponent → **flat magenta**: nearly all particles land at small `r`, so the core colour swallowed the sphere and purple/blue had no population to render.
2. Colouring by the pre-curve uniform instead → **flat purple**: fixed the population split, broke the spatial mapping.
3. Landed on density exponent `1.15` + colour keyed to true spatial radius, stops at `0 / 0.45 / 1.0` (not an even `0/0.5/1.0`, because a sphere's outer shell holds far more volume and therefore far more particles), white-fade confined to the outer 16% at 0.4 strength. Reads as **pink core → purple mid → blue-violet rim** on white.

**Stage 2 detail.** Dolly `START_Z 4.4 → END_Z 1.15`, exponential damping `1 - exp(-6.5·dt)`, `dt` clamped to 100 ms. Two artifacts found at progress 1.0 and fixed:
- `END_Z 0.25` put the camera on the dense core; the end of the dolly rendered as a **flat wall of magenta hexagons**.
- Even pulled back, near particles rendered as visible **hexagons** — a detail-0 icosahedron is fine as a sub-pixel dot and obviously faceted once it fills screen area. Core layer is now detail 1 (42 verts); halo stays detail 0.

**Stage 3 detail.** 260vh track, `sticky top-0 h-screen` child, framer-motion `useScroll`. Verified: forward scrub smooth, reverse works, and **no leaks** — canvas count goes 2 → 0 on navigate away → 2 on return, twice in a row, no duplicates, no climbing count.

**Stage 4 detail.** Scrubbed opacity/scale measured at five scroll offsets: `0.00/0.72` at y=0 and y=700 (below handoff), `0.28/0.797` at 1200, `0.71/0.919` at 1450, `1.00/identity` at 1617. `pointer-events` flips `none → auto` and `aria-hidden` `true → false` at 0.98. Category-strip click focuses a category post-handoff. A **real browser click** on a node opened `ReelDetail` with real content ("Content & Media / video-use / Resource Drop") — synthetic `MouseEvent`s could *not* verify this, because canvas hit-testing needs real `offsetX/offsetY`, so this was done with an actual click rather than a dispatched one.

**Stage 5 detail.** 390×844: **0 canvases**, document height 2148 (not 3529) — i.e. no pinned track — and the graph's own `<768px` fallback (tappable category list) with real data. `prefers-reduced-motion` was tested properly by patching `matchMedia` and forcing a client-side remount, not assumed from the shared code path: **1 canvas** (the graph's own; the sphere is never constructed), document height 2022, graph controls present.

**Regression checks ran at every stage boundary**, per your addition: tsc + `npm run build` after each, plus a `git diff main --name-only` grep confirming zero `.py` files were implicated (so pytest wasn't required mid-stage). Final full run: **1145 passed, 1 xfailed** — and the live homepage was loaded and confirmed rendering (hero copy present, graph canvas present, no errors).

---

## 3. Stages not completed

None. Stages 0–5 all completed and verified; Stage 6 items 1 and 4 done. Stage 6 items 2 and 3 were **deliberately skipped**, not blocked — reasoning in §4.

---

## 4. Decisions made without approval

**Built a new component instead of extending `particle-hero-background.tsx`** → That component's entire scene lives inside one `useEffect` with no external control surface: no progress input, no way to drive the camera from outside. Adding scroll-driven dolly/rush/fade would have meant restructuring its core, which is exactly the "don't rewrite verified code" threshold you flagged. Both now render on the same preview page so the original stays demonstrably working. Cost: ~60 duplicated lines of scene boilerplate. → `components/hero-sphere/scroll-hero-sphere.tsx`, module docstring.

**No GSAP, no Lenis — used framer-motion + CSS sticky** → framer-motion 11.11 is already a dependency (ReelDetail, BlurFade, NumberTicker) and `useScroll({target, offset})` gives exactly the scrubbed 0→1 needed. Net new dependencies: **zero**. → `hero-scroll-sequence.tsx:1-32`.

**Progress is a getter (`progressSource?: () => number`), not a value prop** → A value prop re-runs React reconciliation 60×/sec against a component whose payload is an imperative WebGL scene. A getter read inside the existing RAF loop keeps scroll→render entirely outside React, and works identically for a debug slider or a MotionValue. → `scroll-hero-sphere.tsx`, `ScrollHeroSphereProps.progressSource`.

**Scroll track length = 260vh (~1,440px travel ≈ 1.0–1.4s)** → Erring long per your instruction. → `hero-scroll-sequence.tsx`, `SCROLL_VH`.

**Handoff window 0.6→1.0, particles and graph overlapping rather than sequential** → If particles cleared first there's an empty white beat (a cut); if the graph arrived first it appears through a wall of particles (a crossfade). Overlapping is what makes one object read as becoming the other. `RUSH_START` and `HANDOFF_START` must stay equal. → `scroll-hero-sphere.tsx` `RUSH_START`; `hero-scroll-sequence.tsx` `HANDOFF_START`.

**Graph starts at scale 0.72, not smaller** → react-force-graph does its own internal `zoomToFit`; scaling the wrapper much below this makes node labels illegibly small during the transition rather than merely distant. → `hero-scroll-sequence.tsx`, `GRAPH_START_SCALE`.

**Graph mounts at progress 0, not at the handoff** → Its simulation runs 400 warmup ticks synchronously then cools, so mounting early means it's settled and still when it becomes visible. Deferring the mount would put a visibly self-arranging graph on screen at the exact moment of handover. A cooled sim stops ticking, so it costs nothing during the dolly. → `hero-scroll-sequence.tsx`, the `motion.div` comment.

**Rush is a group scale, not per-instance matrix writes** → Scaling the parent pushes every particle radially outward, which is the required motion, for free. Rewriting 11,000 instance matrices per frame would buy nothing visible. → `scroll-hero-sphere.tsx`, `RUSH_SCALE`.

**Bloom kept in the pipeline despite being near-inert on white** → `UnrealBloomPass` is additive and white has no headroom above 1.0, so it contributes almost nothing at rest *at any strength/threshold*. This is physics, not tuning. Kept because it does real work once the camera is inside the dense core. The at-rest halo comes instead from a second, larger, 0.13-opacity instance layer. → `scroll-hero-sphere.tsx` module docstring + `HALO_OPACITY`.

**Fallback = show the graph directly, not a static image or thinned particles** → A static PNG is a picture of a feature the device isn't running; thinned particles would still pin the page and still scroll-jack a phone through 260vh. The sequence delivers the graph; a device that can't run the delivery mechanism should just get the graph. → `hero-scroll-sequence.tsx`, `!enabled` branch.

**Easing applied to camera only, not to the fades** → Easing one side of the handoff and not the other pulls apart the moment the sequence depends on being simultaneous. → `scroll-hero-sphere.tsx`, the `eased` line.

**Skipped Stage 6 item 2 (depth size attenuation)** → Perspective projection already shrinks distant particles. An additional artificial attenuation risked making the core read hollow, and I couldn't justify the risk against a real visual gain. Easy to add later in the RAF loop if wanted.

**Skipped Stage 6 item 3 (Lenis smooth-scroll)** → It would be a new dependency, and your own condition was "only if scroll scrubbing feels steppy". With exponential damping on the camera it doesn't. It also carries the exact risk you flagged — disturbing the live homepage's scroll — for a problem that isn't present.

**Left `.claude/` and `Mycelium_Project_Report.md` untracked** → Both have been untracked across many prior sessions; untracked files survive branch switches, so nothing was at risk. Committing files you've deliberately never committed seemed worse than leaving them.

---

## 5. Global changes made

**None.** `globals.css` already defines `--background: 0 0% 100%`, and the preview page was already `bg-white` from last session, so the white background required no global change at all. The global-styles exception was not used.

`git diff main --stat` is exactly three files, all preview-scoped:
```
web/src/app/preview/particles/page.tsx              |  43 +-
web/src/components/hero-sphere/hero-scroll-sequence.tsx | 225 ++
web/src/components/hero-sphere/scroll-hero-sphere.tsx   | 500 ++
```
Confirmed unmodified: `app/page.tsx`, `knowledge-graph.tsx`, `graph-fallback-list.tsx`, `site-header.tsx`, `globals.css`, `particle-hero-background.tsx`. Live homepage loaded and verified rendering after all work.

---

## 6. Dependencies added

**None.** Deliberate — see §4.

---

## 7. Known issues / rough edges

1. **The graph's own wheel handler captures scroll once interactive.** `knowledge-graph.tsx` calls `preventDefault()` on wheel to zoom the graph. After handoff, with the pointer over the graph, you cannot scroll back up through the sequence — you have to move the pointer outside the graph card first. Fixing this means touching the existing graph's interaction code, which was out of bounds. **This is the roughest edge and the first thing I'd want your call on.**
2. **Category-select calls `scrollIntoView`.** Clicking a category strip tile scrolls the page, which fights the pinned track and knocks you out of the end state. Same constraint — it lives in the graph component.
3. **Idle drift is not screenshot-verified.** 0.14 world units is deliberately below what a still frame shows, and the browser pane kept changing zoom between captures. The code is a four-line clamped sin/cos; I'd rather flag it than claim a test I didn't run.
4. **"Blue" reads as blue-violet at rest**, not pure blue — overlapping translucent halos tint the outer shell toward purple. It resolves to clearly blue mid-dolly. Change the `blue` constant in `buildParticles` if you want it colder.
5. **The preview route still ships if deployed.** `output: "export"` means `/preview/particles/` becomes a real public page. Unchanged from last session and still flagged in the file's own header.
6. **Two WebGL contexts on the page at once** (scroll sphere + the old Sun/Galaxy card). Fine on desktop, but it's why the fallback gates both.

---

## 8. Exact review steps

```bash
git checkout overnight/hero-particle-transition
cd web && npm run dev
```

Then, in order:

1. **`http://localhost:3000/preview/particles/`** — at rest you should see a pink-cored, purple-middled, blue-rimmed particle sphere on plain white.
2. **Scroll down slowly.** The camera should accelerate into the sphere (slow start, fast finish).
3. **Keep scrolling past ~60%.** Particles rush outward past the frame edges and dissolve while the real graph grows from the same centre. It should read as one object opening up.
4. **At the end**, click a node → the reel detail modal should open with real content. Move the pointer *off* the graph card before scrolling back up (see §7 issue 1).
5. **Scroll back up** — the whole thing reverses.
6. **Mobile check:** devtools → 390×844 → reload. No sphere, no long scroll track, straight to the tappable category list.
7. **Reduced motion:** OS setting, or devtools Rendering → "Emulate prefers-reduced-motion" → reload. Graph directly, no dolly.
8. **Live homepage unaffected:** `http://localhost:3000/` should look exactly as it did.

**Resetting to any stage** (each is independently working):
```bash
git reset --hard 3a2405e   # 6b idle drift (branch tip)
git reset --hard e74bf18   # 6a easing
git reset --hard 66c14f1   # 5  fallbacks
git reset --hard 3b84647   # 4  graph handoff
git reset --hard a6f9e23   # 3  scroll wiring
git reset --hard 7798c57   # 2  dolly + debug slider
git reset --hard 28a0caa   # 1  white bg + gradient
git reset --hard 4b40a24   # 0  baseline
```

**Knobs you're most likely to want**, all single constants:
- `SCROLL_VH` (`hero-scroll-sequence.tsx`) — scroll length
- `HANDOFF_START` + `RUSH_START` — when the swap begins (**keep equal**)
- `START_Z` / `END_Z` / the `1.7` exponent (`scroll-hero-sphere.tsx`) — dolly distance and easing
- `pink` / `purple` / `blue` in `buildParticles` — palette
- `DRIFT_AMOUNT` — idle motion

**To discard everything:** `git checkout main` — nothing was pushed.
