"""Many people on one floor, one a room (db/store.py, Review's calls): a person's change
takes the lock of each room it touches (a space, its doors, the items in it, what is
drawn on it); another person's change to one of those is refused (423, naming who holds
which room, since when) until they are done, leave the floor, make no change to it for
ROOM_IDLE_S (a page left open keeps nothing), or an admin takes it over (recorded).
Their changes to other rooms of the same floor go through. Reading a whole floor again
waits until nobody else holds a room on it. Project-wide steps hold the project's step
lock, in the database: another Studio on it is told it is busy."""

import pytest

from people import Team
from storeypath import accounts as acc
from storeypath.db import Database
from storeypath.db import store as store_module
from storeypath.db.store import StepLock, room_at
from storeypath.web import events as events_module


@pytest.fixture
def team(tmp_path, monkeypatch):
    monkeypatch.setattr(acc, "SCRYPT_N", 2**10)
    monkeypatch.setattr("storeypath.review.BUSY_WAIT_S", 0.2)
    monkeypatch.delenv("STOREYPATH_ALLOWED_HOSTS", raising=False)
    t = Team(tmp_path / "data")
    yield t
    t.close()


def name(team, space_id, who, text):
    return team(who, "POST", f"objects/{space_id}", {"correction": {"name": text}})


def room(team, n, floor=None):
    """A room of HQ's ground floor (or ``floor``) by its own number: 8 is ``…-0008``."""
    return f"{floor or team.hq0}-{n:04d}"


# in the demo's HQ ground floor: offices 8 and 9 side by side on the corridor (1), door 34
# between office 8 and the corridor, office 13 across it; nothing of it at (100, 100)
IN_8, IN_9, IN_13, NOWHERE = (143.0, 63.8), (147.0, 63.8), (135.0, 52.2), (100.0, 100.0)


def place(team, who, at, floor=None):
    return team(who, "POST", f"floors/{floor or team.hq0}/items", {"type": "SOFA", "x": at[0], "y": at[1]})


def test_people_edit_one_floor_at_once_one_a_room(team):
    assert team("khalid", "GET", f"floors/{team.hq0}")[1]["editing"]["editors"] == []
    assert name(team, room(team, 8), "khalid", "Board room")[0] == 200
    assert name(team, room(team, 9), "sara", "Sara's office")[0] == 200  # the same floor, another room
    editing = team("vera", "GET", f"floors/{team.hq0}")[1]["editing"]
    assert [(e["who"]["name"], e["rooms"]) for e in editing["editors"]] == [
        ("Khalid Engineer", [room(team, 8)]), ("Sara Ahmed", [room(team, 9)])]
    assert editing["rooms"][room(team, 9)]["who"]["username"] == "sara" and "session" not in str(editing)
    # khalid's room: refused, naming him and it; nothing changed
    status, refused = name(team, room(team, 8), "sara", "Mine")
    assert status == 423
    assert refused["locked"]["room_name"].startswith("Board room")  # (and its number)
    assert refused["error"].startswith(f"Khalid Engineer is editing {refused['locked']['room_name']} (since ")
    assert "you can look; you can change it when they are done" in refused["error"]
    assert refused["locked"]["who"]["username"] == "khalid" and refused["locked"]["room"] == room(team, 8)
    assert refused["locked"]["floor"] == team.hq0
    assert team.studio.workspace(team.code).overrides[room(team, 8)].name == "Board room"
    # what touches his room is his too: its door, an item in it, a wall drawn across it
    for refused_call in (("POST", f"objects/{room(team, 34)}", {"ignored": True}),
                         ("POST", f"floors/{team.hq0}/items", {"type": "SOFA", "x": IN_8[0], "y": IN_8[1]}),
                         ("POST", f"floors/{team.hq0}/edits", {"add": {"wall": [[142, 61], [142, 66]]}})):
        status, refused = team("sara", *refused_call)
        assert status == 423 and refused["locked"]["room"] == room(team, 8), (refused_call, refused)
    # elsewhere on the floor she goes on: an item in her office, one in no room
    assert place(team, "sara", IN_9)[0] == 200
    status, loose = place(team, "sara", NOWHERE)
    assert status == 200
    assert team.studio.store.locks(team.code)[team.hq0]["rooms"][loose["id"]]["who"]["username"] == "sara"
    # and khalid in his, but not in hers
    assert name(team, room(team, 8), "khalid", "Still his")[0] == 200
    assert name(team, room(team, 9), "khalid", "No")[0] == 423


