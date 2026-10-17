"""Checking an exchange package: structure, schemas, IDs and references."""

from __future__ import annotations

import csv
import io
import json
import math
import zipfile
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from .catalogue import Catalogue
from .models import model_problem
from .ids import LAND_CODE, LEVELS, MAX_ID_LENGTH, is_item_id, is_legacy_item_id, parse_id
from .package import (ASSET_IDS_FROM, COLLECTIONS, DRAWINGS, FILES, FORMAT_NAME, FORMAT_VERSION, LAYOUTS_FROM,
                      ONE_BUILDING_FROM, SITE_ROUTES_FROM, Changes, FeatureCollection, ItemFeature, Manifest,
                      Navigation, version_problem, version_tuple)
from .types import FloorType

KIND_LEVEL = {"location": "location", "building": "building", "floor": "floor",
              "space": "object", "zone": "object", "opening": "object",
              "layout": "floor", "footprint": "object"}  # (a location's layout, 0.12: a level of the location)
LAYOUT_ROLES = ("layout", "footprints")  # a layout's package's own files (0.12)
# the kinds of objects.csv rows this reader knows; rows of others (a later format's) are
# left alone, as a reader leaves unknown files and properties
KNOWN_KINDS = {"project", *KIND_LEVEL, "item"}
LOCAL_AGREES_M = 0.05  # an item's map position and its position in its building
HEADING_AGREES_DEG = 0.5  # its heading on the map and its turn in its building

M = TypeVar("M", bound=BaseModel)


