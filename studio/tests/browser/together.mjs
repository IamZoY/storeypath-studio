// node together.mjs <Studio's address> <project code> <sara's session> <khalid's session>:
// many people on one floor, one a room (Studio's room locks), in Review in headless Chrome
// (the viewers' harness). Sara's page, on HQ's ground floor: the room Khalid starts editing
// is outlined in his colour, the status bar says who is editing how many rooms, the room
// chosen says it is his in a banner; placing an item in it is refused at once, saying
// whose it is, and placing one in the room beside it is not; once he is done, it is free.
// tests/test_review_browser.py starts the Studio (with accounts) and gives the sessions.

import { dirname, join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const { launch } = await import(pathToFileURL(join(here, "../../../storeypath-viewer/viewer/svg/test/harness.mjs")).href);

const [base, code, sara, khalid] = process.argv.slice(2);
const cookie = `sp_session_${new URL(base).port}`;
const truly = (v, what) => {
  if (!v) throw new Error(what);
};
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const page = await launch({ timeout: 300 });
const R = (fn, ...args) => page.run(fn, ...args);

async function until(fn, what, ms = 15000, ...args) {
  const end = Date.now() + ms;
  let last;
  while (Date.now() < end) {
    last = await R(fn, ...args);
    if (last) return last;
    await sleep(100);
  }
  throw new Error(`${what} (waited ${ms} ms; last: ${JSON.stringify(last)})`);
}

/** A call of Khalid's, as his own page makes it: [status, answer]. */
async function asKhalid(method, path, body) {
  const res = await fetch(`${base}/api/projects/${code}/${path}`, {
    method, body: body ? JSON.stringify(body) : undefined,
    headers: { Cookie: `${cookie}=${khalid}`, "X-StoreyPath": "1", "Content-Type": "application/json", Origin: base },
  });
  return [res.status, await res.json()];
}

/** The offices of the floor shown: [{id, at: its label's point}]. */
const offices = () => R(() => window.storeypathReview.state.floor.spaces.filter((x) => x.name === "OFFICE" && x.label_point)
  .map((s) => ({ id: s.id, at: s.label_point })));

/** Where a point of the plan is on the screen. */
const onScreen = (p) => R((p) => {
  const v = window.storeypathReview.state.view, b = document.getElementById("svg").getBoundingClientRect();
  return [b.left + p[0] * v.k + v.tx, b.top - p[1] * v.k + v.ty];
}, p);

/** (In the page) the message shown now, if any: {text, error}. */
const toastNow = () => {
  const t = document.getElementById("toast");
  return t && !t.hidden ? { text: t.textContent, error: t.classList.contains("error") } : null;
};

let failed = false;
try {
  await page.send("Network.setCookie", { name: cookie, value: sara, url: base });
  await page.open(`${base}/review.html?p=${code}`, 1440, 900);
  await until(() => document.body.classList.contains("ready") && window.storeypathReview.state.floor, "Sara's page shows a floor");
  const [mine, his] = await offices();
  truly(mine && his, "the floor has two offices");
  truly(!(await R(() => document.querySelector("#held path"))), "nobody is editing a room yet");

  // Khalid renames office 011: Sara's page shows it his, at once
  const [status, saved] = await asKhalid("POST", `objects/${his.id}`, { correction: { name: "Khalid's office" } });
  truly(status === 200, `Khalid's change was saved (${status}: ${JSON.stringify(saved)})`);
  await until((id) => document.querySelector(`#held path[data-held="${id}"]`), "his room outlined on Sara's plan", 15000, his.id);
  const bar = await until(() => /Khalid \(1\) editing/.test(document.getElementById("sb-lock").textContent)
    && document.getElementById("sb-lock").textContent, "the status bar says Khalid is editing one room");
  truly(!/You're editing/.test(bar), `Sara edits nothing yet: ${bar}`);

  // his room chosen: the banner says whose it is
  await page.click(...await onScreen(his.at));
  await until((id) => window.storeypathReview.state.selected === id, "his room chosen", 5000, his.id);
  const banner = await until(() => !document.getElementById("lock-banner").hidden && document.getElementById("lock-banner").textContent,
    "the banner shows");
  truly(/Khalid Engineer is editing/.test(banner) && /change it when they are done/.test(banner), banner);

  // placing in his room: refused at once, saying so; in hers: placed, and hers
  await page.click(...await onScreen(mine.at));
  await until(() => document.getElementById("lock-banner").hidden, "her own room chosen: no banner");
  await page.key("i", "KeyI", 73);
  await until(() => document.querySelector(".type-picker"), "the types to place open");
  await R(() => [...document.querySelectorAll(".type-tile")].find((b) => /sofa/i.test(b.textContent)).click());
  const before = await R(() => window.storeypathReview.state.floor.items.length);
  await page.click(...await onScreen(his.at));
  const said = await until(toastNow, "placing in his room says why not");
  truly(said.error && /Khalid Engineer is editing/.test(said.text), JSON.stringify(said));
  await sleep(500);
  truly((await R(() => window.storeypathReview.state.floor.items.length)) === before, "nothing placed in his room");
  await page.click(...await onScreen(mine.at));
  await until((n) => window.storeypathReview.state.floor.items.length === n + 1, "the sofa placed in her room", 8000, before);
  await until(() => /You're editing 1 room/.test(document.getElementById("sb-lock").textContent), "the status bar: she edits one room");
  await page.key("Escape", "Escape", 27);

  // Khalid is done: his room is free on Sara's page
  const [done] = await asKhalid("POST", `floors/${await R(() => window.storeypathReview.state.floor.id)}/release`, {});
  truly(done === 200, "Khalid let his rooms go");
  await until((id) => !document.querySelector(`#held path[data-held="${id}"]`), "his room no longer outlined", 15000, his.id);
  truly(!page.errors.length, `the console said ${JSON.stringify(page.errors)}`);
  console.log("ok: many people on a floor, one a room");
} catch (e) {
  failed = true;
  console.error(`FAILED: ${e.message}`);
} finally {
  await page.close();
}
process.exit(failed ? 1 : 0);
