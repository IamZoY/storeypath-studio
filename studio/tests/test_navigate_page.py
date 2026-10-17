"""The Navigate page's own logic (review_app/navigate.js), run in Node on a conformance
package's network: what a way may start and end at (kiosks, then entrances, then every
room, each with its floor), the search over them, and times in words."""

import json
import shutil
import subprocess
import zipfile
from pathlib import Path

import pytest

from storeypath.assets import asset_dir
from storeypath.ids import is_item_id

PAGE = Path(__file__).parent.parent / "src" / "storeypath" / "review_app" / "navigate.js"

IDS = asset_dir("viewer") / "src" / "ids.js"  # served at /viewer/src/ids.js

SCRIPT = r"""
const { register } = await import("node:module");
register("data:text/javascript," + encodeURIComponent(`export async function resolve(s, c, next) {
  return s === "/viewer/src/ids.js" ? { url: ${JSON.stringify(process.argv[2])}, shortCircuit: true } : next(s, c);
}`));
const { choicesOf, search, duration, turnsOf, stepLegs, stepSeconds } = await import(process.argv[1]);
const network = JSON.parse(await new Promise((done) => { let s = ""; process.stdin.on("data", (d) => (s += d)); process.stdin.on("end", () => done(s)); }));
const choices = choicesOf(network);
console.log(JSON.stringify({
  groups: choices.map((c) => c.group),
  kiosk: choices.find((c) => c.group === "kiosk"),
  entrance: choices.find((c) => c.group === "entrance"),
  office: search(choices, "office 112"),
  typed: search(choices, choices[0].id.toLowerCase().replaceAll("-", " ")).map((c) => c.id),
  floor: search(choices, "floor 1 112").map((c) => c.id),
  nothing: search(choices, "no such room"),
  all: search(choices, "  ").length === choices.length,
  times: [duration(42.4), duration(60), duration(89.6), duration(184)],
  turns: {
    left: turnsOf([[0, 0], [10, 0], [10, 10]]),
    right: turnsOf([[0, 0], [0, 10], [10, 10]]),
    jog: turnsOf([[0, 0], [5, 0], [5, 0.3], [10, 0.3]]),
    near: turnsOf([[0, 0], [1, 0], [1, 10]]),
    bend: turnsOf([[0, 0], [10, 0], [20, 8]]),
    two: turnsOf([[0, 0], [10, 0], [10, 1], [20, 1]], { merge: 2.5 }),
  },
  legs: stepLegs({ legs: [{}, {}, {}], steps: [{ kind: "start" }, { kind: "walk" }, { kind: "take" }, { kind: "walk" }, { kind: "take" },
    { kind: "walk" }, { kind: "arrive" }] }),
  seconds: stepSeconds({ seconds: 100, legs: [{}, {}], steps: [{ kind: "start" }, { kind: "walk", metres: 26 }, { kind: "take" },
    { kind: "walk", metres: 13 }, { kind: "arrive" }] }, 1.3),
}));
"""


@pytest.fixture(scope="module")
def page():
    if shutil.which("node") is None:
        pytest.skip("Node.js is not here")
    with zipfile.ZipFile(asset_dir("spec") / "conformance" / "packages" / "campus-hq.storeypath") as z:
        network = json.loads(z.read("navigation.json"))
    out = subprocess.run(["node", "--input-type=module", "-e", SCRIPT, PAGE.as_uri(), IDS.as_uri()], input=json.dumps(network),
                         capture_output=True, text=True, timeout=60, check=True)
    return network, json.loads(out.stdout)


def test_kiosks_then_entrances_then_rooms(page):
    network, got = page
    groups = got["groups"]
    assert groups[0] == "kiosk" and set(groups) == {"kiosk", "entrance", "room"}
    assert groups == sorted(groups, key=["kiosk", "entrance", "room"].index)
    kiosk = got["kiosk"]
    assert kiosk["label"] == "Kiosk in RECEPTION 017" and kiosk["sub"] == f"Ground floor · {kiosk['id']}"
    assert kiosk["node"] == f"kiosk:{kiosk['id']}" and is_item_id(kiosk["id"])  # asked for by its item's ID, its tag
    assert got["typed"] == [kiosk["id"]]  # found by its tag as typed: either case, no hyphens
    assert got["entrance"]["label"].startswith("Entrance into ") and got["entrance"]["id"].startswith("door:")


def test_rooms_are_found_by_name_number_and_floor(page):
    network, got = page
    (office,) = got["office"]
    assert office["label"] == "OFFICE 112" and office["sub"].startswith("Floor 1 · office")
    assert got["floor"] == [office["id"]]
    assert got["nothing"] == [] and got["all"]


def test_times_in_words(page):
    assert page[1]["times"] == ["42 s", "1 min", "1 min 30 s", "3 min"]


