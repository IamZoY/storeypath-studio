"""A drawing drawn whole for the drawing viewer (drawview.py): what it holds read back from
the file the page gets (every layer with what is on it, texts as words where they stand,
fills as triangles with their holes left empty), framed on its main body (a stray far off
does not shrink it to a speck), made as a job of Studio's once for its content and then
answered from the cache, to whoever may see the drawing's words. A floor's part of its
drawing, as a package carries it (format 0.10.1): cut to its region, in its own metres, its
texts as the privacy rules leave them; a package carries them only when asked to."""

import gzip
import io
import json
import math
import struct
import threading
import time
import zipfile

import ezdxf
import numpy as np
import pytest
from ezdxf.enums import TextEntityAlignment

from sessions import call
from storeypath import accounts as acc
from storeypath.accounts import Accounts, Scope
from storeypath.drawview import (VIEW_FORMAT, VIEW_VERSION, main_body, read_view, read_view_head, view_problem,
                                 write_floor_views, write_view)
from storeypath.samples import build_demo
from storeypath.server import Studio
from storeypath.web import make_server
from storeypath.workers import ReadWorkers


class NoModel:
    name, ready = "none", False

    def available(self):
        return False

    def close(self):
        pass


def _drawing(path, stray=False):
    """A small made-up floor in millimetres: walls (red) and room tags (green) on layers of
    their own, a column (a circle), a solid hatch with a hole, a desk placed twice (once
    turned), texts (one turned) and a note of two lines; ``stray``: and a line left far off."""
    doc = ezdxf.new("R2018", setup=True)
    doc.header["$INSUNITS"] = 4
    doc.layers.add("WALLS", color=1)
    doc.layers.add("ROOM_TAGS", color=3)
    msp = doc.modelspace()
    msp.add_line((0, 0), (10000, 0), dxfattribs={"layer": "WALLS"})
    msp.add_lwpolyline([(0, 0), (10000, 0), (10000, 6000), (0, 6000)], close=True, dxfattribs={"layer": "WALLS"})
    msp.add_circle((5000, 3000), 300, dxfattribs={"layer": "ROOM_TAGS"})
    hatch = msp.add_hatch(color=5, dxfattribs={"layer": "WALLS"})
    hatch.paths.add_polyline_path([(1000, 1000), (4000, 1000), (4000, 4000), (1000, 4000)], is_closed=True)
    hatch.paths.add_polyline_path([(2000, 2000), (3000, 2000), (3000, 3000), (2000, 3000)], is_closed=True)
    desk = doc.blocks.new("DESK")
    desk.add_lwpolyline([(0, 0), (1200, 0), (1200, 600), (0, 600)], close=True)
    msp.add_blockref("DESK", (6000, 1000), dxfattribs={"layer": "WALLS"})
    msp.add_blockref("DESK", (8000, 1000), dxfattribs={"layer": "WALLS", "rotation": 90})
    msp.add_text("OFFICE 101", height=250, dxfattribs={"layer": "ROOM_TAGS"}).set_placement((1500, 5000))
    msp.add_text("STORE", height=200, rotation=30, dxfattribs={"layer": "ROOM_TAGS"}).set_placement(
        (8500, 4000), align=TextEntityAlignment.LEFT)
    msp.add_mtext("MEETING\\PROOM", dxfattribs={"layer": "ROOM_TAGS", "char_height": 200, "insert": (6000, 4500)})
    if stray:
        msp.add_line((5_000_000, 5_000_000), (5_000_100, 5_000_000), dxfattribs={"layer": "WALLS"})
    doc.saveas(path)
    return path


def _view(tmp_path, **kw):
    out = tmp_path / "drawing.view"
    numbers = write_view(_drawing(tmp_path / "floor.dxf", **kw), out)
    head, arrays = read_view(out.read_bytes())
    return numbers, head, arrays


def _area(triangles: np.ndarray) -> np.ndarray:
    t = triangles.astype(float)
    return 0.5 * np.abs((t[:, 1, 0] - t[:, 0, 0]) * (t[:, 2, 1] - t[:, 0, 1])
                        - (t[:, 2, 0] - t[:, 0, 0]) * (t[:, 1, 1] - t[:, 0, 1]))


