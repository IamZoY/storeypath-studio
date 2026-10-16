# Location layouts: design

A location's **layout** is its land: the plot, its boundary walls and gates, the open
ground between its buildings divided into areas (parking, a driveway, a garden, the yard),
the things placed on it (cameras on poles, card readers at gates, barriers, lights), and
the footprint of each building standing on it. It is read from a site plan, and the
location's buildings are placed on it by their footprints. Part 1 (this): the land, its
reading, placing the buildings, Review, the package and its readers. Part 2 (later):
ways outdoors between buildings, and how Vigil uses the land.

## The model

A layout is a level of its location, as a floor is a level of its building: everything
that works on a floor by its ID (Review, items, corrections, room locks, history and undo,
reading again) works on it unchanged.

- `Location.layout`: the land, kept as a building is (code `LAND`, its own counter of
  object codes) but apart from the location's buildings: never in a list of buildings,
  never a building's package, no walking network. `Workspace.building()` and
  `Workspace.floor()` find it by its ID; `iter_floors()` does not list it,
  `iter_layouts()` does.
- Its one level, `G00`, a `Floor` of type `layout` (internal: packages have no such floor
  type): `source` (the site plan: path, region, units, offset), `outline` (the boundary:
  the plot, its property limits), `walls` (boundary walls and fences as drawn, with their
  gate gaps), `wall_thickness`, `height` (the boundary walls' height), `edits` (what people
  draw in Review), and `north` (what its north arrow was read as: a bearing, how sure, from
  what).
- Objects on it, as on a floor (`ObjectRecord`, IDs from the land's counter):
  - `space`: an outdoor area, typed: `parking`, `driveway`, `garden`, `courtyard`, `yard`
    (the open ground round the buildings), `path`, `gate_area`, `road` (outside the
    boundary), `pool`, and the outdoor types there were (`terrace`, `roof`);
  - `opening` of type `gate`: a way through the boundary walls, joining the areas on
    each side;
  - `footprint` (a new kind): the outline of a building standing on the land, its labels
    (`label`), and the building it was matched to (`match`: the building, how sure, by
    what, and the position that puts the building's ground floor on it).
- A person's link of a footprint to a building: `Override.building` (a building's ID; ""
  for none: skipped). Kept through every read, as any correction.
- A building placed on its footprint: `Building.site` (its `SitePosition`, as a person
  sets it on the site plan), and `SitePosition.footprint`: the footprint it was put on.
- Items on the land: `Item.floor_id` is the layout's ID; their x, y are in the site's frame.
- North: `Location.north` (bearing, from `drawing` or `person`, how sure). The site's
  bearing is its placement's when it is on the map, else `north`'s; setting either sets
  both.
- A building may have no floors (one offered from a footprint no building matches): it
  stands on the site plan on its footprint, and its floors, added later, are matched to it.

## IDs

```
PROJECT-LOCATION-LAND-G00-OBJECT        K7Q2XM-SITE-LAND-G00-0004
```

Five segments, as every place. `LAND` is reserved at the building's level: no building
may have it (Studio refuses it, and never makes it from a name); `G00` is the land's level
(one in 0.12; a later version may add others). So the grammar, `ids.py`, Go's `ParseID`
and the viewer's `hierarchy()` take a layout's objects apart as they are: the location is
the location, the "building" the land, the floor the layout. Nothing new to parse; a reader
tells the land by its reserved segment, or by the `layout` feature. Codes come from the
land's own counter, never reused; a read keeps an area's or footprint's ID by overlap, a
gate's by where it is, as rooms and doors.

## Frames

- **The site's frame**: metres, its origin the site's centre (the point the location's
  placement puts at its longitude and latitude), +y the site plan's up (at the site's
  bearing). It was there before (buildings' site positions are in it); 0.12 names it.
- **The layout is drawn in the site's frame**: its source's offset is chosen, when it is
  first read, so that the middle of its plot is the origin. Its areas, walls, gates,
  footprints and its items' positions are site metres.
- **A building's frame** (its drawings' metres) lies on the site by its `SitePosition`:
  turned `rotation` degrees clockwise about its `pivot`, the pivot at x, y. Placing a
  building on its footprint sets that, and changes nothing in the building: no room's ID
  or position in its own frame.

## Reading a site plan

