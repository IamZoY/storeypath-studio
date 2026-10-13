// The command palette (⌘K, Ctrl+K or /): one box to find anything. Rooms (by name, number,
// type or ID), items (by tag, typed loosely: any case, with or without hyphens; a tag on
// another floor is found too), floors, and every command (commands.js) with its key.
// Arrows move, Enter runs, Esc closes. Opened to insert (openInsert: the Place tool, I,
// "Place an item here…"), the same box finds an item type to place: by its name in
// English or Arabic, its code or its category; the types placed lately first. A category
// chosen lists its types alone, shown in the box (a chip): back to every category by the
// chip, the "All categories" row, Backspace in the empty box or Esc.

import { canRun, allCommands, getCommand, whyNot, worksNow } from "./commands.js";
import { $, el, icon, save, saved } from "./dom.js";
import { openFloor } from "./floor.js";
import { findAsset, showAsset } from "./items.js";
import { kbd, keyNow } from "./keys.js";
import { toast } from "./notify.js";
import { select, selectAsset } from "./selection.js";
import { code, color, state, title, typeLabel, typeOf, units, visible } from "./state.js";
import { normalizeItemId } from "/viewer/src/ids.js";
import { CATEGORIES, SIDES, categoryOf, categoryName } from "../categories.js";

// with nothing typed: what was run from here last, then what is most asked for
const SUGGESTED = ["review.start", "tool.place", "tool.wall", "view.3d", "view.walk", "edit.history", "share.export",
  "view.side", "view.labels", "panel.view", "help.keys"];
const recent = () => {
  try {
    return JSON.parse(saved("storeypath.review.recent") || "[]").filter((id) => getCommand(id));
  } catch {
    return [];
  }
};
const remember = (id) => save("storeypath.review.recent", JSON.stringify([id, ...recent().filter((x) => x !== id)].slice(0, 5)));

let dialog = null;
let results = [];
let active = 0;
let returnTo = null;
let mode = "find"; // "find" (⌘K) or "insert" (openInsert): what the box finds
let place = null; // (insert) what choosing a type does: place(code)
let within = null; // (insert) the category chosen: its types alone; null, every category

const placedLately = () => {
  try {
    return JSON.parse(saved("storeypath.review.placed") || "[]");
  } catch {
    return [];
  }
};
const rememberPlaced = (code) => save("storeypath.review.placed", JSON.stringify([code, ...placedLately().filter((c) => c !== code)].slice(0, 8)));

const SETS = "sets"; // (within: every set, by category)

/** (insert) A set's pieces in words: "a desk, a chair, a pedestal", "a table, 8 chairs". */
function piecesSaid(set) {
  const counts = new Map();
  for (const p of set.pieces) {
    const name = (typeOf(p.type)?.name_en || p.type).toLowerCase();
    counts.set(name, (counts.get(name) || 0) + 1);
  }
  return [...counts].map(([name, n]) => (n === 1 ? name : `${n} × ${name}`)).join(", ");
}

/** (insert) What to place: with nothing typed, what was placed lately, every set, then each
 * category (chosen: its sets, then its types); else the sets and types that match, best
 * first. Within a category, its own alone (those that match what is typed), after the way
 * back. A set placed is its pieces, each an item of its own, one group. */
