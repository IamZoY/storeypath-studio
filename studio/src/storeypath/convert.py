"""Converting a floor drawing into registered objects with stable IDs.

On every (re)conversion the new spaces and doors are matched against the
floor's current objects. A match keeps its ID; an object that is no longer in
the drawing is retired (its ID is never issued again); anything new gets a new
code from the building's counter.
"""

from __future__ import annotations

import math

import pickle
import threading
from collections import Counter, OrderedDict
from dataclasses import dataclass, field
from pathlib import Path

import shapely
from shapely import STRtree, set_precision
from shapely.geometry import Point, mapping, shape

from .analyse import analyse, name_hint, plan_texts
from .cad import file_sha256, meters_per_unit, read_drawing, read_notes
from .extract import (ExtractedDoor, ExtractedSpace, FloorExtraction, _assign_labels, _drawn_line, add_lift_doors,
                      find_spaces, find_spaces_around, finish_floor, gather_plan, keep_to_the_building,
                      on_the_roof, plan_labels, plan_walls, stair_flights, type_stairs, type_zoned_spaces)
from .geometry import iou
from .ids import child_id, parse_id
from .llm import LocalModel, worth_reading
from .profile import AUTO, load_profile, resolve_profile
from .reading import TextReader
from .symbols import MODEL as SYMBOLS_MODEL
from .symbols import Symbol, SymbolSpotter, from_records, to_records, type_rooms
from .vision import FloorPrint, VisionModel, keep_ways_through, look_at_rooms, split_merged
from .types import VERTICAL_TYPES, SpaceType
from .workspace import ObjectRecord, Workspace, utcnow

MATCH_MIN_IOU = 0.5  # outlines overlapping this much are the same space
MATCH_MIN_IOU_SAME_NUMBER = 0.1  # …or this much when the room number is unchanged
VERTICAL_MIN_IOU = 0.5  # elevator/stairs outlines on two floors this aligned share a code
DOOR_MATCH_DISTANCE = 0.5  # m
SAME_OPENING_M = 0.05  # an opening this close to one read before is that one, whatever kind it is read as
SAMPLE_TEXTS = 6  # unknown texts per layer asked about when reading what a layer holds
# A read that finds no spaces where a floor has some, or that would retire more than
# this share of them, is most likely a bad read (layers renamed, wrong units, a
# broken file): it is held back, the floor keeping its rooms and IDs, until it is
# applied on purpose (``force``).
HOLD_RETIRING_SHARE = 0.5
# A room people corrected (named, typed, numbered, hidden, deleted, seated, linked,
# finished) that a read would retire is theirs to let go: the read is held back and says
# which, unless the room's correction can go on with it. It does when this much of the
# room lies in one room the read finds anew (the same room, grown or reshaped past
# matching), numbered as it was or not at all, which no other such room goes into: a
# person's correction is never put on a room that is another's (two rooms merged into
# one that keeps either's ID; a room split, its pieces each partly it).
CARRY_SHARE = 0.8


@dataclass
class ConversionReport:
    floor_id: str
    method: str = "outlines"
    kept: list[str] = field(default_factory=list)
    added: list[str] = field(default_factory=list)
    retired: list[str] = field(default_factory=list)
    unspecified: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    held: bool = False  # the read was not applied: it would have retired most of the floor, or rooms people corrected
    hold: str | None = None  # why it was held back
    carried: list[tuple[str, str]] = field(default_factory=list)  # (retired, new): a room's correction gone on with it
    lost: list[str] = field(default_factory=list)  # rooms people corrected, retired all the same (force)
    vision: dict = field(default_factory=dict)  # rooms looked at, asked about, kept as barely changed (look_at_rooms)

    def summary(self) -> str:
        if self.held:
            why = (self.hold or "the drawing read too unlike the floor (see the warning)").removeprefix("nothing was changed: ")
            return f"{self.floor_id}: not changed: {why}"
        source = " (spaces found from walls)" if self.method == "walls" else ""
        seen = ""
        if self.vision.get("rooms"):  # (why a floor read again asks about fewer: what it saw before is kept)
            v = self.vision
            seen = (f"; vision asked about {v.get('asked', 0)} of {v['rooms']} rooms, "
                    f"{v['rooms'] - v.get('asked', 0)} kept from before ({v.get('near', 0)} of them barely changed)")
        carried = f"; the corrections of {len(self.carried)} reshaped rooms went on with them" if self.carried else ""
        lost = f"; {len(self.lost)} rooms people corrected retired, their corrections with them" if self.lost else ""
        return (
            f"{self.floor_id}: {len(self.kept)} kept, {len(self.added)} new, "
            f"{len(self.retired)} retired, {len(self.unspecified)} need a type{source}{seen}{carried}{lost}"
        )


def convert_floor(
    ws: Workspace, floor_id: str, workspace_dir: str | Path = ".", model: LocalModel | None = None,
    symbols: SymbolSpotter | None = None, vision: VisionModel | None = None, say=None, force: bool = False,
    tiles: str | Path | None = None, drawn: bool = False, reads: str | Path | None = None,
) -> ConversionReport:
    """Read a floor's drawing and register what it holds (read_floor, applied to ``ws``:
    apply_read). A read that would retire most of the floor is held back unless
    ``force`` (see apply_extraction): the floor is then left as it was. ``drawn``: read
    after a person drew on the floor in review (or undid it), see apply_extraction."""
    read = read_floor(ws, floor_id, workspace_dir, model, symbols, vision, say, tiles, drawn, reads)
    return apply_read(ws, read, force=force, drawn=drawn)


