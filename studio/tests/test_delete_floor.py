"""A floor deleted (POST …/floors/<id>/delete {confirm}): taken out of its building once its
name is typed, by whoever may edit the building, while nobody else is editing a room of
it. Its rooms, doors and items are retired, never erased (their IDs are never issued
again: a floor added afterwards gets new ones), its drawing stays, and the building's
next package lists what it held as retired. One change, recorded (a barrier: no undo
goes back over it), and in the audit log."""

import json
import zipfile

import pytest
from shapely.geometry import shape

from people import Team
from storeypath import accounts as acc
from storeypath.export import export_package
from storeypath.validate import validate_package


@pytest.fixture
def team(tmp_path, monkeypatch):
    monkeypatch.setattr(acc, "SCRYPT_N", 2**10)
    monkeypatch.setattr("storeypath.review.BUSY_WAIT_S", 0.2)
    monkeypatch.delenv("STOREYPATH_ALLOWED_HOSTS", raising=False)
    t = Team(tmp_path / "data")
    yield t
    t.close()


def floor_name(team, floor):
    return team.studio.workspace(team.code).floor(floor).name


def inside(team, floor, n=0):
    """A space of a floor and a point in it."""
    s = team.spaces(floor)[n]
    p = shape(s.geometry).representative_point()
    return s, (p.x, p.y)


def delete(team, who, floor, name=None):
    return team(who, "POST", f"floors/{floor}/delete", {"confirm": floor_name(team, floor) if name is None else name})


def test_a_floor_deleted_retires_what_it_held_and_is_recorded(team):
    ws = team.studio.workspace(team.code)
    objects = {r.id for r in ws.floor_objects(team.hq1)}
    rooms = len(team.spaces(team.hq1))
    drawing = ws.floor(team.hq1).source.path.rsplit("/", 1)[-1]
    space, at = inside(team, team.hq1)
    status, item = team("khalid", "POST", f"floors/{team.hq1}/items", {"type": "SOFA", "x": at[0], "y": at[1]})
    assert status == 200
    name = floor_name(team, team.hq1)
    assert delete(team, "khalid", team.hq1, "not its name") == (400, {"error": "type the floor's name to delete it"})
    assert team.studio.workspace(team.code).floor(team.hq1).name == name  # nothing changed

    status, done = delete(team, "khalid", team.hq1)
    assert status == 200
    assert done == {"deleted": team.hq1, "name": name, "rooms": rooms, "items": 1} and rooms > 0
    ws = team.studio.workspace(team.code)
    assert team.hq1 not in {fid for *_, fid in ws.iter_floors()}
    assert team("khalid", "GET", f"floors/{team.hq1}")[0] == 404
    # retired, not erased: every object it had, and its item, with their IDs
    assert objects and objects <= set(ws.objects) and all(ws.objects[i].status == "retired" for i in objects)
    assert all(ws.objects[i].retired_at is not None for i in objects)
    it = ws.items[item["id"]]
    assert it.status == "retired" and it.floor_id == team.hq1 and it.retired_at is not None
    # its drawing stays among the project's drawings
    assert drawing in {d["name"] for d in team.studio.store.drawings(team.code)}
    # recorded: one change, a barrier on that floor, in words; and audited
    row = next(r for r in team.studio.store.history(team.code) if r["kind"] == "floor deleted")
    assert row["part"] == "building" and row["floors"] == [team.hq1] and row["targets"] == [team.hq1]
    assert row["who"]["name"] == "Khalid Engineer" and row["barrier"] is True
    lines = [e["line"] for e in team("khalid", "GET", "history")[1]["entries"]]
    assert f"deleted the floor {name}" in lines
    audit = team.accounts.audit_tail(5)
    assert audit[0]["action"] == "floor deleted" and audit[0]["target"] == team.hq1


def test_who_may_delete_a_floor(team):
    assert delete(team, "vera", team.hq0)[0] == 403  # she may only look at it
    assert delete(team, "vera", team.hq1)[0] == 404  # she does not see it
    assert delete(team, "bob", team.hq1)[0] in (403, 404)  # the Annex is his, not HQ
    assert delete(team, "sara", team.hq1)[0] == 200  # edit on the whole project


def test_a_floor_someone_else_is_editing_a_room_of_is_not_deleted(team):
    space, _ = inside(team, team.hq1)
    assert team("sara", "POST", f"objects/{space.id}", {"correction": {"name": "Sara's"}})[0] == 200
    status, refused = delete(team, "khalid", team.hq1)
    assert status == 423 and refused["locked"]["who"]["name"] == "Sara Ahmed" and refused["locked"]["room"] == space.id
    assert team.hq1 in {fid for *_, fid in team.studio.workspace(team.code).iter_floors()}
    assert team("sara", "POST", f"floors/{team.hq1}/release", {})[0] == 200
    # rooms of the deleter's own are no matter
    assert team("khalid", "POST", f"objects/{space.id}", {"correction": {"name": "His"}})[0] == 200
    assert delete(team, "khalid", team.hq1)[0] == 200
    with team.studio.db.connection() as conn:  # his locks of it went with it
        assert conn.execute("SELECT count(*) FROM room_locks WHERE floor = %s", (team.hq1,)).fetchone()[0] == 0


