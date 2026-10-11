"""A read of drawings stopped (Jobs.stop, POST /api/jobs/<id>/stop): by who started it,
whoever may read the floor again, or an admin, and nobody else. It stops the next time
it says how far it has got: the floor stays as it was, and what vision answered is kept,
so reading it again asks only about the rest."""

import threading
import time

import pytest

from people import Team
from storeypath import accounts as acc
from storeypath import convert as convert_module


@pytest.fixture
def team(tmp_path, monkeypatch):
    monkeypatch.setattr(acc, "SCRYPT_N", 2**10)
    monkeypatch.delenv("STOREYPATH_ALLOWED_HOSTS", raising=False)
    t = Team(tmp_path / "data")
    yield t
    t.close()


ANSWER = {"drawing": "d", "outline": "one room", "type": "office", "shape": "POLYGON ((0 0, 1 0, 1 1, 0 1, 0 0))"}


def _ended(team, who, job, within=30):
    end = time.monotonic() + within
    while job["state"] not in ("done", "failed"):
        assert time.monotonic() < end, job
        time.sleep(0.05)
        job = team(who, "GET", f"/api/jobs/{job['id']}")[1]
    return job


def _a_read_that_waits(monkeypatch):
    """convert_floor as a long read: vision answers one room, says so, and waits to be let
    go before it says how far it has got again."""
    halfway, go_on = threading.Event(), threading.Event()

    def reading(ws, floor_id, *args, say=None, **kw):
        ws.vision["an-answer"] = dict(ANSWER)
        say("vision: 1 of 2 rooms looked at")
        halfway.set()
        go_on.wait(20)
        say("vision: 2 of 2 rooms looked at")  # (where a read asked to stop stops)
        raise AssertionError("a read asked to stop went on")

    monkeypatch.setattr(convert_module, "convert_floor", reading)
    return halfway, go_on


def test_a_read_stopped_leaves_the_floor_as_it_was_and_keeps_what_vision_answered(team, monkeypatch):
    halfway, go_on = _a_read_that_waits(monkeypatch)
    rooms = {r.id: r.geometry for r in team.spaces(team.an0)}
    status, job = team("bob", "POST", f"floors/{team.an0}/convert", {})
    assert status == 200 and job["kind"] == "read"
    assert halfway.wait(20)
    assert team("vera", "POST", f"/api/jobs/{job['id']}/stop", {})[0] == 404  # (she cannot see the Annex)
    status, stopping = team("bob", "POST", f"/api/jobs/{job['id']}/stop", {})
    assert status == 200 and stopping["log"][-1] == "stopping: asked by Bob Annex"
    go_on.set()
    ended = _ended(team, "bob", job)
    assert ended["state"] == "failed" and ended["stopped"]
    assert ended["error"].startswith(f"stopped by Bob Annex: {team.an0} stays as it was")
    assert {r.id: r.geometry for r in team.spaces(team.an0)} == rooms  # the floor as it was
    assert team.studio.store.load(team.code).vision["an-answer"]["type"] == "office"  # what vision answered, kept
    listed = next(j for j in team("bob", "GET", "jobs")[1]["jobs"] if j["id"] == job["id"])
    assert listed["stopped"] and not listed["stopping"]


def test_who_may_read_the_floor_again_may_stop_its_read_and_only_a_read_stops(team, monkeypatch):
    halfway, go_on = _a_read_that_waits(monkeypatch)
    status, job = team("bob", "POST", f"floors/{team.an0}/convert", {})
    assert halfway.wait(20)
    assert team("sara", "POST", f"/api/jobs/{job['id']}/stop", {})[0] == 200  # edit on the whole project
    go_on.set()
    assert "stopped by Sara Ahmed" in _ended(team, "bob", job)["error"]
    # a job that is not a read is not stopped (it would stop where nothing is whole)
    status, exported = team("khalid", "POST", "export", {"building": team.annex})
    assert status == 200
    status, said = team("khalid", "POST", f"/api/jobs/{exported['id']}/stop", {})
    assert status == 400 and "only a read" in said["error"]
