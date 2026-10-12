"""What changed on a floor since the version a page has it as of (Review.floor
``since``): only the spaces, openings and items changed, the IDs of those gone, what the
items say of the spaces when one of them changed, and the floor's own fields when it
changed itself; the whole floor where Studio cannot say."""

import threading

import pytest
from shapely.geometry import shape

from storeypath.samples import build_demo
from storeypath.server import Studio
from storeypath.workspace import Workspace
from test_review_races import NoModel, call, finished
from sessions import admin_server


@pytest.fixture
def studio(tmp_path):
    ws_path, _ = build_demo(tmp_path / "data" / "demo")
    s = Studio(tmp_path / "data", model=NoModel(), warm=False)
    srv = admin_server(s, port=0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    ws = Workspace.load(ws_path)
    f0 = f"{ws.id}-DEMO-HQ-F00"
    yield f"http://127.0.0.1:{srv.server_port}/api/projects/{ws.id}", s, ws, f0
    srv.shutdown()
    srv.server_close()


def _floor(base, f0, since=None):
    status, floor = call(f"{base}/floors/{f0}" + (f"?since={since}" if since is not None else ""))
    assert status == 200, floor
    return floor


def test_a_floor_since_a_version_is_what_changed_after_it(studio):
    base, s, ws, f0 = studio
    whole = _floor(base, f0)
    v = whole["version"]
    assert "since" not in whole and len(whole["spaces"]) > 5 and isinstance(v, int)
    assert _floor(base, f0, v) | {"editing": None} == {"id": f0, "version": v, "since": v, "spaces": [], "doors": [],
                                                       "items": [], "gone": [], "editing": None}

    # a room named: that room alone
    room = max((sp for sp in whole["spaces"] if sp["kind"] == "space"), key=lambda sp: sp["area"])
    assert call(f"{base}/objects/{room['id']}", {"correction": {"name": "BOARD ROOM"}})[0] == 200
    named = _floor(base, f0, v)
    assert named["since"] == v and named["version"] > v
    assert [sp["id"] for sp in named["spaces"]] == [room["id"]] and named["spaces"][0]["name"] == "BOARD ROOM"
    assert "walls" not in named and "seats" not in named and named["doors"] == []

    # a desk placed in it: the desk, and what each room seats now
    at = shape(room["geometry"]).representative_point()
    status, desk = call(f"{base}/floors/{f0}/items", {"type": "DESK-JUNIOR", "x": at.x, "y": at.y})
    assert status == 200
    placed = _floor(base, f0, named["version"])
    assert [i["id"] for i in placed["items"]] == [desk["id"]]
    assert placed["seats"][room["id"]]["workplaces"] == 1

    # a wall drawn and the floor read again: the floor's own fields and all its openings come too
    x0, y0, x1, y1 = shape(room["geometry"]).bounds
    status, job = call(f"{base}/floors/{f0}/edits", {"add": {"wall": [[x0 + 1, y0 - 0.1], [x0 + 1, y1 + 0.1]]}})
    assert status == 200 and finished(base.rsplit("/api/", 1)[0], job)["state"] == "done"
    drawn = _floor(base, f0, placed["version"])
    assert drawn["edits"]["walls"] and "walls" in drawn and len(drawn["doors"]) == len(whole["doors"])
    after = _floor(base, f0)
    assert sorted(sp["id"] for sp in after["spaces"]) == \
        sorted({sp["id"] for sp in whole["spaces"]} - set(drawn["gone"]) | {sp["id"] for sp in drawn["spaces"]})

    # the desk taken to another floor: gone from this one
    f1 = f"{ws.id}-DEMO-HQ-F01"
    assert call(f"{base}/items/{desk['id']}", {"floor_id": f1, "x": 1.0, "y": 1.0})[0] == 200
    assert desk["id"] in _floor(base, f0, after["version"])["gone"]


def test_a_floor_since_a_version_studio_cannot_account_for_is_the_whole_floor(studio):
    base, s, ws, f0 = studio
    v = _floor(base, f0)["version"]
    s.store.forget()  # (as after Studio started again: what changes touched is not known)
    room = next(r for r in ws.floor_objects(f0) if r.kind == "space")
    assert call(f"{base}/objects/{room.id}", {"correction": {"name": "STORE"}})[0] == 200
    again = _floor(base, f0, v - 1)
    assert "since" not in again and len(again["spaces"]) > 5
    assert call(f"{base}/floors/{f0}?since=soon")[0] == 400
