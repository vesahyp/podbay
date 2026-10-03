"""Two Claude Code accounts on one machine: the console reads both config
dirs, labels every session, keeps the quotas apart and runs claude as the
right account when it opens or resumes a session."""

import json
import os
import shlex
from datetime import datetime
from pathlib import Path

import pytest

from podbay import app as app_mod
from podbay import history as history_mod
from podbay import usage as usage_mod
from podbay.accounts import Account, by_label, discover, label_for
from podbay.sources import gather_sessions, newest_limits, read_status_snapshots
from podbay.state import StateStore

from tests.test_sources import _FakeLister, _write_registry_entry, _write_snapshot


def _accounts(home: Path) -> list[Account]:
    return [
        Account("claude", home / ".claude"),
        Account("personal", home / ".claude-personal"),
    ]


# -- accounts.py ---------------------------------------------------------------


def test_discover_lists_default_then_suffixed_dirs_that_claude_has_used(tmp_path):
    (tmp_path / ".claude" / "sessions").mkdir(parents=True)
    (tmp_path / ".claude-personal" / "projects").mkdir(parents=True)
    (tmp_path / ".claude-work" / "sessions").mkdir(parents=True)
    (tmp_path / ".claude-empty").mkdir()  # no sessions or projects: not a Claude home
    (tmp_path / ".claude-file").write_text("x")

    found = discover(tmp_path)

    assert [a.label for a in found] == ["claude", "personal", "work"]
    assert found[0].is_default and not found[1].is_default
    assert found[1].sessions_dir == tmp_path / ".claude-personal" / "sessions"
    assert found[1].projects_dir == tmp_path / ".claude-personal" / "projects"


def test_discover_without_any_dir_still_names_the_default(tmp_path):
    found = discover(tmp_path)
    assert [(a.label, a.config_dir) for a in found] == [("claude", tmp_path / ".claude")]


def test_label_for_and_by_label():
    assert label_for(Path("/u/.claude")) == "claude"
    assert label_for(Path("/u/.claude-personal")) == "personal"
    assert label_for(Path("/u/other")) == "other"
    accounts = _accounts(Path("/u"))
    assert by_label(accounts, None) is accounts[0]
    assert by_label(accounts, "personal") is accounts[1]
    assert by_label(accounts, "nope") is None


def test_env_and_command_prefix_are_empty_for_the_default_account_only():
    default, personal = _accounts(Path.home())
    assert default.env() == {}
    assert default.command_prefix() == ""
    assert personal.env() == {"CLAUDE_CONFIG_DIR": str(Path.home() / ".claude-personal")}
    assert personal.command_prefix() == "CLAUDE_CONFIG_DIR=~/.claude-personal "


# -- sources.py: one registry per account --------------------------------------


def test_gather_sessions_reads_every_account_and_labels_the_rows(tmp_path, monkeypatch):
    accounts = _accounts(tmp_path)
    pid = os.getpid()
    _write_registry_entry(accounts[0].sessions_dir, "s-default", pid, cwd="/tmp")
    _write_registry_entry(accounts[1].sessions_dir, "s-personal", pid, cwd="/tmp")

    sessions = gather_sessions(
        StateStore(tmp_path / "state.json"),
        _FakeLister({}),
        status_snapshots={},
        pid_by_tty=lambda: {},
        cwd_by_pids=lambda pids: {},
        accounts=accounts,
    )

    assert {s.session_id: s.account for s in sessions} == {"s-default": "claude", "s-personal": "personal"}


def test_gather_sessions_with_explicit_dirs_is_one_default_account(tmp_path):
    sessions_dir = tmp_path / "sessions"
    _write_registry_entry(sessions_dir, "s1", os.getpid())

    sessions = gather_sessions(
        StateStore(tmp_path / "state.json"),
        _FakeLister({}),
        sessions_dir=sessions_dir,
        projects_dir=tmp_path / "projects",
        status_snapshots={},
        pid_by_tty=lambda: {},
        cwd_by_pids=lambda pids: {},
    )

    assert [(s.session_id, s.account) for s in sessions] == [("s1", "claude")]


