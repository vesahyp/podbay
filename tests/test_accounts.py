"""Two Claude Code accounts on one machine: the console reads both config
dirs, labels every session, keeps the quotas apart and runs claude as the
right account when it opens or resumes a session."""

import json
import os
from datetime import datetime
from pathlib import Path

import pytest

from podbay import app as app_mod
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


def _transcript(projects_dir: Path, session_id: str, cwd: str, prompt: str) -> Path:
    slug_dir = projects_dir / cwd.replace("/", "-")
    slug_dir.mkdir(parents=True, exist_ok=True)
    path = slug_dir / f"{session_id}.jsonl"
    path.write_text(
        json.dumps({"type": "user", "cwd": cwd, "message": {"content": prompt}, "timestamp": "2026-09-18T10:00:00Z"})
        + "\n"
    )
    return path


# -- app.py: header and open -------------------------------------------


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

    assert text == "claude 5H 53%  7D 8%" + app_mod.ACCOUNT_SEPARATOR + "personal 5H 2%"


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
async def test_transcript_pane_reads_the_sessions_own_account(tmp_path, monkeypatch):
    """A personal session's transcript lives under ~/.claude-personal/projects;
    the pane must look there, not under the default account."""
    from datetime import datetime

    from textual.widgets import Static

    from tests.test_app import _selection_session

    accounts = _accounts(tmp_path)
    cwd = "/x/two"
    _transcript(accounts[1].projects_dir, "p1", cwd, "hello from the personal side")
    now = datetime.now()
    session = _selection_session("p1", now, account="personal", cwd=cwd)
    monkeypatch.setattr(app_mod.sources, "gather_sessions", lambda *_a, **_k: [session])
    monkeypatch.setattr(app_mod.sources, "read_status_snapshots", lambda *_a, **_k: {}, raising=False)
    app = app_mod.PodbayApp(state_store=StateStore(tmp_path / "s.json"), no_splash=True, accounts=accounts)

    async with app.run_test(size=(140, 40)) as pilot:
        await app.workers.wait_for_complete()
        await pilot.pause()
        entries = app._transcript_cache["p1"][2]
        assert [e["text"] for e in entries] == ["hello from the personal side"]
        assert app.query_one("#transcript-body", Static).content is not None


def test_open_default_dir_is_the_home_base_when_set(tmp_path, monkeypatch):
    from datetime import datetime

    from podbay import sources as sources_mod
    from tests.test_app import _selection_session

    monkeypatch.setattr(sources_mod, "REPOS_DIR", tmp_path)
    session = _selection_session("a", datetime.now(), cwd="/x/elsewhere")

    monkeypatch.setattr(app_mod, "HOME_BASE", "")
    assert app_mod.PodbayApp._open_default_dir(session) == "/x/elsewhere"
    assert app_mod.PodbayApp._open_default_dir(None) == os.path.expanduser("~")

    monkeypatch.setattr(app_mod, "HOME_BASE", "jeeves")
    assert app_mod.PodbayApp._open_default_dir(session) == "/x/elsewhere"  # no such checkout yet
    (tmp_path / "jeeves").mkdir()
    assert app_mod.PodbayApp._open_default_dir(session) == str(tmp_path / "jeeves")
    assert app_mod.PodbayApp._open_default_dir(None) == str(tmp_path / "jeeves")


# -- Remote Control --------------------------------------------------------------


def test_remote_session_comes_from_the_registry_bridge_id(tmp_path):
    accounts = _accounts(tmp_path)
    pid = os.getpid()
    _write_registry_entry(accounts[0].sessions_dir, "bridged", pid)
    path = accounts[0].sessions_dir / f"{pid}.json"
    data = json.loads(path.read_text())
    data["bridgeSessionId"] = "session_01ABC"
    path.write_text(json.dumps(data))
    _write_registry_entry(accounts[1].sessions_dir, "local", pid)

    sessions = gather_sessions(
        StateStore(tmp_path / "state.json"), _FakeLister({}), status_snapshots={},
        pid_by_tty=lambda: {}, cwd_by_pids=lambda pids: {}, accounts=accounts,
    )
    by_id = {s.session_id: s for s in sessions}

    assert by_id["bridged"].remote_url == "https://claude.ai/code/session_01ABC"
    assert by_id["local"].remote_url is None
    rows = {r["session_id"]: r for r in app_mod.build_rows(sessions, datetime.now())}
    assert str(rows["bridged"]["remote"]) == app_mod.REMOTE_GLYPH
    assert rows["local"]["remote"] == ""