def read_floor(
    ws: Workspace, floor_id: str, workspace_dir: str | Path = ".", model: LocalModel | None = None,
    symbols: SymbolSpotter | None = None, vision: VisionModel | None = None, say=None,
    tiles: str | Path | None = None, drawn: bool = False, reads: str | Path | None = None,
) -> FloorRead:
    """Read a floor's drawing: what it holds, to register in the project (apply_read).
    With the "auto" profile the layers are read from what is drawn on them; ``model``
    (a local language model) reads the texts the rules do not know; ``symbols`` spots
    the fixtures drawn in rooms that have no name; ``vision`` looks at every room as
    drawn (vision.py). What the models answer is kept in ``ws``. ``tiles``: a folder
    where the drawing's prints for the vision model are kept, for the floor read again
    (FloorPrint). ``drawn``: read after a person drew on the floor in review (or undid
    it): what can be is taken from its last read (_extract), kept in memory and in
    ``reads`` (a folder), where it is kept for Studio started again."""
    floor = ws.floor(floor_id)
    if floor.source is None:
        raise ValueError(f"floor {floor_id} has no source drawing")
    if vision is not None:
        vision.failed = None  # only what goes wrong converting this floor is reported with it
    src = floor.source
    path = Path(workspace_dir) / src.path
    sha = file_sha256(path)
    doc = _drawing(path, sha)
    base = load_profile(resolve_profile(src.profile, workspace_dir))
    reader = TextReader(ws, base, model)
    drawing = (sha, src.region, src.offset, src.units, src.profile)  # what the floor's plan is read from
    last = _last_read(floor_id, drawing, reads) if drawn else None
    if last is not None:  # read again after a person drew on it: with the layers' roles it was read with
        profile, layers = last.profile, last.layers
    elif src.profile == AUTO:
        reader.learn(plan_texts(doc, src.region, per_layer=SAMPLE_TEXTS,
                                unknown=lambda t: worth_reading(t) and reader.is_room_name(t) is None))
        analysis = analyse(doc, meters_per_unit(doc, src.units), src.region, reader.is_room_name, base)
        profile, layers = analysis.profile, analysis.summary()
    else:
        profile, layers = base, []
    extraction, read = _extract(floor_id, doc, profile, src, floor.edits, reader, drawing, layers, last, say,
                                roof=floor.roof)
    _drawn_spaces(extraction, floor.edits.spaces, profile.spaces.min_area)
    tally: dict = {}  # what vision looked at (the report's)
    _read_room_types(extraction, reader)
    spotted, spot_failed = _spot_symbols(floor, doc, profile, symbols, extraction.scale, sha)
    type_rooms(extraction.units(), spotted)
    if floor.roof:  # its open ground the roof's deck, a lift on it its machine room (no stop)
        on_the_roof(extraction, deck_only=True)
    add_lift_doors(extraction)
    if vision is not None or ws.vision:
        sheet = FloorPrint(doc, extraction.scale, keep=tiles, sha=sha, region=src.region)
        units = extraction.units()
        # (a roof's deck is not asked about: what vision would call the outside is it)
        deck = {i for i, u in enumerate(units) if u.type == SpaceType.ROOF} if floor.roof else set()
        merged = look_at_rooms(units, doc, src, extraction.scale, sha, vision, ws.vision, say, sheet=sheet, tally=tally,
                               only=set(range(len(units))) - deck if deck else None)
        if zones := split_merged(extraction, units, doc, src, sha, vision, ws.vision, merged, say, sheet):
            _name_zones(extraction, zones, profile, reader, spotted)
            units = extraction.units()
            look_at_rooms(units, doc, src, extraction.scale, sha, vision, ws.vision, say,
                          only={i for i, u in enumerate(units) if any(u is z for z in zones)}, sheet=sheet, tally=tally)
        if (ways := keep_ways_through(extraction)) and say is not None:
            say(f"vision: {ways} area(s) it saw as no room kept, as doors join them to several rooms")
        if floor.roof:  # what vision saw as no room, or the outside, on a roof is its deck
            on_the_roof(extraction)
        type_stairs(extraction.units(), _flights(doc, src, extraction.scale))
        type_zoned_spaces(extraction.spaces, extraction.zones)
        keep_to_the_building(extraction)
    else:
        if floor.roof:
            on_the_roof(extraction)
        type_stairs(extraction.units(), _flights(doc, src, extraction.scale))
    _sizes_from_the_schedule(extraction, doc, src.region, vision, model, say)
    _drawn_openings(extraction, floor.edits.openings, profile.doors.reach)
    _resized_openings(extraction, floor.edits.resized, profile.doors.reach)
    notes = read_notes(doc)
    if reader.model_failed:
        notes.append(f"the language model was not used: {reader.model_failed}")
    if spot_failed:
        notes.append(f"symbols were not spotted: {spot_failed}")
    if vision is not None and vision.failed:
        notes.append(f"vision: {vision.failed}")
    return FloorRead(floor_id, extraction, layers, sha, (floor.symbols, floor.symbols_key), tally, notes, read,
                     Path(reads) if reads is not None else None)


@dataclass
class FloorRead:
    """A floor's drawing read (read_floor), to be applied to the project (apply_read):
    to the one it was read from, or to the project as it is when the read ends, with
    what people changed meanwhile."""

    floor_id: str
    extraction: FloorExtraction
    layers: list  # the layers' roles, as the floor lists them
    sha: str  # the drawing's content
    symbols: tuple  # (records, key): the symbols spotted in it, as the floor keeps them
    tally: dict  # what vision looked at
    notes: list[str]  # what the read had to say beside what applying it says
    kept: "_Read"  # what to keep of it for the floor read again…
    keep_in: Path | None = None  # …and where, beside memory


def apply_read(ws: Workspace, read: FloorRead, force: bool = False, drawn: bool = False) -> ConversionReport:
    """A floor's read applied to ``ws`` (apply_extraction), with what was read with it:
    its layers, the drawing's content and the symbols spotted in it."""
    floor = ws.floor(read.floor_id)
    floor.symbols, floor.symbols_key = read.symbols
    report = apply_extraction(ws, read.floor_id, read.extraction, force=force, drawn=drawn)
    report.vision = read.tally
    if not report.held:  # a read held back leaves the floor as it was
        floor.layers = read.layers
        floor.source.sha256 = read.sha
        _keep_read(read.floor_id, read.kept, read.keep_in)
    report.warnings += read.notes
    return report