def test_newest_limits_keeps_the_accounts_apart(tmp_path):
    _write_snapshot(tmp_path / "a.json", "a", five={"used_percentage": 30}, week={"used_percentage": 60})
    personal = json.loads((tmp_path / "a.json").read_text())
    personal.update(session_id="b", account="personal", rate_limits={"five_hour": {"used_percentage": 5}})
    (tmp_path / "b.json").write_text(json.dumps(personal))

    snapshots = read_status_snapshots(tmp_path)

    assert snapshots["a"]["account"] == "claude"  # no account in the file: the default one
    assert snapshots["b"]["account"] == "personal"
    assert newest_limits(snapshots, "claude")["five_pct"] == 30
    assert newest_limits(snapshots, "claude")["week_pct"] == 60
    assert newest_limits(snapshots, "personal")["five_pct"] == 5
    assert newest_limits(snapshots, "personal")["week_pct"] is None
    assert newest_limits(snapshots, "work") == {
        "five_pct": None, "five_resets_at": None, "week_pct": None, "week_resets_at": None,
    }


# -- usage.py: a fetch and a cache per account ---------------------------------


def test_usage_fetch_runs_claude_as_the_account(monkeypatch):
    seen = {}

    class _Result:
        returncode = 0
        stdout = "Current week (Fable): 12% used · resets Sep 24 at 9am (Europe/Helsinki)\n"

    def fake_run(cmd, **kwargs):
        seen["cmd"] = cmd
        seen["env"] = kwargs["env"]
        return _Result()

    monkeypatch.setattr(usage_mod.subprocess, "run", fake_run)
    personal = Account("personal", Path("/u/.claude-personal"))

    data = usage_mod.fetch(account=personal)

    assert seen["cmd"] == ["claude", "-p", "/usage"]
    assert seen["env"]["CLAUDE_CONFIG_DIR"] == "/u/.claude-personal"
    assert data["entries"][0]["label"] == "Fable"
    assert usage_mod.cache_path_for(personal).name == "usage-personal.json"
    assert usage_mod.cache_path_for(Account("claude", Path("/u/.claude"))) == usage_mod.CACHE_PATH


# -- history.py: past sessions of both accounts --------------------------------


def _transcript(projects_dir: Path, session_id: str, cwd: str, prompt: str) -> Path:
    slug_dir = projects_dir / cwd.replace("/", "-")
    slug_dir.mkdir(parents=True, exist_ok=True)
    path = slug_dir / f"{session_id}.jsonl"
    path.write_text(
        json.dumps({"type": "user", "cwd": cwd, "message": {"content": prompt}, "timestamp": "2026-09-18T10:00:00Z"})
        + "\n"
    )
    return path


def test_list_and_search_past_sessions_span_every_account(tmp_path, monkeypatch):
    accounts = _accounts(tmp_path)
    _transcript(accounts[0].projects_dir, "d1", "/x/one", "fix the parser")
    _transcript(accounts[1].projects_dir, "p1", "/x/two", "parser tests for the other account")
    monkeypatch.setattr(history_mod, "_rg_matching_files", lambda *_a, **_k: None)  # pure-Python fallback

    listed = history_mod.list_past_sessions(accounts=accounts, since_days=None)
    assert {s.session_id: s.account for s in listed} == {"d1": "claude", "p1": "personal"}

    found = history_mod.search_sessions("parser", accounts=accounts)
    assert {m.session.session_id: m.session.account for m in found} == {"d1": "claude", "p1": "personal"}


# -- app.py: header, open and resume -------------------------------------------


def _app(tmp_path, monkeypatch, accounts):
    monkeypatch.setattr(app_mod.sources, "gather_sessions", lambda *_a, **_k: [])
    monkeypatch.setattr(app_mod.sources, "read_status_snapshots", lambda *_a, **_k: {}, raising=False)
    return app_mod.PodbayApp(state_store=StateStore(tmp_path / "s.json"), no_splash=True, accounts=accounts)


@pytest.mark.asyncio
async def test_header_shows_one_labelled_quota_group_per_account(tmp_path, monkeypatch):
    app = _app(tmp_path, monkeypatch, _accounts(tmp_path))
    async with app.run_test():
        await app.workers.wait_for_complete()
        app._limits = {
            "claude": {"five_pct": 53, "five_resets_at": None, "week_pct": 8, "week_resets_at": None},
            "personal": {"five_pct": 2, "five_resets_at": None, "week_pct": None, "week_resets_at": None},
        }
        app._limits_now = datetime.now()
        app._update_header()
        text = app.query_one(app_mod.PodbayHeader).query_one("#header-quotas").content.plain

    assert text == "claude 5H 53%  ·  7D 8%" + app_mod.ACCOUNT_SEPARATOR + "personal 5H 2%"