@pytest.mark.asyncio
async def test_helper_tabs_are_not_listed(tmp_path, monkeypatch):
    """A session's second and later iTerm2 tabs are its helper terminals."""
    from datetime import datetime

    from tests.test_app import _selection_session

    now = datetime.now()
    first = _selection_session("first", now, tab_index=1)
    unknown = _selection_session("unknown", now, tab_index=None)
    helper = _selection_session("helper", now, tab_index=2)
    shell_helper = _selection_session("sh", now, last_turn=None, has_transcript=False, is_shell=True, tty="/dev/ttys007", tab_index=3)
    monkeypatch.setattr(app_mod.sources, "gather_sessions", lambda *_a, **_k: [first, unknown, helper, shell_helper])
    monkeypatch.setattr(app_mod.sources, "read_status_snapshots", lambda *_a, **_k: {}, raising=False)
    app = app_mod.PodbayApp(state_store=StateStore(tmp_path / "s.json"), no_splash=True, accounts=_accounts(tmp_path))

    async with app.run_test(size=(160, 40)) as pilot:
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert sorted(app._row_keys) == ["first", "unknown"]


# -- Head Jeeves -----------------------------------------------------------------


def _head(now, **overrides):
    from tests.test_app import _selection_session

    s = _selection_session("hj", now, cwd="/home/base", **overrides)
    s.name = "head-jeeves"
    return s


@pytest.mark.asyncio
async def test_a_handover_he_writes_is_toasted_and_v_shows_it(tmp_path, monkeypatch):
    from datetime import datetime

    from tests.test_app import _selection_session

    accounts = _accounts(tmp_path)
    now = datetime.now()
    worker = _selection_session("p1", now, account="personal", cwd="/x/two")
    worker.name = "worker"
    head = _head(now)
    monkeypatch.setattr(app_mod.sources, "gather_sessions", lambda *_a, **_k: [worker, head])
    monkeypatch.setattr(app_mod.sources, "read_status_snapshots", lambda *_a, **_k: {}, raising=False)
    sent = []
    monkeypatch.setattr(app_mod.iterm_mod, "get_tty_for_pid", lambda pid: "/dev/ttys009")
    monkeypatch.setattr(app_mod.iterm_mod, "send_text", lambda tty, text: sent.append(text) or True)
    reviews = tmp_path / "reviews"
    app = app_mod.PodbayApp(
        state_store=StateStore(tmp_path / "s.json"), no_splash=True, accounts=accounts,
        head_jeeves=True, reviews_dir=reviews,
    )
    toasts = []
    monkeypatch.setattr(app, "notify", lambda message, *a, **k: toasts.append(str(message)))

    async with app.run_test(size=(160, 40)) as pilot:
        await app.workers.wait_for_complete()
        await pilot.pause()
        keys = app._row_keys
        assert keys[0] == "hj"  # Head Jeeves sorts first
        table = app.query_one("#table")
        table.move_cursor(row=keys.index("p1"))
        app.action_view_review()
        assert "has not written" in toasts[-1]
        # he writes the file on his own; the next scan toasts it and v shows it
        reviews.mkdir()
        (reviews / "p1-handover.md").write_text("# worker: handover\n\nIt went south at turn two.\n")
        app.trigger_refresh()
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert any("handover of" in t for t in toasts)
        app.action_view_review()
        await pilot.pause()
        assert isinstance(app.screen, app_mod.ReviewScreen)
        await pilot.press("escape")
        await pilot.pause()

    assert sent == []  # nothing goes to him unasked: the TUI has no key that does