def validate_package(path: str | Path) -> list[str]:
    """Return a list of problems; an empty list means the package is valid."""
    errors: list[str] = []
    try:
        z = zipfile.ZipFile(path)
    except (OSError, zipfile.BadZipFile) as e:
        return [f"not a readable package: {e}"]

    with z:
        names = set(z.namelist())

        def read(name: str) -> str | None:
            if name not in names:
                errors.append(f"missing file {name}")
                return None
            try:
                return z.read(name).decode("utf-8")
            except UnicodeDecodeError as e:
                errors.append(f"{name}: not UTF-8 text ({e})")
                return None

        def load(name: str, model: type[M]) -> M | None:
            """A JSON file of the package, as its model: strictly (a number is a number,
            not a string of one; true is true, not "true"), as every reader reads it."""
            text = read(name)
            if text is None:
                return None
            try:
                doc = _strict_json(text)
            except ValueError as e:
                errors.append(f"{name}: not JSON: {e}")
                return None
            try:
                return model.model_validate_json(text, strict=True)
            except ValidationError as e:
                errors.append(f"{name}: {_first_errors(e, doc)}")
                return None

        manifest = load("manifest.json", Manifest)
        if manifest is None:
            return errors
        if manifest.format != FORMAT_NAME:
            errors.append(f"unknown format {manifest.format!r}")
        if (why := version_problem(manifest.format_version)) is not None:
            errors.append(why)
        try:
            version = version_tuple(manifest.format_version)
        except ValueError:  # not a version: which format's rules it follows is not known
            return errors
        project = manifest.project.id
        one_building = version >= ONE_BUILDING_FROM
        # an item's ID: an asset's tag (0.8), else the project's code, -I and six digits
        asset_ids = version >= ASSET_IDS_FROM
        item_form = is_item_id if asset_ids else is_legacy_item_id
        not_an_item = ("not an item ID (four, four and three symbols of Crockford's base32, the last its "
                       "check: 7K2Q-XM9F-4DP)" if asset_ids
                       else f"not an item ID of project {project} ({project}-I and six digits)")

        def file_of(role: str) -> str:
            """A file of the package: by its role in the manifest's files, else by the name
            the format gives it."""
            return manifest.files.get(role) or FILES[role]

        ids: dict[str, str] = {}  # id -> kind
        collections: dict[str, list] = {}
        layout_scope = manifest.scope.layout if manifest.scope is not None else None
        if layout_scope is not None and version < LAYOUTS_FROM:
            errors.append(f"scope names a layout, which a package of {manifest.format_version} cannot hold (0.12)")
        for role, feature in COLLECTIONS.items():  # each kind's geometry checked by its model
            if role in LAYOUT_ROLES and role not in manifest.files:
                if layout_scope is not None:
                    errors.append(f"a layout's package lists its {role} in its manifest's files")
                continue  # (a building's package, or one before 0.12, holds none)
            fc = load(file_of(role), FeatureCollection[feature])
            if fc is None:
                continue
            if manifest.counts.get(role) != len(fc.features):
                errors.append(f"{file_of(role)}: manifest counts {manifest.counts.get(role)}, file has {len(fc.features)}")
            collections[role] = []  # the features with an ID: one without is a problem of its own
            for f in fc.features:
                kind = f.properties.kind
                try:
                    pid = parse_id(f.id)
                except ValueError as e:
                    errors.append(f"{file_of(role)}: {e}")
                    continue
                collections[role].append(f)
                if f.id in ids:
                    errors.append(f"duplicate ID {f.id}")
                ids[f.id] = kind
                if pid.project != project:
                    errors.append(f"{f.id}: project segment is not the package's project {project}")
                if pid.level != KIND_LEVEL[kind]:
                    errors.append(f"{f.id}: a {kind} ID needs {LEVELS.index(KIND_LEVEL[kind]) + 1} segments")
                land = len(pid.segments) >= 3 and pid.segments[2] == LAND_CODE
                if kind == "building" and land:
                    errors.append(f"{f.id}: {LAND_CODE} is a location's land (its layout): no building has it")
                elif kind in ("layout", "footprint") and not land:
                    errors.append(f"{f.id}: a {kind}'s ID is of its location's land (…-{LAND_CODE}-…)")

        def expect_parent(child: str, parent: str | None, kind: str) -> None:
            if parent is None or ids.get(parent) != kind:
                errors.append(f"{child}: parent {kind} {parent} is not in the package")
            elif not child.startswith(parent + "-"):
                errors.append(f"{child}: ID does not start with its parent {parent}")

        if layout_scope is not None:  # (0.12) a location's layout, in place of a building
            layouts = [f.id for f in collections.get("layout", [])]
            if layouts != [layout_scope]:
                errors.append(f"scope names layout {layout_scope}; the package holds {', '.join(layouts) or 'none'}")
            if manifest.scope.buildings or collections.get("buildings"):
                errors.append("a layout's package holds no building")
            if collections.get("floors") or collections.get("zones"):
                errors.append("a layout's package holds no floors and no zones")
            if "navigation" in manifest.files and version < SITE_ROUTES_FROM:
                errors.append(f"a layout's package of {manifest.format_version} has no walking network (0.13)")
            if set(manifest.placements) != {layout_scope}:
                errors.append(f"a layout's package has one placement, its site's, by the layout's ID {layout_scope}")
            for f in collections.get("layout", []):
                expect_parent(f.id, f.properties.location_id, "location")
                if f.id.rsplit("-", 1)[-1] != f.properties.code:
                    errors.append(f"{f.id}: its code is {f.properties.code}, not its ID's")
        elif any(role in manifest.files for role in LAYOUT_ROLES):  # (0.12) a building's package holds no layout
            errors.append("a building's package holds no layout: its manifest lists layout or footprints")
        if layout_scope is None and one_building:  # from 0.7, a package holds one building, and names it
            if manifest.scope is None or len(manifest.scope.buildings) != 1:
                errors.append("a package of this format holds one building: its manifest's scope names it")
            if len(collections.get("buildings", [])) != 1:
                errors.append(f"{file_of('buildings')}: a package of this format holds one building, "
                              f"this one {len(collections.get('buildings', []))}")
        if manifest.scope is not None:  # exactly the buildings it lists
            held = {f.id for f in collections.get("buildings", [])}
            for b in sorted(set(manifest.scope.buildings) - held):
                errors.append(f"scope lists building {b}, which is not in the package")
            for b in sorted(held - set(manifest.scope.buildings)):
                errors.append(f"building {b} is in the package but not in its scope")
        if "buildings" in collections and layout_scope is None:  # the manifest places every building, no other
            held = {f.id for f in collections["buildings"]}
            for b in sorted(held - set(manifest.placements)):
                errors.append(f"building {b} has no placement in the manifest")
            for b in sorted(set(manifest.placements) - held):
                errors.append(f"the manifest places building {b}, which is not in the package")
        for f in collections.get("buildings", []):
            expect_parent(f.id, f.properties.location_id, "location")
        for f in collections.get("floors", []):
            expect_parent(f.id, f.properties.building_id, "building")
        _check_floors(collections.get("floors", []), errors)
        for f in collections.get("spaces", []) + collections.get("zones", []):
            q = f.properties
            if (q.capacity is None) != (q.capacity_from is None):  # (0.7) a capacity says what says so
                errors.append(f"{f.id}: capacity {q.capacity} from {q.capacity_from}: both are set, or neither")
        def level_of(f) -> str:  # a space's or opening's level: a floor, or (0.12) a layout
            return "layout" if ids.get(f.properties.floor_id) == "layout" else "floor"

        for f in collections.get("spaces", []):
            expect_parent(f.id, f.properties.floor_id, level_of(f))
        zones_of = {f.id: set(f.properties.zones) for f in collections.get("spaces", [])}
        for f in collections.get("zones", []):
            expect_parent(f.id, f.properties.floor_id, "floor")
            space = f.properties.space_id
            if ids.get(space) != "space":
                errors.append(f"{f.id}: zone of unknown space {space}")
            elif f.id not in zones_of.get(space, set()):
                errors.append(f"{f.id}: not listed in the zones of its space {space}")
        space_of = {f.id: f.properties.space_id for f in collections.get("zones", [])}
        for space, zs in zones_of.items():
            for zone in sorted(zs):
                if ids.get(zone) != "zone":
                    errors.append(f"{space}: lists unknown zone {zone}")
                elif space_of.get(zone) != space:  # a zone is part of one space: the one it names
                    errors.append(f"{space}: lists zone {zone}, which is part of {space_of.get(zone)}")
        for f in collections.get("openings", []):
            expect_parent(f.id, f.properties.floor_id, level_of(f))
            for s in f.properties.connects:
                if ids.get(s) != "space":
                    errors.append(f"{f.id}: connects to unknown space {s}")
                elif not s.startswith(f.properties.floor_id + "-"):
                    errors.append(f"{f.id}: connects to {s} on another floor")

        # Footprints (format 0.12): the buildings standing on a layout, each of the layout's
        # location (a building's own package holds it), or of none
        for f in collections.get("footprints", []):
            q = f.properties
            expect_parent(f.id, q.layout_id, "layout")
            if q.building_id is not None:
                try:
                    b = parse_id(q.building_id)
                except ValueError as e:
                    errors.append(f"{f.id}: building {e}")
                    continue
                if b.level != "building" or b.segments[2] == LAND_CODE or \
                        not q.building_id.startswith(q.layout_id.split("-" + LAND_CODE + "-")[0] + "-"):
                    errors.append(f"{f.id}: building {q.building_id} is not a building of its layout's location")
            elif q.site is not None:
                errors.append(f"{f.id}: a footprint of no building, with a building's place on the site")

        # Items (format 0.6): furniture and equipment, when the package has them. Their
        # IDs are assets' tags (0.8; before, the project's and their own number); where
        # they are is data.
        if "items" in manifest.files:
            codes = None
            if "catalogue" in manifest.files and (cat := load(manifest.files["catalogue"], Catalogue)) is not None:
                codes = {t.code for t in cat.types}
                for t in cat.types:  # each type's 3D model (0.9.2): there, and as the format says
                    if t.model is not None:
                        why = model_problem(z.read(t.model)) if t.model in names else "not in the package"
                        if why:
                            errors.append(f"{t.model}: {t.code}'s model: {why}")
            fc = load(manifest.files["items"], FeatureCollection[ItemFeature])
            ways = {o.id: o.properties.type for o in collections.get("openings", [])}  # what a door_id may be
            if fc is not None:
                items = fc.features
                if manifest.counts.get("items") != len(items):
                    errors.append(f"{manifest.files['items']}: manifest counts {manifest.counts.get('items')}, file has {len(items)}")
                for f in items:
                    q = f.properties
                    if not item_form(f.id) or not (asset_ids or f.id.startswith(project + "-")):
                        errors.append(f"{f.id[:MAX_ID_LENGTH]}: {not_an_item}")
                        if not item_form(f.id):  # not an ID at all: nothing else of it is checked
                            continue
                    if f.id in ids:
                        errors.append(f"duplicate ID {f.id}")
                    ids[f.id] = "item"
                    on_land = ids.get(q.floor_id) == "layout"  # (0.12) on a location's land: of no building
                    if on_land:
                        if q.building_id is not None:
                            errors.append(f"{f.id}: on layout {q.floor_id}, in building {q.building_id}")
                    elif q.building_id is None or ids.get(q.building_id) != "building":
                        errors.append(f"{f.id}: in unknown building {q.building_id}")
                    elif ids.get(q.floor_id) != "floor" or not q.floor_id.startswith(q.building_id + "-"):
                        errors.append(f"{f.id}: on unknown floor {q.floor_id} (or not of building {q.building_id})")
                    for key, kind in ((q.space_id, "space"), (q.zone_id, "zone")):
                        if key is not None and (ids.get(key) != kind or not key.startswith(q.floor_id + "-")):
                            errors.append(f"{f.id}: in unknown {kind} {key} (or not on its floor)")
                    if codes is not None and q.type not in codes:
                        errors.append(f"{f.id}: type {q.type} is not in the catalogue")
                    if q.door_id is not None and (ways.get(q.door_id) in (None, "window")
                                                  or not q.door_id.startswith(q.floor_id + "-")):
                        errors.append(f"{f.id}: serves unknown door {q.door_id} (or not a door or opening of its floor)")
                    if one_building and q.local is None:
                        errors.append(f"{f.id}: no position in its building (local)")
                    # (a building without a placement, or not in the package, is a problem of its own above)
                    elif q.local is not None and (p := manifest.placements.get(
                            q.floor_id if on_land else q.building_id)) is not None:
                        lon, lat = _lonlat(p, q.local.x_m, q.local.y_m)
                        off = math.hypot((lon - q.display_point[0]) * 111_320 * math.cos(math.radians(lat)),
                                         (lat - q.display_point[1]) * 110_574)
                        if not off <= LOCAL_AGREES_M:  # (NaN is never within it)
                            errors.append(f"{f.id}: its map position is {off:.2f} m from its position in its building")
                        # its front, 0° the drawings' -y and counter-clockwise, on the map: clockwise from north
                        want = (p.bearing + 180 - q.local.rotation_deg) % 360
                        turn = abs((q.heading - want + 180) % 360 - 180)
                        if not turn <= HEADING_AGREES_DEG:
                            errors.append(f"{f.id}: its heading {q.heading:g}° is {turn:.1f}° from its turn in its "
                                          f"building ({want:.2f}° on the map)")

        # Navigation (format 0.8): the walking network, when the package has it. Its nodes
        # are on the package's floors, in its spaces and zones; its edges join its nodes. A
        # layout's (0.13) is on the layout; its links join its entrances to their buildings'
        # doors, in the buildings' packages.
        if "navigation" in manifest.files:
            name = manifest.files["navigation"]
            nav = load(name, Navigation)
            if nav is not None:
                level = "layout" if layout_scope is not None else "floor"
                held = {f.id for f in collections.get("buildings", [])}
                for b in nav.buildings:
                    if b not in held:
                        errors.append(f"{name}: building {b} is not in the package")
                if nav.layout != layout_scope:
                    errors.append(f"{name}: its layout is {nav.layout}, not the package's {layout_scope}")
                prints = {f.id: f.properties.building_id for f in collections.get("footprints", [])}
                nodes: dict[str, object] = {}
                for n in nav.nodes:
                    if n.id in nodes:
                        errors.append(f"{name}: node {n.id} is there twice")
                    nodes[n.id] = n
                    if ids.get(n.floor_id) != level:
                        errors.append(f"{name}: node {n.id} is on unknown {level} {n.floor_id}")
                    for key, kind in ((n.space_id, "space"), (n.zone_id, "zone")):
                        if key is not None and ids.get(key) != kind:
                            errors.append(f"{name}: node {n.id} is in unknown {kind} {key}")
                    if n.footprint_id is not None and (n.footprint_id not in prints
                                                       or prints[n.footprint_id] != n.building_id):
                        errors.append(f"{name}: entrance {n.id} is into {n.building_id}, which does not stand on "
                                      f"footprint {n.footprint_id}")
                pairs: set[tuple[str, str]] = set()
                for e in nav.edges:
                    if e.kind == "link":  # one end on the land, the other its building's door, in its own package
                        land = nodes.get(e.to) or nodes.get(e.from_)
                        far = e.from_ if land is nodes.get(e.to) else e.to
                        if layout_scope is None or land is None or land.footprint_id is None \
                                or far != f"door:{land.opening_id}" or far in nodes \
                                or not (land.opening_id or "").startswith(f"{land.building_id}-"):
                            errors.append(f"{name}: the link from {e.from_} to {e.to} does not join an entrance on "
                                          "the land to its building's door")
                    else:
                        for end in (e.from_, e.to):
                            if end not in nodes:
                                errors.append(f"{name}: an edge joins unknown node {end}")
                        if len(e.path) < 2:
                            errors.append(f"{name}: the edge from {e.from_} to {e.to} has no line")
                    pair = tuple(sorted((e.from_, e.to)))
                    if pair in pairs:
                        errors.append(f"{name}: two edges join {pair[0]} and {pair[1]}")
                    pairs.add(pair)

        # Drawings (format 0.11): each floor's drawing as drawn, when the export carried
        # them: named by its floor's source, in the folder the manifest lists, a view of a
        # format this reader reads, in metres (the building's own frame)
        views = {s.view: s.floor_id for s in manifest.sources if s.view is not None}
        folder = manifest.files.get("drawings")
        if views and folder != DRAWINGS:
            errors.append(f"the manifest's sources name drawings, but its files list {folder!r} for them, "
                          f"not {DRAWINGS!r}")
        elif folder is not None and not views:
            errors.append(f"the manifest lists {folder} but no floor's source names a drawing in it")
        if views:
            from .drawview import read_view_head, view_problem
        for view, floor in sorted(views.items()):
            if view != f"{DRAWINGS}{floor}.view":
                errors.append(f"{view}: floor {floor}'s drawing is {DRAWINGS}{floor}.view")
            if ids.get(floor) not in ("floor", "layout"):
                errors.append(f"{view}: the drawing of floor {floor}, which is not in the package")
            if view not in names:
                errors.append(f"missing file {view}")
                continue
            data = z.read(view)
            if (why := view_problem(data)) is not None:
                errors.append(f"{view}: {why}")
            elif (units := read_view_head(data).get("units")) != "m":
                errors.append(f"{view}: in {units!r}, not metres of the building's own frame")
        for name in sorted(n for n in names if n.startswith(DRAWINGS) and n != DRAWINGS and n not in views):
            errors.append(f"{name}: no floor's source names it")

        unknown: set[str] = set()
        name = file_of("objects")
        text = read(name)
        if text is not None:
            rows = list(csv.DictReader(io.StringIO(text)))
            unknown = {r.get("id") for r in rows if r.get("kind") not in KNOWN_KINDS}  # a later format's
            listed = {r.get("id") for r in rows if r.get("kind") != "project"} - unknown
            if listed != set(ids):
                errors.append(
                    f"{name}: rows do not match the features "
                    f"({len(listed - set(ids))} extra, {len(set(ids) - listed)} missing)"
                )

        name = file_of("changes")
        changes = load(name, Changes)
        if changes is not None:
            for i in changes.added + changes.changed:
                if i not in ids and i not in unknown:
                    errors.append(f"{name}: {i} is listed as added/changed but not in the package")
            retired = set(changes.retired) | set(changes.all_retired)
            for i in sorted(retired & set(ids)):
                errors.append(f"{name}: retired ID {i} is still in the package")
            for m in changes.moved_away:
                if not item_form(m.id) or m.id in ids:
                    errors.append(f"{name}: {m.id} is listed as moved away but is not an item gone from here")
                elif m.id in retired:  # carried elsewhere, it is not retired: it keeps its ID
                    errors.append(f"{name}: {m.id} is listed as moved away and as retired")
                if not m.building_id.startswith(project + "-") or m.building_id in ids:
                    errors.append(f"{name}: {m.id} moved to {m.building_id}, not another building of the project")
            if changes.sequence != manifest.export.sequence:
                errors.append(f"{name}: sequence does not match the manifest")
            if changes.previous_sequence != manifest.export.previous_sequence:
                errors.append(f"{name}: previous_sequence does not match the manifest")
            if changes.previous_sequence is not None and not changes.previous_sequence < changes.sequence:
                errors.append(f"{name}: previous_sequence {changes.previous_sequence} is not before "
                              f"this export's {changes.sequence}")
    return errors