def test_reading_a_floor_again_waits_for_everyone_elses_rooms(team):
    assert name(team, room(team, 8), "khalid", "Board room")[0] == 200
    status, refused = team("sara", "POST", f"floors/{team.hq0}/convert", {})
    assert status == 423 and refused["locked"]["who"]["username"] == "khalid"
    assert f"{refused['locked']['room_name']} on this floor" in refused["error"] and "Board room" in refused["error"]
    assert "when nobody else is editing it" in refused["error"]
    status, job = team("khalid", "POST", f"floors/{team.hq0}/convert", {})  # only his rooms there: he may
    assert status == 200
    team.finished("khalid", job)
    assert team("khalid", "POST", f"floors/{team.hq0}/release", {}) == (200, {"released": True})
    status, job = team("sara", "POST", f"floors/{team.hq0}/convert", {})
    assert status == 200
    team.finished("sara", job)


def test_an_item_carried_needs_the_rooms_it_leaves_and_comes_to(team):
    status, item = place(team, "khalid", IN_13)
    assert status == 200
    assert name(team, room(team, 9), "sara", "Sara's")[0] == 200
    status, refused = team("khalid", "POST", f"items/{item['id']}", {"x": IN_9[0], "y": IN_9[1]})
    assert status == 423 and refused["locked"]["who"]["name"] == "Sara Ahmed"
    # to the floor above: the room it would stand in there is hers
    ws = team.studio.workspace(team.code)
    above = room_at(ws, team.hq1, IN_13)
    assert above is not None
    assert name(team, above, "sara", "Hers above")[0] == 200
    status, refused = team("khalid", "POST", f"items/{item['id']}", {"floor_id": team.hq1})
    assert status == 423 and refused["locked"]["room"] == above
    assert team.studio.workspace(team.code).items[item["id"]].floor_id == team.hq0
    # sara may not take it from his room either
    assert team("sara", "POST", f"items/{item['id']}", {"x": IN_9[0], "y": IN_9[1]})[0] == 423


def test_done_editing_lets_ones_rooms_go(team):
    assert name(team, room(team, 8), "khalid", "His")[0] == 200
    assert name(team, room(team, 13), "khalid", "His too")[0] == 200
    assert team("sara", "POST", f"floors/{team.hq0}/release", {}) == (200, {"released": False})  # none hers
    assert name(team, room(team, 8), "sara", "Hers")[0] == 423
    assert team("khalid", "POST", f"floors/{team.hq0}/release", {}) == (200, {"released": True})
    assert team("khalid", "GET", f"floors/{team.hq0}")[1]["editing"]["editors"] == []
    assert name(team, room(team, 8), "sara", "Hers")[0] == 200
    editors = team("khalid", "GET", f"floors/{team.hq0}")[1]["editing"]["editors"]
    assert [(e["who"]["name"], e["rooms"]) for e in editors] == [("Sara Ahmed", [room(team, 8)])]


def test_a_room_left_alone_is_free_again_though_its_page_is_open(team, monkeypatch):
    monkeypatch.setattr(events_module, "HEARTBEAT_S", 0.2)

    def last_change(seconds_ago):  # khalid's last change to office 8, as though that long ago
        with team.studio.db.transaction() as conn:
            conn.execute("UPDATE room_locks SET last_seen = now() - make_interval(secs => %s)", (seconds_ago,))

    def age():
        with team.studio.db.connection() as conn:
            return conn.execute("SELECT extract(epoch FROM now() - last_seen) FROM room_locks").fetchone()[0]

    page = team.stream("khalid", team.hq0)  # khalid's page, open on the floor
    try:
        assert page.next()[0] == "heartbeat"
        assert name(team, room(team, 8), "khalid", "His")[0] == 200
        last_change(store_module.ROOM_IDLE_S - 60)
        assert name(team, room(team, 8), "sara", "Hers")[0] == 423  # a minute left: still his
        assert name(team, room(team, 8), "khalid", "His again")[0] == 200
        assert age() < 30  # a change of his keeps it
        last_change(store_module.ROOM_IDLE_S + 1)
        page.until(lambda k, d: k == "heartbeat")  # his page's heartbeats go on…
        page.until(lambda k, d: k == "heartbeat")
        assert age() > store_module.ROOM_IDLE_S  # …and keep nothing
        assert team("sara", "GET", f"floors/{team.hq0}")[1]["editing"]["editors"] == []
        assert name(team, room(team, 8), "sara", "Hers")[0] == 200
    finally:
        page.close()
    last_change(3600)
    assert team.studio.store.sweep_locks() == {team.code: [team.hq0]}
    with team.studio.db.connection() as conn:
        assert conn.execute("SELECT count(*) FROM room_locks").fetchone()[0] == 0