Find plans already tells a site plan by its title (`site_plan`). Its card offers *Use as
⟨location⟩'s layout* (a location of the project, or a new one by name); a job reads it.

1. **What is drawn** in the plan's region: lines (solid and dashed), arcs, polylines,
   hatches' outlines, texts, blocks.
2. **The boundary**: the property limits, by their layer's name or a PROPERTY LINE /
   PLOT LIMIT / BOUNDARY text along them, each piece read as the line it lies on; the plot
   is what those lines close round the plan's content. Without them, the outer outline of
   the boundary walls, their gate gaps closed; else the largest closed outline round the
   rest.
3. **The walls**: the drawn lines inside the plot make faces; thin ones along the plot's
   edge are its boundary walls.
4. **Gates**: gaps in the boundary walls 0.7 to 12 m wide (a swing or a block in one says
   so most surely): an opening of type gate each.
5. **Buildings and land**: texts are read by rules (and the language model for the ones the
   rules do not know): a building's name, a floor's level (FFL, SSL, TOP), a roof say
   building; a ground level (FGL, NGL), PARKING, GARDEN, DRIVEWAY, ROAD say land. The open
   land is the largest face against the boundary walls and the faces land texts are in;
   the rest inside the walls, in one piece, is a footprint when a building's text is in it
   or walls close round it, its outer outline (no holes): buildings are holes in the land.
6. **Areas**: the land's faces by their texts (a labelled face is its label's type); the
   faces with none together are the yard; outside the plot, a face labelled ROAD is road.
7. **North**: a block named so (NORTH, N-ARROW, COMPASS, شمال), or one holding an N, the one
   nearest the plot; its bearing from its turn (and the N's direction in it when it has
   one). How sure: by how many signs agree. A person corrects it on the site plan.

## Placing the buildings

For each footprint and each building of the location:

- **By label**: the footprint's texts against the building's name, code and its floors'
  plan titles, by their words (PROPOSED, NEW, EXISTING, FLOOR, PLAN and the like left
  out); the language model where words cannot decide (an abbreviation: GH for Guest house).
- **By shape**: the building's ground floor outline (else all its floors') fitted onto the
  footprint: rotations from both outlines' main directions and their right angles, the
  middles put together, then nudged; scale 1 (both in metres); never mirrored. How well
  they overlap (IoU) is the score; two rotations as good say the shape cannot tell.
- **Together**: a confidence from both; one building to one footprint, the surest first.

The project's site plan shows the matches before anything moves, each with its confidence;
a person applies, changes (*this footprint is the Annex*) or skips each. Applying sets the
building's `SitePosition` so its ground floor lies on its footprint. A footprint no
building matches is offered as a new building of the location, without floors. Read again,
a site plan keeps its IDs and every placement a person applied, unless asked to place
them again.

## The package (format 0.12)

One building per package stays; a location's layout is **a package of its own** (*Export
⟨location⟩'s layout*): `scope.layout` names it, `scope.buildings` is empty. Its items are
there alone (an item is in one package: the building's it stands in, or the land's), its
`changes.json` is about the land, and a system takes it beside the buildings' packages.

- `layout.geojson`: the layout (kind `layout`): its boundary as geometry, `walls`,
  `wall_height_m`, `wall_thickness_m`, `location_id`, `north` (how the site's bearing is
  known).
- `footprints.geojson`: each footprint (kind `footprint`), its `building_id` (or null),
  its `drawing_label`, and `site`: where the building's frame lies on the site.
- `spaces.geojson`: the areas (spaces whose `floor_id` is the layout); `openings.geojson`:
  the gates (type `gate`); `items.geojson`: the items on the land (`building_id` null,
  `local` in the site's frame). No floors, zones or walking network.
- `manifest.json → placements`: the layout's, the site's own (x, y 0: the site's centre at
  lon, lat; its up at bearing). A building's package says where its building stands on its
  site (`placements[…].site`: its frame's origin and turn in the site's frame).
- New types: the area types above, the opening type `gate`; `objects.csv` kinds `layout`
  and `footprint`. A card reader's `door_id` may be a gate.
- A minor version: readers of 0.11 refuse 0.12; readers of 0.12 read 0.11 as before.
  FORMAT.md: "Location layouts (0.12)" and "Changes from 0.11".
