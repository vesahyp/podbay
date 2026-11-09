"""The Mac side of podbay mobile (mobile.py): upload and delivery."""

from __future__ import annotations

import gzip
import json

from podbay import mobile

NOW = 1_800_000_000.0


class FakeStore:
    def __init__(self, items: list[dict]):
        self.items = {i["sk"]: dict(i) for i in items}
        self.puts: list[dict] = []

    def recent(self, since: float) -> list[dict]:
        return [dict(i) for sk, i in sorted(self.items.items()) if sk >= mobile.id_at(since)]

    def mark(self, sk: str, expected: str | None, fields: dict) -> bool:
        item = self.items.get(sk)
        if item is None or (expected is not None and item.get("state") != expected):
            return False
        item.update(fields)
        return True

    def put(self, item: dict) -> None:
        self.puts.append(item)


def _msg(n: int, **kw) -> dict:
    created = NOW - 60
    item = {"pk": "MSG", "sk": f"{int(created * 1000):013d}{n:08x}", "state": "queued", "created": int(created),
            "expires": int(created + 7200), "target": "#6 sora", "quote": 'Count from "start"?', "text": "Start line"}
    item.update(kw)
    return item


def test_event_line_is_one_line_with_the_quote():
    line = mobile.event_line(_msg(1, text="start\nline   now"))
    assert line.startswith("/head-jeeves phone ")
    assert line.endswith(" #6 sora, on \"Count from 'start'?\": start line now")
    assert "\n" not in line
    assert mobile.event_line(_msg(2, quote="")).endswith(" #6 sora: Start line")


def test_poll_delivers_expires_and_skips_test_messages():
    store = FakeStore([_msg(1), _msg(2, expires=int(NOW - 1)), _msg(3, test=True), _msg(4, state="delivered")])
    typed: list[str] = []
    done = dict(mobile.poll(store, lambda line: typed.append(line) or True, now=NOW))
    ids = sorted(store.items)
    assert done == {ids[0]: "delivered", ids[1]: "expired", ids[2]: "test"}
    assert len(typed) == 1 and typed[0].startswith(f"/head-jeeves phone {ids[0]} ")
    assert store.items[ids[0]]["state"] == "delivered"
    assert store.items[ids[1]]["state"] == "expired"
    assert store.items[ids[2]]["note"] == "test user, not typed"
    # A second pass types nothing again.
    assert mobile.poll(store, lambda line: typed.append(line) or True, now=NOW) == []
    assert len(typed) == 1


def test_poll_leaves_a_failed_send_queued_for_the_next_pass():
    store = FakeStore([_msg(1)])
    sk = next(iter(store.items))
    assert mobile.poll(store, lambda line: False, now=NOW) == [(sk, "retry")]
    assert store.items[sk]["state"] == "queued"
    assert mobile.poll(store, lambda line: True, now=NOW) == [(sk, "delivered")]


def test_a_chat_message_reaches_head_jeeves_as_a_message_to_him():
    """The Thread tab's chat (line head-jeeves): no quote, the target is
    Head Jeeves himself, and his reply lands under it."""
    store = FakeStore([_msg(1, line="head-jeeves", target="head-jeeves", quote="", text="What runs tonight?")])
    typed: list[str] = []
    [(sk, outcome)] = mobile.poll(store, lambda line: typed.append(line) or True, now=NOW)
    assert outcome == "delivered" and typed == [f"/head-jeeves phone {sk} head-jeeves: What runs tonight?"]
    assert mobile.reply(store, sk, "The usual sweep from 01:00.")
    assert store.items[sk]["state"] == "answered"


def test_a_fresh_test_message_is_left_for_the_checks_fake_head_jeeves():
    fresh = _msg(1, test=True, created=int(NOW - 2))
    store = FakeStore([fresh])
    sk = fresh["sk"]
    # The screen leaves it alone for TEST_GRACE seconds...
    assert mobile.poll(store, lambda line: True, now=NOW) == []
    # ...so the check's fake Head Jeeves takes it and gets the event line.
    typed: list[str] = []
    assert mobile.poll(store, lambda line: typed.append(line) or True, now=NOW, fake=True) == [(sk, "delivered")]
    assert typed[0].startswith(f"/head-jeeves phone {sk} ")
    # The fake mode never touches a real message.
    store = FakeStore([_msg(2)])
    assert mobile.poll(store, lambda line: True, now=NOW, fake=True) == []