DRAWINGS_KEPT = 2  # drawings kept read, by their content (a big floor's takes about 0.4 GB)
READS_KEPT = 4  # floors whose last read is kept, for a person drawing on them
_drawings: OrderedDict[str, object] = OrderedDict()
_reads: OrderedDict[str, "_Read"] = OrderedDict()
_kept_lock = threading.Lock()


@dataclass
class _Read:
    """What a floor's last read found in its drawing, kept: read again after a person
    drew on it, the floor takes what it can from it (_extract) instead of going through
    the drawing again (a minute and more on a big floor). Each read takes copies."""

    drawing: tuple  # what it was read from: the drawing's content, the part read, its offset, units, profile
    walls: list  # the walls drawn it was read with
    profile: object  # the layers' roles it was read with…
    layers: list  # …as the floor lists them
    plan: bytes  # what the plan holds (extract.PlanDrawn)
    found: bytes  # the spaces found in it, before they were named (extract.FoundSpaces)


def _drawing(path: Path, sha: str):
    """The drawing at ``path`` (of content ``sha``), read: kept, so that the floor read
    again, or another floor of it, does not read it again."""
    with _kept_lock:
        doc = _drawings.get(sha)
        if doc is not None:
            _drawings.move_to_end(sha)
            return doc
    doc = read_drawing(path)
    with _kept_lock:
        _drawings[sha] = doc
        while len(_drawings) > DRAWINGS_KEPT:
            _drawings.popitem(last=False)
    return doc


def _last_read(floor_id: str, drawing: tuple, folder: Path | str | None = None) -> "_Read | None":
    """The floor's last read, when it was of ``drawing``: kept in memory, else in
    ``folder`` (by a Studio before this one: taken only when read with the same reading
    code, READ_CODE)."""
    with _kept_lock:
        read = _reads.get(floor_id)
    if read is None and folder is not None:
        read = _read_from(Path(folder) / _read_file(floor_id))
        if read is not None:
            with _kept_lock:
                _reads.setdefault(floor_id, read)
                _trim_reads()
    return read if read is not None and read.drawing == drawing else None


def _keep_read(floor_id: str, read: "_Read", folder: Path | str | None = None) -> None:
    """The floor's last read kept in memory and, written in the background, in ``folder``."""
    with _kept_lock:
        _reads[floor_id] = read
        _reads.move_to_end(floor_id)
        _trim_reads()
    if folder is not None:
        threading.Thread(target=_write_read, args=(Path(folder) / _read_file(floor_id), read, READ_CODE), daemon=True,
                         name=f"keeping {floor_id}'s read").start()


def _trim_reads() -> None:
    while len(_reads) > READS_KEPT:
        _reads.popitem(last=False)


def _read_file(floor_id: str) -> str:
    return f"{floor_id}.read"


def _write_read(path: Path, read: "_Read", code: str) -> None:
    """A read written whole or not at all (a file of its own, put in place), with the
    ``code`` that read it (as it was then: written in the background, later)."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        part = path.with_name(f"{path.name}.{threading.get_ident()}.part")
        part.write_bytes(pickle.dumps((code, read), protocol=pickle.HIGHEST_PROTOCOL))
        part.replace(path)
    except OSError:
        pass  # (not kept on disk: read whole the first time again)


def _read_from(path: Path) -> "_Read | None":
    """A read kept in a file of Studio's own cache, when read with this reading code."""
    try:
        code, read = pickle.loads(path.read_bytes())
    except (OSError, pickle.UnpicklingError, EOFError, AttributeError, ImportError, ValueError, TypeError):
        return None
    return read if code == READ_CODE and isinstance(read, _Read) else None


def _reading_code() -> str:
    """What the reading code is: its modules' source, hashed. A read kept by a Studio
    that read otherwise is not taken."""
    import hashlib
    import importlib

    h = hashlib.sha256()
    for name in ("analyse", "cad", "convert", "extract", "geometry", "profile", "split", "walls"):
        h.update(Path(importlib.import_module(f"storeypath.{name}").__file__).read_bytes())
    return h.hexdigest()[:16]


READ_CODE = _reading_code()


def keep_a_read(ws: Workspace, floor_id: str, workspace_dir: str | Path, reads: str | Path | None = None) -> bool:
    """The floor's read worked out and kept, not applied (read_floor's, without vision,
    and the layers' roles as the rules and the texts read already say: no model asked),
    for the floor read again after a person draws on it, where none is kept. Whether one
    was kept."""
    floor = ws.floor(floor_id)
    src = floor.source
    if src is None or (src.sha256 and _last_read(floor_id, (src.sha256, src.region, src.offset, src.units,
                                                             src.profile), reads) is not None):
        return False
    path = Path(workspace_dir) / src.path
    sha = file_sha256(path)
    drawing = (sha, src.region, src.offset, src.units, src.profile)
    if _last_read(floor_id, drawing, reads) is not None:
        return False
    doc = _drawing(path, sha)
    base = load_profile(resolve_profile(src.profile, workspace_dir))
    reader = TextReader(ws, base, None)
    if src.profile == AUTO:
        analysis = analyse(doc, meters_per_unit(doc, src.units), src.region, reader.is_room_name, base)
        profile, layers = analysis.profile, analysis.summary()
    else:
        profile, layers = base, []
    _, read = _extract(floor_id, doc, profile, src, floor.edits, reader, drawing, layers, None)
    _keep_read(floor_id, read, reads)
    return True


def kept_read(floor_id: str, src, reads: str | Path | None = None) -> bool:
    """Whether a read of the floor's drawing (``src``: as it was last read) is kept."""
    return src is not None and bool(src.sha256) and _last_read(
        floor_id, (src.sha256, src.region, src.offset, src.units, src.profile), reads) is not None


