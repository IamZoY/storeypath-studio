// What Review tells the person: a toast for a moment (what was done, what others did, what
// went wrong), and the line of what is under way (a print drawn, a floor read again).

import { $ } from "./dom.js";
import { toast as show } from "../toast.js";

const listeners = new Set();

/** ``fn(message, error)`` for each toast (the status bar keeps the last error). */
export function onToast(fn) {
  listeners.add(fn);
}

/** ``message`` for a moment; ``error``: until it is closed. */
export function toast(message, error = false) {
  show(message, error, 2500);
  for (const fn of listeners) fn(message, error);
}

let sayer = (text) => {
  const s = $("status");
  if (s) s.textContent = text;
};

/** What is under way, in a line ("" when nothing). */
export function say(text) {
  sayer(text || "");
}

/** Where ``say`` writes (the status bar's job line). */
export function sayWith(fn) {
  sayer = fn;
}
