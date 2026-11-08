// The phone app's service worker (mobile/app/sw.js) against stubbed browser
// APIs: a push shows a notification with the push's link, and a tap opens
// that line in the app, or the board for a link to another site. Headless
// Chromium refuses notifications, so this is where the handlers are proven;
// the phone shows the real notification. Run by `make mobile-test`.
import { readFileSync } from "node:fs";
import vm from "node:vm";

const ORIGIN = "https://app.podbay.tienoo.com";
const source = readFileSync(new URL("../mobile/app/sw.js", import.meta.url), "utf8");

function load(windows) {
  const handlers = {};
  const shown = [];
  const opened = [];
  const self = {
    location: { origin: ORIGIN },
    addEventListener: (name, fn) => { handlers[name] = fn; },
    registration: { showNotification: (title, opts) => { shown.push({ title, ...opts }); return Promise.resolve(); } },
    clients: {
      matchAll: () => Promise.resolve(windows),
      openWindow: (url) => { opened.push(url); return Promise.resolve(); },
      claim: () => Promise.resolve(),
    },
    skipWaiting: () => Promise.resolve(),
  };
  vm.runInNewContext(source, { self, caches: {}, fetch: () => Promise.reject(new Error("no network")), URL, console });
  return { handlers, shown, opened };
}

async function fire(handler, event) {
  let wait = Promise.resolve();
  handler({ ...event, waitUntil: (p) => { wait = p; } });
  await wait;
}

const failures = [];
function check(name, ok, detail) {
  console.log(`${ok ? "ok  " : "FAIL"} ${name}${ok ? "" : "  " + JSON.stringify(detail)}`);
  if (!ok) failures.push(name);
}

// A push as scripts/notify sends it.
{
  const sw = load([]);
  const payload = { title: "Toren: Head Jeeves", body: "sora asks: start line or checkpoint?", tag: "head-jeeves", url: `${ORIGIN}/#line/abc`, at: "2026-11-08T12:00:00+0200" };
  await fire(sw.handlers.push, { data: { json: () => payload } });
  const n = sw.shown[0] || {};
  check("push: shows the title and body", n.title === payload.title && n.body === payload.body, n);
  check("push: keeps the link", n.data && n.data.url === payload.url, n.data);
}

// A push with no payload still shows something (iOS withdraws permission otherwise).
{
  const sw = load([]);
  await fire(sw.handlers.push, { data: null });
  check("push: an empty push still shows a notification", sw.shown.length === 1 && sw.shown[0].title === "podbay", sw.shown);
}

// A tap with the app closed opens the line.
{
  const sw = load([]);
  const notification = { close() {}, data: { url: `${ORIGIN}/#line/abc` } };
  await fire(sw.handlers.notificationclick, { notification });
  check("tap, app closed: opens the line", sw.opened[0] === "/#line/abc", sw.opened);
}

// A tap with the app open focuses it and tells it the line.
{
  const posted = [];
  let focused = false;
  const client = { url: `${ORIGIN}/#board`, postMessage: (m) => posted.push(m), focus: () => { focused = true; return Promise.resolve(); } };
  const sw = load([client]);
  await fire(sw.handlers.notificationclick, { notification: { close() {}, data: { url: `${ORIGIN}/#line/abc` } } });
  check("tap, app open: focuses it and sends the line", focused && posted[0] && posted[0].hash === "#line/abc", posted);
}

// A link to another site opens the board, never the outside page.
{
  const sw = load([]);
  await fire(sw.handlers.notificationclick, { notification: { close() {}, data: { url: "https://claude.ai/artifact/x" } } });
  check("tap, outside link: opens the board", sw.opened[0] === "/#board", sw.opened);
}

process.exit(failures.length ? 1 : 0);
