"""A project's drawings: one deleted (POST …/drawings/<name>/delete), by whoever may add
one, with what was made of it (its words, its views, its prints), recorded in the
project's history; refused while a floor reads from it, naming those floors. And the
same file sent again is not kept twice: it is known by the sha256 of the file as sent
(before its private information is taken out, or a DWG converted), or of a drawing kept
as it is, within the project; the answer names the drawing it is."""

import hashlib
import io
import json
import os
import zipfile

import ezdxf
import pytest

from people import NoModel, Team
from sessions import call
from storeypath import accounts as acc
from storeypath.samples import office_floor, write_floor_dxf
from storeypath.server import VIEWS, Studio


@pytest.fixture
def team(tmp_path, monkeypatch):
    monkeypatch.setattr(acc, "SCRYPT_N", 2**10)
    monkeypatch.setattr("storeypath.review.BUSY_WAIT_S", 0.2)
    monkeypatch.delenv("STOREYPATH_ALLOWED_HOSTS", raising=False)
    t = Team(tmp_path / "data")
    yield t
    t.close()


def sheet(path, saved_by="someone", ordinal=0):
    """A made-up floor plan with hidden file data (something private to choose about)."""
    write_floor_dxf(path, office_floor(ordinal), title=f"LEVEL {ordinal} PLAN")
    doc = ezdxf.readfile(path)
    doc.header["$LASTSAVEDBY"] = saved_by
    doc.saveas(path)
    return path.read_bytes()


def send(team, name, data, who="khalid", private=True, code=None):
    """A file sent to the project, as the page sends it: (status, answer)."""
    path = f"/api/projects/{code or team.code}/drawings/{name}" + ("" if private else "?private=0")
    status, answer, _ = call(team.port, "PUT", path, raw=data, token=team.tokens[who])
    return status, answer


def add(team, name, data, keep=(), who="khalid", code=None):
    """A file sent and kept without its private information (but ``keep``): what the job gave."""
    status, job = send(team, name, data, who, code=code)
    assert status == 200 and job.get("id"), job
    found = team.finished(who, job)["result"]
    if found.get("pending"):
        path = f"/api/projects/{code or team.code}/incoming/{found['pending']}"
        status, job, _ = call(team.port, "POST", path, {"keep": list(keep)}, token=team.tokens[who])
        assert status == 200, job
        found = team.finished(who, job)["result"]
    return found


def names(team, code=None):
    return [d["name"] for d in team.studio.store.drawings(code or team.code)]


def listed(team, who="khalid"):
    """The project page's drawings, by name."""
    return {d["name"]: d for d in team(who, "GET", f"/api/projects/{team.code}")[1]["drawings"]}


def delete(team, name, who="khalid"):
    return team(who, "POST", f"drawings/{name}/delete", {})


# ---- deleting a drawing ----------------------------------------------------------------

def test_a_drawing_no_floor_reads_from_is_deleted_with_what_was_made_of_it(team, tmp_path):
    studio, code = team.studio, team.code
    kept = add(team, "plan.dxf", sheet(tmp_path / "plan.dxf"))
    name = kept["drawing"]
    assert name == "drawing-1.dxf" and name in listed(team) and listed(team)[name]["in_use"] is None
    assert studio.store.words(code, name)  # (its words, kept with it)
    sha = studio.store.drawing(code, name)["sha256"]
    # what was made of it: its view and a package's view of a part of it, and a print; and
    # another drawing's, which stay
    views = studio.cache / VIEWS / code
    views.mkdir(parents=True, exist_ok=True)
    made = [views / f"{studio._view_key(code, name)}.view", views / f"{sha[:32]}-0123456789ab-9.view"]
    other = views / f"{studio._view_key(code, 'hq-level-0.dxf')}.view"
    prints = studio.cache / "prints" / code
    prints.mkdir(parents=True, exist_ok=True)
    for path in [*made, other]:
        path.write_bytes(b"made")
    for stem, drawing in (("floor-one", name), ("floor-two", "hq-level-0.dxf")):
        (prints / f"{stem}.png").write_bytes(b"png")
        (prints / f"{stem}.json").write_text(json.dumps({"key": stem, "drawing": drawing}))

    status, done = delete(team, name)
    assert (status, done) == (200, {"deleted": name})
    assert name not in names(team) and name not in listed(team)
    assert studio.store.drawing(code, name) is None
    assert not any(p.exists() for p in made) and other.exists()
    assert not (prints / "floor-one.png").exists() and not (prints / "floor-one.json").exists()
    assert (prints / "floor-two.png").exists()
    assert delete(team, name)[0] == 404  # gone
    # in the project's history, by who deleted it, in words; and audited
    row = next(r for r in studio.store.history(code) if r["kind"] == "drawing removed")
    assert row["targets"] == [name] and row["who"]["name"] == "Khalid Engineer"
    lines = [e["line"] for e in team("khalid", "GET", "history")[1]["entries"]]
    assert f"deleted the drawing {name}" in lines and f"added the drawing {name}" in lines
    assert team.accounts.audit_tail(5)[0]["action"] == "drawing deleted"
    # the project file no longer carries it (nor its words); the others it does
    status, blob, _ = call(team.port, "GET", f"/api/projects/{code}/project.storeypath-project",
                           token=team.tokens["khalid"])
    assert status == 200
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        files = {n.rsplit("/", 1)[-1] for n in z.namelist() if "/drawings/" in n}
    assert "hq-level-0.dxf" in files and not any(f.startswith(name) for f in files)
    # a drawing kept afterwards is never given its name
    assert add(team, "plan-2.dxf", sheet(tmp_path / "plan-2.dxf", ordinal=1))["drawing"] == "drawing-2.dxf"