def _inside(triangles: np.ndarray, x: float, y: float) -> bool:
    t = triangles.astype(float)
    a, b, c = t[:, 0], t[:, 1], t[:, 2]

    def side(p, q):
        return (q[:, 0] - p[:, 0]) * (y - p[:, 1]) - (q[:, 1] - p[:, 1]) * (x - p[:, 0])

    s1, s2, s3 = side(a, b), side(b, c), side(c, a)
    return bool((((s1 >= 0) & (s2 >= 0) & (s3 >= 0)) | ((s1 <= 0) & (s2 <= 0) & (s3 <= 0))).any())


# ---- what a view holds ---------------------------------------------------------------------

def test_a_drawing_is_drawn_whole_with_its_layers_texts_and_fills(tmp_path):
    numbers, head, arrays = _view(tmp_path)
    assert head["format"] == VIEW_FORMAT and head["units"] == "mm" and head["clip"] is None
    assert numbers["texts"] == 4 and numbers["bytes"] > 0
    ox, oy = head["origin"]

    # every layer with something on it, its colour and how many things: the walls' line,
    # outline, hatch and the two desks' outlines (a block's pieces count, the block does not)
    layers = {layer["name"]: layer for layer in head["layers"]}
    assert layers["WALLS"]["count"] == 5 and layers["WALLS"]["color"] == "#ff0000"
    assert layers["ROOM_TAGS"]["count"] == 4 and layers["ROOM_TAGS"]["color"] == "#00ff00"
    assert not any(layer["off"] for layer in head["layers"])
    walls = head["layers"].index(layers["WALLS"])
    batches = [b for b in head["batches"] if b["layer"] == walls]
    assert {b["kind"] for b in batches} == {"lines", "fills"}
    assert head["colors"][next(b for b in batches if b["kind"] == "fills")["color"]] == "#0000ff"  # (the hatch's own)

    # the lines: the walls, and each desk where it was placed (the second turned a quarter)
    ends = arrays["lines"].astype(float) + (ox, oy)
    near = lambda x, y: bool((np.hypot(ends[:, 0] - x, ends[:, 1] - y) < 1).any())  # noqa: E731
    assert near(10000, 6000) and near(7200, 1600) and near(7400, 2200) and near(8000, 2200)
    assert head["bounds"] == pytest.approx([0, 0, 10000, 6000], abs=1)

    # the hatch's fill: its outline less its hole, the hole left empty
    triangles = arrays["fills"].reshape(-1, 3, 2).astype(float) + (ox, oy)
    assert _area(triangles).sum() == pytest.approx(3000 * 3000 - 1000 * 1000, rel=1e-3)
    assert _inside(triangles, 1500, 1500) and _inside(triangles, 3500, 2500)
    assert not _inside(triangles, 2500, 2500)

    # the texts as their words, where they stand and how they turn; the note a line at a time
    words = head["words"]
    assert sorted(words) == ["MEETING", "OFFICE 101", "ROOM", "STORE"]
    texts = {w: arrays["texts"][i].astype(float) for i, w in enumerate(words)}
    x, y, a, b, c, d, cap, width = texts["OFFICE 101"]
    assert (x + ox, y + oy) == pytest.approx((1500, 5000), abs=1) and cap == pytest.approx(250)
    assert math.degrees(math.atan2(b, a)) == pytest.approx(0, abs=0.01) and width > 4 * cap
    x, y, a, b, c, d, cap, _ = texts["STORE"]
    assert (x + ox, y + oy) == pytest.approx((8500, 4000), abs=1) and cap == pytest.approx(200)
    assert math.degrees(math.atan2(b, a)) == pytest.approx(30, abs=0.01)
    assert math.degrees(math.atan2(-c, d)) == pytest.approx(30, abs=0.01)  # (its letters upright to its line)
    meeting, room = texts["MEETING"], texts["ROOM"]
    assert meeting[0] + ox == pytest.approx(6000, abs=1) and room[0] + ox == pytest.approx(6000, abs=1)
    assert 3500 < room[1] + oy < meeting[1] + oy < 4500  # (below where the note is put, one line under the other)
    assert all(head["layers"][layer]["name"] == "ROOM_TAGS" for layer, _ in arrays["styles"])


