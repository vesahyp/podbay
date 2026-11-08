// podbay mobile service worker. The shell is fetched from the network
// first and cached for a start without a connection; /api/ and the
// tracker pixel are never cached. Push arrives here too (slice 6): a
// notification opens the app at the line it is about.
var CACHE = "podbay-shell-v2";
var SHELL = ["/", "/index.html", "/app.css", "/app.js", "/config.js", "/track-config.js", "/tracker.js", "/icon.svg", "/manifest.webmanifest"];

self.addEventListener("install", function (event) {
  event.waitUntil(caches.open(CACHE).then(function (c) { return c.addAll(SHELL); }).then(function () { return self.skipWaiting(); }));
});

self.addEventListener("activate", function (event) {
  event.waitUntil(caches.keys().then(function (keys) {
    return Promise.all(keys.filter(function (k) { return k !== CACHE; }).map(function (k) { return caches.delete(k); }));
  }).then(function () { return self.clients.claim(); }));
});

// A push from scripts/notify (the home repo's Web Push sender):
// {title, body, tag, url, at}. iOS withdraws push permission from a web app
// that receives a push and shows nothing, so this always shows one.
self.addEventListener("push", function (event) {
  var data = {};
  try { data = event.data ? event.data.json() : {}; } catch (e) { /* show the default below */ }
  event.waitUntil(self.registration.showNotification(data.title || "podbay", {
    body: data.body || "",
    icon: "/icon-192.png",
    badge: "/icon-192.png",
    tag: data.tag || "podbay",
    timestamp: data.at ? Date.parse(data.at) : Date.now(),
    data: { url: data.url || "/#board" },
  }));
});

// A link on this app opens there: #line/<id> is that line's reply sheet. A
// link to another site opens the board instead, because iOS shows an
// outside URL from here in a sheet that can leave a blank window behind.
self.addEventListener("notificationclick", function (event) {
  event.notification.close();
  var target = (event.notification.data && event.notification.data.url) || "/#board";
  var url = new URL(target, self.location.origin);
  var destination = url.origin === self.location.origin ? url.pathname + url.hash : "/#board";
  event.waitUntil(self.clients.matchAll({ type: "window", includeUncontrolled: true }).then(function (windows) {
    for (var i = 0; i < windows.length; i++) {
      if (windows[i].url.indexOf(self.location.origin) === 0) {
        windows[i].postMessage({ type: "open", hash: url.origin === self.location.origin ? url.hash : "#board" });
        return windows[i].focus();
      }
    }
    return self.clients.openWindow(destination);
  }));
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
