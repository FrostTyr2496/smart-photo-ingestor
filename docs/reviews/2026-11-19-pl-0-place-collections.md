# pl-0: place collections in review: shoots grouped by where, across time

Status: **building v1 with the recommended answers (2026-11-19, D-138)**: the user asked for it to be built now. Answers
taken: 1 two levels (1.5 / 25 km); 2 confirmed name or shared folder only; 3 home hidden; 4 v1 without merge/split;
5 v2 later. Each stays open to the user's change. It builds on the shoots-to-confirm queue (D-127..D-133) and the
trips view (D-134). Trips group shoots by **time**. A place collection groups them by **where**, across years: the
same lookout visited six times, or the local park. This file is public, so it holds aggregates only.

## 1. What the catalog supports today (newest plan, read-only)

| Fact | Number |
|---|---|
| Shoots with coordinates (camera GPS or phone points in range) | 113 of 193 |
| Unlocked shoots with a proposed place name | 105 of 174 (phone place 56, area 28, venue 19, metadata 2) |
| Place names shared by 2+ shoots | 10 names, 32 shoots |
| Coordinate clusters with 2+ shoots at 0.5 / 1.5 / 5 / 25 km | 8 / 13 / 17 / 18, holding 30 / 41 / 50 / 78 shoots; the largest holds 8 / 9 / 9 / 12 |

Names alone find few repeats, because the phone names the same spot differently from one visit to the next.
Coordinates find more. So a collection is built from coordinates first, and names second.

## 2. Model: two levels, derived each plan

- **Spot**: shoots whose centroids chain within **1.5 km**, like a viewpoint, a beach or a trailhead. This is
  single-link, with a guard: a spot is cut where its diameter would exceed 5 km, so a coastline does not chain
  together.
- **Area**: spots and shoots within **25 km** that share a phone area name, like a town or a park region. An area
  is a header that holds its spots.
- **Shoots with no coordinates** join only through words. Either the user confirmed the same place name, or the
  shoot sits in a human folder whose name another member's folder shares. Otherwise they join nothing. Phone
  evidence weakened by time distance (the decay table) can offer a shoot only as a faded candidate.
- **Name**: what the members say, in this order: user-confirmed names, phone venue, phone place, folder, area.
  The top value wins, and the rest are alternatives with counts ("Harbour Lookout · 4 of 7 shoots").
- **Member confidence**: distance from the spot's centre against its radius, times the member's own place
  confidence. In beta (D-128), faint members are shown faded, as everywhere else.

All of this is computed by the planner (`shoots.plan` adds `places: [...]` to the plan file). No schema change.

## 3. Review pane: "Group by" Time · Trip · Place

The queue gains a grouping switch next to Sort. **Place** mode lists collections, biggest first:

```
▾ Harbour Lookout   7 shoots · 2021–2025 · 2,340 frames        [mini map: 7 pins]
     name: Harbour Lookout (iPhone venue, 4 of 7) · alternatives: Harbourton (area, 3)
     ☑ 2023-09-09 · Harbour Lookout · Moonrise          312 frames   ●●●○
     ☑ 2022-04-17 · Harbourton · Sunset                  88 frames   ●●○○
     ☐ 2021-06-02 · (no place) · Night sky   faded       41 frames   ●○○○   ← word match only
     [Name this place for 6 shoots]  [Open a shoot]
▸ Northland coast (area)   3 spots · 11 shoots
```

Actions:
- **Name this place for N shoots**: one `detail` decision (field `place`) per ticked shoot, all in one batch with
  one undo. This is the main win: one confirmation fixes every visit.
- **Untick** a shoot to leave it out of the naming. Nothing else is stored.
- **Open a shoot** to get the normal stack (sh-10).
- **Merge with…** another collection (the same place, named two ways), or **Split** a spot. These are stored as
  plan decisions, as split and merge are for shoots, and replayed on re-plan. They need the decision kind CHECK to
  widen (`place_merge`, `place_split`), which is a small schema change. Without it, merge and split wait for v2.

Time and Trip mode stay as they are. A shoot can be confirmed from any of the three modes, because each decision
belongs to the shoot.

## 4. Home and privacy

The largest spot is very likely home. The planner already knows `home` from the phone's `search_info`, on 2,054 rows.
That spot is collapsed under "Home (hidden)" by default, and is never offered as a shoot name unless the user names it.
Place names and coordinates stay in the catalog and the plan file under `<home>`, never in git.

## 5. Later (v2, a schema change, needs approval)

A first-class `places` table, `(place_id, name, kind spot|area, lat, lon, radius_m, parent_place_id, created_at)`, with
`shoots.place_id`. It brings:
- stable names across re-plans, with aliases;
- a **Places** view in Photos and Files beside Trips (rv-47 / rv-48);
- per-place defaults, such as keywords or credit;
- the XMP `Location` fields written from one place (sh-6).

v1 needs none of this: names land in `shoots.place` through the existing decisions and Apply.

## 6. Decisions for the user

1. Two levels, a spot (1.5 km) and an area (25 km)? Or one level with a radius slider in the pane?
2. Shoots with no coordinates join by confirmed name or a shared folder only (recommended)? Or also by the phone area
   name?
3. Hide the home spot by default?
4. v1 without merge and split (no schema change), or with them (widen the decision kinds)?
5. Plan v2 (a `places` table and a Places view in Photos and Files) now, or after v1 has been used for a while?

## 7. Build order after approval

| ID | Task |
|---|---|
| pl-1 | Planner: spots, areas, names, member confidence, home; `places` in the plan file; tests on synthetic coordinates |
| pl-2 | Review: the Group by switch, the place list with mini map, Name this place (batch detail decisions with one undo) |
| pl-3 | (if 4 = with) merge and split of places as decisions |
| pl-4 | (v2, proposal first) `places` table, Places view in Photos and Files |
