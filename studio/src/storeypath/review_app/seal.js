// Files sealed with a password (sealed.py, docs/SEALED-FILES.md): the password asked for,
// a file downloaded sealed with it, and a file sent that may be sealed (its password asked
// for when Studio says it needs one, or the one given was not its).

const PASSWORD_MIN = 8;

function node(tag, attrs = {}, ...children) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === null || v === undefined || v === false) continue;
    if (k.startsWith("on")) e.addEventListener(k.slice(2), v);
    else e.setAttribute(k, v === true ? "" : v);
  }
  e.append(...children.flat().filter((c) => c !== null && c !== undefined && c !== false));
  return e;
}

/** A password, asked for in a dialog (twice when ``confirm``: one to seal a file with);
 * null when the person cancels. ``why``: a line above it (as Studio said the last was
 * not the file's). */
export function askPassword({ title = "Password", confirm = false, why = "", ok = "OK" } = {}) {
  return new Promise((resolve) => {
    const first = node("input", { type: "password", autocomplete: confirm ? "new-password" : "current-password",
      "aria-label": "Password", "data-testid": "seal-password", required: true });
    const again = confirm ? node("input", { type: "password", autocomplete: "new-password", "aria-label": "The password again",
      "data-testid": "seal-password-again", required: true }) : null;
    const said = node("p", { class: "small seal-said", role: "alert" }, why);
    const form = node("form", { method: "dialog", class: "seal-form" },
      node("h2", {}, title),
      confirm ? node("p", { class: "muted small" }, `Whoever has the file needs this password to open it, and Studio cannot `
        + `find it again: keep it somewhere safe, and send it apart from the file. At least ${PASSWORD_MIN} characters.`) : null,
      said,
      node("label", { class: "field" }, "Password", first),
      again ? node("label", { class: "field" }, "Again", again) : null,
      node("div", { class: "row seal-actions" },
        node("button", { type: "button", class: "btn-ghost", value: "cancel", onclick: () => close(null) }, "Cancel"),
        node("button", { type: "submit", class: "primary", "data-testid": "seal-ok" }, ok)));
    const dialog = node("dialog", { class: "seal", "aria-label": title }, form);
    const close = (value) => {
      dialog.close();
      dialog.remove();
      resolve(value);
    };
    form.addEventListener("submit", (e) => {
      e.preventDefault();
      const pw = first.value;
      if (confirm && pw.length < PASSWORD_MIN) {
        said.textContent = `At least ${PASSWORD_MIN} characters.`;
        return first.focus();
      }
      if (confirm && pw !== again.value) {
        said.textContent = "The two are not the same.";
        return again.focus();
      }
      if (!pw) return first.focus();
      close(pw);
    });
    dialog.addEventListener("cancel", (e) => {
      e.preventDefault();
      close(null);
    });
    document.body.append(dialog);
    dialog.showModal();
    first.focus();
  });
}

function save(blob, name) {
  const url = URL.createObjectURL(blob);
  const a = node("a", { href: url, download: name });
  document.body.append(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 60_000);
}

/** The file at ``url`` (an /api/… path whose POST seals it) downloaded sealed with a
 * password asked for, saved as ``name``.enc. False when the person cancelled. */
export async function downloadSealed(url, name, { title = "Download it encrypted" } = {}) {
  const password = await askPassword({ title, confirm: true, ok: "Encrypt and download" });
  if (password === null) return false;
  const res = await fetch(url, { method: "POST", headers: { "X-StoreyPath": "1", "Content-Type": "application/json" },
    body: JSON.stringify({ password }) });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw Object.assign(new Error(data.error || `${res.status} ${res.statusText}`), { status: res.status, data });
  }
  const disposition = res.headers.get("Content-Disposition") || "";
  save(await res.blob(), /filename="([^"]+)"/.exec(disposition)?.[1] || `${name}.enc`);
  return true;
}

/** A file PUT to /api/``path`` that may be sealed: its password asked for when Studio
 * says it needs one (or the one given was not its), and the file sent again with it.
 * Its answer; an error as the pages' api() throws it (status, data); null when the
 * person cancelled. */
const opened = new WeakMap(); // each file sent: the password that opened it (sent again with it)

export async function putFile(path, file) {
  let password = opened.get(file) ?? null;
  for (;;) {
    const headers = { "X-StoreyPath": "1", "Content-Type": "application/octet-stream" };
    if (password !== null) headers["X-StoreyPath-Password"] = encodeURIComponent(password);
    const res = await fetch(`/api/${path}`, { method: "PUT", headers, body: file });
    const data = await res.json().catch(() => ({}));
    if (password !== null && !(res.status === 400 && data.password)) opened.set(file, password);
    if (res.ok) return data;
    if (res.status === 400 && data.password) {
      password = await askPassword({ title: `${file.name || "The file"} is encrypted`,
        why: password === null ? "Its password opens it." : data.error, ok: "Open" });
      if (password === null) return null;
      continue;
    }
    throw Object.assign(new Error(data.error || `${res.status} ${res.statusText}`), { status: res.status, data });
  }
}