def _extract(floor_id: str, doc, profile, src, edits, reader: TextReader, drawing: tuple, layers: list,
             last: "_Read | None", say=None, roof: bool = False) -> tuple[FloorExtraction, "_Read"]:
    """The floor's extraction (extract_floor), and the read to keep of it. Read again
    after a person drew on it (``last``: its last read), what its plan holds is taken
    from that read, and so are its spaces: as they were when its walls are (a door, a
    dividing line or a space drawn), else found again around the walls drawn or taken
    away (find_spaces_around), the whole floor's only where that cannot tell."""
    skip = lambda t: reader.is_room_name(t) is False  # noqa: E731
    plan = pickle.loads(last.plan) if last is not None else gather_plan(doc, profile, src.units, src.region)
    labels = plan_labels(plan, profile, skip)
    lines, fills, warnings = plan_walls(plan, edits.walls, src.offset)
    found = None
    if last is not None:
        before = pickle.loads(last.found)
        changed = _walls_changed(last.walls, edits.walls)
        if not changed:
            found = before
        else:
            dx, dy = (src.offset[0] * plan.scale, src.offset[1] * plan.scale) if src.offset else (0.0, 0.0)
            why: list[str] = []
            found = find_spaces_around(plan, profile, labels, lines, fills, before,
                                       [_drawn_line(w, dx, dy) for w in changed], why)
            if say is not None:
                say("the rooms near the walls drawn found again" if found is not None
                    else f"the whole floor read: {why[0]}")
    if found is None:
        found = find_spaces(plan, profile, labels, lines, fills, edits.walls, src.offset, open_ground=roof)
    read = _Read(drawing, [list(map(list, w)) for w in edits.walls], profile, layers,
                 last.plan if last is not None else pickle.dumps(plan, protocol=pickle.HIGHEST_PROTOCOL),
                 pickle.dumps(found, protocol=pickle.HIGHEST_PROTOCOL))  # (before finish_floor names them)
    return finish_floor(plan, found, profile, labels, lines, fills, src.offset, edits.dividers, warnings), read


def _walls_changed(before: list, now: list) -> list:
    """The walls drawn since, and those taken away since."""
    def key(w):
        return tuple(tuple(map(float, p)) for p in w)

    a, b = Counter(key(w) for w in before), Counter(key(w) for w in now)
    return [list(map(list, w)) for w in ((a - b) + (b - a)).elements()]


NOT_SYMBOLS = {"dimension", "grid"}  # layers whose lines are not things in the building


def _spot_symbols(floor, doc, profile, spotter: SymbolSpotter | None, scale: float,
                  sha: str) -> tuple[list[Symbol], str | None]:
    """The symbols drawn in the floor's plan, and why they could not be found. They
    are kept with the floor and found again only when the drawing, the part of it
    read or its units change; without the model, the ones kept are used as long as
    they are of this same drawing."""
    src = floor.source
    key = f"{SYMBOLS_MODEL}/{sha}/{src.region}/{src.offset}/{scale:g}"
    if floor.symbols is not None and floor.symbols_key == key:
        return from_records(floor.symbols), None
    if spotter is None or not spotter.available():
        return [], None

    def skip(layer: str) -> bool:
        return name_hint(layer) in NOT_SYMBOLS or bool(profile.label_layers.fullmatch(layer))

    found = spotter.find(doc, src.region, src.offset, scale, skip)
    if spotter.failed is None:
        floor.symbols, floor.symbols_key = to_records(found), key
    return found, spotter.failed


def _read_room_types(ex: FloorExtraction, reader: TextReader) -> None:
    """Room names the rules do not know, typed by the language model."""
    unknown = [s.name for s in ex.units() if s.name and s.type == SpaceType.UNSPECIFIED]
    reader.learn(unknown, rooms_only=True)
    for s in ex.units():
        if s.name and s.type == SpaceType.UNSPECIFIED and (t := reader.room_type(s.name)) is not None:
            s.type, s.type_source = t, "model"


def _flights(doc, src, scale: float) -> list:
    """The floor's stair flights (stair_flights of its line pieces), kept with the drawing
    read, as its line pieces are."""
    kept = getattr(doc, "_storeypath_flights", None)
    if kept is None:
        kept = doc._storeypath_flights = {}
    key = (src.region, src.offset, scale)
    if key not in kept:
        kept[key] = stair_flights(_floor_segments(doc, src, scale))
    return kept[key]


def _floor_segments(doc, src, scale: float) -> list:
    """Every straight piece drawn on a floor (not text, dimensions or hatches), local meters."""
    from shapely.geometry import box

    from .vision import _segments_in

    from .extract import drawing_extents

    ox, oy = src.offset or (0.0, 0.0)
    extent = src.region if src.region is not None else drawing_extents(doc)
    if extent is None:
        return []
    return _segments_in(doc, box(*extent), (ox, oy, scale))


def _name_zones(ex: FloorExtraction, zones: list, profile, reader: TextReader, spotted) -> None:
    """Zones vision divided an open space into, named from the labels inside each
    and typed as any space is."""
    _assign_labels(zones, ex.labels, profile, [])
    for z in zones:
        z.type, z.type_source = profile.classify(z.name, z.blocks, z.layer)
    _read_room_types(ex, reader)
    type_rooms(zones, spotted)


def _keep_package_values(ws: Workspace, record: ObjectRecord) -> None:
    """A space of a project rebuilt from a package (bundle.py), read from its drawing
    for the first time: what the package said of it (what a person may have set) is
    kept as its correction, over what the drawing is read as."""
    from .workspace import Override

    o = ws.overrides.get(record.id) or Override()
    if o.type is None and record.type in {t.value for t in SpaceType}:
        o.type = SpaceType(record.type)
    if o.name is None:
        o.name = record.name or ""
    if o.number is None:
        o.number = record.number or ""
    ws.overrides[record.id] = o