def test_a_drawing_floors_read_from_is_not_deleted_and_says_which(team):
    studio, code = team.studio, team.code
    ws = studio.store.load(code)
    for floor in (team.hq1, team.an0):  # three floors of two buildings on one drawing
        ws.floor(floor).source.path = "drawings/hq-level-0.dxf"
    studio.store.save(ws)
    before = studio.store.history(code)[0]["seq"]

    status, refused = delete(team, "hq-level-0.dxf")
    said = ("hq-level-0.dxf is the drawing of Headquarters' Ground floor and Floor 1, and Annex's Ground floor: "
            "give those floors another drawing first (Find plans in that drawing, then Replace its drawing), "
            "then delete this one")
    assert status == 409 and refused["error"] == said
    assert refused["floors"] == [team.hq0, team.hq1, team.an0]
    # nothing changed
    assert "hq-level-0.dxf" in names(team) and studio.store.history(code)[0]["seq"] == before
    # the page holds its Delete, saying the same; one floor, said so
    assert listed(team)["hq-level-0.dxf"]["in_use"] == said
    assert listed(team)["hq-level-2.dxf"]["in_use"].startswith(
        "hq-level-2.dxf is the drawing of Headquarters' Floor 2: give that floor another drawing first")
    # unused (floor 1 had it), it goes
    assert listed(team)["hq-level-1.dxf"]["in_use"] is None
    assert delete(team, "hq-level-1.dxf")[0] == 200


def test_who_may_delete_a_drawing_as_who_may_add_one(team, tmp_path):
    data = sheet(tmp_path / "plan.dxf")
    assert send(team, "spare.dxf", data, private=False)[0] == 200
    assert send(team, "spare.dxf", data, who="bob", private=False)[0] == 403  # one building's alone
    assert delete(team, "spare.dxf", "vera")[0] == 403  # she may only look at a floor
    assert delete(team, "spare.dxf", "bob")[0] == 403  # the Annex is his, not the project
    status, _, _ = call(team.port, "POST", f"/api/projects/{team.code}/drawings/spare.dxf/delete", {})
    assert status == 401  # nobody logged in
    assert "spare.dxf" in names(team)
    assert delete(team, "spare.dxf", "sara")[0] == 200  # edit on the whole project


def test_drawings_waiting_to_be_kept_are_not_kept_when_studio_starts_again(team, tmp_path):
    status, job = send(team, "plan.dxf", sheet(tmp_path / "plan.dxf"))
    assert team.finished("khalid", job)["result"]["pending"]
    assert len(team.studio.store.drawings(team.code, incoming=True)) == 1
    again = Studio(team.studio.data, model=NoModel(), warm=False, first_start=False)
    try:
        assert again.store.drawings(team.code, incoming=True) == []
    finally:
        again.close()


# ---- the same file sent again ------------------------------------------------------------

def test_the_same_file_sent_twice_is_kept_once(team, tmp_path):
    data = sheet(tmp_path / "plan.dxf")
    first = add(team, "plan.dxf", data)
    assert first["drawing"] == "drawing-1.dxf"
    kept = team.studio.store.drawings(team.code)
    status, again = send(team, "plan%20(copy).dxf", data)  # another name, the same bytes
    assert status == 200 and again["drawing"] == "drawing-1.dxf" and again["already"] is True
    assert again["added"][:10] == next(d for d in kept if d["name"] == "drawing-1.dxf")["uploaded_at"].isoformat()[:10]
    assert "id" not in again  # no job: nothing looked through, nothing kept
    assert team.studio.store.drawings(team.code) == kept
    assert team.studio.store.drawings(team.code, incoming=True) == []
    # kept as sent, the same: as it is
    assert send(team, "plan.dxf", data, private=False)[1]["drawing"] == "drawing-1.dxf"
    # a file sent as it was kept (its private information out already) is that drawing too
    as_kept = team.studio.store.drawing_bytes(team.code, "drawing-1.dxf")
    assert send(team, "drawing-1.dxf", as_kept)[1] == {**again}
    assert names(team).count("drawing-1.dxf") == 1 and len(names(team)) == len(kept)