def test_a_stray_far_off_does_not_spread_the_main_body(tmp_path):
    _, head, _ = _view(tmp_path, stray=True)
    assert head["body"] == pytest.approx([0, 0, 10000, 6000], abs=1)  # (what the viewer fits to the window)
    assert head["bounds"] == pytest.approx([0, 0, 5_000_100, 5_000_000], abs=1)  # (zoomed out, it is there)
    x0, y0, x1, y1 = head["body"]
    ox, oy = head["origin"]
    assert x0 <= ox <= x1 and y0 <= oy <= y1  # (the numbers are kept from the middle of the main body)


def test_the_main_body_is_where_most_is_and_what_lies_near_it():
    rng = np.random.default_rng(7)
    plan = rng.uniform((0, 0), (100, 60), size=(400, 2))
    legend = rng.uniform((120, 0), (140, 20), size=(20, 2))  # beside the plan: part of the drawing
    origin = np.array([[-90_000, -90_000]])  # a line left at the origin, the plan drawn far from it
    far = rng.uniform((5000, 5000), (5050, 5030), size=(15, 2))  # a legend kilometres off
    body = main_body(np.concatenate([plan, legend, origin, far]))
    assert body[0] == pytest.approx(0, abs=1) and body[1] == pytest.approx(0, abs=1)
    assert body[2] == pytest.approx(140, abs=1) and body[3] == pytest.approx(60, abs=1)
    assert main_body(np.zeros((0, 2))) is None
    assert main_body(np.array([[3.0, 4.0]])) == (3.0, 4.0, 3.0, 4.0)


def test_a_hatch_of_millions_of_dots_is_a_tint_of_its_colour(tmp_path):
    doc = ezdxf.new("R2018", setup=True)
    msp = doc.modelspace()
    msp.add_lwpolyline([(0, 0), (40000, 0), (40000, 30000), (0, 30000)], close=True)
    sand = msp.add_hatch()  # (a site's ground of fine dots)
    sand.set_pattern_fill("DOTS", color=2, scale=10)
    sand.paths.add_polyline_path([(0, 0), (40000, 0), (40000, 30000), (0, 30000)], is_closed=True)
    tiles = msp.add_hatch()  # (a few lines: drawn as they are)
    tiles.set_pattern_fill("ANSI31", color=4, scale=100)
    tiles.paths.add_polyline_path([(1000, 1000), (5000, 1000), (5000, 5000), (1000, 5000)], is_closed=True)
    doc.saveas(tmp_path / "site.dxf")
    numbers = write_view(tmp_path / "site.dxf", tmp_path / "site.view")
    head, arrays = read_view((tmp_path / "site.view").read_bytes())
    assert numbers["points"] == 0 and numbers["lines"] < 1000
    tint = [b for b in head["batches"] if b["kind"] == "fills"]
    assert [head["colors"][b["color"]] for b in tint] == ["#ffff0040"]  # (yellow, a quarter)
    assert _area(arrays["fills"].reshape(-1, 3, 2)).sum() == pytest.approx(40000 * 30000, rel=1e-3)
    cyan = head["colors"].index("#00ffff")
    assert sum(b["count"] for b in head["batches"] if b["kind"] == "lines" and b["color"] == cyan) // 2 > 10


def _sheet(path):
    """A made-up sheet of two floors in millimetres, 30 m apart (the second, a room on
    WALLS and its tag on ROOM_TAGS, as the first), a wall line running from between them
    into the second, a note far off, and beside the second's room a telephone number and an
    office with a person's name."""
    doc = ezdxf.new("R2018", setup=True)
    doc.header["$INSUNITS"] = 4
    doc.layers.add("WALLS", color=1)
    doc.layers.add("ROOM_TAGS", color=3)
    msp = doc.modelspace()
    for ox in (0, 30000):
        msp.add_lwpolyline([(ox, 0), (ox + 10000, 0), (ox + 10000, 6000), (ox, 6000)], close=True,
                           dxfattribs={"layer": "WALLS"})
        msp.add_text("OFFICE 101", height=250, dxfattribs={"layer": "ROOM_TAGS"}).set_placement((ox + 1500, 5000))
    msp.add_line((25000, 3000), (35000, 3000), dxfattribs={"layer": "WALLS"})
    msp.add_text("GENERAL NOTES", height=250, dxfattribs={"layer": "ROOM_TAGS"}).set_placement((90000, 3000))
    msp.add_text("TEL 555 0100", height=250, dxfattribs={"layer": "ROOM_TAGS"}).set_placement((31500, 3000))
    msp.add_text("OFFICE MR. JOHN DOE", height=250, dxfattribs={"layer": "ROOM_TAGS"}).set_placement((31500, 2000))
    doc.saveas(path)
    return path