@pytest.mark.asyncio
async def test_a_hot_session_gets_a_checkup_and_a_missing_head_jeeves_is_started_then_primed(tmp_path, monkeypatch):
    from datetime import datetime, timedelta

    from tests.test_app import _selection_session

    accounts = _accounts(tmp_path)
    now = datetime.now()
    calm = _selection_session("d1", now, cwd="/x/one", last_turn="in_progress")
    calm.name = "worker"
    hot = _selection_session("d1", now, cwd="/x/one", last_turn="in_progress", recent_prompts=["this is STILL broken?! again!!"])
    hot.name = "worker"
    shell = _selection_session("sh", now, last_turn=None, has_transcript=False, is_shell=True, tty="/dev/ttys005")
    fresh_head = _head(now + timedelta(seconds=5), status="idle")
    fresh_head.name = "jeeves-77"  # -n did not stick: podbay renames him
    fresh_head.started_at = now + timedelta(seconds=5)
    batches = [[calm, shell], [hot, shell], [hot, shell, fresh_head]]
    monkeypatch.setattr(app_mod.sources, "gather_sessions", lambda *_a, **_k: batches.pop(0) if batches else [hot, shell, fresh_head])
    monkeypatch.setattr(app_mod.sources, "read_status_snapshots", lambda *_a, **_k: {}, raising=False)
    monkeypatch.setattr(app_mod.sources, "busy_ttys", lambda: set())
    monkeypatch.setattr(app_mod.iterm_mod, "at_empty_prompt", lambda tty: True)
    monkeypatch.setattr(app_mod.sources, "REPOS_DIR", tmp_path)
    (tmp_path / "jeeves").mkdir()
    monkeypatch.setattr(app_mod, "HOME_BASE", "jeeves")
    fresh_head.cwd = str(tmp_path / "jeeves")
    sent = []
    monkeypatch.setattr(app_mod.iterm_mod, "get_tty_for_pid", lambda pid: "/dev/ttys009")
    monkeypatch.setattr(app_mod.iterm_mod, "send_text", lambda tty, text: sent.append((tty, text)) or True)
    app = app_mod.PodbayApp(
        state_store=StateStore(tmp_path / "s.json"), no_splash=True, accounts=accounts,
        voice_mode=app_mod.hal.VOICE_ON, head_jeeves=True, reviews_dir=tmp_path / "reviews",
    )
    monkeypatch.setattr(app, "notify", lambda *a, **k: None)

    async with app.run_test(size=(160, 40)) as pilot:
        await app.workers.wait_for_complete()
        await pilot.pause()
        # first scan: no Head Jeeves is live, so one is started in the free shell
        assert sent == [("/dev/ttys005", f"cd {tmp_path / 'jeeves'} && claude -n head-jeeves")]  # default account, default model
        app.trigger_refresh()  # the session turns hot: the checkup replaces the queued report for duty
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert app._head_jeeves_pending["follow_up"] == "/head-jeeves checkup worker d1"
        app.trigger_refresh()  # Head Jeeves shows up idle in the home repo
        await app.workers.wait_for_complete()
        await pilot.pause()

    assert [t for _tty, t in sent[1:]] == ["/rename head-jeeves", "/head-jeeves", "/head-jeeves checkup worker d1"]
    assert app._head_jeeves_pending is None


@pytest.mark.asyncio
async def test_head_jeeves_runs_as_the_configured_account_and_gets_the_events(tmp_path, monkeypatch):
    from datetime import datetime, timedelta

    from tests.test_app import _selection_session

    accounts = _accounts(Path.home())
    now = datetime.now()
    working = _selection_session("w1", now, cwd="/x/one", last_turn="in_progress")
    working.name = "worker"
    finished = _selection_session("w1", now, cwd="/x/one")
    finished.name = "worker"
    shell = _selection_session("sh", now, last_turn=None, has_transcript=False, is_shell=True, tty="/dev/ttys005")
    head = _head(now + timedelta(seconds=5), status="idle", account="personal")
    head.started_at = now + timedelta(seconds=5)
    monkeypatch.setattr(app_mod.sources, "REPOS_DIR", tmp_path)
    (tmp_path / "jeeves").mkdir()
    monkeypatch.setattr(app_mod, "HOME_BASE", "jeeves")
    head.cwd = str(tmp_path / "jeeves")
    wrong_account = _head(now + timedelta(seconds=5), status="idle", account="claude")
    wrong_account.session_id = "hj2"
    wrong_account.name = "jeeves-99"
    wrong_account.cwd = head.cwd
    wrong_account.started_at = head.started_at
    batches = [[working, shell], [working, shell, wrong_account], [working, shell, wrong_account, head], [finished, shell, wrong_account, head]]
    monkeypatch.setattr(app_mod.sources, "gather_sessions", lambda *_a, **_k: batches.pop(0) if batches else batches)
    monkeypatch.setattr(app_mod.sources, "read_status_snapshots", lambda *_a, **_k: {}, raising=False)
    monkeypatch.setattr(app_mod.sources, "busy_ttys", lambda: set())
    monkeypatch.setattr(app_mod.iterm_mod, "at_empty_prompt", lambda tty: True)
    sent = []
    monkeypatch.setattr(app_mod.iterm_mod, "get_tty_for_pid", lambda pid: "/dev/ttys009")
    monkeypatch.setattr(app_mod.iterm_mod, "send_text", lambda tty, text: sent.append(text) or True)
    app = app_mod.PodbayApp(
        state_store=StateStore(tmp_path / "s.json"), no_splash=True, accounts=accounts,
        voice_mode=app_mod.hal.VOICE_ON, head_jeeves=True, head_jeeves_account="personal", reviews_dir=tmp_path / "reviews",
    )
    monkeypatch.setattr(app, "notify", lambda *a, **k: None)

    async with app.run_test(size=(160, 40)) as pilot:
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert sent == [f"cd {tmp_path / 'jeeves'} && CLAUDE_CONFIG_DIR=~/.claude-personal claude -n head-jeeves"]
        for _ in range(3):
            app.trigger_refresh()
            await app.workers.wait_for_complete()
            await pilot.pause()

    # the claude-account newcomer was not taken for him; the personal one was, and the finish was relayed
    assert sent[1:] == ["/head-jeeves", "/head-jeeves event worker: worker has finished, Frank. It is waiting for you."]