ELEVATION_AGREES_M = 0.01  # a mezzanine's elevation: its host's and its height above it


def _check_floors(floors, errors: list[str]) -> None:
    """A building's levels (0.11): one of each ordinal that is not a mezzanine, any
    number of mezzanines on a floor, each code once. A mezzanine stands in a floor of its
    building (of type floor), of its ordinal, above_host_m above it (its elevation the
    host's and that); any other level has neither. A building's roofs are its top: no
    floor stands above a roof (a second roof over the rooms on the first may)."""
    held = {f.id: f.properties for f in floors}
    plain: dict[tuple[str, int], str] = {}  # (building, ordinal) -> its level that is not a mezzanine
    codes: dict[tuple[str, str], str] = {}  # (building, code) -> the level of that code
    for f in floors:
        q = f.properties
        if (q.building_id, q.code) in codes:
            errors.append(f"{f.id}: code {q.code} is {codes[q.building_id, q.code]}'s too")
        codes.setdefault((q.building_id, q.code), f.id)
        if q.type == FloorType.MEZZANINE:
            continue
        if q.host_floor_id is not None or q.above_host_m is not None:
            errors.append(f"{f.id}: a {q.type}, not a mezzanine, but with a host floor or a height above one")
        if (q.building_id, q.ordinal) in plain:
            errors.append(f"{f.id}: {plain[q.building_id, q.ordinal]} is floor {q.ordinal} of its building already "
                          "(only a mezzanine shares a floor's ordinal)")
        plain.setdefault((q.building_id, q.ordinal), f.id)
    for f in floors:
        q = f.properties
        if q.type == FloorType.ROOF:  # nothing but roofs above it
            above = sorted(g.id for g in floors if g.properties.building_id == q.building_id
                           and g.properties.type == FloorType.FLOOR and g.properties.ordinal > q.ordinal)
            if above:
                errors.append(f"{f.id}: a roof, with {', '.join(above)} above it (a roof is its building's top)")
        if q.type != FloorType.MEZZANINE:
            continue
        host = held.get(q.host_floor_id) if q.host_floor_id is not None else None
        if host is None or host.building_id != q.building_id:
            errors.append(f"{f.id}: a mezzanine of floor {q.host_floor_id}, which is not a floor of its building")
        elif host.type != FloorType.FLOOR:
            errors.append(f"{f.id}: a mezzanine of {q.host_floor_id}, a {host.type} (a mezzanine stands in a floor)")
        elif host.ordinal != q.ordinal:
            errors.append(f"{f.id}: a mezzanine of floor {host.ordinal} ({q.host_floor_id}), its ordinal {q.ordinal}")
        if q.above_host_m is None:
            errors.append(f"{f.id}: a mezzanine without its height above its host floor (above_host_m)")
        elif host is not None and not abs(host.elevation + q.above_host_m - q.elevation) <= ELEVATION_AGREES_M:
            errors.append(f"{f.id}: elevation {q.elevation:g}, not its host floor's ({host.elevation:g}) and "
                          f"{q.above_host_m:g} above it")


