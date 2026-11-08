"""The podbay mobile API's pure checks (mobile/api/handler.py)."""

from __future__ import annotations

import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location("mobile_api", Path(__file__).resolve().parent.parent / "mobile" / "api" / "handler.py")
api = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(api)

STATE = {"lines": {"s1": {"target": "#6 sora", "quote": "Start line or checkpoint?"}, "machine": {"target": "machine", "quote": "the Mac"}},
         "repos": ["atlas", "sora"]}


def test_clean_text_is_one_line_without_control_characters():
    assert api.clean_text("  start\nline\t now \x1b[2J​ ") == "start line now [2J"
    assert api.clean_text("") is None
    assert api.clean_text("   \n ") is None
    assert api.clean_text(42) is None
    assert api.clean_text("x" * api.MAX_TEXT) == "x" * api.MAX_TEXT
    assert api.clean_text("x" * (api.MAX_TEXT + 1)) is None


def test_resolve_line_only_from_the_snapshot():
    assert api.resolve_line(STATE, "s1") == {"target": "#6 sora", "quote": "Start line or checkpoint?"}
    assert api.resolve_line(STATE, "new-session:atlas") == {"target": "new session in atlas", "quote": ""}
    assert api.resolve_line(STATE, "new-session:../../etc") is None
    assert api.resolve_line(STATE, "nope") is None
    assert api.resolve_line(None, "s1") is None
    assert api.resolve_line(STATE, {"target": "x"}) is None


def test_ids_sort_by_time():
    a, b = api.new_id(1000.0), api.new_id(1000.5)
    assert a < b and a.startswith(api.id_at(1000.0))


def test_handler_refuses_without_claims_and_with_an_id_token():
    assert api.handler({"routeKey": "GET /api/me"})["statusCode"] == 401
    event = {"routeKey": "GET /api/me", "requestContext": {"authorizer": {"jwt": {"claims": {"sub": "x", "token_use": "id"}}}}}
    assert api.handler(event)["statusCode"] == 401


def test_respond_writes_dynamodb_numbers():
    from decimal import Decimal

    body = api.respond(200, {"created": Decimal("1794126983"), "pct": Decimal("1.5"), "test": True})["body"]
    assert body == '{"created": 1794126983, "pct": 1.5, "test": true}'


def test_is_test_reads_the_group_claim():
    assert api.is_test({"cognito:groups": "[test]"})
    assert not api.is_test({})


def test_the_chat_line_resolves_and_head_jeeves_lines_show_to_the_owner_only():
    state = {**STATE, "lines": {**STATE["lines"], "head-jeeves": {"target": "head-jeeves", "quote": ""}}}
    assert api.resolve_line(state, "head-jeeves") == {"target": "head-jeeves", "quote": ""}
    said = {"sk": "1", "from": "head-jeeves", "text": "keitos import is live", "sub": ""}
    mine = {"sk": "2", "sub": "owner", "text": "hi"}
    other = {"sk": "3", "sub": "someone", "text": "x"}
    owner, tester = {"sub": "owner"}, {"sub": "tester", "cognito:groups": "[test]"}
    assert [api.visible(i, owner) for i in (said, mine, other)] == [True, True, False]
    assert [api.visible(i, tester) for i in (said, mine, other)] == [False, False, False]
    assert api._message_view(said)["from"] == "head-jeeves"
