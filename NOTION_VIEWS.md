# NOTION_VIEWS.md — phone-friendly views for 📼 Saves (manual setup)

**Why manual:** the public Notion API (what our integration token can use) has no
endpoints for creating or configuring database *views* — sorts, filters, shown/hidden
properties, and per-device layout are UI-only. A `scripts/notion_view_setup.py` is
therefore impossible with the current API; these clicks take ~3 minutes, once.

Do this on desktop (the view editor is fiddly on the phone app; the views then sync to
mobile automatically).

## View 1 — "📱 Triage" (make it the default)

1. Open the **📼 Saves** database → click **+** next to the existing view tabs.
2. Pick **Table** (or **List** if you prefer bigger tap targets on phone) → name it
   `📱 Triage` → **Create**.
3. **Sort** (click `⋯` on the view tab → Sort → Add sort):
   - `Status` — **Ascending**, then drag the Status options into priority order:
     open the Status *property* (⋯ → Edit property) and drag `📥 Inbox` to the top,
     `⏳ Awaiting DM` second. (Select-sorts follow the option order you set here —
     this is the only way to get "Inbox/Awaiting DM first".)
   - Add a second sort: `Saved at` (or `Posted at`) — **Descending**, so newest first
     within each status.
4. **Properties** (⋯ → Properties): toggle ON only `Title`, `Status`, `Topics`,
   `Creator`. Toggle everything else OFF (hidden properties still show when you open
   a page — nothing is lost).
5. **Make it default:** drag the `📱 Triage` tab to the leftmost position. Notion opens
   the leftmost view by default on every device.
6. Optional mobile nicety: ⋯ → Layout → turn **Wrap all columns** OFF so rows stay one
   line tall on a phone.

## View 2 — "This Week"

1. **+** new view → **Gallery** (nicest for casual browsing; List also fine) → name it
   `This Week` → **Create**.
2. **Filter** (⋯ → Filter → Add filter): `Posted at` → **is on or after** → **one week
   ago** (choose the relative date option, not a fixed date — it rolls automatically).
   - If some rows have no `Posted at` (degraded fetches), add an OR group:
     `Saved at` → is on or after → one week ago. Otherwise those rows vanish from the view.
3. **Sort:** `Posted at` descending.
4. If Gallery: ⋯ → Layout → Card preview: **None** (we don't store cover images),
   card size **Small**; Properties: show `Status` + `Topics` only.

## Archived rows stay out of the way automatically

The nightly job now flips stale low-value rows to `🗄 Archived` (see PROGRESS.md).
To keep them out of Triage: on the `📱 Triage` view add a filter
`Status` → **is not** → `🗄 Archived`. Do the same on any other view you use daily.
(An "Archive" view filtered to `Status is 🗄 Archived` is handy for the rare dig-up.)