def apply_extraction(ws: Workspace, floor_id: str, ex: FloorExtraction, force: bool = False,
                     drawn: bool = False) -> ConversionReport:
    """Register an extracted floor in the workspace, reusing IDs where objects match.
    A read that finds no spaces on a floor that has some, that would retire more than
    HOLD_RETIRING_SHARE of them, or rooms people corrected (CARRY_SHARE), is held back
    unless ``force``: nothing changes, and the report says so (``held``) with a
    warning. ``drawn``: read after a person drew on the floor (or undid it): rooms
    people corrected that it retires are not held for, as the person changed them so."""
    report = ConversionReport(floor_id, method=ex.method, warnings=list(ex.warnings))
    floor = ws.floor(floor_id)
    building_id = parse_id(floor_id).prefix("building")
    now = utcnow()

    existing = [r for r in ws.floor_objects(floor_id) if r.kind in ("space", "zone")]
    # What is used keeps its ID first (zones, and spaces with none), so a space that
    # an earlier conversion cut in two and that is now one space with two zones
    # hands its IDs to the zones; then the spaces divided into zones.
    zoned = {z.space for z in ex.zones}
    units = [*ex.zones, *(s for i, s in enumerate(ex.spaces) if i not in zoned)]
    containers = [s for i, s in enumerate(ex.spaces) if i in zoned]
    unit_pairs = _match_spaces(existing, units)
    taken = {r.id for r in unit_pairs.values()}
    container_pairs = _match_spaces([r for r in existing if r.id not in taken], containers)
    found = {id(units[i]): r for i, r in unit_pairs.items()} | {id(containers[i]): r for i, r in container_pairs.items()}
    carry, lost = _carried(ws, existing, found, [*units, *containers])
    if not force and (hold := _hold_back(existing, found, ex) or (None if drawn else _hold_corrected(ws, lost))):
        report.held, report.hold = True, hold
        report.warnings.append(hold)
        return report
    report.lost = [r.id for r in lost]

    def register(obj, kind: str) -> ObjectRecord:
        record = found.get(id(obj))
        if record is None:
            record_id = _new_space_id(ws, floor_id, building_id, obj) if kind == "space" else \
                _allocate_id(ws, floor_id, building_id)
            record = ObjectRecord(id=record_id, kind=kind, type=obj.type, geometry={})
            ws.objects[record_id] = record
            report.added.append(record_id)
        else:
            report.kept.append(record.id)
            if record.type_source == "package":
                _keep_package_values(ws, record)
        record.kind = kind
        record.type, record.type_source = obj.type, obj.type_source
        record.name, record.number = obj.name, obj.number
        record.label = obj.label
        record.issues = list(obj.issues)
        record.detected_ignored = obj.ignored
        record.geometry = _local(obj.polygon)
        record.parent, record.zones = None, []
        if (was := carry.get(id(obj))) is not None:  # a person's correction, going on with the room
            ws.overrides[record.id] = ws.overrides[was.id].model_copy(deep=True)
            report.carried.append((was.id, record.id))
        return record

    space_records = [register(s, "space") for s in ex.spaces]
    space_ids = [r.id for r in space_records]
    for z in ex.zones:
        record = register(z, "zone")
        record.parent = space_ids[z.space]
        space_records[z.space].zones.append(record.id)
    report.unspecified += [r.id for r in (*space_records, *(ws.objects[i] for s in space_records for i in s.zones))
                           if not r.zones and ws.effective(r)["type"] == "unspecified"]

    matched = set(report.kept)
    for r in existing:
        if r.id not in matched:
            _retire(r, now, report)

    _apply_doors(ws, floor_id, building_id, ex, space_ids, now, report)

    floor.outline = _local(ex.outline) if ex.outline is not None else None
    floor.walls = _local(ex.walls) if ex.walls is not None else None
    floor.wall_thickness = ex.wall_thickness
    floor.converted_at = now
    floor.method, floor.warnings = ex.method, list(ex.warnings)
    return report


def _hold_back(existing: list[ObjectRecord], found: dict, ex: FloorExtraction) -> str | None:
    """Why a read is held back (see apply_extraction), or None to apply it."""
    if not existing:
        return None
    kept = {r.id for r in found.values()}
    retiring = sum(1 for r in existing if r.id not in kept)
    if ex.spaces and retiring <= HOLD_RETIRING_SHARE * len(existing):
        return None
    what = "no spaces" if not ex.spaces else f"{len(ex.spaces)} spaces, {len(existing) - retiring} of them where they were"
    return (f"nothing was changed: the drawing read {what}, so {retiring} of the floor's {len(existing)} spaces and "
            "their IDs would be retired. Check the drawing (its layers, units or part read); if it really "
            "changed this much, read it anyway")


def _corrected(ws: Workspace, record: ObjectRecord) -> bool:
    """Whether people set something of a room (a correction setting nothing only says it
    was looked at and accepted as it is)."""
    o = ws.overrides.get(record.id)
    return o is not None and any(v is not None for v in o.model_dump().values())


def _carried(ws: Workspace, existing: list[ObjectRecord], found: dict, found_now: list) -> tuple[dict, list]:
    """The rooms people corrected that a read retires: those whose correction goes on
    with them (CARRY_SHARE), as {id of the room found: the retired record}, and the
    others."""
    kept = {r.id for r in found.values()}
    corrected = [r for r in existing if r.id not in kept and _corrected(ws, r)]
    if not corrected:
        return {}, []
    new = [u for u in found_now if id(u) not in found]
    tree = STRtree([u.polygon for u in new]) if new else None
    into: dict[int, list[tuple[ObjectRecord, object]]] = {}
    for r in corrected:
        was = shape(r.geometry)
        if tree is None or not was.area > 0:
            continue
        for j in tree.query(was, predicate="intersects"):
            u = new[int(j)]
            if was.intersection(u.polygon).area >= CARRY_SHARE * was.area and u.number in (None, "", r.number):
                into.setdefault(id(u), []).append((r, u))
    carry = {k: rooms[0][0] for k, rooms in into.items() if len(rooms) == 1}
    gone_on = {r.id for r in carry.values()}
    return carry, [r for r in corrected if r.id not in gone_on]


HOLD_LISTED = 8  # rooms a held read names


def _hold_corrected(ws: Workspace, lost: list[ObjectRecord]) -> str | None:
    """Why a read is held back for the rooms people corrected it would retire (see
    CARRY_SHARE), or None."""
    if not lost:
        return None

    def named(r: ObjectRecord) -> str:
        e = ws.effective(r)
        words = " ".join(w for w in (e["name"], e["number"]) if w) or str(e["type"]).replace("_", " ")
        return f"{words} ({r.id})"

    listed = ", ".join(named(r) for r in lost[:HOLD_LISTED]) + (f" and {len(lost) - HOLD_LISTED} more"
                                                                  if len(lost) > HOLD_LISTED else "")
    return (f"nothing was changed: the drawing now reads {len(lost)} room(s) people corrected otherwise, and reading it "
            f"would retire them: {listed}. Their corrections stay with their retired IDs (none goes on to another "
            "room). If that is right, read it anyway")


