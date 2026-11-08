// The order of the phone's chat (mobile/app/thread.js), in Node: every item
// at its own time, oldest first, whatever order the API returned them in.
// docs/adr/0002-one-phone-app.md, Update 2026-11-08. Run by
// tests/test_mobile_thread.py and make mobile-test.
import { readFileSync } from "node:fs";
import vm from "node:vm";
import assert from "node:assert/strict";

const source = readFileSync(new URL("../mobile/app/thread.js", import.meta.url), "utf8");
const ctx = { globalThis: {} };
ctx.globalThis = ctx;
vm.runInNewContext(source, ctx);
const T = ctx.PodbayThread;

const fixture = JSON.parse(readFileSync(new URL("../tests/fixtures/mobile/messages.json", import.meta.url), "utf8")).messages;

// The API returns newest first by id; shuffle it so the order cannot come
// from the input.
const shuffled = [...fixture].sort((a, b) => (a.text || "").length % 7 - (b.text || "").length % 7);
const chat = T.chat(shuffled);

for (let i = 1; i < chat.length; i++) assert.ok(chat[i - 1].at <= chat[i].at, `chat item ${i} is before item ${i - 1}`);
assert.deepEqual(T.chat([...fixture].reverse()).map((x) => x.kind + x.id), chat.map((x) => x.kind + x.id), "the order depends on the input order");

// A line reply is one compact entry in the chat, and its answer is not.
const lineMsgs = fixture.filter((m) => m.line && m.line !== T.CHAT_LINE && !m.from);
for (const m of lineMsgs) {
  const inChat = chat.filter((x) => x.id === m.id);
  assert.equal(inChat.length, 1, `line reply ${m.id} shows ${inChat.length} times in the chat`);
  assert.equal(inChat[0].kind, "line");
  assert.equal(inChat[0].at, m.created);
}
// An answer to a chat message is at replied_at, not at its message's time.
for (const m of fixture.filter((x) => x.line === T.CHAT_LINE && x.reply)) {
  const hj = chat.find((x) => x.id === m.id && x.kind === "hj");
  assert.equal(hj.at, m.replied_at);
}
// Every say line is a Head Jeeves item.
assert.equal(chat.filter((x) => x.kind === "say").length, fixture.filter((m) => m.from === "head-jeeves").length);

// A line's sheet: only that line, message before its answer, oldest first.
for (const line of new Set(lineMsgs.map((m) => m.line))) {
  const h = T.forLine(shuffled, line);
  for (let i = 1; i < h.length; i++) assert.ok(h[i - 1].at <= h[i].at, `${line}: item ${i} out of order`);
  assert.ok(h.every((x) => x.msg.line === line));
}
console.log(`ok   thread order: ${chat.length} chat items, ${lineMsgs.length} line replies`);
