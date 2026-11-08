// podbay mobile (docs/adr/0001-podbay-mobile.md). Plain JS, no build, no
// third-party code. All text goes through esc() into markup; widths are set
// through element.style after render, because the CSP allows no inline style.
(function () {
  "use strict";

  var CFG = window.PODBAY || {};
  var COGNITO = "https://cognito-idp." + (CFG.region || "eu-north-1") + ".amazonaws.com/";
  var AUTH_KEY = "podbay.auth";
  var STATE_EVERY = 30000;
  var MESSAGES_EVERY = 15000;

  var app = { state: null, me: null, messages: [], loadedAt: 0, route: "board", arg: "", sheet: null, error: "" };

  // ---- small helpers --------------------------------------------------------

  function $(sel) { return document.querySelector(sel); }
  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }
  function track(name, props) {
    try { if (window.__clvtracker) window.__clvtracker.track(name, props || {}); } catch (e) { /* never break the app */ }
  }
  function ago(seconds) {
    if (seconds < 60) return Math.max(0, Math.round(seconds)) + " s ago";
    if (seconds < 3600) return Math.round(seconds / 60) + " min ago";
    if (seconds < 86400) return Math.round(seconds / 3600) + " h ago";
    return Math.round(seconds / 86400) + " d ago";
  }
  function until(iso) {
    var t = Date.parse(iso);
    if (!t) return "";
    var s = (t - Date.now()) / 1000;
    if (s <= 0) return "now";
    if (s < 3600) return Math.round(s / 60) + " min";
    if (s < 86400) return Math.round(s / 3600) + " h";
    return Math.round(s / 86400) + " d";
  }
  function hhmm(epoch) {
    var d = new Date(epoch * 1000);
    return String(d.getHours()).padStart(2, "0") + ":" + String(d.getMinutes()).padStart(2, "0");
  }
  function dayName(iso) {
    var d = new Date(iso + "T12:00:00");
    return ["Su", "Mo", "Tu", "We", "Th", "Fr", "Sa"][d.getDay()] + " " + d.getDate();
  }
  function longDay(iso) {
    var d = new Date(iso + "T12:00:00");
    return ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"][d.getDay()] + " " + d.getDate() + " " +
      ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"][d.getMonth()];
  }
  function safeUrl(u) {
    if (!u) return "";
    var s = String(u);
    if (!/^https?:\/\//.test(s)) s = "https://" + s;
    return /^https?:\/\/[^\s"<>]+$/.test(s) ? s : "";
  }
  function key() {
    var a = new Uint8Array(12);
    crypto.getRandomValues(a);
    return Array.prototype.map.call(a, function (b) { return b.toString(16).padStart(2, "0"); }).join("");
  }
  function applyWidths(root) {
    (root || document).querySelectorAll("[data-w]").forEach(function (el) {
      el.style.width = Math.max(0, Math.min(100, Number(el.getAttribute("data-w")) || 0)) + "%";
    });
  }

  // ---- auth (Cognito USER_AUTH with EMAIL_OTP, no SDK) ----------------------

  function loadAuth() {
    try { return JSON.parse(localStorage.getItem(AUTH_KEY) || "null"); } catch (e) { return null; }
  }
  function saveAuth(a) {
    try { if (a) localStorage.setItem(AUTH_KEY, JSON.stringify(a)); else localStorage.removeItem(AUTH_KEY); } catch (e) { /* private mode */ }
  }
  function cognito(target, body) {
    return fetch(COGNITO, {
      method: "POST",
      headers: { "Content-Type": "application/x-amz-json-1.1", "X-Amz-Target": "AWSCognitoIdentityProviderService." + target },
      body: JSON.stringify(body),
    }).then(function (r) {
      return r.json().then(function (j) {
        if (!r.ok) { var e = new Error(j.message || j.__type || "sign-in failed"); e.type = j.__type; throw e; }
        return j;
      });
    });
  }
  function storeResult(res, email, clientId, old) {
    var a = {
      access: res.AccessToken,
      refresh: res.RefreshToken || (old && old.refresh),
      exp: Date.now() + (res.ExpiresIn || 900) * 1000,
      email: email,
      clientId: clientId,
    };
    saveAuth(a);
    return a;
  }
  var refreshing = null;
  function accessToken() {
    var a = loadAuth();
    if (!a || !a.refresh) return Promise.resolve(null);
    if (a.access && a.exp - 60000 > Date.now()) return Promise.resolve(a.access);
    if (!refreshing) {
      refreshing = cognito("InitiateAuth", {
        AuthFlow: "REFRESH_TOKEN_AUTH", ClientId: a.clientId || CFG.clientId, AuthParameters: { REFRESH_TOKEN: a.refresh },
      }).then(function (res) {
        return storeResult(res.AuthenticationResult, a.email, a.clientId || CFG.clientId, a).access;
      }, function () {
        saveAuth(null);
        return null;
      }).finally(function () { refreshing = null; });
    }
    return refreshing;
  }
  function signOut() {
    var a = loadAuth();
    saveAuth(null);
    if (a && a.refresh) cognito("RevokeToken", { ClientId: a.clientId || CFG.clientId, Token: a.refresh }).catch(function () {});
    app.state = null; app.me = null; app.messages = [];
    render();
  }

  // ---- API --------------------------------------------------------------------

  function api(method, path, body) {
    return accessToken().then(function (token) {
      if (!token) { var e = new Error("signed out"); e.status = 401; throw e; }
      return fetch("/api/" + path, {
        method: method,
        headers: Object.assign({ Authorization: "Bearer " + token }, body ? { "Content-Type": "application/json" } : {}),
        body: body ? JSON.stringify(body) : undefined,
      });
    }).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (j) {
        if (r.status === 401) { saveAuth(null); render(); }
        if (!r.ok) { var e = new Error(j.message || j.error || ("HTTP " + r.status)); e.status = r.status; e.body = j; throw e; }
        return j;
      });
    });
  }
  function loadState() {
    return api("GET", "state").then(function (s) {
      app.state = s; app.loadedAt = Date.now(); app.error = "";
      render();
    }, function (e) {
      app.error = e.status === 404 ? "The Mac has not sent its state yet." : (e.status === 401 ? "" : "Could not load: " + e.message);
      render();
    });
  }
  function loadMe() {
    return api("GET", "me").then(function (m) { app.me = m; }, function () {});
  }
  function loadMessages() {
    return api("GET", "messages").then(function (m) { app.messages = m.messages || []; if (app.route === "thread") render(); }, function () {});
  }

  // ---- passkeys -----------------------------------------------------------------

  function b64uToBuf(s) {
    s = s.replace(/-/g, "+").replace(/_/g, "/");
    while (s.length % 4) s += "=";
    var bin = atob(s), out = new Uint8Array(bin.length);
    for (var i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
    return out.buffer;
  }
  function bufToB64u(buf) {
    var bytes = new Uint8Array(buf), bin = "";
    for (var i = 0; i < bytes.length; i++) bin += String.fromCharCode(bytes[i]);
    return btoa(bin).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
  }
  function credJSON(c) {
    if (c.toJSON) { try { return c.toJSON(); } catch (e) { /* fall through */ } }
    var r = c.response, out = { id: c.id, rawId: bufToB64u(c.rawId), type: c.type, clientExtensionResults: {}, response: {} };
    out.response.clientDataJSON = bufToB64u(r.clientDataJSON);
    if (r.attestationObject) out.response.attestationObject = bufToB64u(r.attestationObject);
    if (r.authenticatorData) out.response.authenticatorData = bufToB64u(r.authenticatorData);
    if (r.signature) out.response.signature = bufToB64u(r.signature);
    if (r.userHandle) out.response.userHandle = bufToB64u(r.userHandle);
    if (r.getTransports) out.response.transports = r.getTransports();
    return out;
  }
  function addPasskey() {
    return api("POST", "passkey/register/options").then(function (o) {
      o.challenge = b64uToBuf(o.challenge);
      o.user.id = b64uToBuf(o.user.id);
      (o.excludeCredentials || []).forEach(function (c) { c.id = b64uToBuf(c.id); });
      return navigator.credentials.create({ publicKey: o });
    }).then(function (cred) {
      return api("POST", "passkey/register/verify", { credential: credJSON(cred), device: navigator.platform || "" });
    }).then(function (r) { track("passkey", { step: "added" }); return loadMe().then(function () { return r; }); });
  }
  function checkPasskey() {
    return api("POST", "passkey/auth/options").then(function (o) {
      o.challenge = b64uToBuf(o.challenge);
      (o.allowCredentials || []).forEach(function (c) { c.id = b64uToBuf(c.id); });
      return navigator.credentials.get({ publicKey: o });
    }).then(function (cred) {
      return api("POST", "passkey/auth/verify", { credential: credJSON(cred) });
    }).then(function (r) { track("passkey", { step: "checked" }); return loadMe().then(function () { return r; }); });
  }
  function canSend() {
    return app.me && app.me.can_send_until && app.me.can_send_until * 1000 > Date.now();
  }

  // ---- charts (inline SVG, labelled axes, a unit) -----------------------------

  function niceMax(v) {
    var p = Math.pow(10, Math.floor(Math.log10(Math.max(v, 1)))), f = v / p;
    return (f <= 1 ? 1 : f <= 2 ? 2 : f <= 5 ? 5 : 10) * p;
  }
  function lineChart(o) {
    var w = 340, h = o.h || 100, pl = 36, pb = 16, iw = w - pl, ih = h - pb;
    var pts = o.points.filter(function (p) { return p[1] != null; });
    if (pts.length < 2) return '<p class="none">Not enough samples yet.</p>';
    var t0 = o.t0, t1 = o.t1, max = niceMax(o.max), min = 0;
    function x(t) { return pl + ((t - t0) / (t1 - t0)) * iw; }
    function y(v) { return ih - (Math.max(min, Math.min(max, v)) - min) / (max - min) * ih; }
    var line = pts.map(function (p) { return x(p[0]).toFixed(1) + "," + y(p[1]).toFixed(1); }).join(" ");
    var grid = [min, max / 2, max].map(function (v) {
      return '<line x1="' + pl + '" x2="' + w + '" y1="' + y(v) + '" y2="' + y(v) + '" stroke="#221c18"/><text x="' + (pl - 4) + '" y="' + (y(v) + 3) + '" text-anchor="end">' + Math.round(v) + "</text>";
    }).join("");
    var hours = [];
    for (var k = 0; k <= 4; k++) {
      var t = t0 + (k / 4) * (t1 - t0);
      hours.push('<text x="' + x(t) + '" y="' + (h - 3) + '" text-anchor="' + (k === 0 ? "start" : k === 4 ? "end" : "middle") + '">' + (k === 4 ? "now" : hhmm(t)) + "</text>");
    }
    var lim = o.limit != null && o.limit <= max ? '<line x1="' + pl + '" x2="' + w + '" y1="' + y(o.limit) + '" y2="' + y(o.limit) + '" stroke="#e0201f" stroke-dasharray="4 3"/><text class="warn" x="' + w + '" y="' + (y(o.limit) - 4) + '" text-anchor="end">' + esc(o.limitLabel) + "</text>" : "";
    var last = pts[pts.length - 1];
    return '<svg viewBox="0 0 ' + w + " " + h + '" role="img" aria-label="' + esc(o.label) + '">' + grid +
      '<polygon points="' + pl + "," + ih + " " + line + " " + x(last[0]) + "," + ih + '" fill="' + o.color + '" opacity=".14"/>' +
      '<polyline points="' + line + '" fill="none" stroke="' + o.color + '" stroke-width="1.8" stroke-linejoin="round"/>' + lim + hours.join("") +
      '<circle cx="' + x(last[0]) + '" cy="' + y(last[1]) + '" r="3" fill="' + o.color + '"/></svg>';
  }
  function tableTwin(head, rows) {
    return '<details class="table"><summary>As a table</summary><table><tr>' + head.map(function (h) { return "<th>" + esc(h) + "</th>"; }).join("") + "</tr>" +
      rows.map(function (r) { return "<tr>" + r.map(function (c) { return "<td>" + esc(c) + "</td>"; }).join("") + "</tr>"; }).join("") + "</table></details>";
  }
  var PALETTE = ["#e0201f", "#6aa8f0", "#ffb000", "#6fcf86", "#b88cf0", "#8a8f98"];
  function stacked(runs) {
    var w = 340, h = 160, pl = 30, pb = 16, top = 24, iw = w - pl, ih = h - pb - top;
    var days = runs.days, series = runs.series;
    var totals = days.map(function (_, i) { return series.reduce(function (s, x) { return s + (x.minutes[i] || 0); }, 0); });
    var max = Math.max(10, Math.ceil(Math.max.apply(null, totals) / 10) * 10), step = iw / days.length, bw = step - 10, s = "";
    [0, max / 2, max].forEach(function (v) {
      var y = top + ih - (v / max) * ih;
      s += '<line x1="' + pl + '" x2="' + w + '" y1="' + y + '" y2="' + y + '" stroke="#221c18"/><text x="' + (pl - 4) + '" y="' + (y + 3) + '" text-anchor="end">' + Math.round(v) + "</text>";
    });
    days.forEach(function (d, i) {
      var acc = 0;
      series.forEach(function (x, k) {
        var v = x.minutes[i] || 0;
        if (!v) return;
        var y = top + ih - ((acc + v) / max) * ih;
        s += '<rect x="' + (pl + i * step + 5) + '" y="' + y + '" width="' + bw + '" height="' + Math.max(1, (v / max) * ih - 1) + '" fill="' + PALETTE[k % PALETTE.length] + '" opacity=".85"/>';
        acc += v;
      });
      s += '<text x="' + (pl + i * step + step / 2) + '" y="' + (h - 2) + '" text-anchor="middle">' + esc(dayName(d).split(" ")[0]) + "</text>";
    });
    s += '<text x="' + (pl - 4) + '" y="10" text-anchor="end">min</text>';
    var lx = pl;
    series.forEach(function (x, k) {
      var name = x.name.length > 12 ? x.name.slice(0, 11) + "…" : x.name;
      if (lx + 14 + name.length * 6.4 > w) return;
      s += '<rect x="' + lx + '" y="2" width="9" height="9" rx="2" fill="' + PALETTE[k % PALETTE.length] + '"/><text x="' + (lx + 12) + '" y="10">' + esc(name) + "</text>";
      lx += 14 + name.length * 6.4 + 8;
    });
    return '<svg viewBox="0 0 ' + w + " " + h + '" role="img" aria-label="pipeline minutes per night">' + s + "</svg>";
  }

  // ---- screens -----------------------------------------------------------------

  function replyBtn(line, label, prefill) {
    return '<button class="btn reply" type="button" data-reply="' + esc(line) + '"' + (prefill ? ' data-prefill="' + esc(prefill) + '"' : "") + ">" + esc(label || "Reply") + "</button>";
  }
  function section(cls, title, items, empty) {
    return '<h2 class="' + cls + '">' + esc(title) + ' <span class="n">' + items.length + "</span></h2>" + (items.length ? items.join("") : (empty ? '<p class="none">' + esc(empty) + "</p>" : ""));
  }
  function steps(list) {
    return list && list.length ? '<ol class="steps">' + list.map(function (s) { return "<li>" + esc(s) + "</li>"; }).join("") + "</ol>" : "";
  }
  function head(item) {
    var t = item.target && item.target.indexOf(item.project) >= 0 ? item.target.replace(item.project, "").trim() : "";
    return '<div class="repo">' + esc(item.project) + (t ? '<span class="t">' + esc(t) + "</span>" : "") + "</div>";
  }
  function board(s) {
    var sec = s.board.sections || {};
    var ask = (sec.decision || []).map(function (d) {
      return '<article class="card ask" id="line-' + esc(d.id) + '">' + head(d) + '<p class="line">' + esc(d.text) + "</p>" +
        (safeUrl(d.link) ? '<a class="link" href="' + esc(safeUrl(d.link)) + '" target="_blank" rel="noopener">' + esc(safeUrl(d.link)) + "</a>" : "") +
        '<div class="row">' + (d.options || []).map(function (o) { return '<button class="btn opt" type="button" data-reply="' + esc(d.id) + '" data-option="' + esc(o) + '">' + esc(o) + "</button>"; }).join("") +
        replyBtn(d.id, (d.options || []).length ? "Other" : "Answer") + "</div></article>";
    });
    var test = (sec.shipped || []).map(function (d) {
      return '<article class="card" id="line-' + esc(d.id) + '">' + head(d) + '<p class="line">' + esc(d.text) + "</p>" +
        (safeUrl(d.link) ? '<a class="link" href="' + esc(safeUrl(d.link)) + '" target="_blank" rel="noopener">' + esc(safeUrl(d.link)) + "</a>" : "") + steps(d.steps) +
        '<div class="row">' + replyBtn(d.id, "Works", "Works") + replyBtn(d.id, "Found a fault", "Found a fault: ") + replyBtn(d.id) + "</div></article>";
    });
    var done = (sec.finished || []).map(function (d) {
      return '<article class="card" id="line-' + esc(d.id) + '">' + head(d) + '<p class="line">' + esc(d.text) + "</p>" +
        (safeUrl(d.link) ? '<a class="link" href="' + esc(safeUrl(d.link)) + '" target="_blank" rel="noopener">' + esc(safeUrl(d.link)) + "</a>" : "") +
        '<div class="row">' + replyBtn(d.id) + "</div></article>";
    });
    var prog = (sec.progress || []).map(function (d) {
      return '<article class="card" id="line-' + esc(d.id) + '"><div class="mini">' + head(d) + (d.when ? '<span class="chip amber">' + esc(d.when) + "</span>" : "") + "</div>" +
        '<p class="line">' + esc(d.text) + '</p><div class="row">' + replyBtn(d.id) + "</div></article>";
    });
    var now = new Date();
    return "<h1>" + esc(["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"][now.getDay()]) + "</h1>" +
      '<p class="sub">' + esc(s.board.summary) + "</p>" +
      section("ask", "Decisions for you", ask, "Nothing waits on you.") +
      section("", "Ready for you to test", test, "Nothing new to test today.") +
      (done.length ? section("", "Finished today", done) : "") +
      section("", "In progress", prog, "Nothing running.") +
      '<div class="row gap"><button class="btn primary wide" type="button" data-new-session="1">Start a session</button></div>';
  }

  function machine(s) {
    var m = s.machine || {}, h = m.history || [];
    var t1 = s.generated_at, t0 = h.length ? h[0].at : t1 - 3600;
    var power = m.power ? (m.power.percent != null ? m.power.percent + '<small>% · ' + esc(m.power.source === "cable" ? "on cable" : "on battery") + "</small>" : esc(m.power.source || "?")) : '<small>no battery</small>';
    var docker = m.docker_up == null ? '<div class="big">–<small>no docker</small></div>' : m.docker_up ? '<div class="big up">up</div>' : '<div class="big down">down</div>';
    var overloaded = m.overloaded ? '<article class="card warn"><div class="repo">Overloaded</div><p class="line">' + esc(m.overloaded) + "</p></article>" : "";
    var agents = (m.agents || []).map(function (a) {
      var chip = a.running ? '<span class="chip green">running</span>' : a.last_exit ? '<span class="chip red">exit ' + esc(a.last_exit) + "</span>" : a.loaded ? '<span class="chip">idle</span>' : '<span class="chip red">not loaded</span>';
      return '<div class="mini qline"><span class="k">' + esc(a.label) + "</span>" + chip + "</div>";
    }).join("");
    var accounts = (s.accounts || []).map(function (a) {
      var rows = "";
      if (a.logged_out) rows += '<div class="qrow"><span class="k">Login</span><div class="bar"><b class="hot" data-w="100"></b></div><span class="r">logged out</span></div>';
      else if (a.login_ends_at) {
        var days = (Date.parse(a.login_ends_at) - Date.now()) / 86400000;
        rows += '<div class="qrow"><span class="k">Login</span><div class="bar"><b class="' + (days < 1 ? "hot" : days < 3 ? "warn" : "") + '" data-w="' + Math.round(Math.max(0, Math.min(30, days)) / 30 * 100) + '"></b></div><span class="r">ends in ' + esc(until(a.login_ends_at)) + "</span></div>";
      }
      (a.windows || []).forEach(function (wnd) {
        var p = wnd.pct || 0;
        rows += '<div class="qrow"><span class="k">' + esc(wnd.name) + '</span><div class="bar"><b class="' + (p >= 85 ? "hot" : p >= 60 ? "warn" : "") + '" data-w="' + p + '"></b></div><span class="r">' + Math.round(p) + "% · " + esc(until(wnd.resets_at) || "?") + "</span></div>";
      });
      var chip = a.logged_out ? '<span class="chip red">logged out</span>' : a.suggested ? '<span class="chip green">use this one</span>' : "";
      return '<div class="acct"><span>' + esc(a.account) + "</span>" + chip + "</div>" + rows;
    }).join("");
    var loadPts = h.map(function (p) { return [p.at, p.load]; }), freePts = h.map(function (p) { return [p.at, p.free]; }), diskPts = h.map(function (p) { return [p.at, p.disk]; });
    var maxLoad = Math.max(m.overload_at || 16, Math.max.apply(null, loadPts.map(function (p) { return p[1] || 0; }).concat([1])));
    var maxFree = Math.max(1024, Math.max.apply(null, freePts.map(function (p) { return p[1] || 0; }).concat([1])));
    var maxDisk = Math.max((m.disk_low_gb || 15) * 2, Math.max.apply(null, diskPts.map(function (p) { return p[1] || 0; }).concat([1])));
    var needs = m.overloaded || (m.disk_free_gb != null && m.disk_free_gb <= m.disk_low_gb) || m.docker_up === false || (m.power && m.power.source === "battery");
    return "<h1>Machine</h1><p class=\"sub\">" + esc(s.mac || "The Mac") + ". " + (needs ? "Something needs a look." : "Nothing needs you.") + "</p>" + overloaded +
      '<div class="grid2"><article class="card"><div class="label">Power</div><div class="big">' + power + "</div></article>" +
      '<article class="card"><div class="label">Docker</div>' + docker + "</article></div>" +
      "<h2>Last 24 hours</h2>" +
      '<article class="card chartcard"><h3>CPU load</h3><p class="q">1 minute load average. Over the red line podbay calls the Mac overloaded.</p>' +
      lineChart({ points: loadPts, t0: t0, t1: t1, max: maxLoad, limit: m.overload_at, limitLabel: "overloaded", color: "#ffb000", unit: "load", label: "CPU load, 24 hours" }) +
      tableTwin(["time", "load"], h.filter(function (_, i) { return i % 6 === 0; }).map(function (p) { return [hhmm(p.at), p.load == null ? "" : p.load.toFixed(1)]; })) + "</article>" +
      '<article class="card chartcard"><h3>Free memory</h3><p class="q">MB free. Under the red line with swap in use, the Mac is starved.</p>' +
      lineChart({ points: freePts, t0: t0, t1: t1, max: maxFree, limit: m.mem_low_mb, limitLabel: (m.mem_low_mb || 400) + " MB", color: "#6fcf86", unit: "MB", label: "Free memory, 24 hours" }) + "</article>" +
      '<article class="card chartcard"><h3>Disk free</h3><p class="q">GB free on the data volume. Under the red line the nightly jobs start to fail.</p>' +
      lineChart({ points: diskPts, t0: t0, t1: t1, max: maxDisk, limit: m.disk_low_gb, limitLabel: (m.disk_low_gb || 15) + " GB", color: "#6aa8f0", unit: "GB", label: "Disk free, 24 hours" }) + "</article>" +
      (agents ? '<h2>Background jobs</h2><article class="card">' + agents + "</article>" : "") +
      '<h2>Claude accounts</h2><article class="card">' + (accounts || '<p class="none">No accounts.</p>') + "</article>" +
      '<div class="row gap">' + replyBtn("machine", "Ask Head Jeeves about the Mac") + "</div>";
  }

  var CELL_WORD = { ok: "passed", fail: "failed", skip: "skipped", miss: "did not run", pending: "later today", none: "not scheduled", unknown: "no record" };
  function nightly(s, sel) {
    var n = s.nightly || { days: [], jobs: [] };
    var last = n.days[n.days.length - 1], ran = 0, failed = [], missed = 0;
    n.jobs.forEach(function (j) {
      var c = j.cells[j.cells.length - 1] || {};
      if (c.state === "ok" || c.state === "skip") ran++;
      if (c.state === "fail") failed.push(j.label);
      if (c.state === "miss") missed++;
    });
    var summary = n.jobs.length ? "Today: " + ran + " passed" + (failed.length ? ", " + failed.join(", ") + " failed" : "") + (missed ? ", " + missed + " did not run" : "") + ". Tap a night for its log." : "No cronrun jobs in the crontab.";
    var g = '<div class="ng"><span></span>' + n.days.map(function (d) { return '<span class="h">' + esc(dayName(d)) + "</span>"; }).join("");
    n.jobs.forEach(function (j) {
      g += '<span class="j">' + esc(j.label) + "<small>" + esc(j.at) + "</small></span>";
      j.cells.forEach(function (c) {
        var isSel = sel && sel[0] === j.label && sel[1] === c.day;
        g += '<button type="button" class="cell ' + esc(c.state) + (isSel ? " sel" : "") + '" data-cell="' + esc(j.label) + "|" + esc(c.day) + '" aria-label="' + esc(j.label + " " + longDay(c.day) + ": " + CELL_WORD[c.state]) + '"></button>';
      });
    });
    g += "</div>";
    var runs = s.runs && s.runs.series && s.runs.series.length ? '<article class="card chartcard"><h3>Where the pipeline time goes</h3><p class="q">clavesa run minutes per night, by workspace</p>' + stacked(s.runs) +
      tableTwin(["night"].concat(s.runs.series.map(function (x) { return x.name; })), s.runs.days.map(function (d, i) { return [d].concat(s.runs.series.map(function (x) { return String(x.minutes[i]); })); })) + "</article>" : "";
    var page = s.runs && s.runs.page ? '<div class="row gap"><a class="btn wide" href="' + esc(safeUrl(s.runs.page)) + '" target="_blank" rel="noopener">Open the full runs chart</a></div>' : "";
    return "<h1>Nightly runs</h1><p class=\"sub\">" + esc(summary) + "</p>" +
      '<article class="card chartcard"><h3>Every job, the last 7 nights</h3><p class="q">One cell per scheduled run, today on the right</p>' + g +
      '<div class="legend"><span class="ok">passed</span><span class="fail">failed</span><span class="skip">skipped</span><span class="miss">did not run</span><span class="unknown">no record</span><span>not scheduled</span></div></article>' +
      runs + page;
  }

  var STATE_WORD = { queued: "queued", delivering: "sending", delivered: "delivered", answered: "answered", expired: "not sent" };
  function thread() {
    var list = app.messages;
    if (!list.length) return '<h1>Thread</h1><p class="sub">Everything you send, and what Head Jeeves did with it.</p><p class="none">Nothing sent yet.</p>';
    return '<h1>Thread</h1><p class="sub">Everything you send, and what Head Jeeves did with it.</p>' + list.map(function (m) {
      var when = m.state === "delivered" && m.delivered_at ? "delivered " + hhmm(m.delivered_at) : (STATE_WORD[m.state] || m.state) + " " + hhmm(m.created);
      var answer = m.reply ? '<div class="hj"><div class="who"><span>Head Jeeves</span><span>' + (m.replied_at ? hhmm(m.replied_at) : "") + "</span></div>" + esc(m.reply) + "</div>"
        : m.state === "expired" ? '<div class="hj bad"><div class="who"><span class="bad">Not sent: the Mac did not take it within 2 hours</span></div><div class="row"><button class="btn" type="button" data-again="' + esc(m.id) + '">Send again</button></div></div>'
        : m.state === "queued" ? '<div class="hj"><div class="who"><span>Waiting for the Mac</span></div></div>' : "";
      return '<div class="msg"><div class="me"><div class="who"><span>to ' + esc(m.target) + "</span><span>" + esc(when) + "</span></div>" +
        (m.quote ? '<p class="about">on “' + esc(m.quote) + "”</p>" : "") + esc(m.text) + "</div>" + answer + "</div>";
    }).join("");
  }

  // ---- sheets -------------------------------------------------------------------

  function lineInfo(id) {
    var s = app.state || {};
    if (id && id.indexOf("new-session:") === 0) return { target: "new session in " + id.slice(12), quote: "" };
    return (s.lines || {})[id] || null;
  }
  function findItem(id) {
    var sec = (app.state && app.state.board.sections) || {};
    var all = [].concat(sec.decision || [], sec.shipped || [], sec.finished || [], sec.progress || []);
    for (var i = 0; i < all.length; i++) if (all[i].id === id) return all[i];
    return null;
  }
  function sendBlock() {
    if (canSend()) return '<div class="row"><button class="btn primary wide" type="button" data-send="1">Send to Head Jeeves</button></div>';
    if (app.me && app.me.passkeys > 0) return '<div class="row"><button class="btn primary wide" type="button" data-send="1">Confirm with your passkey and send</button></div>' +
      '<p class="fine">Sending needs a passkey check once every 30 days on this sign-in.</p>';
    if (app.me && app.me.enrol_open) return '<div class="row"><button class="btn primary wide" type="button" data-add-passkey="1">Add a passkey to send</button></div>' +
      '<p class="fine">The Mac opened a passkey window. Add one now; it lets this phone send for 30 days at a time.</p>';
    return '<p class="err">Sending needs a passkey, and one can be added only from the Mac. On the Mac, run: podbay mobile passkey-allow. Then open this sheet again.</p>';
  }
  function replySheet(id, prefill, option) {
    var info = lineInfo(id), item = findItem(id);
    if (!info) return '<button class="scrim" type="button" data-close="1" aria-label="Close"></button><section class="sheet" role="dialog"><div class="grab"></div><p class="err">That line is no longer on the board.</p><div class="row"><button class="btn wide" type="button" data-close="1">Close</button></div></section>';
    var opts = item && item.options && item.options.length ? '<div class="opts">' + item.options.map(function (o) {
      return '<button class="btn opt' + (o === option ? " on" : "") + '" type="button" data-pick="' + esc(o) + '">' + esc(o) + "</button>";
    }).join("") + "</div>" : "";
    var newSession = id === "new-session:" ? true : false;
    var repos = newSession ? '<label class="f" for="repo">Repo</label><select id="repo">' + ((app.state && app.state.repos) || []).map(function (r) { return '<option value="' + esc(r) + '">' + esc(r) + "</option>"; }).join("") + "</select>" : "";
    var title = newSession ? "Start a session" : info.target;
    return '<button class="scrim" type="button" data-close="1" aria-label="Close"></button><section class="sheet" role="dialog" aria-label="Message">' +
      '<div class="grab"></div><div class="mini"><div class="repo">' + esc(title) + '</div><button class="btn ghost" type="button" data-close="1">Close</button></div>' +
      (info.quote ? '<div class="quote">' + esc(info.quote) + "</div>" : "") + opts + repos +
      '<label class="f" for="text">' + (opts ? "Or write the answer" : newSession ? "What the session should do" : "Your message") + "</label>" +
      '<textarea id="text" maxlength="1000" placeholder="One line for Head Jeeves">' + esc(option || prefill || "") + "</textarea>" +
      sendBlock() + '<p class="err" id="sheet-err" hidden></p>' +
      '<p class="fine">Reaches Head Jeeves in about 15 s' + (info.target && !newSession ? " and goes to " + esc(info.target) : "") + ' with the line quoted. If the Mac does not take it within 2 hours, it is not sent and the Thread tab says so.</p></section>';
  }
  function logSheet(label, day) {
    var job = ((app.state && app.state.nightly.jobs) || []).filter(function (j) { return j.label === label; })[0];
    var c = job && job.cells.filter(function (x) { return x.day === day; })[0];
    if (!c) return "";
    var chip = c.state === "fail" ? '<span class="chip red">failed · exit ' + esc(c.rc) + "</span>" : c.state === "ok" ? '<span class="chip green">passed</span>' : '<span class="chip">' + esc(CELL_WORD[c.state]) + "</span>";
    var kv = c.started ? '<div class="kv"><span>started</span><b>' + esc(String(c.started).slice(11, 19)) + "</b><span>took</span><b>" + (c.elapsed != null ? Math.floor(c.elapsed / 60) + " min " + (c.elapsed % 60) + " s" : "?") + "</b>" + (c.status ? "<span>status</span><b>" + esc(c.status) + "</b>" : "") + "</div>" : "";
    var log = c.log && c.log.length ? '<pre class="log">' + esc(c.log.join("\n")) + "</pre>" : c.started ? '<p class="none">The run wrote no log lines.</p>' : "";
    var line = "nightly:" + label + ":" + day;
    var ask = lineInfo(line) ? '<div class="row">' + replyBtn(line, "Ask Head Jeeves about this run") + "</div>" : "";
    return '<button class="scrim" type="button" data-close="1" aria-label="Close"></button><section class="sheet" role="dialog" aria-label="Run log"><div class="grab"></div>' +
      '<div class="mini"><div class="repo">' + esc(label) + '<span class="t">' + esc(longDay(day)) + "</span></div>" + chip + "</div>" + kv + log + ask +
      '<div class="row"><button class="btn ghost wide" type="button" data-close="1">Close</button></div></section>';
  }

  // ---- render and routing ---------------------------------------------------------

  var ICONS = {
    board: '<svg viewBox="0 0 24 24"><rect x="4" y="4" width="16" height="16" rx="3"/><path d="M8 9h8M8 13h8M8 17h5"/></svg>',
    machine: '<svg viewBox="0 0 24 24"><rect x="3" y="5" width="18" height="12" rx="2"/><path d="M8 21h8M12 17v4"/><path d="M6 13l3-3 3 2 4-4 2 2"/></svg>',
    nightly: '<svg viewBox="0 0 24 24"><path d="M20 14.5A8 8 0 0 1 9.5 4a8 8 0 1 0 10.5 10.5z"/></svg>',
    thread: '<svg viewBox="0 0 24 24"><path d="M4 5h16v11H9l-5 4z"/></svg>',
  };
  function tabs(on) {
    var s = app.state, dec = s ? (s.board.sections.decision || []).length : 0, bad = 0;
    if (s) s.nightly.jobs.forEach(function (j) { var c = j.cells[j.cells.length - 1]; if (c && (c.state === "fail" || c.state === "miss")) bad++; });
    var queued = app.messages.filter(function (m) { return m.state === "expired"; }).length;
    return [["board", "Board", dec ? '<span class="badge amber">' + dec + "</span>" : ""], ["machine", "Machine", s && s.machine.overloaded ? '<span class="badge">!</span>' : ""],
      ["nightly", "Nightly", bad ? '<span class="badge">' + bad + "</span>" : ""], ["thread", "Thread", queued ? '<span class="badge">' + queued + "</span>" : ""]]
      .map(function (t) { return '<button class="tab' + (t[0] === on ? " on" : "") + '" type="button" data-tab="' + t[0] + '" aria-current="' + (t[0] === on ? "page" : "false") + '">' + ICONS[t[0]] + t[1] + t[2] + "</button>"; }).join("");
  }
  function freshness() {
    var el = $("#fresh");
    if (!app.state) { el.textContent = "…"; return; }
    var age = Date.now() / 1000 - app.state.generated_at;
    el.textContent = (app.state.mac || "Mac") + " · " + ago(age);
    el.className = "fresh" + (age > 900 ? " red" : age > 300 ? " amber" : "");
  }
  function signInScreen(stage, email, err) {
    return '<section class="signin"><div class="eye"></div><h1>PODBAY</h1><p class="sub">Sign in with a code sent to your email.</p>' +
      (stage === "code"
        ? '<form id="code-form"><label class="f" for="code">The code from the mail to ' + esc(email) + '</label><input id="code" type="text" name="code" inputmode="numeric" autocomplete="one-time-code" required><button class="btn primary" type="submit">Sign in</button><button class="btn ghost" type="button" data-restart="1">Use another address</button></form>'
        : '<form id="email-form"><label class="f" for="email">Email</label><input id="email" type="email" name="email" autocomplete="email" required><button class="btn primary" type="submit">Send the code</button></form>') +
      (err ? '<p class="err">' + esc(err) + "</p>" : "") + "</section>";
  }

  var signin = { stage: "email", email: "", session: "", err: "" };
  function render() {
    try {
      var signedIn = !!(loadAuth() || {}).refresh;
      $("#top").hidden = !signedIn;
      $("#tabs").hidden = !signedIn;
      if (!signedIn) {
        $("#main").innerHTML = signInScreen(signin.stage, signin.email, signin.err);
        $("#overlay").innerHTML = "";
        return;
      }
      freshness();
      $("#tabs").innerHTML = tabs(app.route);
      var s = app.state, html;
      if (!s) html = '<p class="sub boot">' + esc(app.error || "Loading…") + "</p>";
      else if (app.route === "machine") html = machine(s);
      else if (app.route === "nightly") html = nightly(s, app.sheet && app.sheet.kind === "log" ? [app.sheet.label, app.sheet.day] : null);
      else if (app.route === "thread") html = thread();
      else html = board(s);
      if (app.error && s) html = '<p class="err">' + esc(app.error) + "</p>" + html;
      html += '<div class="row gap"><button class="btn ghost" type="button" data-signout="1">Sign out</button></div>';
      var keep = window.scrollY;
      $("#main").innerHTML = html;
      window.scrollTo(0, keep);
      var sheet = "";
      if (app.sheet && s) sheet = app.sheet.kind === "log" ? logSheet(app.sheet.label, app.sheet.day) : replySheet(app.sheet.line, app.sheet.prefill, app.sheet.option);
      var overlay = $("#overlay"), hadText = $("#text") ? $("#text").value : null;
      if (overlay.getAttribute("data-sheet") !== JSON.stringify(app.sheet) || !app.sheet) {
        overlay.innerHTML = sheet;
        overlay.setAttribute("data-sheet", JSON.stringify(app.sheet));
      } else if (hadText != null && $("#text")) {
        $("#text").value = hadText;
      }
      applyWidths();
    } catch (e) {
      track("error", { msg: String(e && e.message || e).slice(0, 200), type: "boundary" });
      $("#main").innerHTML = '<p class="err">Something broke while drawing this screen. Reload the app.</p>';
    }
  }
  function go(route, arg) {
    app.route = route; app.arg = arg || "";
    var hash = "#" + route + (arg ? "/" + arg : "");
    if (location.hash !== hash) history.replaceState(null, "", hash);
    track("tab", { name: route });
    window.scrollTo(0, 0);
    render();
  }
  function fromHash() {
    var h = decodeURIComponent((location.hash || "#board").slice(1)), parts = h.split("/");
    app.sheet = null;
    if (parts[0] === "line" && parts[1]) {
      app.route = "board";
      app.sheet = { kind: "reply", line: parts.slice(1).join("/") };
      if (app.sheet.line.indexOf("nightly:") === 0) app.route = "nightly";
      if (app.sheet.line === "machine") app.route = "machine";
    } else if (parts[0] === "nightly" && parts[1] && parts[2]) {
      app.route = "nightly";
      app.sheet = { kind: "log", label: parts[1], day: parts[2] };
    } else {
      app.route = ["board", "machine", "nightly", "thread"].indexOf(parts[0]) >= 0 ? parts[0] : "board";
    }
    if (app.route === "thread") loadMessages();
    render();
  }
  function closeSheet() {
    app.sheet = null;
    history.replaceState(null, "", "#" + app.route);
    render();
  }

  // ---- sending ---------------------------------------------------------------------

  function sheetError(text) {
    var el = $("#sheet-err");
    if (el) { el.textContent = text; el.hidden = !text; }
  }
  function send() {
    var sh = app.sheet;
    if (!sh || sh.kind !== "reply") return;
    var text = ($("#text").value || "").trim();
    var line = sh.line;
    if (line === "new-session:") line = "new-session:" + ($("#repo") ? $("#repo").value : "");
    if (!text) { sheetError("Write something first."); return; }
    var btn = document.querySelector("[data-send]");
    if (btn) btn.disabled = true;
    var first = canSend() ? Promise.resolve() : checkPasskey();
    first.then(function () {
      return api("POST", "messages", { line: line, text: text, key: sh.key || (sh.key = key()) });
    }).then(function () {
      track("send", { kind: line.split(":")[0] === "new-session" ? "new-session" : line.split(":")[0] === "nightly" ? "nightly" : line === "machine" ? "machine" : "board" });
      app.sheet = null;
      loadMessages();
      go("thread");
    }).catch(function (e) {
      if (btn) btn.disabled = false;
      if (e && e.name === "NotAllowedError") sheetError("The passkey check was cancelled.");
      else if (e && e.status === 403 && e.body && e.body.error === "passkey") { loadMe().then(render); sheetError("Confirm with your passkey first."); }
      else sheetError(e && e.message ? e.message : "Could not send.");
    });
  }
  function sendAgain(id) {
    var m = app.messages.filter(function (x) { return x.id === id; })[0];
    if (!m) return;
    app.sheet = { kind: "reply", line: m.line, prefill: m.text };
    render();
  }

  // ---- events -------------------------------------------------------------------------

  document.addEventListener("click", function (ev) {
    var t = ev.target.closest("button");
    if (!t) return;
    if (t.dataset.tab) { app.sheet = null; go(t.dataset.tab); if (t.dataset.tab === "thread") loadMessages(); return; }
    if (t.dataset.close) { closeSheet(); return; }
    if (t.dataset.reply) {
      app.sheet = { kind: "reply", line: t.dataset.reply, prefill: t.dataset.prefill || "", option: t.dataset.option || "" };
      history.replaceState(null, "", "#line/" + encodeURIComponent(t.dataset.reply));
      track("reply-open", { kind: t.dataset.option ? "option" : t.dataset.prefill ? "prefill" : "reply" });
      loadMe().then(render);
      render();
      return;
    }
    if (t.dataset.pick) { $("#text").value = t.dataset.pick; document.querySelectorAll("[data-pick]").forEach(function (b) { b.classList.toggle("on", b === t); }); return; }
    if (t.dataset.newSession) { app.sheet = { kind: "reply", line: "new-session:" }; loadMe().then(render); render(); return; }
    if (t.dataset.cell) { var p = t.dataset.cell.split("|"); app.sheet = { kind: "log", label: p[0], day: p[1] }; history.replaceState(null, "", "#nightly/" + p[0] + "/" + p[1]); track("log-open", {}); render(); return; }
    if (t.dataset.send) { send(); return; }
    if (t.dataset.addPasskey) {
      t.disabled = true;
      addPasskey().then(function () { render(); }, function (e) { t.disabled = false; sheetError(e && e.name === "NotAllowedError" ? "The passkey was not added." : (e.message || "Could not add the passkey.")); });
      return;
    }
    if (t.dataset.again) { sendAgain(t.dataset.again); return; }
    if (t.dataset.signout) { signOut(); return; }
    if (t.dataset.restart) { signin = { stage: "email", email: "", session: "", err: "" }; render(); return; }
    if (t.id === "fresh") { loadState(); return; }
  });

  document.addEventListener("submit", function (ev) {
    ev.preventDefault();
    var f = ev.target;
    var btn = f.querySelector("button[type=submit]");
    if (btn) btn.disabled = true;
    if (f.id === "email-form") {
      var email = f.email.value.trim().toLowerCase();
      cognito("InitiateAuth", { AuthFlow: "USER_AUTH", ClientId: CFG.clientId, AuthParameters: { USERNAME: email, PREFERRED_CHALLENGE: "EMAIL_OTP" } })
        .then(function (res) {
          signin = { stage: "code", email: email, session: res.Session, err: "" };
          track("signin", { step: "code-sent" });
          render();
          var c = $("#code"); if (c) c.focus();
        }, function (e) { signin.err = e.type === "UserNotFoundException" || e.type === "NotAuthorizedException" ? "That address cannot sign in here." : e.message; render(); });
    } else if (f.id === "code-form") {
      cognito("RespondToAuthChallenge", { ChallengeName: "EMAIL_OTP", ClientId: CFG.clientId, Session: signin.session,
        ChallengeResponses: { USERNAME: signin.email, EMAIL_OTP_CODE: f.code.value.trim() } })
        .then(function (res) {
          if (!res.AuthenticationResult) throw new Error("The code did not work. Ask for a new one.");
          storeResult(res.AuthenticationResult, signin.email, CFG.clientId, null);
          signin = { stage: "email", email: "", session: "", err: "" };
          track("signin", { step: "done" });
          start();
        })
        .catch(function (e) { signin.err = e.type === "CodeMismatchException" ? "Wrong code. Try again." : e.message; if (btn) btn.disabled = false; render(); });
    }
  });

  window.addEventListener("hashchange", fromHash);
  document.addEventListener("visibilitychange", function () { if (!document.hidden && loadAuth()) { loadState(); loadMe(); } });

  // ---- start ----------------------------------------------------------------------------

  var timers = [];
  function start() {
    timers.forEach(clearInterval);
    timers = [];
    if (!(loadAuth() || {}).refresh) { render(); return; }
    fromHash();
    loadState();
    loadMe();
    loadMessages();
    timers.push(setInterval(function () { if (!document.hidden) loadState(); }, STATE_EVERY));
    timers.push(setInterval(function () { if (!document.hidden && (app.route === "thread" || app.messages.some(function (m) { return m.state === "queued"; }))) loadMessages(); }, MESSAGES_EVERY));
    timers.push(setInterval(function () { if (app.state) freshness(); }, 5000));
  }
  if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js").catch(function () {});
  start();
})();