def _sizes_from_the_schedule(ex: FloorExtraction, doc, region, vision, model, say) -> None:
    """Each tagged door's and window's sill and height, from the drawing's schedule of
    openings (schedule.py), its rows read by the strongest model that runs."""
    from .schedule import sizes_near
    from .vision import InWords

    if vision is not None and vision.available():
        reader = InWords(vision)
    else:
        reader = model if model is not None and model.available() else None
    sizes = sizes_near(doc, region, reader)
    sized = 0
    for d in ex.doors:
        if d.tag and (size := sizes.get(d.tag)) is not None:
            d.sill, d.height = size.sill, size.height
            sized += 1
    if sized and say:
        say(f"{sized} doors and windows sized from the schedule of openings")


def _local(geom) -> dict:
    """GeoJSON geometry in local meters, rounded to 0.1 mm. An outline that cannot be
    rounded as it is (a split that left it invalid: a hole outside its shell, a ring
    crossing itself) is first taken as what it encloses: rounding it would fail the
    whole read."""
    try:
        return mapping(set_precision(geom, 1e-4))
    except shapely.errors.GEOSException:
        return mapping(set_precision(_enclosed(geom), 1e-4))


def _enclosed(geom):
    """What an outline encloses, valid: each shell less its holes. A hole outside its
    shell takes nothing away (GEOS's own repair would make it a piece of its own)."""
    parts = []
    for poly in shapely.get_parts(geom):
        if poly.geom_type != "Polygon" or poly.is_empty:
            continue
        shell = shapely.make_valid(shapely.Polygon(poly.exterior))
        holes = [shapely.make_valid(shapely.Polygon(r)) for r in poly.interiors]
        parts.append(shell.difference(shapely.union_all(holes)) if holes else shell)
    kept = [g for g in shapely.get_parts(shapely.union_all(parts)) if g.geom_type == "Polygon" and not g.is_empty]
    return shapely.MultiPolygon(kept) if len(kept) > 1 else kept[0] if kept else shapely.Polygon()


def _retire(record: ObjectRecord, now, report: ConversionReport) -> None:
    record.status = "retired"
    record.retired_at = now
    report.retired.append(record.id)


def _match_spaces(existing: list[ObjectRecord], spaces: list[ExtractedSpace]) -> dict[int, ObjectRecord]:
    """One-to-one matching, best candidates first."""
    old_shapes = [shape(r.geometry) for r in existing]
    tree = STRtree(old_shapes)  # only spaces that overlap can match: no all-against-all on big floors
    candidates = []
    for i, s in enumerate(spaces):
        for j in sorted(int(k) for k in tree.query(s.polygon, predicate="intersects")):
            r = existing[j]
            overlap = iou(s.polygon, old_shapes[j])
            same_number = bool(s.number) and s.number == r.number
            if overlap >= MATCH_MIN_IOU or (same_number and overlap >= MATCH_MIN_IOU_SAME_NUMBER):
                candidates.append((overlap + (1.0 if same_number else 0.0), i, j))
    candidates.sort(reverse=True)
    pairs: dict[int, ObjectRecord] = {}
    used: set[int] = set()
    for _, i, j in candidates:
        if i not in pairs and j not in used:
            pairs[i] = existing[j]
            used.add(j)
    return pairs


def _new_space_id(ws: Workspace, floor_id: str, building_id: str, space: ExtractedSpace) -> str:
    """Elevators and stairs that line up with one on another floor of the same
    building reuse its object code, as long as that full ID was never issued. The
    other floor's is taken as a person corrected it (a lift the drawing left untyped)."""
    if space.type in VERTICAL_TYPES:
        floor_prefix = floor_id + "-"
        for r in ws.objects.values():
            if (
                r.status == "active"
                and ws.effective(r)["type"] in VERTICAL_TYPES
                and r.id.startswith(building_id + "-")
                and not r.id.startswith(floor_prefix)
                and iou(space.polygon, shape(r.geometry)) >= VERTICAL_MIN_IOU
            ):
                candidate = child_id(floor_id, parse_id(r.id).code)
                if candidate not in ws.objects:
                    return candidate
    return _allocate_id(ws, floor_id, building_id)


def _allocate_id(ws: Workspace, floor_id: str, building_id: str) -> str:
    while True:
        candidate = child_id(floor_id, ws.allocate_object_code(building_id))
        if candidate not in ws.objects:
            return candidate


def _opening_type(source: str) -> str:
    return {"door": "door", "glazing": "door", "assumed": "door", "window": "window",
            "drawn door": "door", "drawn window": "window", "drawn opening": "opening"}.get(source, "opening")


DRAWN_NOTE = "drawn in review: where the drawing encloses no space"


CARVE_SHARE = 0.5  # a space drawn this much inside the rooms found is cut out of them
WAY_ROOM_M = 1.5  # its way through is on the edge with the most room in front of it, this deep
CARVED_NOTE = "drawn in review: cut out of the room it was drawn in"


def _drawn_spaces(ex: FloorExtraction, drawn, min_area: float) -> None:
    """The spaces a person drew. Where the drawing encloses none (a colonnade between
    columns): without what the rooms found already cover, and joined to the ways
    through around it that led nowhere until then. Drawn mostly inside the rooms found
    (a lift or stairs the drawing left in a corridor): cut out of them, with a way
    through where they meet (no wall is drawn there); a room it covers whole is left as
    it is. Added as spaces, and to the floor's outline."""
    from shapely import union_all
    from shapely.geometry import Polygon

    from .geometry import as_polygons

    added = []
    for ring in drawn:
        rooms = union_all([s.polygon for s in ex.spaces if not s.ignored]) if ex.spaces else None
        area = Polygon(ring).buffer(0)
        if rooms is not None and not rooms.is_empty and area.intersection(rooms).area > CARVE_SHARE * area.area:
            added += _carve(ex, area, min_area)
            continue
        if rooms is not None and not rooms.is_empty:
            area = area.difference(rooms)
        for part in as_polygons(area):
            if part.area >= min_area:
                ex.spaces.append(ExtractedSpace(polygon=part, layer="drawn", issues=[DRAWN_NOTE]))
                added.append(part)
                _join_loose_doors(ex, len(ex.spaces) - 1)
    if added and ex.outline is not None:
        ex.outline = union_all([ex.outline, *added])


