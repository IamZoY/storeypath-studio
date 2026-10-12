"""Floors read in processes of their own (workers.py): what they read is what Studio reads
itself, a building's floors are read side by side, and a read asked to stop stops."""

import pytest
from shapely.geometry import shape

import storeypath.vision as vision
from storeypath.server import Job, Stopped, Studio
from storeypath.workers import ReadWorkers
from storeypath.workspace import Workspace
from test_reading_robustness import _three_floors


class NoModel:
    name, ready = "none", False

    def available(self):
        return False

    def close(self):
        pass


def _held(ws, floors):
    return sorted((r.id, r.kind, r.type, r.name, r.number, tuple(sorted(r.connects or ())),
                   round(shape(r.geometry).area, 2)) for f in floors for r in ws.floor_objects(f))


def test_floors_read_in_workers_side_by_side_are_as_read_here(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    ws_file, floors = _three_floors(data)
    code = Workspace.load(ws_file).id
    here = Studio(data, model=NoModel(), warm=False, vision=vision.VisionModel(url=""))
    here._convert(code, floors, Job("a", "Converting"))
    first = _held(here.workspace(code), floors)
    assert len(first) > 3 * 20

    there = Studio(data, model=NoModel(), warm=False, vision=vision.VisionModel(url=""), first_start=False,
                   read_workers=2)
    try:
        job = Job("b", "Converting")
        result = there._convert(code, floors, job)
        assert len(result["summaries"]) == 3 and all("0 new, 0 retired" in s for s in result["summaries"])
        assert _held(there.workspace(code), floors) == first  # the same rooms, doors and IDs
        assert any(line.startswith(f"  {floors[1]}: ") for line in job.log)  # (side by side: said by floor)
    finally:
        there.close()


def test_a_read_in_a_worker_asked_to_stop_stops(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    ws_file, floors = _three_floors(data)
    ws = Workspace.load(ws_file)
    workers = ReadWorkers(1)
    try:
        def say(line):
            raise Stopped("stopped by Ann")

        def task(drawn: bool) -> dict:
            return {"ws": ws.model_dump_json(), "folder": str(data), "tiles": None, "reads": None, "drawn": drawn,
                    "models": {"model_url": None, "vision": False, "helpers": None, "symbols": False}}

        read, _, _ = workers.read(floors[0], task(False))  # (kept in the worker)
        assert read.floor_id == floors[0] and len(read.extraction.spaces) > 20
        x0, y0, x1, y1 = read.extraction.spaces[0].polygon.bounds
        ws.floor(floors[0]).edits.walls.append([[x0 + 0.5, y0 - 0.1], [x0 + 0.5, y1 + 0.1]])
        with pytest.raises(Stopped, match="stopped by Ann"):  # (a wall's read says how it was read)
            workers.read(floors[0], task(True), say=say)
        assert workers.read(floors[0], task(True))[0].floor_id == floors[0]  # and the next is read
    finally:
        workers.close()
