// The toast: a message for a moment at the foot of the page (every page's #toast). One that
// says what went wrong stays until it is closed (×), and its words can be selected and
// copied: gone after a few seconds, it was often gone before it was read.

let timer;

/** ``message`` shown for ``ms`` (else a moment); ``error``: until it is closed. */
export function toast(message, error = false, ms = undefined) {
  const t = document.getElementById("toast");
  if (!t) return;
  clearTimeout(timer);
  const text = document.createElement("span");
  text.className = "toast-text";
  text.textContent = message;
  t.replaceChildren(text);
  t.classList.toggle("error", error);
  t.setAttribute("role", error ? "alert" : "status");
  t.hidden = false;
  if (error) {
    const close = document.createElement("button");
    close.type = "button";
    close.className = "toast-close";
    close.title = "Close";
    close.setAttribute("aria-label", "Close"); // (its × drawn by ui.css: the message's text is the message alone)
    close.addEventListener("click", () => (t.hidden = true));
    t.append(close);
  } else {
    timer = setTimeout(() => (t.hidden = true), ms ?? 3000);
  }
}
