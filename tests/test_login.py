"""An account whose login has expired: never suggested, shown as logged out,
and its sessions marked "login expired" (2026-10-24: the default account had
been logged out for a day while `podbay accounts` still suggested it)."""

import json

from podbay import accounts, board, quota, usage
from podbay.accounts import Account
from podbay.inventory import inventory_payload, render_status, render_table

from .test_inventory import _session

NOW = 1_800_000_000.0
LATER = NOW + 3600
# What /logout and an expired login leave in the keychain item.
LOGGED_OUT = {"claudeAiOauth": {"accessToken": "", "refreshToken": "", "expiresAt": 0, "refreshTokenExpiresAt": 1792735745483}}
LOGGED_IN = {"claudeAiOauth": {"accessToken": "a" * 108, "refreshToken": "r" * 108, "expiresAt": (NOW + 600) * 1000}}


def _accounts(tmp_path):
    default = tmp_path / ".claude"
    personal = tmp_path / ".claude-personal"
    default.mkdir()
    personal.mkdir()
    return Account("claude", default), Account("personal", personal)


def test_credential_states():
    assert accounts.credential_logged_out(LOGGED_OUT, NOW)
    assert accounts.credential_logged_out(None, NOW)
    assert accounts.credential_logged_out({}, NOW)
    assert not accounts.credential_logged_out(LOGGED_IN, NOW)
    # An expired access token is fine while a refresh token can renew it.
    expired = {"claudeAiOauth": {**LOGGED_IN["claudeAiOauth"], "expiresAt": (NOW - 60) * 1000}}
    assert not accounts.credential_logged_out(expired, NOW)
    expired["claudeAiOauth"]["refreshToken"] = ""
    assert accounts.credential_logged_out(expired, NOW)


def test_keychain_service_is_hashed_for_every_account_but_the_default(tmp_path):
    default, personal = _accounts(tmp_path)
    assert default.keychain_service == "Claude Code-credentials"
    assert personal.keychain_service.startswith("Claude Code-credentials-")
    assert len(personal.keychain_service) == len("Claude Code-credentials-") + 8


def test_logged_in_reads_the_keychain_then_the_credentials_file(tmp_path):
    default, personal = _accounts(tmp_path)
    items = {default.keychain_service: json.dumps(LOGGED_OUT)}
    read = items.get
    assert accounts.logged_in(default, read) is False
    # No keychain item and no file: unknown, so never called logged out.
    assert accounts.logged_in(personal, read) is None
    (personal.config_dir / ".credentials.json").write_text(json.dumps(LOGGED_IN))
    assert accounts.logged_in(personal, read) is True
    assert accounts.logged_out_labels([default, personal], read) == {"claude"}


def test_the_default_account_never_inherits_the_callers_config_dir(tmp_path, monkeypatch):
    """The root cause: podbay run from a personal session has
    CLAUDE_CONFIG_DIR set, and /usage for the default account read the
    personal account's figures, so they always looked fresh."""
    default, personal = _accounts(tmp_path)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(personal.config_dir))
    assert "CLAUDE_CONFIG_DIR" not in default.run_env()
    assert personal.run_env()["CLAUDE_CONFIG_DIR"] == str(personal.config_dir)

    seen = {}

    def run(_cmd, **kwargs):
        seen.update(kwargs["env"])
        raise OSError

    monkeypatch.setattr(usage.subprocess, "run", run)
    assert usage.fetch(account=default) is None
    assert "CLAUDE_CONFIG_DIR" not in seen


def test_a_logged_out_account_is_never_suggested(tmp_path):
    """The 2026-10-24 figures: the default account's 5-hour window had reset
    four days earlier, so it read as empty and won on its 5x plan."""
    default, personal = _accounts(tmp_path)
    quota.state_file(default).write_text(json.dumps({"oauthAccount": {"organizationRateLimitTier": "default_claude_max_5x"}}))
    stale = {"five_pct": 90.0, "five_resets_at": NOW - 4 * 86400, "week_pct": 25.0, "week_resets_at": LATER}
    entries = [
        quota.account_entry(default, stale, None, NOW, logged_in=False),
        quota.account_entry(personal, {"five_pct": 21.0, "five_resets_at": LATER, "week_pct": 20.0, "week_resets_at": LATER}, None, NOW, logged_in=True),
    ]
    data = quota.payload(entries)
    assert data["accounts"][0]["logged_out"] and not data["accounts"][1]["logged_out"]
    assert {o["account"] for o in data["options"]} == {"personal"}
    assert data["suggested"]["heavy"]["account"] == "personal"
    assert data["suggested"]["routine"]["account"] == "personal"
    assert quota.best_model(entries, "claude") is None
    assert "claude: logged out" in quota.render(data)
    # Unknown login state is read as logged in.
    assert not quota.account_entry(default, stale, None, NOW)["logged_out"]


def test_inventory_marks_sessions_on_a_logged_out_account():
    dead, alive = _session("raide-rt2"), _session("vinkki-1")
    dead.account, alive.account = "claude", "personal"
    payload = inventory_payload([dead, alive], set(), logged_out={"claude"})
    by_name = {s["name"]: s for s in payload["sessions"]}
    assert by_name["raide-rt2"]["login_expired"] and not by_name["vinkki-1"]["login_expired"]
    assert payload["logged_out_accounts"] == ["claude"]
    assert payload["login_expired"] == ["raide-rt2"]
    assert "logged out: claude (run /login); login expired in: raide-rt2" in render_status(payload)
    assert "login-expired" in render_table(payload)
    assert inventory_payload([dead], set())["login_expired"] == []


def test_board_machine_room_shows_the_logged_out_account_and_its_sessions():
    session = {
        "name": "raide-rt2", "title": "Raide", "tab": "3", "repo": "raide", "state": "working", "age_seconds": 30,
        "parked_until": None, "last_text": "x", "waiting_on": None, "has_transcript": True, "head_jeeves": False,
        "context_pct": 10.0, "work_repo": "raide", "session_id": "s-1", "short_id": "s-1", "login_expired": True,
    }
    limits = {"claude": {"five_pct": 0.0, "five_resets_at": None, "week_pct": 25.0, "week_resets_at": None},
              "personal": {"five_pct": 21.0, "five_resets_at": None, "week_pct": 20.0, "week_resets_at": None}}
    html = board.render({"sessions": [session], "logged_out_accounts": ["claude"]}, limits=limits)
    room = html[html.index("Machine room"):]
    assert "<b>claude</b> <span class=\"out\">logged out · run /login</span>" in room
    assert room.count("<b>claude</b>") == 1 and "<b>personal</b>" in room
    assert "login expired" in room