def test_turns_along_a_walk_in_words(page):
    turns = page[1]["turns"]
    assert turns["left"] == [{"at": 10, "side": "left", "slight": False}]  # y grows up: east, then north
    assert turns["right"] == [{"at": 10, "side": "right", "slight": False}]
    assert turns["jog"] == [] and turns["near"] == []  # a jog of 30 cm; a turn a metre from the start
    assert turns["bend"] == [{"at": 10, "side": "left", "slight": True}]
    assert turns["two"] == []  # a step aside and on: two turns a metre apart that undo each other


def test_each_step_on_its_leg_with_its_time(page):
    assert page[1]["legs"] == [0, 0, 0, 1, 1, 2, 2]
    assert page[1]["seconds"] == [0, 20, 70, 10, 0]  # walks at the network's pace; the ride what is left


# ---- across a site (format 0.13) ----------------------------------------------------------

SITE_SCRIPT = r"""
const { register } = await import("node:module");
register("data:text/javascript," + encodeURIComponent(`export async function resolve(s, c, next) {
  return s === "/viewer/src/ids.js" ? { url: ${JSON.stringify(process.argv[2])}, shortCircuit: true } : next(s, c);
}`));
const { choicesOf, pointChoice, search, stepLegs, stepSeconds } = await import(process.argv[1]);
const network = JSON.parse(await new Promise((done) => { let s = ""; process.stdin.on("data", (d) => (s += d)); process.stdin.on("end", () => done(s)); }));
const names = new Map(network.floors.map((f) => [f.id, f.id === network.layout ? "Site" : `${f.building_id} · ${f.name}`]));
const choices = choicesOf(network, { floorName: (id) => names.get(id) });
console.log(JSON.stringify({
  groups: choices.map((c) => c.group),
  gates: choices.filter((c) => c.group === "gate"),
  areas: choices.filter((c) => c.group === "area").map((c) => c.label),
  entrances: choices.filter((c) => c.group === "entrance").map((c) => c.id),
  kiosk: choices.find((c) => c.group === "kiosk"),
  bedroom: search(choices, "bedroom").filter((c) => c.group === "room").map((c) => c.sub),
  point: pointChoice(-15.004, -12.0, network.layout),
  legs: stepLegs({ legs: [{}, {}, {}], steps: [{ kind: "start" }, { kind: "walk" }, { kind: "leave" }, { kind: "walk" },
    { kind: "enter" }, { kind: "walk" }, { kind: "arrive" }] }),
  seconds: stepSeconds({ seconds: 40, legs: [{}, {}], steps: [{ kind: "start" }, { kind: "walk", metres: 26 }, { kind: "enter" },
    { kind: "walk", metres: 13 }, { kind: "arrive" }] }, 1.3),
}));
"""


@pytest.fixture(scope="module")
def site_page():
    from storeypath.navigation import site_graph

    if shutil.which("node") is None:
        pytest.skip("Node.js is not here")

    def nav(name):
        with zipfile.ZipFile(asset_dir("spec") / "conformance" / "packages" / f"{name}.storeypath") as z:
            return json.loads(z.read("navigation.json"))

    network = site_graph(nav("site-plot"), [nav(f"site-plot-{b}") for b in ("house", "guest-a", "guest-b")]).nav
    out = subprocess.run(["node", "--input-type=module", "-e", SITE_SCRIPT, PAGE.as_uri(), IDS.as_uri()],
                         input=json.dumps(network), capture_output=True, text=True, timeout=60, check=True)
    return network, json.loads(out.stdout)


def test_across_a_site_kiosks_then_gates_entrances_the_lands_areas_and_rooms(site_page):
    network, got = site_page
    order = ["kiosk", "gate", "entrance", "area", "room"]
    assert set(got["groups"]) == set(order) and got["groups"] == sorted(got["groups"], key=order.index)
    assert sorted(g["label"] for g in got["gates"]) == ["The pedestrian gate", "The vehicle gate"]
    assert all(g["node"] == f"door:{g['id']}" and g["sub"].startswith("Site · gate") for g in got["gates"])
    assert sorted(got["areas"]) == ["GARDEN", "PARKING", "STREET", "The yard"]
    # the buildings' doors to the outside, not the land's side of them
    assert len(got["entrances"]) == 3 and all(i.startswith("door:") for i in got["entrances"])
    assert got["kiosk"]["label"] == "Kiosk in the yard" and got["kiosk"]["sub"].startswith("Site · ")
    assert len(got["bedroom"]) == 2 and all(" · Ground floor · " in s for s in got["bedroom"])  # one each guest house


def test_across_a_site_a_point_and_the_ways_in_and_out(site_page):
    network, got = site_page
    assert got["point"]["id"] == "-15,-12" and got["point"]["group"] == "point" and got["point"]["floor"] == network["layout"]
    assert got["legs"] == [0, 0, 0, 1, 1, 2, 2]  # out of one building, into another: each ends a leg
    assert got["seconds"] == [0, 20, 0, 10, 0]  # (no time through an entrance)
