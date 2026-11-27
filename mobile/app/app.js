// podbay mobile (docs/adr/0001-podbay-mobile.md). Plain JS, no build, no
// third-party code. All text goes through esc() into markup; widths are set
// through element.style after render, because the CSP allows no inline style.
(function () {
  "use strict";

  var CFG = window.PODBAY || {};
  var STATE_EVERY = 30000;
  var MESSAGES_EVERY = 15000;

  var app = { state: null, me: null, messages: [], loadedAt: 0, route: "board", arg: "", sheet: null, error: "", tested: {}, unfolded: {} };

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
      if (el.hasAttribute("data-bg")) el.style.background = el.getAttribute("data-bg");
    });
  }

  // ---- auth (mobile/app/auth.js: Cognito USER_AUTH with EMAIL_OTP) -----------

  // Cognito ended the login (the 30 days are over): say why on the sign-in
  // screen and keep what was typed in the chat box for after the sign-in.
  // Called before the login is deleted, so the box is still on the page.
  function loginEnded() {
    var box = document.querySelector("#chat");
    if (box && box.value) app.draft = box.value;
    signin.err = "Your monthly sign-in is due.";
  }
  var AUTH = window.PodbayAuth.create({ storage: localStorage, fetch: window.fetch.bind(window), region: CFG.region, clientId: CFG.clientId, onEnded: loginEnded });
  function loadAuth() { return AUTH.load(); }
  function cognito(target, body) { return AUTH.cognito(target, body); }
  function storeResult(res, email, clientId, old) { return AUTH.store(res, email, clientId, old); }
  function signOut() {
    AUTH.signOut();
    app.state = null; app.me = null; app.messages = [];
    render();
  }

  // ---- API --------------------------------------------------------------------

  // A call that finds the login gone (Cognito refused the refresh token)
  // shows the sign-in screen. A call that could not reach Cognito or the API
  // keeps the login and shows the error; the next poll tries again.
  function api(method, path, body) {
    return AUTH.authorized("/api/" + path, {
      method: method,
      headers: body ? { "Content-Type": "application/json" } : {},
      body: body ? JSON.stringify(body) : undefined,
    }).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (j) {
        if (!r.ok) { var e = new Error(j.message || j.error || ("HTTP " + r.status)); e.status = r.status; e.body = j; throw e; }
        return j;
      });
    }, function (e) {
      if (e.signedOut) render();
      throw e;
    });
  }
  function loadState() {
    return api("GET", "state").then(function (s) {
      app.state = s; app.loadedAt = Date.now(); app.error = "";
      render();
    }, function (e) {
      app.error = e.status === 404 ? "The Mac has not sent its state yet." : (e.signedOut ? "" : "Could not load: " + e.message);
      render();
    });
  }
  function loadMe() {
    return api("GET", "me").then(function (m) {
      var changed = JSON.stringify(m) !== JSON.stringify(app.me);
      app.me = m;
      if (changed && app.route === "thread") { app.chatRedraw = !($("#chat") && $("#chat").value); render(); }
    }, function () {});
  }
  function loadMessages() {
    return api("GET", "messages").then(function (m) {
      var next = m.messages || [], changed = JSON.stringify(next) !== JSON.stringify(app.messages);
      app.messages = next;
      app.messagesLoaded = true;
      if (changed) render();
    }, function () {});
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
  // ---- clavesa run timings (the snapshot's runs part) ------------------------
  // One colour per phase, fixed: colour belongs to the phase, never to the
  // rank. "nodes" is the whole node time of a run without phase data.
  var PHASE_COLOR = { startup: "#3987e5", metastore: "#d95926", spark: "#199e70", reads: "#c98500", transform: "#d55181",
    writes: "#008300", stats: "#9085e9", nodes: "#4f5560", bookkeeping: "#6b7078" };
  var PHASE_LABEL = { nodes: "nodes (no phase data)" };
  function sum(a) { return (a || []).reduce(function (s, x) { return s + (x || 0); }, 0); }
  function minSec(min) {
    var s = Math.round(min * 60), h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60);
    return h ? h + " h " + String(m).padStart(2, "0") + " min" : m + " min " + String(s % 60).padStart(2, "0") + " s";
  }
  function ddmm(iso) { return iso.slice(8, 10) + "." + iso.slice(5, 7); }
  function runStep(max) {
    var steps = [1, 2, 5, 10, 15, 20, 30, 60, 120, 240, 480, 960], step = steps[steps.length - 1];
    for (var i = 0; i < steps.length; i++) if (max / steps[i] <= 4.2) { step = steps[i]; break; }
    return { step: step, top: Math.max(step, Math.ceil(max / step) * step) };
  }
  // items: [{label, short, v: minutes per phase, failed, empty}]; chart: its
  // index on the screen, for the tap; sel: the selected item index or -1.
  function runChart(items, phases, chart, sel, aria) {
    var w = 340, h = 170, pl = 30, pb = 16, top = 12, iw = w - pl, ih = h - pb - top, n = items.length;
    var sc = runStep(Math.max.apply(null, items.map(function (it) { return sum(it.v); }).concat([0.5])));
    var slot = iw / n, bw = Math.min(16, Math.max(2, slot - 2)), s = "";
    function y(v) { return top + ih - (v / sc.top) * ih; }
    for (var t = 0; t <= sc.top + 1e-9; t += sc.step) {
      s += '<line x1="' + pl + '" x2="' + w + '" y1="' + y(t) + '" y2="' + y(t) + '" stroke="#221c18"/><text x="' + (pl - 4) + '" y="' + (y(t) + 3) + '" text-anchor="end">' + t + "</text>";
    }
    s += '<text x="' + (pl - 4) + '" y="4" text-anchor="end">min</text>';
    items.forEach(function (it, i) {
      var x = pl + i * slot + (slot - bw) / 2, acc = 0;
      if (i === sel) s += '<rect x="' + (pl + i * slot) + '" y="' + top + '" width="' + slot + '" height="' + ih + '" fill="#ffffff" opacity=".08"/>';
      if (!it.empty) {
        phases.forEach(function (p, k) {
          var v = it.v[k] || 0;
          if (v <= 0) return;
          var hh = (v / sc.top) * ih;
          s += '<rect x="' + x.toFixed(1) + '" y="' + y(acc + v).toFixed(1) + '" width="' + bw.toFixed(1) + '" height="' + Math.max(0.75, hh > 3 ? hh - 1 : hh).toFixed(1) + '" fill="' + PHASE_COLOR[p] + '"/>';
          acc += v;
        });
        if (it.failed) {
          var cx = x + bw / 2, ty = y(acc) - 3;
          s += '<path d="M' + (cx - 4) + " " + (ty - 7) + " L" + (cx + 4) + " " + (ty - 7) + " L" + cx + " " + ty + ' Z" fill="#e0201f"/>';
        }
      }
      if ((n - 1 - i) % 7 === 0) s += '<text x="' + Math.min(w - 14, Math.max(pl + 14, pl + i * slot + slot / 2)) + '" y="' + (h - 2) + '" text-anchor="middle">' + esc(it.short) + "</text>";
    });
    // The tap targets: one full-height column per item.
    items.forEach(function (it, i) {
      s += '<rect class="hit" data-bar="' + chart + "|" + i + '" x="' + (pl + i * slot) + '" y="0" width="' + slot + '" height="' + h + '" fill="transparent"><title>' + esc(it.label) + "</title></rect>";
    });
    return '<svg viewBox="0 0 ' + w + " " + h + '" role="img" aria-label="' + esc(aria) + '">' + s + "</svg>";
  }
  function swatch(p) { return '<svg class="sw" viewBox="0 0 10 10"><rect width="10" height="10" rx="2" fill="' + PHASE_COLOR[p] + '"/></svg>'; }
  function phaseLegend(phases, items) {
    var nodes = phases.indexOf("nodes"), any = items.some(function (it) { return !it.empty && it.v[nodes] > 0; });
    return '<div class="plegend">' + phases.filter(function (p) { return p !== "nodes" || any; }).map(function (p) {
      return "<span>" + swatch(p) + esc(PHASE_LABEL[p] || p) + "</span>";
    }).join("") + "</div>";
  }
  function runDetail(it, phases) {
    if (!it) return "";
    if (it.empty) return '<div class="rdetail"><b>' + esc(it.label) + '</b><p class="none">No runs.</p></div>';
    var tot = sum(it.v);
    return '<div class="rdetail"><div class="mini"><b>' + esc(it.label) + "</b>" + (it.failed ? '<span class="chip red">' + esc(it.failedText) + "</span>" : "") + "</div>" +
      '<div class="prow total"><span>Total</span><span>' + esc(tot.toFixed(1) + " min · " + minSec(tot)) + "</span></div>" +
      phases.map(function (p, k) {
        var v = it.v[k] || 0;
        if (v <= 0) return "";
        return '<div class="prow"><span>' + swatch(p) + esc(p) + "</span><span>" + esc(v.toFixed(1) + " min · " + Math.round((100 * v) / tot) + "%") + "</span></div>";
      }).join("") + "</div>";
  }
  function runTable(items, phases) {
    return tableTwin(["run", "status", "total"].concat(phases), items.slice().reverse().map(function (it) {
      return [it.label, it.empty ? "" : it.failed ? it.failedText : "ok", it.empty ? "" : sum(it.v).toFixed(1)].concat(phases.map(function (_, k) { return it.empty ? "" : (it.v[k] || 0).toFixed(1); }));
    }));
  }
  function runCharts(r) {
    if (!r || !r.nights || !r.nights.length) return [];
    var charts = [{
      title: "All pipelines, per night", aria: "Pipeline minutes per night, stacked by phase, last 30 nights",
      items: r.nights.map(function (x) {
        return { label: longDay(x.d) + (x.n ? ", " + x.n + (x.n === 1 ? " run" : " runs") : ""), short: ddmm(x.d), v: x.v, empty: !x.n,
          failed: x.f, failedText: x.f + " failed" };
      }),
    }];
    (r.pipelines || []).forEach(function (p) {
      charts.push({
        title: p.name, aria: "Minutes per run for " + p.name + ", stacked by phase",
        sum: "latest " + minSec(p.latest_ms / 60000) + (p.median_of ? ", median of the previous " + p.median_of + (p.median_of === 1 ? " run " : " runs ") + minSec(p.median_ms / 60000) : ""),
        items: p.runs.map(function (x) {
          return { label: longDay(x.at.slice(0, 10)) + " " + x.at.slice(11, 16), short: ddmm(x.at), v: x.v, failed: x.f, failedText: "failed" };
        }),
      });
    });
    return charts;
  }
  function runsBlock(s) {
    var r = s.runs, charts = runCharts(r);
    if (!charts.length) return '<h2>Pipeline time</h2><p class="none">No clavesa run timings on the Mac yet. The 10:45 runs-export writes them.</p>';
    var last = r.nights[r.nights.length - 1], g = r.generated_at || "";
    var sub = (g ? "Exported " + ddmm(g.slice(0, 10)) + " " + g.slice(11, 16) + " · " : "") + r.count + " runs in 90 days · last night " +
      Math.round(sum(last.v)) + " min across " + last.p + (last.p === 1 ? " pipeline" : " pipelines") + ". Tap a bar for its phases.";
    var sel = app.runSel || { c: -1, i: -1 };
    return '<h2>Pipeline time</h2><p class="sub">' + esc(sub) + "</p>" + charts.map(function (c, k) {
      var on = sel.c === k ? sel.i : -1;
      return '<article class="card chartcard" id="runs-' + k + '"><h3>' + esc(c.title) + "</h3>" +
        '<p class="q">' + esc(c.sum || "clavesa minutes, stacked by phase; a red mark is a night with a failed run") + "</p>" +
        phaseLegend(r.phases, c.items) + runChart(c.items, r.phases, k, on, c.aria) + (on >= 0 ? runDetail(c.items[on], r.phases) : "") + runTable(c.items, r.phases) + "</article>";
    }).join("");
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
  // A line cut short on the server carries its whole text in d.full: a tap
  // unfolds it in place (text and link), another tap folds it.
  function lineBody(d) {
    var link = safeUrl(d.link) ? '<a class="link" href="' + esc(safeUrl(d.link)) + '" target="_blank" rel="noopener">' + esc(safeUrl(d.link)) + "</a>" : "";
    if (!d.full) return '<p class="line">' + esc(d.text) + "</p>" + link;
    var open = !!app.unfolded[d.id];
    return '<div class="fold" data-fold="' + esc(d.id) + '" role="button" tabindex="0" aria-expanded="' + open + '"><p class="line">' + esc(open ? d.full : d.text) + '</p></div>' + link;
  }

  function board(s) {
    var sec = s.board.sections || {};
    var ask = (sec.decision || []).map(function (d) {
      return '<article class="card ask' + (d.asks ? " waiting" : "") + '" id="line-' + esc(d.id) + '">' + head(d) + lineBody(d) +
        lineThread(d.id) + '<div class="row">' + (d.options || []).map(function (o) { return '<button class="btn opt" type="button" data-reply="' + esc(d.id) + '" data-option="' + esc(o) + '">' + esc(o) + "</button>"; }).join("") +
        replyBtn(d.id, (d.options || []).length ? "Other" : "Answer") + "</div></article>";
    });
    var test = (sec.shipped || []).filter(function (d) { return !app.tested[d.id]; }).map(function (d) {
      return '<article class="card' + (d.asks ? " waiting" : "") + '" id="line-' + esc(d.id) + '">' + head(d) + lineBody(d) + steps(d.steps) +
        lineThread(d.id) + '<div class="row">' + '<button class="btn opt" type="button" data-tested="' + esc(d.id) + '">Tested</button>' + replyBtn(d.id, "Works", "Works") + replyBtn(d.id, "Found a fault", "Found a fault: ") + replyBtn(d.id) + "</div></article>";
    });
    var done = (sec.finished || []).map(function (d) {
      return '<article class="card' + (d.asks ? " waiting" : "") + '" id="line-' + esc(d.id) + '">' + head(d) + lineBody(d) +
        lineThread(d.id) + '<div class="row">' + replyBtn(d.id) + "</div></article>";
    });
    var prog = (sec.progress || []).map(function (d) {
      return '<article class="card' + (d.asks ? " waiting" : "") + '" id="line-' + esc(d.id) + '"><div class="mini">' + head(d) + (d.asks ? '<span class="chip waiting">asks you</span>' : "") + (d.when ? '<span class="chip amber">' + esc(d.when) + "</span>" : "") + "</div>" +
        lineBody(d) + lineThread(d.id) + '<div class="row">' + replyBtn(d.id) + "</div></article>";
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
    // Every live session, one row each; a session that asks you something
    // is orange and first, and opens its board line's sheet.
    var STATE_NAME = { working: "working", watching: "watching a job", due: "due", parked: "parked", needs_you: "your turn", stalled: "stalled", empty: "new" };
    var sess = (s.sessions || []).map(function (x) {
      var tag = x.line ? "button" : "div";
      return "<" + tag + ' class="srow' + (x.asks ? " waiting" : "") + '"' + (x.line ? ' type="button" data-reply="' + esc(x.line) + '"' : "") + '><span class="k">' + esc(x.target) +
        "<small>" + esc(x.name) + "</small></span>" + (x.asks ? '<span class="chip waiting">asks you</span>' : '<span class="chip">' + esc(STATE_NAME[x.state] || x.state) + "</span>") + "</" + tag + ">";
    }).join("");
    var sessions = sess ? '<h2>Sessions <span class="n">' + s.sessions.length + "</span></h2>" + '<article class="card">' + sess + "</article>" : "";
    return "<h1>Machine</h1><p class=\"sub\">" + esc(s.mac || "The Mac") + ". " + (needs ? "Something needs a look." : "Nothing needs you.") + "</p>" + overloaded + sessions +
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
      '<div class="row gap">' + replyBtn("machine", "Ask Head Jeeves about the Mac") + "</div>" + pushCard() +
      costsCard(s) +
      '<h2>This app</h2><div class="row"><button class="btn wide" type="button" data-stats="1">How the app is used, and its errors</button></div>';
  }
  // The Machine tab's way into the Costs screen: the normal daily cost of
  // all accounts and the age of the last monthly run, amber when a run was
  // missed or an account is stale.
  function costsCard(s) {
    var sp = s.spend;
    if (!sp || !sp.accounts || !sp.accounts.length) return "";
    var all = sp.accounts.reduce(function (t, x) { return t + (x.normal_per_day || 0); }, 0), age = daysOld(sp.generated);
    var stale = sp.accounts.filter(function (x) { return x.stale; }).length, late = age != null && age > 35;
    return '<h2>AWS costs</h2><button class="card ccard' + (stale || late ? " warnish" : "") + '" type="button" data-costs="1"><span class="mini"><span class="big">' + all.toFixed(2) + '<small>USD/day normal</small></span><span class="chev">›</span></span>' +
      '<span class="label">' + esc(sp.accounts.map(function (x) { return x.profile; }).join(", ")) + " · run " + esc(sp.generated ? ddmm(sp.generated.slice(0, 10)) : "?") +
      (stale ? " · " + stale + " stale" : "") + (late ? " · no run for " + Math.floor(age) + " days" : "") + "</span></button>";
  }

  // ---- release watch (the snapshot's releases part: jeeves release-watch, daily) ----

  var RW_SECTIONS = [
    ["remove", "Could remove a part"],
    ["runner", "Another runner"],
    ["simplify", "Could simplify"],
    ["nothing yet", "Touches clavesa, no use yet"]
  ];
  function rwStamp(iso) { return iso ? ddmm(iso.slice(0, 10)) + " " + iso.slice(11, 16) : ""; }
  function rwCounts(items) {
    var n = {};
    items.forEach(function (it) { n[it.use] = (n[it.use] || 0) + 1; });
    return n;
  }
  // The Nightly tab's way into the Release watch screen.
  function releasesCard(s) {
    var r = s.releases;
    if (!r) return "";
    var n = rwCounts(r.items || []), lr = r.last_run || {}, bad = (lr.errors || []).length;
    var line = ["remove", "runner", "simplify"].filter(function (u) { return n[u]; }).map(function (u) { return n[u] + " " + u; }).join(" · ") || "nothing that could help yet";
    return '<h2>Release watch</h2><button class="card ccard' + (bad ? " warnish" : "") + '" type="button" data-releases="1"><span class="mini"><span class="big">' + (r.items || []).length +
      '<small>kept, 90 days</small></span><span class="chev">›</span></span><span class="label">' + esc(line) + " · run " + esc(rwStamp(lr.at)) + (bad ? " · " + bad + " errors" : "") + "</span></button>";
  }
  function rwItem(it) {
    var pushed = it.pushed_at ? '<span class="chip amber">pushed ' + esc(rwStamp(it.pushed_at)) + "</span>" : "";
    var breaks = it.breaks ? '<span class="chip red">could break</span>' : "";
    var link = safeUrl(it.link);
    return '<article class="card rwitem' + (it.pushed_at ? " pushed" : "") + '"><div class="mini"><span class="label">' + esc(it.source) + " · " + esc(it.date ? ddmm(it.date) : "") + "</span>" + pushed + breaks + "</div>" +
      (link ? '<h3><a href="' + esc(link) + '" target="_blank" rel="noopener">' + esc(it.title) + ' <span class="ext" aria-hidden="true">↗</span></a></h3>' +
        '<a class="rwsrc" href="' + esc(link) + '" target="_blank" rel="noopener">' + esc(link.replace(/^https?:\/\//, "").split("/")[0]) + "</a>"
        : "<h3>" + esc(it.title) + "</h3>") +
      '<p class="q">' + (it.part ? "<b>" + esc(it.part) + ":</b> " : "") + esc(it.why || "") + "</p></article>";
  }
  function releases(s) {
    var r = s.releases, back = '<div class="row gap"><button class="btn wide" type="button" data-tab="nightly">Back to the Nightly tab</button></div>';
    if (!r) return '<h1>Release watch</h1><p class="none">No release watch file on the Mac yet. The release-watch run at 10:20 each day writes it.</p>' + back;
    var lr = r.last_run || {}, items = r.items || [];
    var sub = "Release notes that could let clavesa drop a part, get simpler or use another runner. Last run " + rwStamp(lr.at) + ": read " + (lr.read || 0) +
      ", " + (lr.new || 0) + " new, kept " + (lr.kept || 0) + ", " + (lr.model_calls || 0) + " " + (lr.model || "model") + (lr.model_calls === 1 ? " call, " : " calls, ") + Math.round(lr.seconds || 0) + " s." +
      (r.last_push ? " Last push " + ddmm(r.last_push) + "." : "");
    var warn = (lr.errors || []).map(function (e) { return '<p class="err">' + esc(e) + "</p>"; }).join("") +
      (lr.gaps || []).map(function (g) { return '<p class="err">' + esc(g) + ": the feed starts after the last run, so some items were missed.</p>"; }).join("");
    var src = r.sources || [], max = Math.max.apply(null, src.map(function (x) { return x.kept + x.dropped; }).concat([1]));
    var bars = src.map(function (x) {
      var all = x.kept + x.dropped;
      return '<div class="qrow"><span class="k">' + esc(x.source) + '</span><div class="bar"><b class="rwall" data-w="' + Math.round(all / max * 100) + '"><i data-w="' + (all ? Math.round(x.kept / all * 100) : 0) + '"></i></b></div><span class="r">' + x.kept + " of " + all + "</span></div>";
    }).join("");
    var body = RW_SECTIONS.map(function (sec) {
      var list = items.filter(function (it) { return it.use === sec[0]; });
      if (!list.length) return "";
      var head = sec[1] + ' <span class="n">' + list.length + "</span>";
      if (sec[0] === "nothing yet") return '<details class="rwmore"><summary><h2>' + head + "</h2></summary>" + list.map(rwItem).join("") + "</details>";
      return "<h2>" + head + "</h2>" + list.map(rwItem).join("");
    }).join("");
    return "<h1>Release watch</h1><p class=\"sub\">" + esc(sub) + "</p>" + warn + body +
      (items.length ? "" : '<p class="none">Nothing kept yet.</p>') +
      '<article class="card chartcard"><h3>Kept and left out, per source</h3><p class="q">Every run in the file. The green part was kept; the rest the model or the feed filter left out.</p>' + (bars || '<p class="none">No runs yet.</p>') + "</article>" + back;
  }

  // ---- pushes (the snapshot's pushes part: every push of the last 30 days) ----

  var PUSH_KIND = { "head-jeeves": "Head Jeeves", nightly: "Nightly", "release-watch": "Release watch", alarm: "Alarm", aws: "AWS", other: "Mac" };
  function isoDay(epoch) {
    var d = new Date(epoch * 1000);
    return d.getFullYear() + "-" + String(d.getMonth() + 1).padStart(2, "0") + "-" + String(d.getDate()).padStart(2, "0");
  }
  // Where tapping a push goes: the place in the app its link names (a
  // board line, a screen, the board), or nothing when the link was only the
  // app's root, as for an AWS alarm.
  function pushTarget(p) {
    var u = String(p.url || ""), i = u.indexOf("#");
    return i >= 0 ? u.slice(i + 1) : "";
  }
  function linkify(text) {
    return esc(text).replace(/https?:\/\/[^\s<>"']+/g, function (m) {
      var u = safeUrl(m.replace(/&amp;/g, "&"));
      return u ? '<a href="' + esc(u) + '" target="_blank" rel="noopener">' + m + "</a>" : m;
    });
  }
  function pushItem(p) {
    var chips = '<span class="chip">' + esc(PUSH_KIND[p.kind] || p.kind) + "</span>";
    if (p.kind === "alarm") {
      chips += p.state_now === "ALARM" ? '<span class="chip red">still in alarm</span>' : p.state_now === "OK" ? '<span class="chip green">OK now</span>' :
        p.state_now ? '<span class="chip amber">' + esc(p.state_now.toLowerCase().replace(/_/g, " ")) + " now</span>" : '<span class="chip">state unknown</span>';
    }
    if (!p.delivered) chips += '<span class="chip red">no device took it</span>';
    var target = pushTarget(p), open = target ? '<span class="chev">›</span>' : "";
    var inner = '<div class="mini"><span class="label">' + esc(hhmm(p.at)) + "</span>" + chips + open + "</div><h3>" + esc(p.title) + "</h3>" +
      (p.body ? '<p class="q pbody">' + linkify(p.body) + "</p>" : "");
    // An article, not a button, so a link in the body stays a link.
    return target ? '<article class="card pitem tap" role="link" tabindex="0" data-open-push="' + esc(target) + '">' + inner + "</article>" : '<article class="card pitem">' + inner + "</article>";
  }
  function pushes(s) {
    var p = s.pushes;
    if (!p) return '<h1>Pushes</h1><p class="none">The Mac sends no push history yet.</p>';
    var items = p.items || [], days = [], byDay = {}, today = isoDay(Date.now() / 1000);
    for (var k = (p.days || 30) - 1; k >= 0; k--) { var iso = isoDay(Date.now() / 1000 - k * 86400); days.push({ date: iso, n: 0 }); byDay[iso] = days[days.length - 1]; }
    items.forEach(function (it) { var d = byDay[isoDay(it.at)]; if (d) d.n++; });
    var open = items.filter(function (it) { return it.kind === "alarm" && it.state_now === "ALARM"; }).map(function (it) { return it.alarm; });
    open = open.filter(function (a, i) { return open.indexOf(a) === i; });
    var sub = items.length + (items.length === 1 ? " push" : " pushes") + " in " + (p.days || 30) + " days, newest first. Tap one to open its board line or screen.";
    var list = "", last = "";
    items.forEach(function (it) {
      var d = isoDay(it.at);
      if (d !== last) { list += "<h2>" + esc(d === today ? "Today" : longDay(d)) + "</h2>"; last = d; }
      list += pushItem(it);
    });
    return "<h1>Pushes</h1><p class=\"sub\">" + esc(sub) + "</p>" +
      (open.length ? '<p class="err">Still in alarm: ' + esc(open.join(", ")) + "</p>" : "") +
      '<article class="card chartcard"><h3>Pushes per day</h3>' + dayBars(days, "n", "#e0a030", "Pushes per day, last " + (p.days || 30) + " days") + "</article>" +
      (list || '<p class="none">No pushes in ' + (p.days || 30) + " days.</p>");
  }

  // ---- stats (the nightly podbay-mobile-traffic rollup) ----------------------------

  function dayBars(days, key, color, label) {
    var w = 340, h = 110, pl = 30, pb = 16, top = 6, iw = w - pl, ih = h - pb - top;
    if (!days.length) return '<p class="none">No days yet.</p>';
    var vals = days.map(function (d) { return d[key] || 0; });
    var max = niceMax(Math.max.apply(null, vals.concat([1]))), step = iw / days.length, bw = Math.max(2, step - 3), s = "";
    [0, max / 2, max].forEach(function (v) {
      var y = top + ih - (v / max) * ih;
      s += '<line x1="' + pl + '" x2="' + w + '" y1="' + y + '" y2="' + y + '" stroke="#221c18"/><text x="' + (pl - 4) + '" y="' + (y + 3) + '" text-anchor="end">' + (v % 1 ? v.toFixed(1) : v) + "</text>";
    });
    days.forEach(function (d, i) {
      var v = vals[i], bh = (v / max) * ih;
      if (v) s += '<rect x="' + (pl + i * step + 1) + '" y="' + (top + ih - bh) + '" width="' + bw + '" height="' + bh + '" rx="1.5" fill="' + color + '"/>';
    });
    var idx = days.length === 1 ? [0] : [0, Math.floor((days.length - 1) / 2), days.length - 1];
    idx.forEach(function (i, k) {
      s += '<text x="' + (pl + i * step + step / 2) + '" y="' + (h - 2) + '" text-anchor="' + (idx.length === 1 ? "middle" : k === 0 ? "start" : k === idx.length - 1 ? "end" : "middle") + '">' + esc(dayName(days[i].date)) + "</text>";
    });
    return '<svg viewBox="0 0 ' + w + " " + h + '" role="img" aria-label="' + esc(label) + '">' + s + "</svg>";
  }
  function lastDays(rows, n) {
    var by = {}, out = [];
    rows.forEach(function (r) { by[r.date] = r; });
    for (var i = n - 1; i >= 0; i--) {
      var d = new Date(Date.now() - i * 86400000), iso = d.getFullYear() + "-" + String(d.getMonth() + 1).padStart(2, "0") + "-" + String(d.getDate()).padStart(2, "0");
      out.push(by[iso] || { date: iso });
    }
    return out;
  }
  function stats() {
    if (!app.stats) return '<h1>This app</h1><p class="sub boot">Loading…</p>';
    var rows = app.stats.days || [], days = lastDays(rows, 30);
    var tabs = {}, errs = {};
    days.forEach(function (d) {
      Object.keys(d.tabs || {}).forEach(function (k) { tabs[k] = (tabs[k] || 0) + d.tabs[k]; });
    });
    lastDays(rows, 7).forEach(function (d) {
      (d.errorList || []).forEach(function (e) {
        var k = e.type + "|" + e.path + "|" + e.msg;
        if (!errs[k]) errs[k] = { msg: e.msg, type: e.type, path: e.path, n: 0, last: d.date };
        errs[k].n += e.n; errs[k].last = d.date;
      });
    });
    var tabMax = Math.max.apply(null, Object.keys(tabs).map(function (k) { return tabs[k]; }).concat([1]));
    var tabRows = Object.keys(tabs).sort(function (a, b) { return tabs[b] - tabs[a]; }).map(function (k) {
      return '<div class="qrow"><span class="k">' + esc(k) + '</span><div class="bar"><b data-w="' + Math.round(tabs[k] / tabMax * 100) + '"></b></div><span class="r">' + tabs[k] + "</span></div>";
    }).join("");
    var errRows = Object.keys(errs).sort(function (a, b) { return errs[b].n - errs[a].n; }).map(function (k) {
      var e = errs[k];
      return "<tr><td>" + esc(e.msg || "(no message)") + "</td><td>" + esc(e.type) + "</td><td>" + esc(e.path) + "</td><td>" + e.n + "</td><td>" + esc(dayName(e.last)) + "</td></tr>";
    }).join("");
    var updated = rows.length ? "Updated nightly; the last day counted is " + longDay(rows[rows.length - 1].date) + "." : "Updated nightly. No days counted yet.";
    return "<h1>This app</h1><p class=\"sub\">How podbay mobile is used and where it breaks, from its own tracker. " + esc(updated) + "</p>" +
      '<article class="card chartcard"><h3>Sessions per day</h3><p class="q">Last 30 days</p>' + dayBars(days, "sessions", "#ffb000", "sessions per day") + "</article>" +
      '<article class="card chartcard"><h3>Messages sent per day</h3><p class="q">Sends from the reply sheet, last 30 days</p>' + dayBars(days, "sends", "#6fcf86", "messages per day") + "</article>" +
      '<article class="card chartcard"><h3>Which screens get opened</h3><p class="q">Tab opens, last 30 days</p>' + (tabRows || '<p class="none">No tab opens yet.</p>') + "</article>" +
      '<article class="card chartcard"><h3>Errors per day</h3><p class="q">Uncaught errors, failed promises and screens that broke, last 30 days</p>' + dayBars(days, "errors", "#e0201f", "errors per day") + "</article>" +
      '<h2>Errors, last 7 days <span class="n">' + Object.keys(errs).length + "</span></h2>" +
      (errRows ? '<article class="card"><table class="errs"><tr><th>Message</th><th>Kind</th><th>Page</th><th>Count</th><th>Last</th></tr>' + errRows + "</table></article>" : '<p class="none">No errors in the last 7 days.</p>') +
      '<div class="row gap"><button class="btn wide" type="button" data-tab="machine">Back to the Machine tab</button></div>';
  }
  function loadStats() {
    return api("GET", "stats").then(function (s) { app.stats = s; if (app.route === "stats") render(); }, function (e) {
      app.stats = { days: [] }; app.error = "Could not load the stats: " + e.message; render();
    });
  }

  // ---- AWS costs (the snapshot's spend part: the monthly aws-costs report) --------
  // A service keeps one colour in every account and every filter: the 8
  // services that cost most over all accounts get the categorical slots in
  // fixed order (validated for this dark surface), every other one is grey.
  var SPEND_SLOTS = ["#3987e5", "#d95926", "#199e70", "#c98500", "#d55181", "#0ca30c", "#9085e9", "#e66767"], SPEND_OTHER = "#6b7078";
  var COSTS_KEY = "podbay.costs";
  function costsPrefs() {
    if (!app.costs) {
      var saved = {};
      try { saved = JSON.parse(localStorage.getItem(COSTS_KEY) || "{}") || {}; } catch (e) { saved = {}; }
      app.costs = { acct: saved.acct || "", range: saved.range === 30 ? 30 : 90, hidden: saved.hidden || {}, sel: -1 };
    }
    return app.costs;
  }
  function saveCosts() {
    var c = app.costs;
    try { localStorage.setItem(COSTS_KEY, JSON.stringify({ acct: c.acct, range: c.range, hidden: c.hidden })); } catch (e) { /* private mode */ }
  }
  function spendColors(sp) {
    var tot = {};
    sp.accounts.forEach(function (a) { Object.keys(a.series).forEach(function (k) { tot[k] = (tot[k] || 0) + sum(a.series[k]); }); });
    var col = {};
    Object.keys(tot).sort(function (a, b) { return tot[b] - tot[a]; }).forEach(function (k, i) { col[k] = i < SPEND_SLOTS.length ? SPEND_SLOTS[i] : SPEND_OTHER; });
    return col;
  }
  function usd(v) { return (v || 0).toFixed(2) + " USD"; }
  function monthName(ym) {
    return ym ? ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"][Number(ym.slice(5, 7)) - 1] + " " + ym.slice(0, 4) : "";
  }
  function daysOld(stamp) {
    var t = Date.parse(String(stamp || "").replace(" ", "T"));
    return isNaN(t) ? null : (Date.now() - t) / 86400000;
  }
  function costStep(max) {
    var steps = [0.05, 0.1, 0.2, 0.25, 0.5, 1, 2, 5, 10, 20, 50, 100, 200, 500], step = steps[steps.length - 1];
    for (var i = 0; i < steps.length; i++) if (max / steps[i] <= 4.2) { step = steps[i]; break; }
    return { step: step, top: Math.max(step, Math.ceil(max / step - 1e-9) * step) };
  }
  // The daily stacked bars of one account: one bar per day, one segment per
  // shown service, the normal level as a line, an anomaly day marked red.
  function costChart(a, from, svcs, col, sel, anomalyDay) {
    var days = a.days.slice(from), n = days.length, w = 340, h = 180, pl = 34, pb = 16, top = 12, iw = w - pl, ih = h - pb - top;
    var totals = days.map(function (_, i) { return svcs.reduce(function (t, k) { return t + (a.series[k][from + i] || 0); }, 0); });
    var normal = (a.normal || []).slice(from);
    var sc = costStep(Math.max.apply(null, totals.concat(normal, [0.05]))), slot = iw / n, bw = Math.max(1.5, slot - (slot > 5 ? 2 : 1)), s = "";
    function y(v) { return top + ih - (v / sc.top) * ih; }
    for (var t = 0; t <= sc.top + 1e-9; t += sc.step) {
      s += '<line x1="' + pl + '" x2="' + w + '" y1="' + y(t) + '" y2="' + y(t) + '" stroke="#221c18"/><text x="' + (pl - 4) + '" y="' + (y(t) + 3) + '" text-anchor="end">' + (sc.step < 1 ? t.toFixed(2) : Math.round(t)) + "</text>";
    }
    s += '<text x="' + (pl - 4) + '" y="4" text-anchor="end">USD</text>';
    days.forEach(function (d, i) {
      var x = pl + i * slot + (slot - bw) / 2, acc = 0;
      if (i === sel) s += '<rect x="' + (pl + i * slot) + '" y="' + top + '" width="' + slot + '" height="' + ih + '" fill="#ffffff" opacity=".08"/>';
      svcs.forEach(function (k) {
        var v = a.series[k][from + i] || 0;
        if (v <= 0) return;
        var hh = (v / sc.top) * ih;
        s += '<rect x="' + x.toFixed(1) + '" y="' + y(acc + v).toFixed(1) + '" width="' + bw.toFixed(1) + '" height="' + Math.max(0.75, hh > 3 ? hh - 1 : hh).toFixed(1) + '" fill="' + col[k] + '"/>';
        acc += v;
      });
      if (anomalyDay[d]) {
        var cx = x + bw / 2, ty = y(acc) - 3;
        s += '<path d="M' + (cx - 3.5) + " " + (ty - 6) + " L" + (cx + 3.5) + " " + (ty - 6) + " L" + cx + " " + ty + ' Z" fill="#e0201f"/>';
      }
      if ((n - 1 - i) % (n > 40 ? 14 : 7) === 0) s += '<text x="' + Math.min(w - 14, Math.max(pl + 14, pl + i * slot + slot / 2)) + '" y="' + (h - 2) + '" text-anchor="middle">' + esc(ddmm(d)) + "</text>";
    });
    if (normal.length === n) {
      s += '<polyline points="' + normal.map(function (v, i) { return (pl + i * slot + slot / 2).toFixed(1) + "," + y(v).toFixed(1); }).join(" ") +
        '" fill="none" stroke="#f2ede8" stroke-width="1.5" stroke-dasharray="4 3" opacity=".85"/>';
    }
    days.forEach(function (d, i) {
      s += '<rect class="hit" data-cbar="' + i + '" x="' + (pl + i * slot) + '" y="0" width="' + slot + '" height="' + h + '" fill="transparent"><title>' + esc(longDay(d) + ", " + usd(totals[i])) + "</title></rect>";
    });
    return '<svg viewBox="0 0 ' + w + " " + h + '" role="img" aria-label="' + esc(a.profile + ": USD per day, stacked by service, last " + n + " days") + '">' + s + "</svg>";
  }
  function costs(s) {
    var sp = s.spend, back = '<div class="row gap"><button class="btn wide" type="button" data-tab="machine">Back to the Machine tab</button></div>';
    if (!sp || !sp.accounts || !sp.accounts.length) {
      return '<h1>AWS costs</h1><p class="none">No AWS cost report on the Mac yet. The aws-costs run on the 3rd of each month at 19:10 writes it.</p>' + back;
    }
    var p = costsPrefs(), col = spendColors(sp);
    var a = sp.accounts.filter(function (x) { return x.profile === p.acct; })[0] || sp.accounts[0];
    p.acct = a.profile;
    var age = daysOld(sp.generated), run = sp.generated ? longDay(sp.generated.slice(0, 10)) : "?";
    var sub = "From the monthly aws-costs run, " + run + (age != null ? " (" + Math.floor(age) + (Math.floor(age) === 1 ? " day ago" : " days ago") + ")" : "") +
      ". It runs on the 3rd of each month. Running cost only: tax, credits and one-offs such as domain renewals are left out.";
    var tiles = sp.accounts.map(function (x) {
      var lm = x.last_month || {}, on = x.profile === a.profile;
      return '<button class="card ctile' + (on ? " on" : "") + (x.stale ? " stale" : "") + '" type="button" data-cacct="' + esc(x.profile) + '" aria-pressed="' + on + '">' +
        '<span class="label">' + esc(x.profile) + "</span>" +
        '<span class="big">' + (x.normal_per_day || 0).toFixed(2) + "<small>USD/day</small></span>" +
        '<span class="label">' + esc(monthName(lm.month).slice(0, 3) || "Last month") + " " + (lm.running != null ? (lm.running).toFixed(2) : "–") + " USD</span>" +
        (x.stale ? '<span class="chip amber">stale</span>' : "") + "</button>";
    }).join("");
    var allNormal = sp.accounts.reduce(function (t, x) { return t + (x.normal_per_day || 0); }, 0);
    var n = a.days.length, from = Math.max(0, n - p.range), svcAll = Object.keys(a.series);
    var inRange = {};
    svcAll.forEach(function (k) { inRange[k] = sum(a.series[k].slice(from)); });
    svcAll.sort(function (x, y) { return inRange[y] - inRange[x]; });
    var shown = svcAll.filter(function (k) { return !p.hidden[k]; });
    var anomalyDay = {};
    (a.anomalies || []).forEach(function (x) {
      if (!x.start) return;
      a.days.forEach(function (d) { if (d >= x.start && d <= (x.end || x.start) && !p.hidden[x.service]) (anomalyDay[d] = anomalyDay[d] || []).push(x); });
    });
    var sel = p.sel >= 0 && p.sel < n - from ? p.sel : -1;
    var chips = svcAll.map(function (k) {
      var off = !!p.hidden[k];
      return '<button class="schip' + (off ? " off" : "") + '" type="button" data-csvc="' + esc(k) + '" aria-pressed="' + !off + '">' +
        '<svg class="sw" viewBox="0 0 10 10"><rect width="10" height="10" rx="2" fill="' + (off ? "none" : col[k]) + '" stroke="' + col[k] + '"/></svg>' + esc(k) + " <small>" + inRange[k].toFixed(2) + "</small></button>";
    }).join("");
    var lm = a.last_month || {}, tm = a.this_month || {};
    var stale = a.stale ? '<article class="card warn"><div class="repo">' + esc(a.profile) + ' is stale</div><p class="line">The last run could not read this account (' + esc(a.stale) +
      "). These figures are from " + esc(String(a.fetched || "?")) + ".</p></article>" : "";
    var months = '<div class="grid2"><article class="card"><div class="label">' + esc(monthName(lm.month) || "Last month") + '</div><div class="big">' + (lm.running != null ? lm.running.toFixed(2) : "–") + "<small>USD</small></div>" +
      '<div class="label">running cost' + (lm.anomalies ? ", of which " + lm.anomalies.toFixed(2) + " above normal" : "") + "</div></article>" +
      '<article class="card"><div class="label">' + esc(monthName(tm.month).split(" ")[0] || "This month") + " to date</div><div class=\"big\">" + (tm.running != null ? tm.running.toFixed(2) : "–") + "<small>USD</small></div>" +
      '<div class="label">' + (tm.through ? "1 to " + esc(ddmm(tm.through)) : "no days yet") + "</div></article></div>";
    var detail = "";
    if (sel >= 0) {
      var d = a.days[from + sel], rows = svcAll.map(function (k) { return [k, a.series[k][from + sel] || 0]; }).filter(function (r) { return r[1] >= 0.005; }).sort(function (x, y) { return y[1] - x[1]; });
      var tot = rows.reduce(function (t, r) { return t + r[1]; }, 0), max = rows.length ? rows[0][1] : 1;
      detail = '<div class="rdetail"><div class="mini"><b>' + esc(longDay(d)) + "</b>" + (anomalyDay[d] ? '<span class="chip red">above normal</span>' : "") + "</div>" +
        '<div class="prow total"><span>All services</span><span>' + usd(tot) + "</span></div>" +
        rows.map(function (r) {
          return '<div class="qrow' + (p.hidden[r[0]] ? " off" : "") + '"><span class="k"><svg class="sw" viewBox="0 0 10 10"><rect width="10" height="10" rx="2" fill="' + col[r[0]] + '"/></svg>' + esc(r[0]) +
            '</span><div class="bar"><b data-bg="' + col[r[0]] + '" data-w="' + Math.round((r[1] / max) * 100) + '"></b></div><span class="r">' + r[1].toFixed(2) + "</span></div>";
        }).join("") + (rows.length ? "" : '<p class="none">No running cost this day.</p>') +
        (anomalyDay[d] || []).map(function (x) {
          return '<p class="q">' + esc(x.service + ": " + (x.kind === "new service" ? "new service" : "+" + (x.excess || 0).toFixed(2) + " USD above normal") +
            ((x.start !== x.end) ? ", " + ddmm(x.start) + " to " + ddmm(x.end) : "") + ((x.usage_types || []).length ? " (" + x.usage_types.slice(0, 3).join(", ") + ")" : "")) + "</p>";
        }).join("") +
        '<div class="row">' + replyBtn("machine", "Ask Head Jeeves about this day", "AWS costs, " + a.profile + ", " + d + ": ") + "</div></div>";
    }
    var anomalies = (a.anomalies || []).filter(function (x) { return (x.end || x.start) >= a.days[from]; }).sort(function (x, y) { return (y.excess || 0) - (x.excess || 0); }).map(function (x) {
      return '<div class="prow"><span>' + esc(ddmm(x.start) + (x.end && x.end !== x.start ? "–" + ddmm(x.end) : "") + " " + x.service) + "</span><span>" +
        esc(x.kind === "new service" ? "new" : "+" + (x.excess || 0).toFixed(2)) + "</span></div>";
    }).join("");
    var table = tableTwin(["day", "total"].concat(shown), a.days.slice(from).map(function (d, i) {
      return [d, shown.reduce(function (t, k) { return t + (a.series[k][from + i] || 0); }, 0).toFixed(2)].concat(shown.map(function (k) { return (a.series[k][from + i] || 0).toFixed(2); }));
    }).reverse());
    return "<h1>AWS costs</h1><p class=\"sub\" id=\"costs-run\">" + esc(sub) + "</p>" +
      '<h2>Normal daily cost <span class="n">' + allNormal.toFixed(2) + " USD/day</span></h2>" +
      '<div class="ctiles">' + tiles + "</div>" + stale + months +
      '<article class="card chartcard" id="costs-chart"><div class="mini"><h3>' + esc(a.profile) + ", per day</h3>" +
      '<div class="seg">' + [30, 90].map(function (r) { return '<button class="btn' + (p.range === r ? " on" : "") + '" type="button" data-crange="' + r + '">' + r + " d</button>"; }).join("") + "</div></div>" +
      '<p class="q">USD per day, stacked by service. The dashed line is the normal level; a red mark is a day above it. Tap a bar for its services.</p>' +
      costChart(a, from, shown, col, sel, anomalyDay) + detail +
      '<div class="schips">' + chips + "</div>" + table + "</article>" +
      (anomalies ? '<h2>Above normal, last ' + p.range + ' days</h2><article class="card">' + anomalies + "</article>" : "") +
      '<p class="q">Cost Explorer read ' + esc(String(a.fetched || "?")) + ". Totals are in USD before tax.</p>" + back;
  }

  // ---- notifications ------------------------------------------------------------

  var push = { status: "", busy: false };
  function pushState() {
    if (!("serviceWorker" in navigator) || !("PushManager" in window) || !("Notification" in window)) return "unsupported";
    return Notification.permission;
  }
  function pushCard() {
    var st = pushState(), body;
    if (st === "unsupported") body = '<p class="line">Add podbay to the Home Screen first: tap Share, then Add to Home Screen, and open it from there. Notifications work only in the installed app.</p>';
    else if (st === "denied") body = '<p class="line">Notifications are blocked for podbay. Allow them in Settings, Notifications, podbay.</p>';
    else body = '<p class="line">' + (st === "granted" ? "On for this phone. Tap again after reinstalling the app." : "Pushes from the Mac open the line they are about, here.") + "</p>" +
      '<div class="row"><button class="btn' + (st === "granted" ? "" : " primary") + '" type="button" data-push="1"' + (push.busy ? " disabled" : "") + ">" + (st === "granted" ? "Register this phone again" : "Turn on notifications") + "</button></div>";
    return '<h2>Notifications</h2><article class="card">' + body + (push.status ? '<p class="fine">' + esc(push.status) + "</p>" : "") + "</article>";
  }
  function turnOnPush() {
    if (!CFG.vapidKey) { push.status = "This deploy has no push key."; render(); return; }
    push.busy = true; push.status = ""; render();
    Notification.requestPermission().then(function (perm) {
      if (perm !== "granted") throw new Error("Notifications were not allowed.");
      return navigator.serviceWorker.ready;
    }).then(function (reg) {
      return reg.pushManager.getSubscription().then(function (old) {
        return old || reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: b64uToBuf(CFG.vapidKey) });
      });
    }).then(function (sub) {
      return api("POST", "push", { subscription: sub.toJSON() });
    }).then(function () {
      push.status = "Saved. The Mac registers this phone within a minute and sends a test push.";
      track("push", { step: "on" });
    }, function (e) {
      push.status = e.message || "Could not turn notifications on.";
    }).finally(function () { push.busy = false; render(); });
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
    return "<h1>Nightly runs</h1><p class=\"sub\">" + esc(summary) + "</p>" +
      '<article class="card chartcard"><h3>Every job, the last 7 nights</h3><p class="q">One cell per scheduled run, today on the right</p>' + g +
      '<div class="legend"><span class="ok">passed</span><span class="fail">failed</span><span class="skip">skipped</span><span class="miss">did not run</span><span class="unknown">no record</span><span>not scheduled</span></div></article>' +
      releasesCard(s) + runsBlock(s);
  }

  // ---- the chat with Head Jeeves -------------------------------------------------
  // docs/adr/0002-one-phone-app.md, Update 2026-11-08. thread.js puts the
  // items in time order; this draws them: his messages on the right, Head
  // Jeeves' on the left, a day label where the day changes, and a reply to
  // a board line as one compact entry that opens that line's sheet.

  var CHAT_LINE = "head-jeeves";
  var STATE_WORD = { queued: "waiting for the Mac", delivering: "sending", delivered: "delivered", answered: "answered", expired: "not sent" };
  function dayLabel(epoch) {
    var d = new Date(epoch * 1000), t = new Date();
    var start = function (x) { return new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime(); };
    var diff = Math.round((start(t) - start(d)) / 86400000);
    if (diff === 0) return "Today";
    if (diff === 1) return "Yesterday";
    return ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"][d.getDay()] + " " + d.getDate() + " " +
      ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"][d.getMonth()];
  }
  function clip(text, n) {
    var t = String(text || "").replace(/\s+/g, " ").trim();
    return t.length > n ? t.slice(0, n - 1) + "…" : t;
  }
  // The small line under one of his own messages: its time and what
  // happened to it; a message the Mac never took gets "Send again".
  function myMeta(m) {
    if (m.state === "expired") return '<div class="meta bad">' + esc(hhmm(m.created)) + " · not sent: the Mac did not take it within 2 hours" +
      ' <button class="link-btn" type="button" data-again="' + esc(m.id) + '">Send again</button></div>';
    var word = m.state === "delivered" && m.delivered_at ? "delivered " + hhmm(m.delivered_at) : (STATE_WORD[m.state] || m.state || "");
    return '<div class="meta">' + esc(hhmm(m.created)) + (word && m.state !== "answered" ? " · " + esc(word) : "") + "</div>";
  }
  // Bubbles for a list from thread.js. In the chat, an answer that does
  // not come right after its message quotes the first words of it.
  function bubbles(items, opts) {
    var out = [], day = "";
    items.forEach(function (it, i) {
      var m = it.msg, d = dayLabel(it.at);
      if (opts.days && d !== day) { out.push('<div class="day"><span>' + esc(d) + "</span></div>"); day = d; }
      if (it.kind === "me") {
        out.push('<div class="b me" data-at="' + it.at + '"><div class="bub">' + esc(m.text) + "</div>" + myMeta(m) + "</div>");
      } else if (it.kind === "hj" || it.kind === "say") {
        var text = it.kind === "hj" ? m.reply : m.text;
        var prev = items[i - 1];
        var re = it.kind === "hj" && !(prev && prev.id === it.id && prev.kind === "me") ? '<div class="re">re: ' + esc(clip(m.text, 60)) + "</div>" : "";
        out.push('<div class="b hj" data-at="' + it.at + '"><div class="bub">' + re + esc(text) + '</div><div class="meta">' + esc(hhmm(it.at)) + "</div></div>");
      } else {
        var ns = String(m.line || "").indexOf("new-session:") === 0;
        var about = ns ? "New session in " + m.line.slice(12) : "“" + clip(m.quote || m.target || m.line, 70) + "”";
        var st = m.state === "answered" ? '<span class="st ok">answered</span>' : m.state === "expired" ? '<span class="st bad">not sent</span>' : '<span class="st">' + esc(STATE_WORD[m.state] || m.state || "") + "</span>";
        var tag = ns ? "div" : "button";
        out.push('<div class="b me" data-at="' + it.at + '"><' + tag + ' class="lref"' + (ns ? "" : ' type="button" data-open-line="' + esc(m.line) + '"') + ">" +
          '<span class="lq">' + esc(about) + "</span>" +
          '<span class="lt">' + esc(clip(m.text, 90)) + "</span>" +
          '<span class="lf">' + (ns ? "" : esc(m.target || "") + " · ") + st + (ns ? "" : ' <span class="go">›</span>') + "</span></" + tag + '><div class="meta">' + esc(hhmm(m.created)) + "</div></div>");
      }
    });
    return out.join("");
  }
  function threadList() {
    var items = window.PodbayThread.chat(app.messages);
    if (!items.length) return '<p class="none center">No messages yet. Write to Head Jeeves below.</p>';
    return bubbles(items, { days: true });
  }
  function composer() {
    var me = app.me || {}, act = "", note = "";
    var box = '<textarea id="chat" rows="1" maxlength="1000" placeholder="Message Head Jeeves" aria-label="Message Head Jeeves"' + (app.me ? "" : " disabled") + ">" + esc(app.draft || "") + "</textarea>";
    if (!app.me) note = "";
    else if (canSend() || me.passkeys > 0) {
      act = '<button class="send" type="button" data-chat-send="1" aria-label="' + (canSend() ? "Send" : "Confirm with your passkey and send") + '"><svg viewBox="0 0 24 24"><path d="M12 19V5M5 12l7-7 7 7"/></svg></button>';
      if (!canSend()) note = '<p class="fine">Send asks for your passkey once, then works for 30 days.</p>';
    } else if (me.enrol_open) note = '<div class="row"><button class="btn primary wide" type="button" data-add-passkey="1">Add a passkey to send</button></div>';
    else note = '<p class="fine">Sending needs a passkey, and one can be added only from the Mac: podbay mobile passkey-allow.</p>';
    return '<div class="composer"><p class="err" id="chat-err" hidden></p><div class="cin">' + box + act + "</div>" + note + "</div>";
  }
  function thread() {
    return '<div class="chathead"><span class="eye" aria-hidden="true"></span><div><b>Head Jeeves</b><small>Gets your message in about 15 s</small></div></div>' +
      '<div id="thread-list" class="convo">' + threadList() + "</div>" + composer();
  }
  // On a board card: how many replies the line has and the latest state,
  // so a reply and its answer are found where the line is.
  function lineThread(id) {
    var mine = window.PodbayThread.forLine(app.messages, id).filter(function (it) { return it.kind === "me"; });
    if (!mine.length) return "";
    var last = mine[mine.length - 1].msg;
    var word = last.state === "answered" ? "answered " + (last.replied_at ? hhmm(last.replied_at) : "") : (STATE_WORD[last.state] || last.state);
    return '<button class="thr' + (last.state === "answered" ? " ok" : last.state === "expired" ? " bad" : "") + '" type="button" data-reply="' + esc(id) + '">' +
      mine.length + (mine.length === 1 ? " reply" : " replies") + " · " + esc(word) + "</button>";
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
  // A line's sheet: the line quoted, every reply to it and Head Jeeves'
  // answers in time order, then the box for the next one.
  function lineHistory(id) {
    var items = window.PodbayThread.forLine(app.messages, id);
    return items.length ? '<div class="convo insheet">' + bubbles(items, { days: false }) + "</div>" : "";
  }
  function replySheet(id, prefill, option) {
    var info = lineInfo(id), item = findItem(id), hist = lineHistory(id);
    if (!info) {
      var old = app.messages.filter(function (m) { return m.line === id && m.from !== "head-jeeves"; })[0];
      return '<button class="scrim" type="button" data-close="1" aria-label="Close"></button><section class="sheet" role="dialog" aria-label="Replies"><div class="grab"></div>' +
        (old ? '<div class="mini"><div class="repo">' + esc(old.target || "") + '</div><button class="btn ghost" type="button" data-close="1">Close</button></div>' +
          (old.quote ? '<div class="quote">' + esc(old.quote) + "</div>" : "") + hist : "") +
        '<p class="err">That line is no longer on the board, so a new reply cannot go to it.</p><div class="row"><button class="btn wide" type="button" data-close="1">Close</button></div></section>';
    }
    var opts = item && item.options && item.options.length ? '<div class="opts">' + item.options.map(function (o) {
      return '<button class="btn opt' + (o === option ? " on" : "") + '" type="button" data-pick="' + esc(o) + '">' + esc(o) + "</button>";
    }).join("") + "</div>" : "";
    var newSession = id === "new-session:" ? true : false;
    var repos = newSession ? '<label class="f" for="repo">Repo</label><select id="repo">' + ((app.state && app.state.repos) || []).map(function (r) { return '<option value="' + esc(r) + '">' + esc(r) + "</option>"; }).join("") + "</select>" : "";
    var title = newSession ? "Start a session" : info.target;
    return '<button class="scrim" type="button" data-close="1" aria-label="Close"></button><section class="sheet" role="dialog" aria-label="Message">' +
      '<div class="grab"></div><div class="mini"><div class="repo">' + esc(title) + '</div><button class="btn ghost" type="button" data-close="1">Close</button></div>' +
      (info.quote ? '<div class="quote">' + esc(info.quote) + "</div>" : "") + hist + opts + repos +
      '<label class="f" for="text">' + (opts ? "Or write the answer" : newSession ? "What the session should do" : hist ? "Another reply" : "Your message") + "</label>" +
      '<textarea id="text" maxlength="1000" placeholder="One line for Head Jeeves">' + esc(option || prefill || "") + "</textarea>" +
      sendBlock() + '<p class="err" id="sheet-err" hidden></p>' +
      '<p class="fine">Reaches Head Jeeves in about 15 s' + (info.target && !newSession ? " and goes to " + esc(info.target) : "") + ' with the line quoted. His answer shows here. If the Mac does not take it within 2 hours, it is not sent.</p></section>';
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
    pushes: '<svg viewBox="0 0 24 24"><path d="M6 16V11a6 6 0 0 1 12 0v5l2 2H4z"/><path d="M10 20a2 2 0 0 0 4 0"/></svg>',
  };
  function tabs(on) {
    var s = app.state, dec = s ? (s.board.sections.decision || []).length : 0, bad = 0;
    if (s) s.nightly.jobs.forEach(function (j) { var c = j.cells[j.cells.length - 1]; if (c && (c.state === "fail" || c.state === "miss")) bad++; });
    var queued = app.messages.filter(function (m) { return m.state === "expired"; }).length;
    var open = {};
    if (s && s.pushes) s.pushes.items.forEach(function (it) { if (it.kind === "alarm" && it.state_now === "ALARM") open[it.alarm] = 1; });
    var alarms = Object.keys(open).length;
    return [["board", "Board", dec ? '<span class="badge amber">' + dec + "</span>" : ""], ["machine", "Machine", s && s.machine.overloaded ? '<span class="badge">!</span>' : ""],
      ["nightly", "Nightly", bad ? '<span class="badge">' + bad + "</span>" : ""], ["thread", "Thread", queued ? '<span class="badge">' + queued + "</span>" : ""], ["pushes", "Pushes", alarms ? '<span class="badge">' + alarms + "</span>" : ""]]
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
        if ($("#main").contains(document.activeElement)) document.activeElement.blur();
        $("#main").innerHTML = signInScreen(signin.stage, signin.email, signin.err);
        $("#overlay").innerHTML = "";
        return;
      }
      freshness();
      $("#tabs").innerHTML = tabs((app.route === "stats" || app.route === "costs" ? "machine" : app.route === "releases" ? "nightly" : app.route));
      // While the chat box is in use, a poll redraws only the list under
      // it: the keyboard stays up and nothing typed is lost.
      var chat = $("#chat");
      var atEnd = app.route === "thread" && (app.stick || window.innerHeight + window.scrollY >= document.documentElement.scrollHeight - 160);
      document.body.classList.toggle("chatting", app.route === "thread");
      if (app.route === "thread" && chat && (document.activeElement === chat || chat.value) && $("#thread-list") && !app.chatRedraw) {
        $("#thread-list").innerHTML = threadList();
        if (atEnd) window.scrollTo(0, document.documentElement.scrollHeight);
        return;
      }
      app.chatRedraw = false;
      var s = app.state, html;
      if (!s) html = '<p class="sub boot">' + esc(app.error || "Loading…") + "</p>";
      else if (app.route === "machine") html = machine(s);
      else if (app.route === "nightly") html = nightly(s, app.sheet && app.sheet.kind === "log" ? [app.sheet.label, app.sheet.day] : null);
      else if (app.route === "thread") html = thread();
      else if (app.route === "stats") html = stats();
      else if (app.route === "costs") html = costs(s);
      else if (app.route === "releases") html = releases(s);
      else if (app.route === "pushes") html = pushes(s);
      else html = board(s);
      if (app.error && s) html = '<p class="err">' + esc(app.error) + "</p>" + html;
      if (app.route !== "thread") html += '<div class="row gap"><button class="btn ghost" type="button" data-signout="1">Sign out</button></div>';
      var keep = window.scrollY;
      // A focused box that is removed never blurs, and iOS then keeps the
      // keyboard's shorter viewport (see viewportGuard).
      if ($("#main").contains(document.activeElement)) document.activeElement.blur();
      $("#main").innerHTML = html;
      // The chat opens at its newest message and stays there while new
      // ones arrive, unless he scrolled up to read.
      if (atEnd) { window.scrollTo(0, document.documentElement.scrollHeight); if (s && app.messagesLoaded) app.stick = false; }
      else window.scrollTo(0, keep);
      var sheet = "";
      if (app.sheet && s) sheet = app.sheet.kind === "log" ? logSheet(app.sheet.label, app.sheet.day) : replySheet(app.sheet.line, app.sheet.prefill, app.sheet.option);
      // Redraw the sheet only when it or what it offers changed, and keep
      // what was typed: a poll must never wipe a half-written message.
      var overlay = $("#overlay"), hadText = $("#text") ? $("#text").value : null;
      var me = app.me || {};
      var hist = app.sheet && app.sheet.kind === "reply" ? window.PodbayThread.forLine(app.messages, app.sheet.line).map(function (it) { return [it.id, it.kind, it.msg.state]; }) : null;
      var sheetKey = JSON.stringify([app.sheet, canSend(), me.passkeys, me.enrol_open, hist]);
      if (overlay.getAttribute("data-sheet") !== sheetKey || !app.sheet) {
        var sameSheet = JSON.parse(overlay.getAttribute("data-sheet") || "[null]")[0];
        // A focused box that is removed never blurs, and iOS then keeps
        // the keyboard's shorter viewport (see viewportGuard).
        if (overlay.contains(document.activeElement)) document.activeElement.blur();
        overlay.innerHTML = sheet;
        overlay.setAttribute("data-sheet", sheetKey);
        if (hadText != null && $("#text") && JSON.stringify(sameSheet) === JSON.stringify(app.sheet)) $("#text").value = hadText;
        var sh = overlay.querySelector(".sheet .insheet");
        if (sh) { var box = overlay.querySelector(".sheet"); box.scrollTop = Math.max(0, sh.offsetTop + sh.offsetHeight - box.clientHeight * 0.6); }
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
    app.stick = route === "thread";
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
      app.route = ["board", "machine", "nightly", "thread", "stats", "costs", "releases", "pushes"].indexOf(parts[0]) >= 0 ? parts[0] : "board";
    }
    if (app.route === "thread") { app.stick = true; loadMessages(); }
    if (app.route === "stats") loadStats();
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
      if (sh.line === "new-session:") { app.sheet = null; loadMessages(); go("thread"); return; }
      // A reply to a line stays with the line: the sheet stays open and
      // shows it under the earlier ones, with its state.
      if ($("#text")) $("#text").value = "";
      app.sheet = { kind: "reply", line: sh.line, prefill: "", option: "" };
      if (btn) btn.disabled = false;
      return loadMessages().then(render);
    }).catch(function (e) {
      if (btn) btn.disabled = false;
      if (e && e.name === "NotAllowedError") sheetError("The passkey check was cancelled.");
      else if (e && e.status === 403 && e.body && e.body.error === "passkey") { loadMe().then(render); sheetError("Confirm with your passkey first."); }
      else sheetError(e && e.message ? e.message : "Could not send.");
    });
  }
  function chatError(text) {
    var el = $("#chat-err");
    if (el) { el.textContent = text; el.hidden = !text; }
  }
  function sendChat() {
    var box = $("#chat"), text = ((box && box.value) || "").trim();
    if (!text) { chatError("Write something first."); return; }
    var btn = document.querySelector("[data-chat-send]");
    if (btn) btn.disabled = true;
    app.chatKey = app.chatKey || key();
    var first = canSend() ? Promise.resolve() : checkPasskey();
    first.then(function () {
      return api("POST", "messages", { line: CHAT_LINE, text: text, key: app.chatKey });
    }).then(function () {
      track("send", { kind: "chat" });
      app.chatKey = null;
      app.draft = "";
      box.value = "";
      box.style.height = "";
      box.blur();
      app.chatRedraw = true;
      app.stick = true;
      return loadMessages().then(render);
    }).catch(function (e) {
      if (btn) btn.disabled = false;
      if (e && e.name === "NotAllowedError") chatError("The passkey check was cancelled.");
      else if (e && e.status === 403 && e.body && e.body.error === "passkey") { loadMe().then(function () { app.chatRedraw = true; render(); }); chatError("Confirm with your passkey first."); }
      else chatError(e && e.message ? e.message : "Could not send.");
    });
  }
  // Tested: a message that says only "Tested" on a shipped line. The Mac
  // marks the line tested in headlines.json and does not type it to Head
  // Jeeves. The card leaves at once; the next board upload drops it for good.
  function markTested(btn) {
    var id = btn.dataset.tested;
    btn.disabled = true;
    var first = canSend() ? Promise.resolve() : checkPasskey();
    first.then(function () {
      return api("POST", "messages", { line: id, text: "Tested", key: key() });
    }).then(function () {
      track("send", { kind: "tested" });
      app.tested[id] = true;
      render();
    }).catch(function (e) {
      btn.disabled = false;
      if (e && e.status === 403 && e.body && e.body.error === "passkey") { loadMe().then(render); }
      app.sheet = { kind: "reply", line: id, prefill: "Tested", option: "" };
      loadMe().then(render);
      render();
    });
  }
  function sendAgain(id) {
    var m = app.messages.filter(function (x) { return x.id === id; })[0];
    if (!m) return;
    if (m.line === CHAT_LINE) {
      var box = $("#chat");
      if (box) { box.value = m.text; box.focus(); }
      return;
    }
    app.sheet = { kind: "reply", line: m.line, prefill: m.text };
    render();
  }

  // ---- events -------------------------------------------------------------------------

  document.addEventListener("click", function (ev) {
    var pushCard = ev.target.closest && !ev.target.closest("a") && ev.target.closest("[data-open-push]");
    if (pushCard) { track("push-open", {}); location.hash = "#" + pushCard.getAttribute("data-open-push"); return; }
    var bar = ev.target.closest && ev.target.closest("[data-bar]");
    if (bar) {
      var b = bar.getAttribute("data-bar").split("|").map(Number), cur = app.runSel;
      app.runSel = cur && cur.c === b[0] && cur.i === b[1] ? null : { c: b[0], i: b[1] };
      track("runs-bar", { chart: b[0] === 0 ? "nights" : "pipeline" });
      render();
      return;
    }
    var cbar = ev.target.closest && ev.target.closest("[data-cbar]");
    if (cbar) {
      var cp = costsPrefs(), ci = Number(cbar.getAttribute("data-cbar"));
      cp.sel = cp.sel === ci ? -1 : ci;
      track("costs-bar", {});
      render();
      return;
    }
    var fold = ev.target.closest && ev.target.closest("[data-fold]");
    if (fold && !(ev.target.closest("a"))) {
      var fid = fold.getAttribute("data-fold");
      app.unfolded[fid] = !app.unfolded[fid];
      track("line-fold", { open: app.unfolded[fid] ? 1 : 0 });
      render();
      return;
    }
    var t = ev.target.closest("button");
    if (!t) return;
    if (t.dataset.tab) { app.sheet = null; go(t.dataset.tab); if (t.dataset.tab === "thread") loadMessages(); return; }
    if (t.dataset.close) { closeSheet(); return; }
    if (t.dataset.tested) { markTested(t); return; }
    if (t.dataset.reply) {
      app.sheet = { kind: "reply", line: t.dataset.reply, prefill: t.dataset.prefill || "", option: t.dataset.option || "" };
      history.replaceState(null, "", "#line/" + encodeURIComponent(t.dataset.reply));
      track("reply-open", { kind: t.dataset.option ? "option" : t.dataset.prefill ? "prefill" : "reply" });
      loadMe().then(render);
      render();
      return;
    }
    if (t.dataset.openLine) {
      app.sheet = { kind: "reply", line: t.dataset.openLine, prefill: "", option: "" };
      track("reply-open", { kind: "chat" });
      loadMe().then(render);
      render();
      return;
    }
    if (t.dataset.pick) { $("#text").value = t.dataset.pick; document.querySelectorAll("[data-pick]").forEach(function (b) { b.classList.toggle("on", b === t); }); return; }
    if (t.dataset.newSession) { app.sheet = { kind: "reply", line: "new-session:" }; loadMe().then(render); render(); return; }
    if (t.dataset.cell) { var p = t.dataset.cell.split("|"); app.sheet = { kind: "log", label: p[0], day: p[1] }; history.replaceState(null, "", "#nightly/" + p[0] + "/" + p[1]); track("log-open", {}); render(); return; }
    if (t.dataset.send) { send(); return; }
    if (t.dataset.chatSend) { sendChat(); return; }
    if (t.dataset.addPasskey) {
      t.disabled = true;
      addPasskey().then(function () { render(); }, function (e) { t.disabled = false; sheetError(e && e.name === "NotAllowedError" ? "The passkey was not added." : (e.message || "Could not add the passkey.")); });
      return;
    }
    if (t.dataset.again) { sendAgain(t.dataset.again); return; }
    if (t.dataset.push) { turnOnPush(); return; }
    if (t.dataset.stats) { app.sheet = null; go("stats"); loadStats(); return; }
    if (t.dataset.costs) { app.sheet = null; go("costs"); return; }
    if (t.dataset.releases) { app.sheet = null; go("releases"); return; }
    if (t.dataset.cacct) { var ca = costsPrefs(); ca.acct = t.dataset.cacct; ca.sel = -1; saveCosts(); track("costs-account", { n: [].indexOf.call(t.parentNode.children, t) }); render(); return; }
    if (t.dataset.crange) { var cr = costsPrefs(); cr.range = Number(t.dataset.crange); cr.sel = -1; saveCosts(); track("costs-range", { days: cr.range }); render(); return; }
    if (t.dataset.csvc) { var cs = costsPrefs(); if (cs.hidden[t.dataset.csvc]) delete cs.hidden[t.dataset.csvc]; else cs.hidden[t.dataset.csvc] = 1; saveCosts(); track("costs-service", { on: cs.hidden[t.dataset.csvc] ? 0 : 1 }); render(); return; }
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

  // The chat box grows with what is typed, up to the CSS max-height.
  document.addEventListener("input", function (ev) {
    if (ev.target.id !== "chat") return;
    ev.target.style.height = "auto";
    ev.target.style.height = Math.min(140, ev.target.scrollHeight + 2) + "px";
  });

  window.addEventListener("hashchange", fromHash);

  // The home-screen app on iOS 26 can keep the keyboard's shorter layout
  // viewport after the keyboard closes (WebKit bug 297779): window.innerHeight
  // stays about 300 px short, so everything at bottom: 0 (the tab bar, the
  // chat box, a sheet) floats in the middle of the screen with the page
  // under it. Only in standalone mode, where nothing but the keyboard
  // changes the height: in Safari the address bar does too. When no text
  // field has focus and the height is below the tallest seen at this width,
  // a forced reflow often makes WebKit measure again; if it is still short,
  // --stuck moves the bottom bars down by the missing height.
  // navigator.standalone is true only for the iOS home-screen app: an
  // installed desktop or Android window also matches display-mode, and a
  // window made shorter there is not this bug.
  var standalone = navigator.standalone === true;
  var fullH = {}, guardQueued = false;  // the tallest height seen, per width
  function typing() {
    var el = document.activeElement;
    return !!el && (el.tagName === "TEXTAREA" || (el.tagName === "INPUT" && el.type !== "button" && el.type !== "submit"));
  }
  function viewportGuard(heal) {
    guardQueued = false;
    if (!standalone) return;
    var w = window.innerWidth;
    if (!(window.innerHeight <= fullH[w])) fullH[w] = window.innerHeight;
    var gap = typing() ? 0 : fullH[w] - window.innerHeight;
    if (gap > 4 && heal) {
      var body = document.body, y = window.scrollY;
      body.style.display = "none";
      void body.offsetHeight;
      body.style.display = "";
      window.scrollTo(0, y);
      gap = fullH[w] - window.innerHeight;
    }
    var want = gap > 4 ? gap + "px" : "0px";
    if (document.documentElement.style.getPropertyValue("--stuck") !== want) {
      document.documentElement.style.setProperty("--stuck", want);
      if (gap > 4) track("viewport-stuck", { gap: gap });
    }
  }
  function queueGuard() {
    if (guardQueued) return;
    guardQueued = true;
    requestAnimationFrame(function () { viewportGuard(false); });
  }
  function afterKeyboard() { setTimeout(function () { viewportGuard(true); }, 150); setTimeout(function () { viewportGuard(true); }, 600); }
  viewportGuard(false);
  document.addEventListener("focusout", afterKeyboard);
  window.addEventListener("resize", queueGuard);
  window.addEventListener("scroll", queueGuard, { passive: true });
  if (window.visualViewport) window.visualViewport.addEventListener("resize", queueGuard);
  document.addEventListener("visibilitychange", function () { if (!document.hidden) afterKeyboard(); });

  // A tapped notification: go to its line. sw.js writes the tap's target
  // to the "podbay-open" cache before it opens or wakes the app, because on
  // iOS the hash in the URL and the worker's message can both be lost (the
  // reasons are in sw.js). So the page takes the target from there when it
  // starts, when it becomes visible and when the worker posts. A target
  // older than OPEN_FRESH is from a tap that already led somewhere.
  var OPEN_FRESH = 120000;
  function openHash(hash) {
    if (location.hash === hash) fromHash();
    else { history.replaceState(null, "", hash); fromHash(); }
    loadState();
    if (app.sheet && app.sheet.kind === "reply") loadMessages();
  }
  function takeOpen() {
    if (!window.caches) return Promise.resolve(false);
    return caches.open("podbay-open").then(function (c) {
      return c.match("/__open").then(function (res) {
        if (!res) return null;
        return c.delete("/__open").then(function () { return res.json(); });
      });
    }).then(function (open) {
      if (!open || !open.hash || Date.now() - (open.at || 0) > OPEN_FRESH || !(loadAuth() || {}).refresh) return false;
      track("push-open", {});
      openHash(open.hash);
      return true;
    }).catch(function () { return false; });
  }
  if ("serviceWorker" in navigator) navigator.serviceWorker.addEventListener("message", function (ev) {
    if (!ev.data || ev.data.type !== "open" || !ev.data.hash) return;
    var hash = ev.data.hash;
    takeOpen().then(function (taken) { if (!taken) openHash(hash); });
  });
  document.addEventListener("visibilitychange", function () {
    if (document.hidden || !loadAuth()) return;
    takeOpen();
    loadState(); loadMe();
  });

  // ---- start ----------------------------------------------------------------------------

  var timers = [];
  function start() {
    timers.forEach(clearInterval);
    timers = [];
    if (!(loadAuth() || {}).refresh) { render(); return; }
    fromHash();
    takeOpen();
    loadState();
    loadMe();
    loadMessages();
    timers.push(setInterval(function () { if (!document.hidden) loadState(); }, STATE_EVERY));
    timers.push(setInterval(function () { if (!document.hidden && (app.route === "thread" || (app.sheet && app.sheet.kind === "reply") || app.messages.some(function (m) { return m.state === "queued" || m.state === "delivered"; }))) loadMessages(); }, MESSAGES_EVERY));
    timers.push(setInterval(function () { if (app.state) freshness(); }, 5000));
  }
  if ("serviceWorker" in navigator) {
    // A new worker takes over after a deploy: reload once so the page runs
    // the new shell. Not on the first install (no controller before it).
    var hadController = !!navigator.serviceWorker.controller;
    var reloaded = false;
    navigator.serviceWorker.addEventListener("controllerchange", function () {
      if (!hadController) { hadController = true; return; }
      if (reloaded) return;
      reloaded = true;
      location.reload();
    });
    navigator.serviceWorker.register("/sw.js").then(function (reg) { reg.update().catch(function () {}); }).catch(function () {});
    document.addEventListener("visibilitychange", function () {
      if (!document.hidden) navigator.serviceWorker.getRegistration().then(function (reg) { if (reg) reg.update().catch(function () {}); }).catch(function () {});
    });
  }
  start();
})();
