// podbay mobile service worker. The shell is fetched from the network
// first and cached for a start without a connection; /api/ and the
// tracker pixel are never cached. Push arrives here too (slice 6): a
// notification opens the app at the line it is about.
var CACHE = "podbay-shell-v1";
var SHELL = ["/", "/index.html", "/app.css", "/app.js", "/config.js", "/track-config.js", "/tracker.js", "/icon.svg", "/manifest.webmanifest"];

self.addEventListener("install", function (event) {
  event.waitUntil(caches.open(CACHE).then(function (c) { return c.addAll(SHELL); }).then(function () { return self.skipWaiting(); }));
});

self.addEventListener("activate", function (event) {
  event.waitUntil(caches.keys().then(function (keys) {
    return Promise.all(keys.filter(function (k) { return k !== CACHE; }).map(function (k) { return caches.delete(k); }));
  }).then(function () { return self.clients.claim(); }));
});

self.addEventListener("fetch", function (event) {
  var url = new URL(event.request.url);
  if (event.request.method !== "GET" || url.origin !== self.location.origin) return;
  if (url.pathname.indexOf("/api/") === 0 || url.pathname === "/t.gif") return;
  event.respondWith(fetch(event.request).then(function (res) {
    if (res.ok) {
      var copy = res.clone();
      caches.open(CACHE).then(function (c) { c.put(event.request, copy); });
    }
    return res;
  }).catch(function () {
    return caches.match(event.request).then(function (hit) { return hit || caches.match("/index.html"); });
  }));
});