def validate_across(paths) -> list[str]:
    """Packages held together (each checked by validate_package): an asset is one
    project's, so the same item ID in packages of two projects is a clash, not one
    item. Each is a problem of the later package (in the order given), which a reader
    refuses. In packages of one project the same ID is the same item, carried from
    building to building."""
    seen: dict[str, tuple[str, str]] = {}  # item ID -> its project, and the package it was first in
    errors: list[str] = []
    for path in paths:
        name = Path(path).name
        try:
            with zipfile.ZipFile(path) as z:
                manifest = json.loads(z.read("manifest.json"))
                project, items = manifest["project"]["id"], manifest.get("files", {}).get("items")
                features = json.loads(z.read(items))["features"] if items in z.namelist() else []
        except (OSError, zipfile.BadZipFile, KeyError, ValueError, TypeError, AttributeError):
            continue  # not read: validate_package says why
        for f in features:
            i = f.get("id") if isinstance(f, dict) else None
            if not isinstance(i, str):
                continue
            project_first, where = seen.setdefault(i, (project, name))
            if project_first != project:
                errors.append(f"{name}: item {i[:MAX_ID_LENGTH]} is an item of project {project_first} too "
                              f"({where}): an item's ID is one project's")
    return errors


def _lonlat(p, x: float, y: float) -> tuple[float, float]:
    """A point of a building's own frame on the map, by its placement."""
    from .georef import Georeferencer
    from .workspace import Placement

    return Georeferencer(Placement(lon=p.lon, lat=p.lat, x=p.x, y=p.y, bearing=p.bearing)).lonlat(x, y)


