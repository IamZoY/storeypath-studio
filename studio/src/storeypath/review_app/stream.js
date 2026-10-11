// A page's stream of a project's events (GET /api/projects/<code>/events, web/events.py):
// what changes, who is on which floor, and jobs' progress, as they happen. The browser
// opens it again by itself when it is lost (``reopened`` is then called: what changed
// meanwhile was not heard).
//
// A page hidden for HIDDEN_S (another tab in front, the window minimised) lets its stream
// go, and opens it again when it is seen (``reopened`` then too). Over HTTP/1.1 a browser
// keeps at most 6 connections to a site, for all its tabs, and a stream holds one for as
// long as it is open: six Studio tabs left open took them all, and every other request of
// Studio's (an item placed) waited, unsent, until one was closed.

const HIDDEN_S = 10;

export class ProjectStream {
  /** On a project (``floor``: the floor the page shows); ``ask(path)`` is the page's
   * GET, for a job no word comes of. */
  constructor(code, floor, ask) {
    this.ask = ask;
    this.jobs = new Map(); // job id → its progress as last told
    this.waiting = new Map(); // job id → [resolve]
    this.broken = false;
    this.resting = false; // let go while the page is hidden
    this.closed = false;
    this.reopened = () => {};
    this.heard = []; // [kind, listener]: given to each EventSource opened
    const q = floor ? `?floor=${encodeURIComponent(floor)}` : "";
    this.url = `/api/projects/${encodeURIComponent(code)}/events${q}`;
    this.open();
    this.on("job", (j) => this.heardJob(j));
    this.hiddenTimer = 0;
    this.seen = () => {
      clearTimeout(this.hiddenTimer);
      if (document.hidden) this.hiddenTimer = setTimeout(() => this.rest(), HIDDEN_S * 1000);
      else if (this.resting && !this.closed) {
        this.resting = false;
        this.open();
        this.reopened(); // what changed while it was let go
      }
    };
    document.addEventListener("visibilitychange", this.seen);
    if (document.hidden) this.seen(); // (opened in a tab behind)
  }

  open() {
    this.source = new EventSource(this.url);
    this.source.addEventListener("open", () => {
      if (this.broken) this.reopened();
      this.broken = false;
    });
    this.source.addEventListener("error", () => { this.broken = true; });
    for (const [kind, listener] of this.heard) this.source.addEventListener(kind, listener);
  }

  /** The page hidden a while: its stream let go until it is seen again. */
  rest() {
    if (this.closed || this.resting || !document.hidden) return;
    this.resting = true;
    this.source.close();
  }

  /** ``fn(data)`` for each event of ``kind`` (change, presence, job, deleted). */
  on(kind, fn) {
    const listener = (e) => fn(JSON.parse(e.data));
    this.heard.push([kind, listener]);
    this.source.addEventListener(kind, listener);
    return this;
  }

  close() {
    this.closed = true;
    clearTimeout(this.hiddenTimer);
    document.removeEventListener("visibilitychange", this.seen);
    this.source.close();
    for (const waiting of this.waiting.values()) for (const resolve of waiting) resolve(null);
    this.waiting.clear();
  }

  heardJob(j) {
    const had = this.jobs.get(j.id);
    const job = { ...had, ...j, log: had ? had.log.slice(0, j.from).concat(j.log) : j.log };
    this.jobs.set(j.id, job);
    if (job.state === "done" || job.state === "failed") {
      for (const resolve of this.waiting.get(j.id) || []) resolve(job);
      this.waiting.delete(j.id);
    }
  }

  /** A job followed to its end, its progress said with ``say(lines)`` (its log so far):
   * as the stream tells it, else (no word of it for a while, or no stream: let go while
   * the page is hidden) asked for. Resolves with the job, done or failed. */
  async follow(job, say = () => {}) {
    say(job.log || []);
    let asked = Date.now();
    while (job.state === "waiting" || job.state === "running") {
      const heard = this.jobs.get(job.id);
      if (heard && (heard.state === "done" || heard.state === "failed")) return heard;
      const ended = new Promise((resolve) => this.waiting.set(job.id, [...(this.waiting.get(job.id) || []), resolve]));
      const got = await Promise.race([ended, new Promise((r) => setTimeout(r, 700))]);
      if (got) return got;
      const now = this.jobs.get(job.id);
      if (now) say(now.log);
      if (this.broken || this.source.readyState === EventSource.CLOSED || (!now && Date.now() - asked > 3000)) {
        job = await this.ask(`jobs/${job.id}`); // (as before there was a stream)
        say(job.log);
        asked = Date.now();
      }
    }
    return job;
  }
}
