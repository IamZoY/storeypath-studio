// Rooms someone else is editing now (many people a floor, one a room: together.js), on the
// plan: each outlined in its editor's colour (the colour of their mark at the top). When
// the room chosen is one of them, a banner over the plan says whose it is, since when, and
// lets an admin take it over.

import { avatar, heldByOther, hueOf, presence, takeOver } from "../together.js";
import { editableByAccess, roomName } from "./access.js";
import { on } from "./bus.js";
import { $, el, svg } from "./dom.js";
import { pathData, state } from "./state.js";

const clock = (iso) => new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });

/** The room a space or zone of the floor is part of (a zone: its space). */
const roomOf = (id) => {
  const s = id ? state.byId.get(id) : null;
  return s?.kind === "zone" ? s.space_id || s.id : id;
};

/** The rooms others are editing, outlined on the plan. */
export function renderHeld() {
  const layer = $("held");
  if (!layer) return;
  const outlines = [];
  for (const e of state.floor ? presence().editing : []) {
    for (const id of e.rooms) {
      const s = state.byId.get(id);
      if (!s?.geometry) continue;
      const path = svg("path", { d: pathData(s.geometry), class: "held", style: `--hue:${hueOf(e.who)}` });
      path.dataset.held = id;
      outlines.push(path);
    }
  }
  layer.replaceChildren(...outlines);
  renderBanner();
}

/** The banner over the plan: the room chosen is someone else's to change now. */
function renderBanner() {
  const banner = $("lock-banner");
  const room = roomOf(state.selected);
  const h = room && editableByAccess() ? heldByOther(room) : null;
  if (!h) {
    banner.hidden = true;
    return;
  }
  const { me } = presence();
  const take = me?.role === "admin" || me?.local
    ? el("button", { type: "button", class: "btn-sm", "data-tip": "Edit it yourself: they can no longer save changes to it" }, "Take over")
    : null;
  take?.addEventListener("click", () => takeOver(room));
  banner.className = "lock-banner other";
  banner.replaceChildren(...[avatar(h.who, "editing"),
    el("span", { class: "lb-text" }, el("strong", {}, h.who.name),
      ` is editing ${roomName(room)} since ${clock(h.since)}: you can look, and change it when they are done`),
    take].filter(Boolean));
  banner.hidden = false;
}

export function setupHeld() {
  for (const kind of ["floor", "presence", "access"]) on(kind, renderHeld);
  on("selection", renderBanner);
}
