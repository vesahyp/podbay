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


class _Done:
    def __init__(self, code, out=""):
        self.returncode, self.stdout = code, out


def test_the_login_users_keychain_item_wins_over_a_stray_one_under_the_same_service(monkeypatch):
    # 2026-10-25: an item with account "unknown" and only MCP logins in it
    # came first in a service-only lookup, so a fresh /login read as logged out.
    items = {("Claude Code-credentials", "alice"): json.dumps(LOGGED_IN),
             ("Claude Code-credentials", "unknown"): json.dumps({"mcpOAuth": {}})}
    calls = []

    def fake_run(cmd, **_kw):
        calls.append(cmd)
        service = cmd[cmd.index("-s") + 1]
        if "-a" in cmd:
            secret = items.get((service, cmd[cmd.index("-a") + 1]))
        else:
            secret = items[(service, "unknown")]  # what the keychain returns first
        return _Done(0, secret) if secret else _Done(44)

    monkeypatch.setattr(accounts.subprocess, "run", fake_run)
    monkeypatch.setattr(accounts, "_keychain_user", lambda: "alice")
    assert json.loads(accounts._read_keychain("Claude Code-credentials")) == LOGGED_IN
    # No item under the user's name: the service-only lookup still answers.
    monkeypatch.setattr(accounts, "_keychain_user", lambda: "someone")
    assert json.loads(accounts._read_keychain("Claude Code-credentials")) == {"mcpOAuth": {}}
    assert all("-w" in c for c in calls)


def test_a_new_login_makes_the_usage_cache_stale_at_once(tmp_path):
    old = {"claudeAiOauth": {**LOGGED_IN["claudeAiOauth"], "refreshTokenExpiresAt": 1_801_000_000_000}}
    new = {"claudeAiOauth": {**LOGGED_IN["claudeAiOauth"], "refreshTokenExpiresAt": 1_802_000_000_000}}
    assert accounts.login_stamp(LOGGED_OUT) is None
    assert accounts.login_stamp(old) == 1_801_000_000_000
    path = tmp_path / "usage.json"
    usage.write_cache({"fetched_at": 9e12, "entries": [], "login": accounts.login_stamp(old)}, path)
    assert usage.read_cache(path, login=accounts.login_stamp(old)) is not None
    assert usage.read_cache(path, login=accounts.login_stamp(new)) is None


def test_a_five_hour_window_past_its_reset_takes_the_usage_figure(tmp_path):
    default, _ = _accounts(tmp_path)
    # The newest snapshot is from before the login went, days ago.
    stale = {"five_pct": 91.0, "five_resets_at": NOW - 5 * 86400, "week_pct": 25.0, "week_resets_at": LATER}
    fresh = {"fetched_at": NOW, "entries": [{"key": "session", "label": None, "pct": 12.0, "resets_at": LATER}]}
    entry = quota.account_entry(default, stale, fresh, NOW, logged_in=True)
    assert entry["five_hour"]["used_pct"] == 12.0
    assert entry["five_hour"]["resets_at"] == quota._iso(LATER)
    # Without a /usage figure the old reset time is dropped, not shown.
    assert quota.account_entry(default, stale, None, NOW)["five_hour"] == {"used_pct": 0.0, "left_pct": 100.0, "resets_at": None}