function typeResults(q) {
  const inside = (category) => !within || within === SETS || categoryOf(category).key === within;
  const live = (state.catalogue || []).filter((t) => !t.retired && within !== SETS && inside(t.category));
  const sets = (state.sets || []).filter((s) => inside(s.category));
  const row = (t, group, sc) => ({ group, score: sc, swatch: t.color || "#8a8a8a", title: t.name_en,
    sub: [t.name_ar, categoryName(t.category), `${t.width} × ${t.depth} m`, t.mount && t.mount !== "floor" ? `on the ${t.mount}` : null]
      .filter(Boolean).join(" · "),
    run: () => {
      rememberPlaced(t.code);
      place(t.code);
    } });
  const setRow = (s, group, sc) => ({ group, score: sc, icon: "layers", title: s.name_en,
    sub: [s.name_ar, `${s.pieces.length} items: ${piecesSaid(s)}`].filter(Boolean).join(" · "),
    run: () => {
      rememberPlaced(`set:${s.code}`);
      place({ set: s.code });
    } });
  const setWords = (s) => `${s.name_en} ${s.name_ar || ""} ${s.code} set ${categoryName(s.category)} ${categoryOf(s.category).ar} ${piecesSaid(s)}`;
  if (within) {
    const name = within === SETS ? "Sets" : categoryName(within);
    const back = { group: name, score: 0, icon: "chevron-left", title: "All categories", sub: "Backspace or Esc", keep: true,
      run: () => goBack() };
    const best = (list, words, by) => (q ? list.map((x) => [score(q, words(x)), x]).filter(([sc]) => sc > 0).sort((a, b) => b[0] - a[0])
      .map(([, x]) => x) : by(list));
    const foundSets = best(sets, setWords, (l) => l);
    const found = best(live, (t) => `${t.name_en} ${t.name_ar || ""} ${t.code} ${t.shape || ""}`,
      (l) => [...l].sort((a, b) => a.name_en.localeCompare(b.name_en)));
    return [back, ...foundSets.map((x) => setRow(x, within === SETS ? categoryName(x.category) : "Sets", 1)),
      ...found.map((t) => row(t, foundSets.length ? "Types" : name, 1))];
  }
  if (!q) {
    const lately = placedLately().map((c) => (c.startsWith("set:") ? sets.find((x) => `set:${x.code}` === c)
      : live.find((t) => t.code === c))).filter(Boolean);
    const counts = new Map(), setCounts = new Map();
    for (const t of live) counts.set(categoryOf(t.category).key, (counts.get(categoryOf(t.category).key) || 0) + 1);
    for (const x of sets) setCounts.set(categoryOf(x.category).key, (setCounts.get(categoryOf(x.category).key) || 0) + 1);
    // the categories in their order (one a later format adds, after), under furniture or equipment
    const order = [...CATEGORIES.map((c) => c.key), ...[...counts.keys()].filter((k) => !CATEGORIES.some((c) => c.key === k))];
    const plural = (n, one) => `${n} ${one}${n === 1 ? "" : "s"}`;
    return [...lately.map((x) => (x.pieces ? setRow(x, "Placed lately", 1) : row(x, "Placed lately", 1))),
      ...(sets.length ? [{ group: "Sets", score: 0, icon: "layers", title: "All sets",
        sub: `${plural(sets.length, "set")}: each piece an item of its own, placed together`, keep: true, run: () => goInto(SETS) }] : []),
      ...order.filter((k) => counts.has(k) || setCounts.has(k)).map((k) => {
        const c = categoryOf(k), n = counts.get(k) || 0, m = setCounts.get(k) || 0;
        return { group: SIDES[c.side], score: 0, icon: c.icon, title: c.name,
          sub: [[n ? plural(n, "type") : null, m ? plural(m, "set") : null].filter(Boolean).join(", "), c.ar].filter(Boolean).join(" · "),
          keep: true, run: () => goInto(k) };
      })];
  }
  const found = [...sets.map((x) => [score(q, setWords(x)), setRow, x]),
    ...live.map((t) => [score(q, `${t.name_en} ${t.name_ar || ""} ${t.code} ${categoryName(t.category)} ${categoryOf(t.category).ar} ${t.shape || ""}`), row, t])];
  return found.filter(([sc]) => sc > 0).sort((a, b) => b[0] - a[0]).slice(0, 16)
    .map(([sc, make, x]) => make(x, make === setRow ? "Sets" : "Item types", sc));
}

/** How well ``q`` matches ``text``: its words found at the start of words score most, in
 * order more than anywhere; 0: not at all. */
