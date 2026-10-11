// What the person may do on a floor: change it (their access lets them), or only look at
// it; and which of its rooms someone else is editing now (many people a floor, one a room:
// theirs to change until they are done). The server refuses changes anyway; the page
// offers nothing that would be refused, and says why.

import { heldByOther } from "../together.js";
import { toast } from "./notify.js";
import { state } from "./state.js";

/** Whether the person's access lets them change a floor (else they may only view it). */
export function editableByAccess(floorId = state.floor?.id) {
  const f = state.project?.floors.find((x) => x.id === floorId);
  return !f || f.can === undefined || f.can === "edit" || f.can === "share";
}

/** Whether the person may change the floor (others editing some of its rooms: the rest). */
export function editable(floorId = state.floor?.id) {
  return editableByAccess(floorId);
}

/** Why the floor shown may not be changed now ("" when it may). */
export function whyNotEditable() {
  return editable() ? "" : "View only: you may look at this floor, not change it";
}

/** True (and says so) when the floor shown may only be looked at. */
export function viewOnly() {
  const why = whyNotEditable();
  if (!why) return false;
  toast(why, true);
  return true;
}

/** A room of the floor shown as people know it: its name and number, else "this room". */
export function roomName(id) {
  const s = state.byId.get(id);
  return (s && [s.name, s.number].filter(Boolean).join(" ")) || "this room";
}

/** Why a room of the floor shown may not be changed now: someone else is editing it ("" when
 * it may). */
export function whyNotRoom(id) {
  const h = heldByOther(id);
  if (!h) return "";
  const since = new Date(h.since).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  return `${h.who.name} is editing ${roomName(id)} (since ${since}): you can change it when they are done`;
}

/** True (and says so) when one of ``rooms`` (IDs; none: nothing) is someone else's now. */
export function roomTaken(...rooms) {
  for (const id of rooms) {
    const why = whyNotRoom(id);
    if (why) {
      toast(why, true);
      return true;
    }
  }
  return false;
}