def _carve(ex: FloorExtraction, area, min_area: float) -> list:
    """A space drawn inside rooms found: cut out of each (one it covers whole stays as
    it is, and is left out of it), the largest part of each kept; and a way through
    (an opening, "split") on the longest edge it shares with each."""
    from shapely.geometry import LineString

    from .geometry import as_polygons

    hosts = []
    for i, s in enumerate(ex.spaces):
        if s.ignored or not s.polygon.intersects(area):
            continue
        rest = s.polygon.difference(area)
        if rest.area < min_area:  # covered whole: it is the room drawn, as it was found
            area = area.difference(s.polygon)
            continue
        if s.polygon.intersection(area).area < 0.01:
            continue
        s.polygon = max(as_polygons(rest), key=lambda p: p.area)
        for z in ex.zones:
            if z.space == i:
                left = [p for p in as_polygons(z.polygon.difference(area)) if p.area >= 0.01]
                if left:
                    z.polygon = max(left, key=lambda p: p.area)
        hosts.append(i)
    out = []
    for part in as_polygons(area.buffer(0)):
        if part.area < min_area:
            continue
        ex.spaces.append(ExtractedSpace(polygon=part, layer="drawn", issues=[CARVED_NOTE]))
        new = len(ex.spaces) - 1
        out.append(part)
        for i in hosts:
            shared = part.exterior.intersection(ex.spaces[i].polygon.buffer(0.02))
            edges = [LineString([a, b]) for g in getattr(shared, "geoms", [shared]) if isinstance(g, LineString)
                     for a, b in zip(g.coords, list(g.coords)[1:])]
            edges = [e for e in edges if e.length >= 0.3]
            if not edges:
                continue
            # the edge that opens onto most of the room (not onto a strip left by a wall)
            host = ex.spaces[i].polygon
            span = max(edges, key=lambda e: (round(e.buffer(WAY_ROOM_M, cap_style="flat").difference(part)
                                                   .intersection(host).area, 2), e.length))
            ex.doors.append(ExtractedDoor(footprint=span.buffer(0.15, cap_style="flat"),
                                          point=span.interpolate(0.5, normalized=True), connects=[i, new],
                                          source="split", span=span, width=round(span.length, 3)))
    return out


def _join_loose_doors(ex: FloorExtraction, new: int, reach: float = 0.4) -> None:
    """Ways through that led out of the floor (one space on their side) and open onto a
    space drawn where there was none: joined to it."""
    space = ex.spaces[new].polygon.buffer(reach)
    for d in ex.doors:
        if d.source == "window" or len(d.connects) != 1 or new in d.connects:
            continue
        if d.footprint.intersects(space):
            d.connects.append(new)


def _drawn_openings(ex: FloorExtraction, drawn, reach: float) -> None:
    """The doors, windows and openings a person added in review: openings like any,
    joined to the spaces beside them, with their gap cut in the walls."""
    from shapely.geometry import LineString

    from .extract import _connect_doors

    thickness = max(ex.wall_thickness or 0.2, 0.1)
    for o in drawn:
        span = LineString(o.span)
        if span.length < 0.2:
            continue
        found = _connect_doors(ex.spaces, [span.buffer(thickness, cap_style="flat")], reach, [],
                               [(span, round(span.length, 3))])
        for d in found:
            d.source = f"drawn {o.type}"
            d.sill, d.height = o.sill, o.height
            if o.type == "door" and o.swing and len(o.swing) == 2:
                d.swings = [hung(d.span, o.swing)]
            ex.doors.append(d)
        if ex.walls is not None:
            ex.walls = ex.walls.difference(span.buffer(thickness, cap_style="flat"))


RESIZE_REACH_M = 0.5  # an opening given another size in review is found this near its middle
RETYPED = {"door": "door", "window": "window", "opening": "doorway"}  # an opening given another type: its source


def _resized_openings(ex: FloorExtraction, resized, reach: float = 0.4) -> None:
    """The drawing's doors, windows and openings given another size or type, or moved, in
    review: each the opening nearest its middle, its gap in the walls made to fit (what it
    no longer takes is wall again, as thick as the wall on either side), a door's leaves
    with it; one given another type is that (a window read where a door is: a door); one
    moved, along its wall to where it was put (_moved_along)."""
    if not resized:
        return
    from shapely.geometry import LineString, Point
    from shapely.ops import unary_union

    from .geometry import leaves_of_width, span_of_width

    thickness = max(ex.wall_thickness or 0.2, 0.1)
    rooms = unary_union([s.polygon for s in ex.spaces]) if ex.spaces else None
    middle = lambda d: d.span.interpolate(0.5, normalized=True) if d.span is not None else d.point  # noqa: E731
    for rz in resized:
        at = Point(rz.at)
        near = [(middle(d).distance(at), k) for k, d in enumerate(ex.doors) if not d.source.startswith("drawn")]
        near = [n for n in near if n[0] <= RESIZE_REACH_M]
        if not near:
            continue
        d = ex.doors[min(near)[1]]
        if rz.type is not None and _opening_type(d.source) != rz.type:  # read wrong: what it is
            d.source = RETYPED[rz.type]
            if rz.type != "door":
                d.swings = []  # (no leaf: a window, a way through)
        if rz.sill is not None:
            d.sill = rz.sill
        if rz.height is not None:
            d.height = rz.height
        if d.span is None or d.span.length < 0.05:
            if rz.width is not None:
                d.width = rz.width
            continue
        if rz.width is not None:
            old = [list(c) for c in d.span.coords]
            new = span_of_width(old, rz.width)
            d.swings = [tuple(map(tuple, leaf)) for leaf in leaves_of_width(d.swings, old, new)] if d.swings else []
            d.span, d.width = LineString(new), round(rz.width, 3)
            _regap(ex, LineString(old), d.span, thickness, rooms)
        if rz.to is not None:
            _moved_along(ex, d, rz.to, thickness, rooms, reach)
        if rz.swing is not None and _opening_type(d.source) == "door":
            d.swings = [hung(d.span, rz.swing)]


