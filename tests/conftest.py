import pytest

from podbay import usage as usage_mod


@pytest.fixture(autouse=True)
def _no_real_claude_cli(monkeypatch):
    """PodbayApp.on_mount always kicks off a usage.fetch() worker, which
    shells out to the real `claude` binary -- tests must never do that.
    Default subprocess.run to "binary not found"; tests exercising fetch()
    itself monkeypatch it again to whatever behaviour they need."""

    def _missing_binary(*_args, **_kwargs):
        raise FileNotFoundError("claude")

    monkeypatch.setattr(usage_mod.subprocess, "run", _missing_binary)


@pytest.fixture(autouse=True)
def _isolated_usage_cache(monkeypatch, tmp_path):
    """The real cache under ~/.local/state/podbay is written by the running
    podbay, so a test reading CACHE_PATH would pass or fail depending on
    what the user's own session last fetched."""
    monkeypatch.setattr(usage_mod, "CACHE_PATH", tmp_path / "usage.json")


@pytest.fixture(autouse=True)
def _single_default_account(monkeypatch):
    """The machine running the tests may have several Claude Code config
    dirs; the suite is written against one, the default ~/.claude, so every
    place that discovers accounts gets that one back."""
    from pathlib import Path

    from podbay import app as app_mod
    from podbay import history as history_mod
    from podbay import sources as sources_mod
    from podbay.accounts import Account

    one = [Account(label="claude", config_dir=Path.home() / ".claude")]
    for module in (app_mod, history_mod, sources_mod):
        monkeypatch.setattr(module, "discover", lambda home=None, _one=one: list(_one))