export function score(q, text) {
  if (!q) return 1;
  const t = (text || "").toLowerCase();
  const words = q.toLowerCase().split(/\s+/).filter(Boolean);
  let total = 0;
  for (const w of words) {
    const i = t.indexOf(w);
    if (i < 0) {
      // the letters in order (a loose typing): little
      let j = 0;
      for (const ch of t) if (ch === w[j]) j++;
      if (j < w.length) return 0;
      total += 1;
      continue;
    }
    total += i === 0 ? 12 : /[\s\-_·(]/.test(t[i - 1]) ? 8 : 4;
    if (t === w) total += 10;
  }
  return total;
}

function sources(q) {
  const out = [];
  const tag = normalizeItemId(q);
  // items: by tag (loosely) on this floor; a whole tag on another floor is looked up
  const loose = q.toLowerCase().replace(/[^a-z0-9]/g, "");
  for (const a of state.floor?.items || []) {
    if (a.retired && !state.showHidden) continue;
    const t = typeOf(a.type);
    const s = loose.length >= 3 && a.id.toLowerCase().replace(/-/g, "").includes(loose) ? 30
      : score(q, `${t?.name_en || a.type} ${a.id}`) / 2;
    if (q && s > 0) out.push({ group: "Items", score: s, icon: "tag", swatch: t?.color, title: t?.name_en || a.type, sub: a.id,
      run: () => {
        selectAsset(a.id);
        showAsset(a);
      } });
  }
  if (tag && !(state.floor?.items || []).some((a) => a.id === tag)) {
    out.push({ group: "Items", score: 40, icon: "tag", title: `Find item ${tag}`, sub: "on any floor of this project",
      run: () => findAsset(tag).then((r) => r.error && toast(r.error, true)) });
  }
  // rooms of the floor shown
  for (const s of units().filter(visible)) {
    const sc = score(q, `${title(s)} ${s.name || ""} ${s.number || ""} ${typeLabel(s.type)} ${code(s.id)} ${s.id}`);
    if (sc > 0 && q) out.push({ group: "Rooms", score: sc + (s.reasons.length ? 0.5 : 0), swatch: color(s.type), title: title(s),
      sub: `${typeLabel(s.type)} · ${code(s.id)}${s.reasons.length ? " · to review" : ""}`, run: () => select(s.id, { fly: true }) });
  }
  // floors
  for (const f of state.project?.floors || []) {
    const sc = score(q, `${f.name} ${f.building} ${f.location} floor`);
    if (sc > 0 && q) out.push({ group: "Floors", score: sc / 1.5, icon: "layers", title: f.name, sub: `${f.location} · ${f.building}${f.review ? ` · ${f.review} to review` : ""}`,
      current: f.id === state.floor?.id, run: () => openFloor(f.id) });
  }
  // commands
  for (const c of allCommands()) {
    if (!c.palette) continue;
    const sc = score(q, `${c.title} ${c.group} ${c.words || ""}`);
    if (sc <= 0) continue;
    const ok = canRun(c);
    out.push({ group: "Commands", id: c.id, score: sc * 1.1 + (ok ? 1 : 0), icon: c.icon || "command", title: c.title, sub: ok ? c.group : whyNot(c),
      keys: keyNow(c.id) && worksNow(c) ? [keyNow(c.id)] : [], disabled: !ok, run: () => { // (its key when it runs it here)
        remember(c.id);
        c.run();
      } });
  }
  return out;
}

const ORDER = ["Rooms", "Items", "Commands", "Floors"];

function render(q) {
  const list = $("palette-list");
  const found = mode === "insert" ? [] : sources(q.trim());
  // without words: the commands, by group; with: the best of each kind, the best kind first
  let shown;
  if (mode === "insert") {
    shown = typeResults(q.trim());
  } else if (!q.trim()) {
    const byId = new Map(found.filter((r) => r.group === "Commands" && !r.disabled).map((r) => [r.id, r]));
    const last = recent().map((id) => byId.get(id)).filter(Boolean).map((r) => ({ ...r, group: "Recent" }));
    const picked = new Set(last.map((r) => r.id));
    shown = [...last, ...SUGGESTED.filter((id) => !picked.has(id)).map((id) => byId.get(id)).filter(Boolean).map((r) => ({ ...r, group: "Suggested" }))];
  }
  else {
    const best = new Map();
    for (const r of found) best.set(r.group, Math.max(best.get(r.group) || 0, r.score));
    const groups = [...best.keys()].sort((a, b) => best.get(b) - best.get(a) || ORDER.indexOf(a) - ORDER.indexOf(b));
    shown = groups.flatMap((g) => found.filter((r) => r.group === g).sort((a, b) => b.score - a.score).slice(0, g === "Rooms" ? 8 : 6));
  }
  results = shown;
  active = Math.min(active, Math.max(0, shown.length - 1));
  const parts = [];
  let group = null;
  shown.forEach((r, i) => {
    if (r.group !== group) {
      group = r.group;
      parts.push(el("li", { class: "pl-group", role: "presentation" }, group));
    }
    const li = el("li", { class: `pl-item${i === active ? " active" : ""}${r.disabled ? " disabled" : ""}`, role: "option", id: `pl-${i}`,
      "aria-selected": String(i === active), "aria-disabled": r.disabled ? "true" : null },
    r.swatch ? el("span", { class: "swatch", style: `background:${r.swatch}` }) : icon(r.icon || "command", { size: 15 }),
    el("span", { class: "pl-text" }, el("span", { class: "pl-title" }, r.title), r.sub ? el("span", { class: "pl-sub" }, r.sub) : null),
    r.current ? el("span", { class: "badge" }, "shown") : null,
    r.keys?.length ? el("span", { class: "pl-keys" }, kbd(r.keys[0])) : null);
    li.addEventListener("pointermove", () => {
      if (active !== i) {
        active = i;
        mark();
      }
    });
    li.addEventListener("click", () => choose(i));
    parts.push(li);
  });
  if (!shown.length) parts.push(el("li", { class: "pl-empty" }, "Nothing found"));
  list.replaceChildren(...parts);
  $("palette-input").setAttribute("aria-activedescendant", shown.length ? `pl-${active}` : "");
}

/** (insert) Into a category: its types alone, the category shown in the box. */
function goInto(category) {
  within = category;
  showWithin();
  const input = $("palette-input");
  input.value = "";
  active = 1; // (its first type, past the way back)
  render("");
  input.focus();
}

/** (insert) Back to every category, from one. */
function goBack() {
  if (!within) return;
  within = null;
  showWithin();
  const input = $("palette-input");
  input.value = "";
  active = 0;
  render("");
  input.focus();
}

/** The chip in the box saying which category is shown (a click goes back), when one is. */
function showWithin() {
  const chip = $("palette-within");
  chip.hidden = !within;
  const name = within === SETS ? "Sets" : within ? categoryName(within) : "";
  chip.querySelector(".pl-chip-text").textContent = name;
  chip.setAttribute("aria-label", within ? `Back to all categories (from ${name})` : "");
}

function mark() {
  for (const li of document.querySelectorAll("#palette-list .pl-item")) {
    const on = li.id === `pl-${active}`;
    li.classList.toggle("active", on);
    li.setAttribute("aria-selected", String(on));
    if (on) li.scrollIntoView({ block: "nearest" });
  }
  $("palette-input").setAttribute("aria-activedescendant", results.length ? `pl-${active}` : "");
}

function choose(i) {
  const r = results[i];
  if (!r || r.disabled) return;
  if (!r.keep) close();
  r.run();
}

export const paletteOpen = () => Boolean(dialog?.open);

export function close() {
  if (!dialog?.open) return;
  dialog.close();
}

export function openPalette() {
  open("find");
}

/** The box opened to find an item type to place: ``pick(code)`` when one is chosen. */
export function openInsert(pick) {
  place = pick;
  open("insert");
}

function open(what) {
  if (dialog?.open) close();
  mode = what;
  if (!dialog) {
    const input = el("input", { id: "palette-input", type: "text", role: "combobox", "aria-expanded": "true", "aria-controls": "palette-list",
      "aria-autocomplete": "list", autocomplete: "off", spellcheck: "false", placeholder: "Find a room, an item's tag, a floor or a command…" });
    const chip = el("button", { id: "palette-within", type: "button", class: "pl-chip", hidden: true, title: "Back to all categories" },
      icon("chevron-left", { size: 14 }), el("span", { class: "pl-chip-text" }));
    chip.addEventListener("click", () => goBack());
    dialog = el("dialog", { class: "dialog palette", "aria-label": "Search and commands" },
      el("div", { class: "pl-search" }, icon("search", { size: 16 }), chip, input, el("kbd", {}, "Esc")),
      el("ul", { id: "palette-list", class: "pl-list", role: "listbox", "aria-label": "Found" }),
      el("div", { class: "pl-foot" }, el("span", {}, kbd("arrowup"), kbd("arrowdown"), " move"), el("span", {}, kbd("enter"), " choose"),
        el("span", { class: "pl-hint" }, "Rooms by name, number, type or ID · items by tag")));
    document.body.append(dialog);
    input.addEventListener("input", () => {
      active = 0;
      render(input.value);
    });
    input.addEventListener("keydown", (e) => {
      if (e.key === "ArrowDown" || e.key === "ArrowUp") {
        e.preventDefault();
        if (!results.length) return;
        active = (active + (e.key === "ArrowDown" ? 1 : results.length - 1)) % results.length;
        mark();
      } else if (e.key === "Enter") {
        e.preventDefault();
        choose(active);
      } else if (e.key === "Escape") {
        e.preventDefault();
        e.stopPropagation();
        if (within) goBack(); // (out of a category first, then closed)
        else close();
      } else if (e.key === "Backspace" && within && !input.value) {
        e.preventDefault();
        goBack();
      }
    });
    dialog.addEventListener("close", () => {
      returnTo?.focus?.({ preventScroll: true });
      returnTo = null;
    });
    dialog.addEventListener("click", (e) => { if (e.target === dialog) close(); }); // the backdrop
  }
  returnTo = document.activeElement;
  const input = $("palette-input");
  input.placeholder = mode === "insert" ? "Find what to place: its name, in English or Arabic, or its kind…"
    : "Find a room, an item's tag, a floor or a command…";
  dialog.setAttribute("aria-label", mode === "insert" ? "Place an item" : "Search and commands");
  dialog.querySelector(".pl-hint").textContent = mode === "insert" ? "Sets, furniture and equipment by name, code or kind"
    : "Rooms by name, number, type or ID · items by tag";
  input.value = "";
  within = null;
  showWithin();
  active = 0;
  render("");
  dialog.showModal();
  input.focus();
}