def test_a_floors_part_is_cut_to_its_region_in_the_floors_own_metres(tmp_path):
    """As a package carries it: what lies in the floor's region of the drawing, what crosses
    its edge cut there, moved by its offset and in metres (where its spaces are read), and
    its texts as the privacy rules leave them; the drawing read once for both floors."""
    said = []
    made = write_floor_views(_sheet(tmp_path / "sheet.dxf"), [
        {"out": str(tmp_path / "f0.view"), "region": [-500, -500, 10500, 6500], "offset": None, "units": None},
        {"out": str(tmp_path / "f1.view"), "region": [29500, -500, 40500, 6500], "offset": [30000, 0], "units": None},
    ], said.append)
    assert [line for line in said if line.startswith("reading")] == ["reading sheet.dxf"]
    assert [m["texts"] for m in made] == [1, 2]
    for name in ("f0", "f1"):
        data = (tmp_path / f"{name}.view").read_bytes()
        assert view_problem(data) is None
        head, arrays = read_view(data)
        assert read_view_head(data) == head
        assert head["units"] == "m" and head["clip"] == [-0.5, -0.5, 10.5, 6.5]
        ends = arrays["lines"].astype(float) + head["origin"]
        left = -0.5 if name == "f1" else 0  # (the second's: the line from between them, cut at the edge)
        assert ends.min(axis=0) == pytest.approx([left, 0], abs=1e-3)
        assert ends.max(axis=0) == pytest.approx([10, 6], abs=1e-3)  # (the room's walls, where the floor's are)
        assert head["bounds"] == pytest.approx([left, 0, 10, 6], abs=1e-3)
        assert head["body"] == head["bounds"]  # (all of a floor's part is its main body)
        x, y, a, b, c, d, cap, width = (arrays["texts"][head["words"].index("OFFICE 101")].astype(float)
                                        + [*head["origin"], 0, 0, 0, 0, 0, 0])
        assert (x, y, cap) == pytest.approx((1.5, 5.0, 0.25), abs=1e-3) and (a, d) == pytest.approx((1, 1))
        assert 1.0 < width < 3.0  # (in metres)
    # the second floor's texts: the telephone number left out, the name taken out of its office
    assert sorted(read_view((tmp_path / "f1.view").read_bytes())[0]["words"]) == ["OFFICE", "OFFICE 101"]
    assert read_view((tmp_path / "f0.view").read_bytes())[0]["words"] == ["OFFICE 101"]  # (nothing of the notes)


def _repacked(data: bytes, change=None, cut: int = 0) -> bytes:
    """A view with its head changed (``change(head)``) or its last ``cut`` bytes gone."""
    raw = gzip.decompress(data)
    (n,) = struct.unpack("<I", raw[4:8])
    head, rest = json.loads(raw[8:8 + n]), raw[8 + n:]
    if change is not None:
        change(head)
    text = json.dumps(head).encode()
    text += b" " * (-len(text) % 4)
    raw = raw[:4] + struct.pack("<I", len(text)) + text + rest
    return gzip.compress(raw[:len(raw) - cut])


def test_a_view_that_is_not_as_the_format_has_it_is_refused(tmp_path):
    numbers = write_view(_drawing(tmp_path / "floor.dxf"), tmp_path / "floor.view")
    data = (tmp_path / "floor.view").read_bytes()
    assert view_problem(data) is None and numbers["bytes"] == len(data)
    assert view_problem(b"not gzip").startswith("not gzip")
    assert view_problem(gzip.compress(b"PK\x03\x04 a zip")) == "not a drawing's view (no SPDV at its start)"
    assert view_problem(_repacked(data, lambda h: h.update(format=2))) == "format 2: this reader reads 1"
    assert "are not where its head says" in view_problem(_repacked(data, cut=64))
    assert "not one each" in view_problem(_repacked(data, lambda h: h["words"].pop()))
    assert "not within them" in view_problem(_repacked(data, lambda h: h["batches"][0].update(count=10**6)))
    assert view_problem(_repacked(data, lambda h: h.update(unknown_to_this_reader=[1, 2]))) is None  # (ignored)


