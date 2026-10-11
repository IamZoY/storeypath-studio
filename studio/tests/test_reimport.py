"""IDs must survive re-importing a revised drawing."""

from storeypath.convert import convert_floor
from storeypath.ids import parse_id
from storeypath.samples import Cell, Door, office_floor, write_floor_dxf
from storeypath.workspace import Override, SourceDrawing


def _by_number(ws, floor_id):
    return {r.number: r.id for r in ws.floor_objects(floor_id) if r.kind == "space" and r.number}


def _renovate(cells: list[Cell]) -> list[Cell]:
    """Merge 208+209, move the wall between 201 and 202, renumber 203,
    split the open office to add meeting room 218."""
    out = []
    for c in cells:
        n = c.number
        if n == "209":
            continue
        if n == "208":
            c.x1 = 48
            c.door = Door(44, 8.5, "h", -1)
        elif n == "201":
            c.x1 = 12.5
        elif n == "202":
            c.x0 = 12.5
        elif n == "203":
            c.label = ["OFFICE", "203A"]
        elif n == "217":
            c.x1 = 44
            c.door = Door(40, 11.5, "h", +1)
        out.append(c)
    out.append(Cell(44, 11.5, 48, 20, ["MEETING ROOM", "218"], "meeting_room", Door(46, 11.5, "h", +1)))
    return out


def test_reconverting_the_same_drawing_keeps_every_id(converted):
    ws, d, f_id, _, _, first = converted
    before = {i for i, r in ws.objects.items() if r.status == "active"}
    again = convert_floor(ws, f_id, d)
    assert again.added == [] and again.retired == []
    assert set(again.kept) == before


def test_revised_drawing_keeps_matching_ids_and_retires_removed(converted):
    ws, d, f_id, b_id, cells, _ = converted
    old = _by_number(ws, f_id)
    old_max_seq = ws.building(b_id).next_object_seq

    write_floor_dxf(d / "level-2-rev.dxf", _renovate(office_floor(2)))
    ws.floor(f_id).source = SourceDrawing(path="level-2-rev.dxf")
    report = convert_floor(ws, f_id, d)
    new = _by_number(ws, f_id)

    for n in ("201", "202", "204", "208", "210", "214", "217"):
        assert new[n] == old[n], n
    assert new["203A"] == old["203"]  # renumbered, same room
    assert old["209"] in report.retired
    assert ws.objects[old["209"]].status == "retired"
    assert "218" in new and new["218"] in report.added
    assert int(parse_id(new["218"]).code) >= old_max_seq  # fresh code


def test_retired_ids_are_never_issued_again(converted):
    ws, d, f_id, _, _, _ = converted
    retired_id = _by_number(ws, f_id)["209"]

    write_floor_dxf(d / "rev.dxf", _renovate(office_floor(2)))
    ws.floor(f_id).source = SourceDrawing(path="rev.dxf")
    convert_floor(ws, f_id, d)
    # put the original plan back: room 209 reappears in the same place
    ws.floor(f_id).source = SourceDrawing(path="level-2.dxf")
    convert_floor(ws, f_id, d)

    reborn = _by_number(ws, f_id)["209"]
    assert reborn != retired_id
    assert ws.objects[retired_id].status == "retired"


def test_corrections_survive_reconversion(converted):
    ws, d, f_id, _, _, _ = converted
    target = _by_number(ws, f_id)["214"]
    assert ws.effective(ws.objects[target])["type"] == "unspecified"
    ws.overrides[target] = Override(type="office", name="QUIET ROOM")
    convert_floor(ws, f_id, d)
    eff = ws.effective(ws.objects[target])
    assert eff["type"] == "office" and eff["name"] == "QUIET ROOM"
    assert ws.objects[target].type == "unspecified"  # detection itself unchanged


def test_review_list_and_accepting(converted):
    ws, d, f_id, _, _, _ = converted
    reasons = {r.id: ws.review_reasons(r) for r in ws.floor_objects(f_id)}
    flagged = {i: v for i, v in reasons.items() if v}
    assert sorted(map(tuple, flagged.values())) == [("no name or number",), ("no type",)]
    unnamed = next(i for i, v in flagged.items() if v == ["no name or number"])
    ws.overrides[unnamed] = Override()  # accepted as it is
    assert ws.review_reasons(ws.objects[unnamed]) == []
    untyped = next(i for i, v in flagged.items() if v == ["no type"])
    ws.overrides[untyped] = Override(name="QUIET ROOM")  # still needs a type
    assert ws.review_reasons(ws.objects[untyped]) == ["no type"]


def test_empty_correction_removes_a_detected_name(converted):
    ws, d, f_id, _, _, _ = converted
    target = _by_number(ws, f_id)["201"]
    ws.overrides[target] = Override(name="")
    eff = ws.effective(ws.objects[target])
    assert eff["name"] is None and eff["number"] == "201"


