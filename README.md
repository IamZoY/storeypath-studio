<h1 align="center">StoreyPath</h1>

<p align="center">
  <b>From the architect's DWG to a living indoor map you can walk through.</b><br>
  StoreyPath Studio reads real floor plans the way a person does, gives every room, door and
  desk an ID that never changes, and lets your team check, furnish and finish them together,
  in 2D and in 3D. Self-hosted, fully offline, with or without a GPU.
</p>

<p align="center">
  <a href="https://github.com/StoreyPath/storeypath-studio/actions/workflows/ci.yml"><img alt="CI" src="https://github.com/StoreyPath/storeypath-studio/actions/workflows/ci.yml/badge.svg"></a>
  <a href="LICENSE"><img alt="Licence: Apache 2.0" src="https://img.shields.io/badge/licence-Apache%202.0-blue.svg"></a>
  <a href="#quick-start"><img alt="Docker: ghcr.io/storeypath/studio, amd64 and arm64" src="https://img.shields.io/badge/docker-ghcr.io%2Fstoreypath%2Fstudio-2496ED?logo=docker&logoColor=white"></a>
</p>

<p align="center">
  <img src="docs/images/hero.webp" width="100%" alt="A drawing is dropped into Studio and its three plans are found; Review shows the floor and the rooms to check; the floor turns in 3D; then a walk through the front doors into a marble reception with a wayfinding kiosk">
</p>

```sh
docker run -d --name storeypath -p 127.0.0.1:8080:8080 -v storeypath:/data ghcr.io/storeypath/studio
docker exec storeypath storeypath demo --studio    # optional: a furnished demo campus to explore
```

Then open **https://localhost:8080** and log in as `admin`, password `admin`. Studio
makes its own certificate, so the browser warns once (the log shows its fingerprint).
No GPU, and no internet once the image is pulled; nothing else to install: amd64 or
arm64, on Linux, macOS or Windows with Docker. [More ways to run it](docs/INSTALL.md).

## Who it is for

- **Facilities and workplace teams** who need every floor as it really is: each room
  named and typed, with its area, capacity and finishes, and every desk, screen, copier
  and access point placed, each with an asset tag; every camera with what it covers,
  every card reader with the door it serves.
- **Wayfinding and kiosk systems**: the way from a kiosk in the lobby to anyone's office,
  across floors by lift or stairs, up to a mezzanine or out onto the roof, found the
  same in Studio, in Go and in the browser.
- **Integrators**: an open package format (plain JSON, GeoJSON and CSV in a ZIP) with
  stable IDs to key your own records to, each floor's drawing in it when you ask for it,
  a Go reader, and viewers to embed.

## Why StoreyPath

- **It reads drawings as an architect would.** No layer standards, no templates, no
  cleaning up first. Plans side by side on one sheet are found and titled, a floor
  drawn as two wings where they stand is offered as one, units are worked out from what
  is drawn, floors are stacked on their walls, mezzanines and roofs are levels of their
  own, rooms are found even where nothing outlines them, and room names are understood
  in English, Arabic and more, by the language model where the rules stop. A damaged
  file is read as far as it goes. [How Studio reads a drawing](docs/HOW-STUDIO-READS-A-DRAWING.md).
- **The drawing stays in view.** Any drawing opens whole in the browser, as its own
  program shows it, with the plans Studio found in it outlined; Review shows each floor
  over its print; and a package can carry each floor's drawing, for other systems to
  show under the plan.
- **AI where it helps, rules where they suffice, with or without a GPU.** A small
  language model on the CPU reads what the rules do not know; with a GPU, a vision model
  looks at each room as printed. Every answer is kept with the project, so it reads the
  same again without one, and a person confirms what a model decided.
- **Yours, offline.** One container with its database inside, on your own machine.
  Nothing is downloaded when it runs: no telemetry, no map tiles; the language model
  runs inside it, and the vision model on a GPU of yours.
- **Built for a team.** Accounts, sharing down to a single floor, many people on a
  floor at once (one a room), everyone else's changes shown live, undo, history and an
  audit log.
- **IDs that never change.** Read a revised drawing again and every room keeps its ID;
  carry a desk to another building and it keeps its tag. Each export lists what was
  added, changed and retired.
