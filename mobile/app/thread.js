// The order of the phone's messages (docs/adr/0002-one-phone-app.md, Update
// 2026-11-08: the Thread tab is one chat). Pure functions over the list
// GET /api/messages returns, in any order. Loaded by index.html before
// app.js; scripts/thread_check.mjs tests it in Node.
(function (root) {
  "use strict";

  var CHAT_LINE = "head-jeeves";

  function num(v) { var n = Number(v); return isFinite(n) ? n : 0; }

  // Same time: the message before its answer, then by id, which sorts by
  // creation time as a string (mobile/api/handler.py new_id).
  function byTime(a, b) {
    if (a.at !== b.at) return a.at - b.at;
    if (a.rank !== b.rank) return a.rank - b.rank;
    return a.id < b.id ? -1 : a.id > b.id ? 1 : 0;
  }

  // The chat with Head Jeeves, oldest first. Each item is at its own time:
  //   me    a message to Head Jeeves, at created
  //   hj    his answer to it, at replied_at
  //   say   a line he wrote himself (podbay say), at created
  //   line  a reply to a board line, one compact entry at created; its
  //         answer lives in that line's sheet only
  function chat(messages) {
    var out = [];
    (messages || []).forEach(function (m) {
      var id = String(m.id || "");
      if (m.from === "head-jeeves") { out.push({ kind: "say", at: num(m.created), rank: 0, id: id, msg: m }); return; }
      if (m.line === CHAT_LINE) {
        out.push({ kind: "me", at: num(m.created), rank: 0, id: id, msg: m });
        if (m.reply) out.push({ kind: "hj", at: num(m.replied_at) || num(m.created), rank: 1, id: id, msg: m });
        return;
      }
      out.push({ kind: "line", at: num(m.created), rank: 0, id: id, msg: m });
    });
    return out.sort(byTime);
  }

  // One board line's replies and their answers, oldest first.
  function forLine(messages, line) {
    var out = [];
    (messages || []).forEach(function (m) {
      if (m.from === "head-jeeves" || m.line !== line) return;
      var id = String(m.id || "");
      out.push({ kind: "me", at: num(m.created), rank: 0, id: id, msg: m });
      if (m.reply) out.push({ kind: "hj", at: num(m.replied_at) || num(m.created), rank: 1, id: id, msg: m });
    });
    return out.sort(byTime);
  }

  var api = { CHAT_LINE: CHAT_LINE, chat: chat, forLine: forLine };
  root.PodbayThread = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof window !== "undefined" ? window : globalThis);