def test_the_same_file_is_known_whatever_was_kept_of_it(team, tmp_path):
    data = sheet(tmp_path / "plan.dxf")
    first = add(team, "plan.dxf", data, keep=["hidden $LASTSAVEDBY"])  # (who saved it, kept on purpose)
    name = first["drawing"]
    info = team.studio.store.drawings(team.code)
    kept = next(d for d in info if d["name"] == name)
    sent = hashlib.sha256(data).hexdigest()
    assert kept["sent_sha256"] == sent and kept["sha256"] != sent  # (what was kept is not the file sent)
    status, again = send(team, "plan.dxf", data)
    assert status == 200 and again["drawing"] == name and again["already"]
    # a different file is a new drawing
    other = add(team, "plan.dxf", sheet(tmp_path / "other.dxf", saved_by="someone else"))
    assert other["drawing"] != name and other["drawing"] in names(team)


def test_the_same_file_sent_twice_before_either_is_kept_is_kept_once(team, tmp_path):
    data = sheet(tmp_path / "plan.dxf")
    pending = []
    for _ in range(2):
        status, job = send(team, "plan.dxf", data)
        pending.append(team.finished("khalid", job)["result"]["pending"])
    results = []
    for token in pending:
        status, job, _ = call(team.port, "POST", f"/api/projects/{team.code}/incoming/{token}", {"keep": []},
                              token=team.tokens["khalid"])
        results.append(team.finished("khalid", job)["result"])
    assert results[0]["drawing"] == "drawing-1.dxf" and not results[0].get("already")
    assert results[1]["drawing"] == "drawing-1.dxf" and results[1]["already"]
    assert names(team).count("drawing-1.dxf") == 1 and "drawing-2.dxf" not in names(team)
    assert team.studio.store.drawings(team.code, incoming=True) == []


def test_the_same_file_in_another_project_is_kept_there(team, tmp_path):
    data = sheet(tmp_path / "plan.dxf")
    assert add(team, "plan.dxf", data)["drawing"] == "drawing-1.dxf"
    status, made = team("khalid", "POST", "/api/projects", {"name": "Another site"})
    assert status == 200
    there = add(team, "plan.dxf", data, code=made["code"])
    assert there["drawing"] == "drawing-1.dxf" and not there.get("already")
    assert names(team, made["code"]) == ["drawing-1.dxf"]


def test_a_dwg_sent_twice_is_known(team, tmp_path, monkeypatch):
    plan = tmp_path / "converted.dxf"
    write_floor_dxf(plan, office_floor(0), title="LEVEL 0 PLAN")
    bin_dir = tmp_path / "bin"  # a stand-in for LibreDWG's dwg2dxf: writes the DXF where it is told
    bin_dir.mkdir()
    (bin_dir / "dwg2dxf").write_text(f'#!/bin/sh\ncp "{plan}" "$3"\n')
    (bin_dir / "dwg2dxf").chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    dwg = b"AC1032 a made-up DWG"
    first = add(team, "plan.dwg", dwg)
    assert first["drawing"] == "drawing-1.dxf"
    kept = next(d for d in team.studio.store.drawings(team.code) if d["name"] == "drawing-1.dxf")
    assert kept["sent_sha256"] == hashlib.sha256(dwg).hexdigest()
    status, again = send(team, "plan.dwg", dwg)
    assert status == 200 and again["drawing"] == "drawing-1.dxf" and again["already"]
    assert names(team).count("drawing-1.dxf") == 1 and "drawing-2.dxf" not in names(team)


def test_drawings_kept_twice_are_listed_as_the_same(team, tmp_path):
    data = sheet(tmp_path / "plan.dxf")
    for name in ("drawing-3.dxf", "drawing-7.dxf"):  # (kept before files were known again)
        team.studio.store.put_drawing(team.code, name, data)
    drawings = listed(team)
    assert drawings["drawing-3.dxf"]["same_as"] == ["drawing-7.dxf"]
    assert drawings["drawing-7.dxf"]["same_as"] == ["drawing-3.dxf"]
    assert drawings["hq-level-0.dxf"]["same_as"] == [] and drawings["drawing-3.dxf"]["added"]
    # the spare one deleted, the other's views stay (they are its too)
    view = team.studio.cache / VIEWS / team.code / f"{team.studio._view_key(team.code, 'drawing-3.dxf')}.view"
    view.parent.mkdir(parents=True, exist_ok=True)
    view.write_bytes(b"made")
    assert delete(team, "drawing-7.dxf")[0] == 200
    assert view.exists() and listed(team)["drawing-3.dxf"]["same_as"] == []