def test_an_admin_takes_a_room_over_and_that_is_recorded(team):
    assert name(team, room(team, 8), "khalid", "Board room")[0] == 200
    assert name(team, room(team, 13), "khalid", "His office")[0] == 200
    assert team("sara", "POST", f"floors/{team.hq0}/take-over", {"room": room(team, 8)})[0] == 403  # an admin's
    status, done = team("boss", "POST", f"floors/{team.hq0}/take-over", {"room": room(team, 8)})
    assert status == 200 and [p["name"] for p in done["from"]] == ["Khalid Engineer"]
    rooms = team.studio.store.locks(team.code)[team.hq0]["rooms"]
    assert rooms[room(team, 8)]["who"]["name"] == "The Boss" and rooms[room(team, 13)]["who"]["name"] == "Khalid Engineer"
    status, refused = name(team, room(team, 8), "khalid", "Mine again")
    assert status == 423 and refused["locked"]["who"]["name"] == "The Boss"
    assert name(team, room(team, 13), "khalid", "Still mine")[0] == 200
    assert name(team, room(team, 8), "boss", "The boss's")[0] == 200
    row = next(r for r in team.studio.store.history(team.code) if r["kind"] == "take over")
    assert row["part"] == "floor" and row["floors"] == [team.hq0] and row["who"]["name"] == "The Boss"
    assert row["before"]["lock"]["name"] == "Khalid Engineer" and row["before"]["rooms"] == [room(team, 8)]
    lines = team("khalid", "GET", f"history?floor={team.hq0}")[1]["entries"]
    assert any(e["line"].startswith("took over editing Board room") and e["line"].endswith("from Khalid Engineer")
               for e in lines)
    audit = team.accounts.audit_tail(5)
    assert audit[0]["action"] == "room taken over" and audit[0]["target"] == room(team, 8) and audit[0]["was"] == "khalid"
    # all of the floor that others edit: his last room too
    status, done = team("boss", "POST", f"floors/{team.hq0}/take-over", {})
    assert status == 200 and [p["username"] for p in done["from"]] == ["khalid"]
    assert name(team, room(team, 13), "khalid", "Gone")[0] == 423
    assert team("boss", "POST", f"floors/{team.hq0}/take-over", {}) == (200, {"from": []})  # nobody else's now


def test_project_wide_steps_hold_the_project_in_the_database(team):
    """Another Studio on the same database working on the project (its step lock held):
    Review's changes here are refused as busy, as when a job here works on it; a file is
    not opened into it."""
    s0 = team.spaces(team.hq0)[0].id
    other = Database(team.studio.db.url, migrate=False)  # another Studio's connections
    held = StepLock(other, team.code)
    try:
        assert held.acquire(timeout=1)
        status, busy = name(team, s0, "khalid", "During the step")
        assert status == 409 and busy["busy"] is True and "a job is working on this project" in busy["error"]
        assert not team.studio._changing(team.code).acquire(blocking=False)  # here, the step waits too
        status, _ = team("khalid", "POST", "delete", {"confirm": "Demo Campus"})
        assert status == 400 and team.studio.store.exists(team.code)
    finally:
        held.release()
        other.close()
    assert name(team, s0, "khalid", "After the step")[0] == 200
    lock = team.studio._changing(team.code)
    with lock:  # re-entrant here, one database lock however deep
        with lock:
            pass
        assert not StepLock(team.studio.db, team.code).acquire(blocking=False)
    again = StepLock(team.studio.db, team.code)
    assert again.acquire(blocking=False)  # let go once the outermost let it go
    again.release()