@pytest.mark.asyncio
async def test_header_drops_the_label_with_a_single_account(tmp_path, monkeypatch):
    app = _app(tmp_path, monkeypatch, _accounts(tmp_path)[:1])
    async with app.run_test():
        await app.workers.wait_for_complete()
        app._limits = {"claude": {"five_pct": 53, "five_resets_at": None, "week_pct": None, "week_resets_at": None}}
        app._update_header()
        text = app.query_one(app_mod.PodbayHeader).query_one("#header-quotas").content.plain

    assert text == "5H 53%"


@pytest.mark.asyncio
async def test_open_claude_asks_which_account_then_runs_claude_as_it(tmp_path, monkeypatch):
    from textual.widgets import Input

    # under the real home, so the command shows the ~ form of the config dir
    app = _app(tmp_path, monkeypatch, _accounts(Path.home()))
    writes = []
    monkeypatch.setattr(app_mod.iterm_mod, "open_window", lambda *_a, **_k: "win-1", raising=False)
    monkeypatch.setattr(app_mod.iterm_mod, "write_text_to_window", lambda w, t: writes.append(t) or True, raising=False)

    async with app.run_test(size=(140, 40)) as pilot:
        await app.workers.wait_for_complete()
        app.action_open_claude()
        await pilot.pause()
        assert isinstance(app.screen, app_mod.AccountScreen)
        table = app.screen.query_one("#account-table")
        assert [str(table.get_cell_at((i, 1))) for i in range(table.row_count)] == ["claude", "personal"]
        assert table.cursor_row == 0  # the default account, nothing highlighted
        await pilot.press("2")  # the personal row's digit
        await pilot.pause()
        assert isinstance(app.screen, app_mod.PromptScreen)
        box = app.screen.query_one("#prompt-input", Input)
        assert box.value == os.path.expanduser("~")
        box.value = "~/Repositories/keitos"
        await pilot.press("enter")
        await pilot.pause()

    expected_dir = shlex.quote(os.path.expanduser("~/Repositories/keitos"))
    assert writes == [f"cd {expected_dir} && CLAUDE_CONFIG_DIR=~/.claude-personal claude"]


@pytest.mark.asyncio
async def test_open_claude_pick_list_preselects_the_highlighted_sessions_account(tmp_path, monkeypatch):
    from datetime import datetime

    from tests.test_app import _selection_session

    accounts = _accounts(Path.home())
    now = datetime.now()
    session = _selection_session("p", now, account="personal")
    monkeypatch.setattr(app_mod.sources, "gather_sessions", lambda *_a, **_k: [session])
    monkeypatch.setattr(app_mod.sources, "read_status_snapshots", lambda *_a, **_k: {}, raising=False)
    app = app_mod.PodbayApp(state_store=StateStore(tmp_path / "s.json"), no_splash=True, accounts=accounts)

    async with app.run_test(size=(140, 40)) as pilot:
        await app.workers.wait_for_complete()
        await pilot.pause()
        app.action_open_claude()
        await pilot.pause()
        table = app.screen.query_one("#account-table")
        assert table.cursor_row == 1
        assert str(table.get_cell_at((1, 3))) == "1"  # one live personal session
        await pilot.press("escape")
        await pilot.pause()
        assert not isinstance(app.screen, app_mod.AccountScreen)


@pytest.mark.asyncio
async def test_open_claude_skips_the_pick_list_with_one_account(tmp_path, monkeypatch):
    app = _app(tmp_path, monkeypatch, _accounts(Path.home())[:1])
    async with app.run_test(size=(140, 40)) as pilot:
        await app.workers.wait_for_complete()
        app.action_open_claude()
        await pilot.pause()
        assert isinstance(app.screen, app_mod.PromptScreen)
        await pilot.press("escape")
        await pilot.pause()


@pytest.mark.asyncio
async def test_resume_command_runs_claude_as_the_session_account(tmp_path, monkeypatch):
    app = _app(tmp_path, monkeypatch, _accounts(Path.home()))
    sent = []
    monkeypatch.setattr(app_mod.iterm_mod, "open_window", lambda command=None, **_k: sent.append(command) or "w", raising=False)
    monkeypatch.setattr(app, "notify", lambda *a, **k: None)
    monkeypatch.setattr(app, "trigger_refresh", lambda: None)

    async with app.run_test(size=(140, 40)):
        await app.workers.wait_for_complete()
        app._resume_entries([
            {"session_id": "p1", "cwd": "/x/two", "account": "personal"},
            {"session_id": "d1", "cwd": "/x/one", "account": None},
        ])

    assert sent == [
        "cd /x/two && CLAUDE_CONFIG_DIR=~/.claude-personal claude --resume p1",
        "cd /x/one && claude --resume d1",
    ]
