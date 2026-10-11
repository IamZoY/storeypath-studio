// node progress.mjs <Studio's address> <project code> <khalid's session> <a floor>: pages opened
// while Studio works (tests/test_review_browser.py holds its jobs, and lets each go when
// this says "release <n>" on its output). The project's page, opened while finding plans
// runs and a reading of the floor waits: the job shown and followed, the floor marked as
// being read, the drawing that waits for khalid to choose what to keep listed. Review,
// opened on the floor while it waits to be read: the floor shown as being read until it
// is. The project's page opened again after: the plans found shown again, to add.

import { dirname, join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const { launch } = await import(pathToFileURL(join(here, "../../../storeypath-viewer/viewer/svg/test/harness.mjs")).href);

const [base, code, khalid, floor] = process.argv.slice(2);
const truly = (v, what) => {
  if (!v) throw new Error(what);
};
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const page = await launch({ timeout: 600 });
const R = (fn, ...args) => page.run(fn, ...args);

async function until(fn, what, ms = 30000, ...args) {
  const end = Date.now() + ms;
  let last;
  while (Date.now() < end) {
    last = await R(fn, ...args);
    if (last) return last;
    await sleep(100);
  }
  throw new Error(`${what} (waited ${ms} ms; last: ${JSON.stringify(last)})`);
}

let failed = false;
try {
  await page.send("Network.setCookie", { name: `sp_session_${new URL(base).port}`, value: khalid, url: base });
  await page.open(`${base}/#/p/${code}`, 1300, 900);

  // the project's page, opened while Studio works
  await until((f) => document.querySelector(`tr[data-floor="${f}"]`), "the project's floors shown", 30000, floor);
  const panel = await until(() => !document.getElementById("job").hidden && document.getElementById("job-title").textContent,
    "the job going on shown");
  truly(/Reading the plans in hq-level-0\.dxf/.test(panel), `the job shown: ${panel}`);
  await until((f) => document.querySelector(`tr[data-floor="${f}"].reading .badge.reading`), "the floor marked as being read", 10000, floor);
  const waiting = await until(() => document.getElementById("waiting")?.textContent, "the drawing waiting listed");
  truly(/Owner villa\.dxf/.test(waiting) && /Choose what to keep/.test(waiting), waiting);
  console.log("release 1");

  // Review, opened on the floor while its reading waits its turn
  await page.open(`${base}/review.html?p=${code}#floor=${encodeURIComponent(floor)}`, 1300, 900);
  await until(() => document.body.classList.contains("ready") && window.storeypathReview.state.floor, "Review shows the floor");
  await until(() => window.storeypathReview.state.converting, "the floor shown as being read");
  const bar = await R(() => document.getElementById("sb-hint")?.textContent || "");
  console.log("release 2");
  await until(() => !window.storeypathReview.state.converting, "the reading over: the floor shown as read", 120000);

  // the project's page opened again: the plans found a moment ago, to add as floors
  await page.open(`${base}/#/p/${code}`, 1300, 900);
  await until(() => document.querySelector('[data-plans="hq-level-0.dxf"] .plan'), "the plans found shown again", 30000);
  const said = await R(() => document.querySelector('[data-plans="hq-level-0.dxf"]').textContent);
  truly(/before this page was opened again/.test(said), said);
  truly(!(await R((f) => document.querySelector(`tr[data-floor="${f}"].reading`), floor)), "no floor marked as being read now");
  // the drawing still waiting: not added, at khalid's word
  await R(() => [...document.querySelectorAll("#waiting button")].find((b) => /Don't add it/.test(b.textContent)).click());
  await until(() => !document.querySelector("#waiting li"), "no drawing waiting now");
  truly(!page.errors.length, `the console said ${JSON.stringify(page.errors)} (hint: ${bar})`);
  console.log("ok: work in progress picked up");
} catch (e) {
  failed = true;
  console.error(`FAILED: ${e.message}`);
} finally {
  await page.close();
}
process.exit(failed ? 1 : 0);