def test_a_view_is_drawn_in_a_read_worker_too(tmp_path):
    workers = ReadWorkers(1)
    said = []
    try:
        numbers = workers.view("key", {"drawing": str(_drawing(tmp_path / "floor.dxf")),
                                       "out": str(tmp_path / "views" / "key.view")}, said.append)
    finally:
        workers.close()
    head, _ = read_view((tmp_path / "views" / "key.view").read_bytes())
    assert numbers["texts"] == 4 and head["counts"]["texts"] == 4
    assert said and said[0].startswith("reading")  # (what it says comes back as it says it)


# ---- through Studio's web server -------------------------------------------------------------

@pytest.fixture
def studio(tmp_path, monkeypatch):
    """Studio with the tests' campus; an admin, an engineer it is not shared with, someone
    who may view one of its floors, and someone who may change the whole project."""
    monkeypatch.setattr(acc, "SCRYPT_N", 2**10)
    monkeypatch.delenv("STOREYPATH_ALLOWED_HOSTS", raising=False)
    data = tmp_path / "data"
    ws_path, _ = build_demo(data / "demo")
    studio = Studio(data, model=NoModel(), warm=False)
    accounts = Accounts(data)
    srv = make_server(studio, port=0, accounts=accounts)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    people = {name: accounts.add_user(name, "a password", role=role, must_change_password=False)
              for name, role in (("boss", "admin"), ("eng", "engineer"), ("vera", "user"), ("ed", "user"))}
    code = studio.projects()[0]["code"]
    hq0 = f"{code}-DEMO-HQ-F00"
    accounts.set_grant(code, people["vera"].id, Scope(kind="floor", id=hq0), "view", people["boss"].id)
    accounts.set_grant(code, people["ed"].id, Scope(kind="project"), "edit", people["boss"].id)
    tokens = {n: accounts.start_session(u) for n, u in people.items()}
    yield srv.server_port, studio, tokens, code
    srv.shutdown()
    srv.server_close()


def _done(studio, job_id):
    for _ in range(600):
        job = studio.jobs.get(job_id)
        if job.state in ("done", "failed"):
            return job
        time.sleep(0.05)
    raise AssertionError("the job did not end")


def test_a_drawing_is_drawn_as_a_job_once_then_sent_from_the_cache(studio):
    port, s, tokens, code = studio
    view = f"/api/projects/{code}/drawings/hq-level-0.dxf/view"
    status, said, _ = call(port, "GET", view, token=tokens["boss"])
    assert status == 404 and "not drawn whole yet" in said["error"]

    status, job, _ = call(port, "POST", view, {}, token=tokens["boss"])
    assert status == 200 and job["kind"] == "view" and job["state"] in ("waiting", "running", "done")
    ended = _done(s, job["id"])
    assert ended.state == "done", ended.error
    made = ended.result
    assert made["url"].startswith(view + "?v=") and made["key"].endswith(f"-{VIEW_VERSION}") and made["bytes"] > 0
    assert any(line.startswith("drawing it") for line in ended.log)  # (its progress, as it goes)

    # sent as it is kept: gzip already, never compressed a second time on the way
    status, blob, res = call(port, "GET", made["url"], token=tokens["boss"], headers={"Accept-Encoding": "gzip"})
    assert status == 200 and res.getheader("Content-Type") == "application/gzip"
    assert res.getheader("Content-Encoding") is None and len(blob) == made["bytes"]
    head, arrays = read_view(blob)
    assert head["format"] == VIEW_FORMAT and len(arrays["lines"]) > 100 and head["layers"]

    # asked again: from the cache, no job
    jobs = len(s.jobs._jobs)
    status, again, _ = call(port, "POST", view, {}, token=tokens["boss"])
    assert status == 200 and "state" not in again and again == made
    assert len(s.jobs._jobs) == jobs


