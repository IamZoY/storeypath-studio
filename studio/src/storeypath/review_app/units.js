// Lengths and areas as the person reads them: metres (the default) or feet and inches,
// remembered in this browser. Only what is shown: Studio keeps metres, and a measure,
// a grid or a scale says its length in either. The site plan and Review's Measure tool.

const KEY = "storeypath.units";
const FOOT = 0.3048, INCH = 0.0254;
const listeners = new Set();

/** "metric" or "imperial". */
export function units() {
  try {
    return localStorage.getItem(KEY) === "imperial" ? "imperial" : "metric";
  } catch {
    return "metric";
  }
}

export function setUnits(u) {
  try {
    localStorage.setItem(KEY, u === "imperial" ? "imperial" : "metric");
  } catch {
    // (remembered for this page alone)
  }
  for (const fn of listeners) fn(units());
}

/** ``fn`` called when the units change; what stops it. */
export function onUnits(fn) {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

/** ``m`` metres as read: "4.25 m", "120.5 m"; or 13' 11", 1,234 ft. */
export function lengthSaid(m) {
  if (units() === "imperial") {
    const inches = Math.round(Math.abs(m) / INCH);
    if (inches >= 12 * 1000) return `${Math.round(Math.abs(m) / FOOT).toLocaleString("en")} ft`;
    const ft = Math.floor(inches / 12), inch = inches % 12;
    return ft ? `${ft}' ${inch}"` : `${inch}"`;
  }
  return `${m < 10 ? m.toFixed(2) : m.toFixed(1)} m`;
}

/** ``m2`` square metres as read: "12.50 m²", or "134.5 ft²". */
export function areaSaid(m2) {
  if (units() === "imperial") {
    const ft2 = m2 / (FOOT * FOOT);
    return `${ft2.toFixed(ft2 < 100 ? 1 : 0)} ft²`;
  }
  return `${m2.toFixed(m2 < 100 ? 2 : 1)} m²`;
}

/** A round step (metres) about ``m`` long in the units read: 1, 2 or 5 of a power of ten
 * metres, or of feet; and how it is said ("50 m", "200 ft"). */
export function roundStep(m) {
  const unit = units() === "imperial" ? FOOT : 1;
  const want = Math.max(m / unit, 1);
  const p = 10 ** Math.floor(Math.log10(want));
  const n = [1, 2, 5, 10].find((k) => k * p >= want) * p;
  return { metres: n * unit, said: `${n.toLocaleString("en")} ${unit === 1 ? "m" : "ft"}` };
}

/** The switch between them: Metres | Feet (a segmented control). */
export function unitsSwitch(el) {
  const box = el("div", { class: "segmented units-switch", role: "radiogroup", "aria-label": "Units" });
  const draw = () => box.replaceChildren(...[["metric", "m"], ["imperial", "ft"]].map(([u, label]) => {
    const b = el("button", { type: "button", role: "radio", "aria-checked": String(units() === u),
      title: u === "metric" ? "Metres" : "Feet and inches" }, label);
    b.addEventListener("click", () => setUnits(u));
    return b;
  }));
  draw();
  const off = onUnits(() => (box.isConnected ? draw() : off())); // (a switch drawn again is a new one)
  return box;
}
