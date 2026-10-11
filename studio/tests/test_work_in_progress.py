"""Work in progress, for a page opened again (a refresh, another tab): Studio's jobs run
on the server whatever the page does, and a project's page asks what is going on —
GET …/jobs: its jobs the person may see, to run, running, or ended in the last 30
minutes with what they gave; GET …/incoming: the drawings the person sent that wait for
them to choose what to keep. A job is held here on an event, never on the clock."""

import threading
import time

import ezdxf
import pytest

from people import Team
from sessions import call
from storeypath import accounts as acc
from storeypath import server as server_module


@pytest.fixture
def team(tmp_path, monkeypatch):
    monkeypatch.setattr(acc, "SCRYPT_N", 2**10)
    monkeypatch.delenv("STOREYPATH_ALLOWED_HOSTS", raising=False)
    t = Team(tmp_path / "data")
    yield t
    t.close()


@pytest.fixture
def held(monkeypatch):
    """Finding a drawing's plans held where it reads its units, until ``.set()``."""
    gate = threading.Event()
    real = server_module.read_units

    def waiting(*args, **kwargs):
        assert gate.wait(120), "never let go"
        return real(*args, **kwargs)

    monkeypatch.setattr(server_module, "read_units", waiting)
    yield gate
    gate.set()


def jobs(team, who):
    status, listed = team(who, "GET", "jobs")
    assert status == 200
    return listed["jobs"]


def until(fn, what, within=60):
    end = time.monotonic() + within
    while not (got := fn()):
        assert time.monotonic() < end, what
        time.sleep(0.05)
    return got


def test_a_projects_jobs_are_listed_while_they_run_and_a_while_after(team, held, monkeypatch):
    status, plans = team("khalid", "POST", "drawings/hq-level-0.dxf/plans", {})
    assert status == 200
    until(lambda: team.studio.jobs.get(plans["id"]).state == "running", "finding plans starts")
    status, read = team("khalid", "POST", f"floors/{team.hq0}/convert", {})  # its turn comes after
    assert status == 200
    listed = jobs(team, "khalid")
    assert [j["id"] for j in listed] == [read["id"], plans["id"]]  # the latest first
    reading, finding = listed
    assert reading["kind"] == "read" and reading["state"] == "waiting" and reading["floors"] == [team.hq0]
    assert finding["kind"] == "plans" and finding["about"] == "hq-level-0.dxf" and finding["state"] == "running"
    assert finding["by"]["name"] == "Khalid Engineer" and finding["started"] and finding["ended"] is None
    assert finding["result"] is None and finding["log"]
    # as GET jobs/<id>: what the person may see of the project
    assert [j["id"] for j in jobs(team, "sara")] == [read["id"], plans["id"]]  # the whole project
    assert [j["id"] for j in jobs(team, "vera")] == [read["id"]]  # her floor's reading alone
    assert jobs(team, "bob") == []  # the annex only: neither
    assert [j["id"] for j in jobs(team, "boss")] == [read["id"], plans["id"]]
    held.set()
    team.finished("khalid", plans)
    team.finished("khalid", read)
    listed = {j["id"]: j for j in jobs(team, "khalid")}
    found = listed[plans["id"]]
    assert found["state"] == "done" and found["ended"] and found["result"]["plans"]  # what it gave, to show again
    assert listed[read["id"]]["state"] == "done" and listed[read["id"]]["floors"] == [team.hq0]
    monkeypatch.setattr(server_module, "JOBS_SHOWN_S", 0)  # ended longer ago than they are listed
    assert jobs(team, "khalid") == []


def test_a_job_ended_long_ago_is_forgotten(team, monkeypatch):
    status, job = team("khalid", "POST", f"floors/{team.hq0}/convert", {})
    team.finished("khalid", job)
    monkeypatch.setattr(server_module, "JOBS_KEPT_S", 0)
    status, again = team("khalid", "POST", f"floors/{team.hq0}/convert", {})  # (jobs are forgotten as one comes)
    assert team("khalid", "GET", f"/api/jobs/{job['id']}")[0] == 404
    team.finished("khalid", again)


def test_a_drawing_waiting_for_its_sender_to_choose_is_there_when_they_come_back(team, tmp_path):
    sheet = tmp_path / "villa.dxf"
    doc = ezdxf.new()
    doc.modelspace().add_line((0, 0), (10, 0))
    doc.header["$LASTSAVEDBY"] = "someone"
    doc.saveas(sheet)
    status, job, _ = call(team.port, "PUT", f"/api/projects/{team.code}/drawings/Owner%20villa.dxf",
                          raw=sheet.read_bytes(), token=team.tokens["khalid"])
    assert status == 200
    found = team.finished("khalid", job)["result"]
    assert found["pending"]
    # the page that sent it is gone (nothing of it was kept there): a page opened again asks
    status, waiting = team("khalid", "GET", "incoming")
    assert status == 200
    (w,) = waiting["incoming"]
    assert w["pending"] == found["pending"] and w["name"] == "Owner villa.dxf" and w["since"]
    assert w["found"] == found["found"] and any(f["id"] == "hidden $LASTSAVEDBY" for f in w["found"])
    listed = next(j for j in jobs(team, "khalid") if j["id"] == job["id"])
    assert listed["kind"] == "private" and listed["about"] == "Owner villa.dxf"
    # what was found private is its sender's to see (and an admin's), not everyone's who may add drawings
    assert team("sara", "GET", "incoming") == (200, {"incoming": []})
    assert [x["pending"] for x in team("boss", "GET", "incoming")[1]["incoming"]] == [found["pending"]]
    assert team("vera", "GET", "incoming")[0] == 403
    assert team("bob", "GET", "incoming")[0] == 403
    # chosen now: added, and no longer waiting
    status, kept = team("khalid", "POST", f"incoming/{found['pending']}", {"keep": []})
    assert status == 200
    assert team.finished("khalid", kept)["result"]["drawing"].startswith("drawing-")
    assert team("khalid", "GET", "incoming") == (200, {"incoming": []})