- **Open format, open viewers.** Packages anyone can read without our code, and a 3D
  world, a 2D plan, the drawing view and a Go reader in [StoreyPath Viewer](https://github.com/StoreyPath/storeypath-viewer).

## See it

### A drawing comes in: its plans found, its private information out

| Three plans on one sheet, found, titled and sized | What is private, found before anything is kept |
|---|---|
| ![The three plans of a sheet, each with its title, size and the floor it will be](docs/images/plans-found.webp) | ![Studio lists the file's hidden data to take out before it keeps the drawing](docs/images/privacy.webp) |

Drop the DWG or DXF files an architect hands over onto a project. Studio finds every plan
on the sheets and reads its title (*ground floor plan* is floor 0), works out the real
units from what is drawn, reads floor heights off the sections, and offers each plan as a
floor. Before it keeps a drawing it finds the title blocks, names, phone numbers, emails,
permit numbers and the file's hidden data, and takes them out (you choose what to keep).
Then it lines the floors up and finds the rooms, doors, windows and what every room is.

### The drawing itself, as it is drawn

<p align="center">
  <img src="docs/images/drawing-view.webp" width="100%" alt="The drawing viewer on a sheet of three floor plans in their CAD colours on dark: each plan Studio found outlined in orange with its number, the pointer over plan 2 saying FIRST FLOOR PLAN · Floor plan · 56.2 × 21.2 m, Click: add as a floor; the list of plans found, each with Add as a floor; the pointer's place in metres and a scale bar at the foot">
</p>

**View**, beside any drawing, shows it whole, as its own program does: every line,
curve, hatch, fill, text, block and dimension on every layer, drawn with WebGL, in its
CAD colours on dark or black on white (*Print*). Layers go on and off, texts stay sharp
at any zoom, the foot of the window says where the pointer is in metres, and *Fit*
frames the main body of the drawing, not a stray line far off. From Find plans, *See the
whole drawing* outlines each plan found with its card's number: click one to add it as a
floor. From the list of drawings, the parts already read as floors are outlined and
named.

**Include the drawings**, beside *Export package*, puts each floor's drawing in its
package, placed on its plan: StoreyPath Viewer's 2D plan shows it under the rooms, and a
system that works on the drawing itself reads its lines and texts. It is off unless
ticked, only those who may edit the whole project may tick it, and any text with a phone
number, an email, a web address, a permit, plot or licence number, or a name with a
title, is taken out once more.

### Real drawings, as they come

<img src="docs/images/wings.webp" align="right" width="300" alt="Find plans on a sheet with one floor drawn as two wings: plans 1 and 2, both titled FIRST FLOOR PLAN, each card saying Plans 1 + 2: one floor, its parts drawn where they stand (their room labels carry on one numbering), with These are separate; the button says Add 1 floor">

- **A floor drawn in parts**, its wings where they stand with open ground between them,
  is found as a plan per part. Plans whose room labels carry on one numbering are offered
  as one floor (*These are separate* says otherwise), and any plans can be made one
  floor by giving them the same building and floor: Studio refuses, saying why, when
  something else is drawn between them.
- **A block standing in a courtyard** is part of the plan round it, not a plan of its own.
- **A facade that stands apart**, a row of short piers and windows across an open hall,
  is taken into its plan.
- **A small floor over part of a big one** (a mezzanine) stays where its drawing puts it
  when its walls already line up there.
- **A damaged file** is read as far as it goes. Hatch outlines that do not close are left
  out, as CAD programs leave them unfilled, instead of drawing wedges across the plan;
  texts of impossible sizes or places, which could stop a drawing's view and its prints
  for good, are repaired or left out; a room whose outline comes out invalid is kept as
  what it encloses. The floor's warnings say what was repaired.
- **Cancel** puts the plans found away, adding no floor.
- **The same file sent again** is recognised, whatever was kept or taken out for privacy,
  and not kept twice: Studio points to the drawing it already has. **A drawing no floor
  reads from** can be deleted; one a floor still reads from cannot, until that floor is
  given another.

<br clear="right">

### Every level: mezzanines and roofs

![A made-up warehouse in 3D, its levels lifted apart: the ground floor; the mezzanine, a block of rooms up the stairs over its west end; the first floor; the roof's open deck behind its parapets, with the stair room, the lift machine room and a plant room standing on it; the floor stack lists RF, F01, M00 and F00](docs/images/levels.webp)

Each floor is a level of its building: a **floor**, a **mezzanine** or the **roof**
(package format 0.11), chosen in a plan's *Level* when it is added (Studio chooses it
when the plan's title says MEZZANINE or ROOF). A mezzanine stands over part of a floor:
it keeps that floor's number, stands its own height above it, and is coded M00 over the
ground floor, its rooms' IDs carrying the code. The roof is the building's top level,
coded RF: its open deck is outdoor and walked through, the rooms standing on it (plant
rooms, stair and lift rooms, a penthouse's) are indoor, its parapets are its edge, and
nothing is built above it in 3D. Stairs link the ground floor to the mezzanine and the
mezzanine to the first floor, and reach the roof; a lift drawn on the roof is read as
its machine room, not a stop.

### Where buildings stand: the site plan

<p align="center">
  <img src="docs/images/site-plan.webp" width="75%" alt="The demo campus's site plan: the main building and the pavilion from above on a 5 m grid; the pavilion chosen and turned 12°, its round handle above it; Measure snapped corner to corner between them, 30.7 m; a compass in the corner, metres or feet, zoom buttons; below, the pavilion's x, y and turn, its plan's up facing 12° (NNE)">
</p>

Each location's site plan shows its buildings from above, where they stand relative to
each other. Drag a building, turn it by its handle, or type its x, y and turn, to any
degree: this is where a building is turned to stand as it really does, every floor with
it, never in Review. A compass says which way north is, − and + zoom, and **Measure**
gives the distance between two points, snapped to the buildings' corners, in metres or
feet. *Place on the map* then puts the whole site on the earth.

### Review: what it read, over the original drawing

![Review: the tools on the left, the rooms grouped by type, review mode on a copy room the rules could not type, its likely types in the inspector](docs/images/review.webp)

Review shows each floor over its drawing as printed, and counts the few rooms that need a
person: no type, no name, two rooms' labels in one, a gap to the outside, what a model
decided. **Review mode** (<kbd>N</kbd>) takes you through them one by one: <kbd>1</kbd>–<kbd>9</kbd>
sets one of its likely types, <kbd>Enter</kbd> accepts it. The tools draw what the drawing
leaves out (walls, doors, windows, dividers, spaces, stairs and lifts), and every
correction survives the next revision of the drawing.

| <kbd>⌘K</kbd> finds any room, item, floor or command | Light or dark |
|---|---|
| ![The command palette: the manager's room and a manager's desk by its tag](docs/images/palette.webp) | ![Review in the light theme: the executives' floor, the president's office chosen, its finishes and contents](docs/images/light.webp) |

### Furniture and equipment, each with an asset tag

| An item chosen: its tag, type and room | Placing one: it settles against the walls |
|---|---|
| ![The president's desk chosen in 3D, its tag and its Arabic name in the inspector](docs/images/furniture.webp) | ![Placing a copier in 3D: its ghost pulled into the corner by the door](docs/images/place.webp) |

Furniture and the building's equipment, in categories: desks by grade (from a junior's
to the president's), chairs, meeting tables for 4 to 16, storage, sofas; cameras, card
readers, smoke detectors, call points, extinguishers, emergency lights, access points,
network racks, screens, wayfinding kiosks, air-conditioning units, appliances. Each piece
is an item of its own, with its own tag (a desk's chair too), placed on the plan, in 3D or
while walking. An item stays in its room and lines up with the
walls and the items near it. Its ID is an asset's tag (`7K2Q-XM9F-4DP`) with a check
symbol: carried to another room or building, it keeps it, and ⌘K finds it as a person types
it. Each room's capacity comes from its desks unless you set it.

![Review on the demo's ground floor with what every camera covers drawn on the plan: a bullet camera's 60° view down the corridor from its east end, and two dome cameras' views from the corners of the café and the conference room; an access card reader chosen in the corridor, a dashed line to the conference room's door, the inspector naming the door it serves](docs/images/cameras.webp)

**Cameras and card readers.** A card reader, a door controller, an exit button or an
intercom placed by a door is linked to the nearest one: chosen, a dashed line joins it
to its door, and the inspector links it to another. A camera's mark is as wide as it
sees; chosen, or every camera at once (⌘K, *Show what every camera covers*), its view
is drawn out to its range: its type's (a dome 90° and 12 m, a bullet 60° and 20 m, a
PTZ all round), or its own. Walls are not taken off it.

**A 3D model for each type.** An item type can have a model of the real thing, in the
formats furniture is published in: glTF, FBX, OBJ, Collada, 3DS and more, or a `.zip`
with its textures. Studio makes it glTF as it is uploaded, reads it in metres, gives the
type its size, and says when a file is wrong about its size or which way is up (turn
it, fit it in other units, stand it up). Its items are then drawn as the model in 3D.
Project files carry the types' models with the files they were made from, and opening a
project file or a building's package gives a type its model where it has none.

### Floor and wall finishes

![Paint finishes in 3D: the floor finishes' swatches over the ground floor, the rooms grouped by floor finish](docs/images/paint.webp)

Fifty finishes (carpets, vinyl, porcelain, marble, terrazzo, wood, concrete; paints,
wallpapers, tiles, wood slats and panels, stone) for each room's floor and walls, or its
type's when it has none. Paint them in 3D (<kbd>P</kbd>), set them on the plan, or give
every office of a floor the same in one go; they go into the package.

### 3D: Real or Model, cut away, lifted apart, x-ray

![The first floor in 3D, cut away: the left half Real, finished as built; the right half Model, white with its edges drawn](docs/images/looks.webp)

Every building opens in 3D straight from Studio, nothing exported first: walls as drawn,
doors with architraves and handles, windows with glass, furniture and equipment, floors
and walls in their finishes, soft shadows. **Real** (left) or **Model** (right), one click
apart, at a quality that suits the machine.

| Studio's 3D window: every floor, lifted apart | X-ray: see-through walls, rooms tinted by type |
|---|---|
| ![Studio's 3D page: the main building's three floors lifted apart and cut away, the floor stack on the right](docs/images/world.webp) | ![X-ray of the ground floor: every room a translucent volume in its type's colour](docs/images/xray.webp) |

**Studio's 3D window** shows a building the whole window: the dollhouse or a walk, the
floor stack, a room's or an item's details, full screen, and **presenting** (<kbd>P</kbd>):
nothing but the building, turning slowly, for a big screen.

<p align="center">
  <img src="docs/images/void.webp" width="80%" alt="The demo's main building in 3D, its ground and first floors: on the first floor the room over the reception typed Open to below, labelled VOID, its floor cut away so that the reception below shows through, its desk and sofa; the offices and the meeting room around it; the compass and the floor stack in the corner">
</p>

A room typed *Open to below* (an atrium, a double-height hall) is a hole in its floor's
slab, the floor below seen through it. A compass over the floors turns with the view, in
the dollhouse and walking (a click faces north), and **Top** (<kbd>U</kbd>) looks
straight down, the plan's up at the top, and tilts back again.

### Walk through it, doors and all

<p align="center">
  <img src="docs/images/walk-door.webp" width="80%" alt="Walking down a corridor to the president's office: the cross on its door, E opens it, and in, past the walnut panels to the desk">
</p>

Walk in from the front door: mouse to look, <kbd>W A S D</kbd> to move. Doors open as you
walk into them, or with <kbd>E</kbd> or a click at the cross; a map follows you, the room
you are in is named, and at stairs and lifts you go up and down.

### Navigation: from the kiosk to your office

<p align="center">
  <img src="docs/images/route.webp" width="100%" alt="Find the way: OFFICE 213 typed and chosen; the way draws itself in from You are here at the reception's kiosk to the stairs, marked Up to Floor 2; Play walks a dot along it, up to Floor 2 and down the corridor to the office, each step lit on the left as it gets there">
</p>

Every package carries its building's walking network: its doors and openings, a point in
each room, the lifts and stairs that serve each floor, its entrances and kiosks. **Find
the way** goes from a kiosk, an entrance or any room to any room: the quickest way, or
one without stairs, in steps to read (*Walk 26 m through RECEPTION 001 to the stairs*,
*Take the stairs up to Floor 2*, turn by turn), drawn on a calm plan of each floor it
crosses: *You are here* at its start, a badge where it changes floor, a pin and a card on
the room it ends in. **Play** walks it, floor by floor. In 3D the way glows through the
building, and **Fly along** takes the camera with it, up the stairs and on to the office.
Ways go through every level: up the stairs to a mezzanine and on to the floor above, or
out onto the roof. Studio, the Go module and the viewers find the same way.

<p align="center">
  <img src="docs/images/route-fly.webp" width="80%" alt="The same way in 3D: a glowing ribbon from the kiosk through the reception to the stairs, a column up to Floor 2, then Fly along: the camera follows it through the reception, up the stairs and down the corridor to OFFICE 213, its pin and card">
</p>

### Share an area Studio read wrong

![Share an area: the part of the floor chosen, as drawn and as Studio read it, the note, what is taken out for privacy](docs/images/share-area.webp)

Drag a rectangle over a part of a floor Studio read wrong and download a small sample
file to send to the StoreyPath team: the drawing there, how Studio read it and why, and
what people corrected; names, contacts, IDs and where it is are taken out first. Nothing
is sent by Studio. [Area samples](docs/AREA-SAMPLES.md).

### Many people at once

![Omar looks at the first floor while Maya edits it: her lock in a banner, her and Lena's avatars in the top bar, the history of her changes](docs/images/together.webp)

Several people edit a floor at once, one a room: everyone else sees the rooms each is
editing outlined in their colour, and their changes as they are saved, with who made
each. Undo and redo are each person's own, and History lists who changed what, in words.

### Security and privacy

- **Everyone logs in.** Admins, engineers and users; each project shared on the whole,
  a building or a single floor, to view, edit or share. A person sees only what is
  shared with them, down to their floor's part of a sheet.
- **HTTPS by default**, with Studio's own certificate or yours; behind your proxy if you
  like. Studio answers only to the names it is given, and limits failed logins.
- **An audit log** of logins, sharing, exports and backups; backups of the whole
  database, as one file.
- **Private information out**: title blocks, names, contacts and a file's hidden data are
  found and taken out of every drawing before it is kept, unless you choose otherwise.
  A package carries the floors' drawings only when its export asks for them.
- **Encrypted files**: a package, a project file or a backup can be downloaded encrypted
  with a password (AES-256) and is opened with it in Studio; the viewers and the Go
  reader open an encrypted package too ([Sealed files](docs/SEALED-FILES.md)).
- **Offline**: no telemetry and nothing fetched while it runs; the GPU helper, when you
  use one, is yours too, behind a key.

[Users, sharing and backups](docs/ACCOUNTS.md).

### With a GPU, or without

| | Without a GPU | With a GPU |
|---|---|---|
| Reads the drawing | rules | rules |
| Reads its texts (room names, titles, notes, levels) | a small language model on the CPU (Qwen3.5-4B) | the same |
| Looks at each room as printed (is it a room, what kind, two rooms in one) | left to a person in Review | a vision model (Gemma 4 31B) on the [GPU helper](https://github.com/StoreyPath/storeypath-gpu-helper), with vLLM or llama.cpp |

The GPU helper is a second, optional image for an NVIDIA GPU; one Studio spreads each
floor's rooms over several. Any OpenAI-compatible server that takes images works too.
Measured on rooms checked by hand, the vision model judges 84% of outlines and 85% of types
right, and every call it makes is marked for review. [With a GPU or without](docs/GPU.md).

### When something goes wrong

An error's message stays at the foot of the page until it is closed, its words there to
be read and copied. Studio's log (the terminal, or `docker logs`) records every file
opened, with who sent it, its size and how long it took, or why it was not. When an
answer is not Studio's but a proxy's in front of it (one that stopped waiting, or would
not take a file that size), the page says so, and [the install guide](docs/INSTALL.md)
says what a proxy must let through.

## Quick start

**With Docker**, as above:

```sh
docker run -d --name storeypath -p 127.0.0.1:8080:8080 -v storeypath:/data ghcr.io/storeypath/studio
docker exec storeypath storeypath demo --studio
```

Open https://localhost:8080, log in as `admin` / `admin`, and open *Demo Campus*: a main
building of three floors and a pavilion of two, furnished and finished, with a few rooms
left for you to review. Drop your own DWG or DXF onto a new project. Everything is kept in
the `storeypath` volume. Upgrades, your own certificate, a proxy, Compose, offline
machines: [Installing StoreyPath Studio](docs/INSTALL.md).

**From source**, with [uv](https://docs.astral.sh/uv/) and a PostgreSQL 17 with PostGIS:

```sh
git clone --recurse-submodules https://github.com/StoreyPath/storeypath-studio
cd storeypath-studio/studio && uv sync
export STOREYPATH_DATABASE_URL=postgresql://user:password@localhost/storeypath
uv run storeypath demo --studio && uv run storeypath serve --data ~/storeypath --open
```

DWG files and the language model need two more programs: [Without Docker](docs/INSTALL.md#without-docker).

## Documentation

| | |
|---|---|
| [Installing StoreyPath Studio](docs/INSTALL.md) | Docker, the demo, offline images, without Docker, requirements, configuration |
| [Using Studio](docs/USING-STUDIO.md) | projects, drawings and the drawing viewer, mezzanines and roofs, the site plan, Review, furniture and equipment, finishes, export, navigation, 3D |
| [How Studio reads a drawing](docs/HOW-STUDIO-READS-A-DRAWING.md) | step by step: what rules decide and what the models decide |
| [With a GPU or without](docs/GPU.md) | the GPU helper, the language model, the vision model |
| [Users, sharing and backups](docs/ACCOUNTS.md) | roles, sharing, HTTPS, backups, the audit log |
| [The command line](docs/CLI.md) | every command; the workflow without the web app |
| [Area samples](docs/AREA-SAMPLES.md) | sharing a part of a drawing Studio read wrong |
| [Sealed files](docs/SEALED-FILES.md) | packages, project files and backups encrypted with a password, and their format |
| [How it fits together](docs/ARCHITECTURE.md) | the pieces, the package for other systems, stable IDs, status |
| [studio/README.md](studio/README.md) | Studio's reference: every setting, the API's access rules, many people at once |

## The StoreyPath family

| | |
|---|---|
| [**storeypath-studio**](https://github.com/StoreyPath/storeypath-studio) (here) | the authoring app: drawings in, packages out |
| [**storeypath-viewer**](https://github.com/StoreyPath/storeypath-viewer) | the package format, its JSON Schemas and conformance tests; the 3D world, the 2D plan and the drawing view to embed in your own web app; the Go reader (a submodule here) |
| [**storeypath-gpu-helper**](https://github.com/StoreyPath/storeypath-gpu-helper) | the optional vision model server for an NVIDIA GPU, with vLLM or llama.cpp |

## Contributing

Issues and pull requests are welcome. The tests need a PostgreSQL with PostGIS to make
their databases on:

```sh
docker run -d --name storeypath-test-db -p 127.0.0.1:55470:5432 \
    -e POSTGRES_USER=storeypath -e POSTGRES_PASSWORD=storeypath postgis/postgis:17-3.5
cd studio && uv sync --extra vision && uv run pytest
```

(`STOREYPATH_TEST_DATABASE_URL` names another.) The browser tests also need Node.js,
Chrome and the 2D viewer built (`npm ci && npm run build` in `storeypath-viewer/viewer/svg`).
A drawing Studio reads wrong is best reported with an [area sample](docs/AREA-SAMPLES.md).
The pictures on this page are taken from the running app, of made-up projects only (the
demo campus, Studio's sample warehouse, a made-up sheet of two wings); most by
[docs/media/capture.mjs](docs/media/README.md), so they can be taken again after a change.

## Licence

StoreyPath is [Apache License 2.0](LICENSE). The images also hold, each under its own
licence (all in `/usr/share/storeypath/licenses`): PostgreSQL (PostgreSQL Licence),
PostGIS (GPL-2.0-or-later) and Assimp (BSD-3-Clause), as Debian packages, run as
separate programs;
LibreDWG's `dwg2dxf` (GPL-3.0-or-later, run as a separate program; its exact source
is in the image), llama.cpp (MIT), the Qwen3.5 model weights (Apache-2.0), three.js
(MIT), N8AO (CC0-1.0), MapLibre GL JS (BSD-3-Clause), JSZip (MIT), Node.js (MIT) and
the Lucide icons of Studio's pages (ISC). The GPU helper's images list theirs in [its
repository](https://github.com/StoreyPath/storeypath-gpu-helper) (the Gemma 4 model
weights are Apache-2.0). SymPoint-V2 is never in an image unless you fetch it yourself
before building, for research use only.