def test_doors_keep_ids_and_follow_their_spaces(converted):
    ws, d, f_id, _, _, _ = converted
    doors = {r.id: r.connects for r in ws.floor_objects(f_id) if r.kind == "opening"}
    report = convert_floor(ws, f_id, d)
    assert {r.id: r.connects for r in ws.floor_objects(f_id) if r.kind == "opening"} == doors
    assert not report.retired


def test_elevators_and_stairs_share_a_code_across_floors(workspace):
    ws, d, f2, b_id, _ = workspace
    write_floor_dxf(d / "level-3.dxf", office_floor(3))
    f3 = ws.add_floor(b_id, 3, source=SourceDrawing(path="level-3.dxf"))
    convert_floor(ws, f2, d)
    convert_floor(ws, f3, d)

    def vertical_codes(fid):
        return sorted(parse_id(r.id).code for r in ws.floor_objects(fid) if r.type in ("elevator", "stairs"))

    assert len(vertical_codes(f2)) == 3
    assert vertical_codes(f2) == vertical_codes(f3)
    offices2 = {parse_id(r.id).code for r in ws.floor_objects(f2) if r.type == "office"}
    offices3 = {parse_id(r.id).code for r in ws.floor_objects(f3) if r.type == "office"}
    assert not offices2 & offices3  # everything else gets its own code


def test_a_read_that_would_retire_a_room_people_corrected_is_held_back_and_says_which(converted):
    # The drawing revised: 208 and 209 made one room, 208 (it keeps 208's ID). A person
    # had named 209: their correction cannot go on to the room that is 208's, so the
    # read is held back and says which room it would retire; read anyway, it is retired
    # with its correction, which stays with its retired ID.
    ws, d, f_id, _, _, _ = converted
    old = _by_number(ws, f_id)
    ws.overrides[old["209"]] = Override(name="QUIET ROOM")
    ws.overrides[old["201"]] = Override()  # (accepted as it is: nothing a read loses)
    write_floor_dxf(d / "level-2-rev.dxf", _renovate(office_floor(2)))
    ws.floor(f_id).source = SourceDrawing(path="level-2-rev.dxf")
    held = convert_floor(ws, f_id, d)
    assert held.held and f"QUIET ROOM 209 ({old['209']})" in held.hold and "not changed: the drawing now reads" in held.summary()
    assert ws.objects[old["209"]].status == "active" and "218" not in _by_number(ws, f_id)  # nothing changed

    anyway = convert_floor(ws, f_id, d, force=True)
    assert anyway.lost == [old["209"]] and ws.objects[old["209"]].status == "retired"
    assert ws.overrides[old["209"]].name == "QUIET ROOM"
    assert "1 rooms people corrected retired" in anyway.summary()


def test_a_correction_goes_on_with_its_room_reshaped_past_matching(converted):
    # The drawing revised: 213, 214 and 215 made one room, its tag left off. 214 is
    # wholly in it, and is a third of it: too unlike to keep its ID, but the person's
    # correction of it goes on to the room it became (the others had none).
    ws, d, f_id, _, _, _ = converted
    old = _by_number(ws, f_id)
    ws.overrides[old["214"]] = Override(type="office", name="QUIET ROOM")
    cells = [c for c in office_floor(2) if c.label is None or c.label[-1] not in ("213", "215")]
    for c in cells:
        if c.label == ["214"]:
            c.x0, c.x1, c.label = 20, 32, None
    write_floor_dxf(d / "level-2-rev.dxf", cells)
    ws.floor(f_id).source = SourceDrawing(path="level-2-rev.dxf")
    report = convert_floor(ws, f_id, d)
    assert not report.held and len(report.carried) == 1
    was, now = report.carried[0]
    assert was == old["214"] and now in report.added and ws.objects[was].status == "retired"
    e = ws.effective(ws.objects[now])
    assert (e["type"], e["name"]) == ("office", "QUIET ROOM")
    assert "the corrections of 1 reshaped rooms went on with them" in report.summary()


def test_a_read_after_a_person_drew_on_the_floor_is_not_held_for_the_rooms_it_changed(converted):
    # A wall taken away in review (read again, as drawing does): the rooms it joined are
    # the person's doing, their corrections retired with the room that went
    ws, d, f_id, _, _, _ = converted
    old = _by_number(ws, f_id)
    ws.overrides[old["209"]] = Override(name="QUIET ROOM")
    write_floor_dxf(d / "level-2-rev.dxf", _renovate(office_floor(2)))
    ws.floor(f_id).source = SourceDrawing(path="level-2-rev.dxf")
    report = convert_floor(ws, f_id, d, drawn=True)
    assert not report.held and report.lost == [old["209"]]