def test_a_floor_added_afterwards_gets_new_ids(team):
    ws = team.studio.workspace(team.code)
    before = {r.id for r in ws.floor_objects(team.hq1)}
    counter = ws.building(team.hq).next_object_seq
    drawing = ws.floor(team.hq1).source.path.rsplit("/", 1)[-1]
    assert delete(team, "khalid", team.hq1)[0] == 200
    assert team.studio.workspace(team.code).building(team.hq).next_object_seq == counter  # never goes back
    # the same drawing added again as floor 1: a floor of the same ID, rooms of new IDs
    status, job = team("khalid", "POST", f"drawings/{drawing}/plans", {})
    assert status == 200, job
    plan = max(team.finished("khalid", job)["result"]["plans"], key=lambda x: x["size"][0] * x["size"][1])
    status, job = team("khalid", "POST", "floors", {"drawing": drawing, "plans": [
        {"index": plan["index"], "title": plan["title"], "region": plan["region"], "building_id": team.hq, "ordinal": 1}]})
    assert status == 200, job
    team.finished("khalid", job)
    ws = team.studio.workspace(team.code)
    again = {r.id for r in ws.floor_objects(team.hq1)}
    assert again and not again & before  # none of the retired IDs issued again
    assert all(int(i.rsplit("-", 1)[1]) >= counter for i in again)
    assert all(ws.objects[i].status == "retired" for i in before)


def test_the_next_package_lists_what_the_floor_held_as_retired(team, tmp_path):
    space, at = inside(team, team.hq1)
    status, item = team("khalid", "POST", f"floors/{team.hq1}/items", {"type": "SOFA", "x": at[0], "y": at[1]})
    assert status == 200
    status, job = team("khalid", "POST", "export", {"building": team.hq})  # the package wayfinder has
    assert status == 200, job
    team.finished("khalid", job)
    held = {r.id for r in team.studio.workspace(team.code).floor_objects(team.hq1)}
    assert delete(team, "khalid", team.hq1)[0] == 200
    out = tmp_path / "hq.storeypath"
    export_package(team.studio.workspace(team.code).model_copy(deep=True), out, building=team.hq, bake=False,
                   record=False)
    assert validate_package(out) == []
    with zipfile.ZipFile(out) as z:
        changes = json.loads(z.read("changes.json"))
        floors = {f["id"] for f in json.loads(z.read("floors.geojson"))["features"]}
        spaces = {f["id"] for f in json.loads(z.read("spaces.geojson"))["features"]}
    assert team.hq1 not in floors and not held & spaces
    retired = set(changes["retired"])
    assert team.hq1 in retired and item["id"] in retired and space.id in held and held <= retired
    assert held | {item["id"]} <= set(changes["all_retired"])


def test_no_undo_goes_back_over_a_floor_deleted_and_the_other_floors_keep_theirs(team):
    f2 = f"{team.hq}-F02"
    (on1, _), (on2, _) = inside(team, team.hq1), inside(team, f2)
    assert team("khalid", "POST", f"objects/{on1.id}", {"correction": {"name": "On floor 1"}})[0] == 200
    assert team("khalid", "POST", f"objects/{on2.id}", {"correction": {"name": "On floor 2"}})[0] == 200
    assert delete(team, "khalid", team.hq1)[0] == 200
    status, done = team("khalid", "POST", "undo", {"floor": f2})  # floor 2's own: still his to undo
    assert status == 200, done
    assert team.studio.workspace(team.code).overrides.get(on2.id) is None
    status, refused = team("khalid", "POST", "undo", {})  # floor 1's is behind the floor deleted
    assert status == 409, refused
    assert team.studio.workspace(team.code).overrides[on1.id].name == "On floor 1"


def test_a_lift_linked_by_hand_to_one_of_a_floor_deleted_stands_alone(team):
    above, below = f"{team.hq}-F02-0023", f"{team.hq1}-0024"
    assert team("khalid", "POST", f"objects/{above}", {"stack": below})[0] == 200
    assert delete(team, "khalid", team.hq1)[0] == 200
    status, info = team("khalid", "GET", f"objects/{above}/stack")
    assert status == 200 and info["how"] == "alone"
    assert team.hq1 not in {f["id"] for f in info["floors"]}
    status, job = team("khalid", "POST", "export", {"building": team.hq})  # its package is whole
    assert status == 200 and team.finished("khalid", job)["state"] == "done"