def test_say_writes_head_jeeves_line_to_the_thread():
    store = FakeStore([_msg(1), _msg(2, test=True)])
    sk = mobile.say(store, "keitos\nimport is live.", now=NOW)
    item = store.puts[-1]
    assert item["sk"] == sk and item["from"] == "head-jeeves" and item["state"] == "said"
    assert item["text"] == "keitos import is live." and sk.startswith(mobile.id_at(NOW))
    store.items[sk] = item
    view = mobile.thread(store, now=NOW)
    assert [m["sk"] for m in view] == sorted([sk, _msg(1)["sk"]], reverse=True)
    assert all(not m.get("test") for m in view)


def test_reply_and_passkey_window():
    store = FakeStore([_msg(1)])
    sk = next(iter(store.items))
    assert mobile.reply(store, sk, "Passed to\nsora #6.")
    assert store.items[sk]["reply"] == "Passed to sora #6." and store.items[sk]["state"] == "answered"
    assert not mobile.reply(store, "nope", "x")
    until = mobile.passkey_allow(store, minutes=15)
    assert store.puts[-1]["pk"] == "ENROL" and store.puts[-1]["expires"] == until


class PushStore(FakeStore):
    def query(self, pk: str, since: float) -> list[dict]:
        return [dict(i) for i in self.items.values() if i["pk"] == pk]

    def mark(self, sk: str, expected, fields: dict, pk: str = "MSG") -> bool:
        return super().mark(sk, expected, fields)


def test_register_pushes_hands_new_subscriptions_to_the_command_once():
    sub = {"endpoint": "https://push.example/1", "keys": {"p256dh": "a", "auth": "b"}}
    store = PushStore([{"pk": "PUSHSUB", "sk": "1", "state": "new", "subscription": sub},
                       {"pk": "PUSHSUB", "sk": "2", "state": "new", "subscription": sub, "test": True},
                       {"pk": "PUSHSUB", "sk": "3", "state": "registered", "subscription": sub}])
    seen: list[str] = []
    assert dict(mobile.register_pushes(store, lambda s: seen.append(s) or True, now=NOW)) == {"1": "registered", "2": "test"}
    assert [json.loads(s) for s in seen] == [sub]
    assert mobile.register_pushes(store, lambda s: True, now=NOW) == []


def test_notify_line_url(monkeypatch, tmp_path):
    from podbay import config, notify

    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "config.json")
    assert notify.line_url("abc") is None
    config.set_value("mobile-url", "https://app.example.com/", tmp_path / "config.json")
    assert notify.line_url("nightly:backup:2026-11-08") == "https://app.example.com/#line/nightly%3Abackup%3A2026-11-08"


def test_uploader_puts_on_change_and_on_heartbeat():
    puts: list[bytes] = []
    clock = [NOW]
    up = mobile.Uploader(put=puts.append, clock=lambda: clock[0])
    state = {"generated_at": 1, "board": {"x": 1}}
    assert up.tick(state) is True
    assert json.loads(gzip.decompress(puts[0])) == state
    clock[0] += 60
    assert up.tick(dict(state, generated_at=2)) is False  # same content
    assert up.tick({"generated_at": 3, "board": {"x": 2}}) is True  # changed
    clock[0] += mobile.HEARTBEAT
    assert up.tick({"generated_at": 4, "board": {"x": 2}}) is True  # heartbeat


def test_a_tested_message_marks_the_line_and_is_not_typed():
    store = FakeStore([_msg(1, line="shipped:x", text="Tested"), _msg(2, line="shipped:gone", text="Tested")])
    typed: list[str] = []
    marked: list[str] = []
    done = dict(mobile.poll(store, lambda line: typed.append(line) or True, now=NOW,
                            tested=lambda line: marked.append(line) or line == "shipped:x"))
    ids = sorted(store.items)
    assert done == {ids[0]: "tested", ids[1]: "delivered"}
    assert marked == ["shipped:x", "shipped:gone"] and len(typed) == 1