def hung(span, swing) -> tuple:
    """A door's leaf as it was set (``swing``: [hinge, free edge]), on its span as it is
    now: hung on the jamb nearest where it was hung, opening to the same side of its wall,
    as long as the door is wide."""
    (x0, y0), (x1, y1) = span.coords[0], span.coords[-1]
    width = math.hypot(x1 - x0, y1 - y0) or 1.0
    (hx, hy), (fx, fy) = swing
    jamb = min(((x0, y0), (x1, y1)), key=lambda j: math.hypot(j[0] - hx, j[1] - hy))
    nx, ny = -(y1 - y0) / width, (x1 - x0) / width
    side = 1.0 if (fx - hx) * nx + (fy - hy) * ny >= 0 else -1.0
    return ((round(jamb[0], 4), round(jamb[1], 4)), (round(jamb[0] + nx * side * width, 4), round(jamb[1] + ny * side * width, 4)))


def _regap(ex: FloorExtraction, old, new, thickness: float, rooms) -> None:
    """An opening's gap in the walls from ``old`` (its span) to ``new``: what it no longer
    takes is wall again, as thick as the wall on either side, and where it is now is open."""
    if ex.walls is None:
        return
    fill = _gap_band(ex.walls, old, thickness)
    if fill is not None and rooms is not None:
        fill = fill.difference(rooms)
    walls = ex.walls.union(fill) if fill is not None else ex.walls
    ex.walls = walls.difference(new.buffer(thickness, cap_style="flat"))


def _moved_along(ex: FloorExtraction, d, to, thickness: float, rooms, reach: float) -> None:
    """An opening moved along its wall so that its middle is as near ``to`` as its wall
    lets: its span, point and leaves with it, its gap with it, joined to the spaces beside
    it where it is now."""
    from shapely.affinity import translate

    from .extract import _connect_doors

    (x0, y0), (x1, y1) = d.span.coords[0], d.span.coords[-1]
    ux, uy = (x1 - x0) / d.span.length, (y1 - y0) / d.span.length
    m = d.span.interpolate(0.5, normalized=True)
    t = (to[0] - m.x) * ux + (to[1] - m.y) * uy
    if abs(t) < 0.005:
        return
    dx, dy = ux * t, uy * t
    old = d.span
    d.span, d.point, d.footprint = translate(old, dx, dy), translate(d.point, dx, dy), translate(d.footprint, dx, dy)
    d.swings = [((h[0] + dx, h[1] + dy), (f[0] + dx, f[1] + dy)) for h, f in d.swings]
    _regap(ex, old, d.span, thickness, rooms)
    found = _connect_doors(ex.spaces, [d.span.buffer(thickness, cap_style="flat")], reach, [],
                           [(d.span, round(d.span.length, 3))])
    if found:
        d.connects = found[0].connects


def _gap_band(walls, span, thickness: float):
    """The wall a gap interrupts, as if it did not: the hull of the wall's ends on either
    side of the span (None when there are none)."""
    from shapely.geometry import LineString

    (x0, y0), (x1, y1) = span.coords[0], span.coords[-1]
    ux, uy = (x1 - x0) / span.length, (y1 - y0) / span.length
    e = max(thickness, 0.3)
    longer = LineString([(x0 - ux * e, y0 - uy * e), (x1 + ux * e, y1 + uy * e)])
    ends = walls.intersection(longer.buffer(1.5 * thickness + 0.05, cap_style="flat"))
    return None if ends.is_empty else ends.convex_hull


def _apply_doors(ws, floor_id, building_id, ex: FloorExtraction, space_ids, now, report) -> None:
    """Openings keep their IDs by position: windows are matched only with windows,
    ways through (doors, doorways) only with ways through, unless it stands where it
    stood (the same opening, read as another kind: what was done to it stays)."""
    existing = [r for r in ws.floor_objects(floor_id) if r.kind == "opening"]
    old_points = [shape(r.geometry) for r in existing]
    tree = STRtree(old_points)
    used: set[int] = set()
    for door in ex.doors:
        kind = _opening_type(door.source)
        best, best_d = None, DOOR_MATCH_DISTANCE
        near = tree.query(door.point, predicate="dwithin", distance=DOOR_MATCH_DISTANCE)
        for j in sorted(int(k) for k in near):
            p = old_points[j]
            d = door.point.distance(p)
            alike = (existing[j].type == "window") == (kind == "window") or d <= SAME_OPENING_M
            if j not in used and alike and d <= best_d:
                best, best_d = j, d
        if best is None:
            record_id = _allocate_id(ws, floor_id, building_id)
            record = ObjectRecord(id=record_id, kind="opening", type=kind, geometry={})
            ws.objects[record_id] = record
            report.added.append(record_id)
        else:
            used.add(best)
            record = existing[best]
            report.kept.append(record.id)
        record.type, record.type_source = kind, door.source
        record.tag, record.issues = door.tag, list(door.issues)
        record.geometry = mapping(Point(round(door.point.x, 4), round(door.point.y, 4)))
        record.connects = [space_ids[i] for i in door.connects]
        record.span = [[round(x, 4), round(y, 4)] for x, y in door.span.coords] if door.span is not None else None
        record.width = door.width
        record.swings = [[[round(x, 4), round(y, 4)] for x, y in leaf] for leaf in door.swings] or None
        record.sill, record.height = door.sill, door.height
    for j, r in enumerate(existing):
        if j not in used:
            _retire(r, now, report)