def test_who_may_see_a_drawing_whole_is_who_may_see_its_words(studio):
    port, s, tokens, code = studio
    drawing = f"/api/projects/{code}/drawings/hq-level-0.dxf"
    status, job, _ = call(port, "POST", drawing + "/view", {}, token=tokens["boss"])
    assert _done(s, job["id"]).state == "done"
    for who in ("boss", "eng", "vera", "ed", None):
        token = tokens.get(who)
        words = call(port, "GET", drawing + "/words", token=token)[0]
        asked = call(port, "POST", drawing + "/view", {}, token=token)[0]
        got = call(port, "GET", drawing + "/view", token=token)[0]
        assert asked == got == words, who
    assert call(port, "GET", drawing + "/view", token=tokens["ed"])[0] == 200
    assert call(port, "GET", drawing + "/view", token=tokens["vera"])[0] == 403  # (one floor of it: not the drawing)


def test_a_package_carries_its_floors_drawings_only_when_asked(studio, tmp_path):
    """Export's drawings (format 0.10.1): each floor's part of its drawing in the package
    when asked for, the export's history saying so; drawn once and kept, the next export
    takes them as they are; not asked for, none."""
    from storeypath.validate import validate_package

    port, s, tokens, code = studio
    hq = f"{code}-DEMO-HQ"

    def export(body):
        status, job, _ = call(port, "POST", f"/api/projects/{code}/export", body, token=tokens["boss"])
        assert status == 200, job
        ended = _done(s, job["id"])
        assert ended.state == "done", ended.error
        status, blob, _ = call(port, "GET", f"/api/projects/{code}/exports/{ended.result['file']}", token=tokens["boss"])
        assert status == 200
        (tmp_path / ended.result["file"]).write_bytes(blob)
        assert validate_package(tmp_path / ended.result["file"]) == []
        return ended, zipfile.ZipFile(io.BytesIO(blob))

    floors = [f"{hq}-{f.code}" for f in s.store.current(code).building(hq).floors if f.source]
    ended, z = export({"building": hq, "drawings": True})
    manifest = json.loads(z.read("manifest.json"))
    assert manifest["format_version"] == "0.10.1" and manifest["files"]["drawings"] == "drawings/"
    assert sorted(src["view"] for src in manifest["sources"]) == sorted(f"drawings/{f}.view" for f in floors)
    assert ended.result["drawings"] == len(floors) >= 2
    assert any(line.startswith("drawing ") and " from " in line for line in ended.log)  # (what it is doing)
    for f in floors:
        info = z.getinfo(f"drawings/{f}.view")
        assert info.compress_type == zipfile.ZIP_STORED  # (gzip already)
        head = read_view_head(z.read(info))
        assert head["units"] == "m" and head["layers"] and head["counts"]["lines"] > 100
    lines = [e["line"] for e in s.history(code)["entries"] if e["kind"] == "export"]
    assert lines[0] == f"exported {ended.result['file']}, with its drawings"

    again, z = export({"building": hq, "drawings": True})  # (drawn once: taken from the cache)
    assert not any(line.startswith("drawing ") and " from " in line for line in again.log)
    assert sorted(n for n in z.namelist() if n.startswith("drawings/")) == sorted(f"drawings/{f}.view" for f in floors)

    plain, z = export({"building": hq})
    manifest = json.loads(z.read("manifest.json"))
    assert "drawings" not in plain.result and "drawings" not in manifest["files"]
    assert not [n for n in z.namelist() if n.startswith("drawings/")] and all(src["view"] is None for src in manifest["sources"])
    assert [e["line"] for e in s.history(code)["entries"] if e["kind"] == "export"][0] == f"exported {plain.result['file']}"
    status, said, _ = call(port, "POST", f"/api/projects/{code}/export", {"building": hq, "drawings": "yes"},
                           token=tokens["boss"])
    assert status == 400 and "drawings" in said["error"]


def test_the_project_says_where_each_floor_is_in_its_drawing(studio):
    port, s, tokens, code = studio
    status, p, _ = call(port, "GET", f"/api/projects/{code}", token=tokens["boss"])
    floors = [f for loc in p["locations"] for b in loc["buildings"] for f in b["floors"]]
    assert floors and all({"region", "offset", "units"} <= set(f) for f in floors)