def _not_json(constant: str):
    raise ValueError(f"{constant} is not a number in JSON")


def _finite(number: str) -> float:
    value = float(number)
    if not math.isfinite(value):
        raise ValueError(f"{number[:32]} is too large a number")
    return value


def _whole(number: str) -> int:
    value = int(number)
    if not -2**63 <= value < 2**63:
        raise ValueError(f"{number[:32]} is too large a number")
    return value


def _strict_json(text: str):
    """A file's document, read as a strict JSON reader reads it: NaN and Infinity are
    not JSON, and a number too large for a double (or a whole number for 64 bits) is
    not one (Python's json reads them all), wherever they are, in properties a reader
    knows or not."""
    return json.loads(text, parse_constant=_not_json, parse_float=_finite, parse_int=_whole)


def _first_errors(e: ValidationError, doc=None, n: int = 3) -> str:
    def where(loc) -> str:
        at = ".".join(map(str, loc))
        if len(loc) > 1 and loc[0] == "features" and isinstance(loc[1], int):  # say which feature
            try:
                fid = doc["features"][loc[1]]["id"]
            except (KeyError, IndexError, TypeError):
                fid = None
            if isinstance(fid, str):
                return f"{fid[:MAX_ID_LENGTH]} ({at})"
        return at

    parts = [f"{where(err['loc'])}: {err['msg']}" for err in e.errors()[:n]]
    more = e.error_count() - n
    return "; ".join(parts) + (f" (+{more} more)" if more > 0 else "")


def load_package_json(path: str | Path, name: str):
    with zipfile.ZipFile(path) as z:
        return json.loads(z.read(name))
