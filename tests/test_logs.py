import logging
import os
from datetime import datetime

import pytest
from textual.app import App

from podbay import logs
from podbay.app import PodbayApp
from podbay.state import StateStore


@pytest.fixture
def log_file(tmp_path):
    path = tmp_path / "podbay.log"
    logs.configure(path)
    yield path
    logs.configure(tmp_path / "unused.log")  # detach the handler on tmp_path


def test_configure_writes_to_the_given_file(log_file):
    logging.getLogger("podbay.test").warning("hello from the test")
    assert "WARNING podbay.test: hello from the test" in log_file.read_text()


def test_configure_is_idempotent(tmp_path):
    first = tmp_path / "a.log"
    second = tmp_path / "b.log"
    logs.configure(first)
    logs.configure(second)
    logging.getLogger("podbay.test").warning("only once")
    assert not first.exists() or "only once" not in first.read_text()
    assert second.read_text().count("only once") == 1
    logs.configure(tmp_path / "unused.log")


def test_configure_survives_an_unwritable_directory(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("not a directory")
    logger = logs.configure(blocker / "podbay.log")
    assert not any(getattr(h, "_podbay_file_handler", False) for h in logger.handlers)


def test_level_from_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("PODBAY_LOG_LEVEL", "debug")
    logger = logs.configure(tmp_path / "podbay.log")
    assert logger.level == logging.DEBUG
    logs.configure(tmp_path / "unused.log", level="INFO")


def test_save_survives_a_refused_rename(tmp_path, monkeypatch, caplog):
    """The 2026-09-21 crash: os.replace raised EPERM inside a refresh and
    the unhandled error took the TUI down. save() must report and carry on."""
    store = StateStore(tmp_path / "state.json")
    first = datetime(2026, 9, 21, 12, 0)
    store.set_seen("sid", first)  # a good save, so the file exists

    def refuse(src, dst):
        raise PermissionError(1, "Operation not permitted", src)

    monkeypatch.setattr(os, "replace", refuse)
    with caplog.at_level(logging.WARNING, logger="podbay"):
        assert store.save() is False
    assert "state save skipped" in caplog.text
    assert "Operation not permitted" in caplog.text
    assert list(tmp_path.glob(".state-*.json.tmp")) == []  # temp file cleaned up
    assert StateStore(tmp_path / "state.json").get("sid").seen_at == first  # old file intact


def test_textual_still_exposes_the_exception_hook():
    """PodbayApp overrides Textual's private _handle_exception to log the
    crash; fail loudly if a Textual upgrade renames it."""
    assert "_handle_exception" in vars(App)


def test_tui_crash_lands_in_the_log(tmp_path, monkeypatch, caplog):
    seen: list[Exception] = []
    monkeypatch.setattr(App, "_handle_exception", lambda self, error: seen.append(error))
    app = PodbayApp(state_store=StateStore(tmp_path / "s.json"), no_splash=True)
    error = RuntimeError("boom")
    with caplog.at_level(logging.CRITICAL, logger="podbay"):
        app._handle_exception(error)
    assert seen == [error]
    assert "unhandled exception, the TUI is exiting" in caplog.text
    assert "RuntimeError: boom" in caplog.text
