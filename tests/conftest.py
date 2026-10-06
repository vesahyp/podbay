import os

# HAL addresses the operator by PODBAY_USER, read once when podbay.voice is
# imported, and otherwise by the login name of whoever runs the tests. The
# suite addresses the crew member of the film, so the expected strings are
# the same on every machine and name nobody.
os.environ["PODBAY_USER"] = "Frank"

import pytest  # noqa: E402

from podbay import usage as usage_mod  # noqa: E402
from podbay import voice as voice_mod  # noqa: E402


@pytest.fixture(autouse=True)
def _no_real_claude_cli(monkeypatch):
    """PodbayApp.on_mount always kicks off a usage.fetch() worker, which
    shells out to the real `claude` binary -- tests must never do that.
    Default subprocess.run to "binary not found"; tests exercising fetch()
    itself monkeypatch it again to whatever behaviour they need."""

    def _missing_binary(*_args, **_kwargs):
        raise FileNotFoundError("claude")

    monkeypatch.setattr(usage_mod.subprocess, "run", _missing_binary)
    monkeypatch.setattr(voice_mod, "USER_NAME", "Frank")


@pytest.fixture(autouse=True)
def _no_signals_to_real_processes(monkeypatch):
    """The live screen and every session on the machine are processes this
    suite did not start. No test may signal one: os.kill (except the
    signal-0 liveness probe and a process the test started), os.killpg and
    a pkill, killall or kill command all raise. A test of the close path patches os.kill with its own fake,
    which replaces this guard."""
    import os
    import subprocess

    real_kill = os.kill
    started = set()  # pids of processes this test started: subprocess kills its own on a timeout

    def _guarded_kill(pid, sig):
        if sig == 0 or pid in started:
            return real_kill(pid, sig)
        raise AssertionError(f"a test tried to send signal {sig} to pid {pid}")

    def _guarded_killpg(pgid, sig):
        raise AssertionError(f"a test tried to send signal {sig} to process group {pgid}")

    real_popen_init = subprocess.Popen.__init__

    def _guarded_popen_init(self, args, *a, **kw):
        argv = [args] if isinstance(args, (str, bytes, os.PathLike)) else list(args)
        words = " ".join(os.fsdecode(w) for w in argv).split()
        if words and os.path.basename(words[0]) in ("pkill", "killall", "kill"):
            raise AssertionError(f"a test tried to run {words[0]}")
        real_popen_init(self, args, *a, **kw)
        started.add(self.pid)

    monkeypatch.setattr(os, "kill", _guarded_kill)
    monkeypatch.setattr(os, "killpg", _guarded_killpg)
    monkeypatch.setattr(subprocess.Popen, "__init__", _guarded_popen_init)


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
    from podbay import sources as sources_mod
    from podbay.accounts import Account

    one = [Account(label="claude", config_dir=Path.home() / ".claude")]
    for module in (app_mod, sources_mod):
        monkeypatch.setattr(module, "discover", lambda home=None, _one=one: list(_one))


@pytest.fixture(autouse=True)
def _home_base_is_jeeves(monkeypatch):
    """The suite's example sessions launch from a home-base repo called
    jeeves (PODBAY_HOME_REPO), whatever the machine running the tests has."""
    from podbay import app as app_mod
    from podbay import inventory as inventory_mod
    from podbay import model as model_mod

    for module in (app_mod, inventory_mod, model_mod):
        monkeypatch.setattr(module, "HOME_BASE", "jeeves")


@pytest.fixture(autouse=True)
def _no_home_base_checkout(monkeypatch, tmp_path):
    """The open prompt defaults to the home-base checkout when it exists;
    the suite must not see this machine's, so the repos dir is an empty
    temp dir. Only the runtime lookup reads sources.REPOS_DIR; the path
    regex and inventory bound their copy at import."""
    from podbay import sources as sources_mod

    monkeypatch.setattr(sources_mod, "REPOS_DIR", tmp_path / "Repositories")


@pytest.fixture(autouse=True)
def _isolated_opened_file(monkeypatch, tmp_path):
    """`podbay open` records its launches and gather_sessions reads them;
    the suite must neither write nor see this machine's file."""
    from podbay import opened as opened_mod

    monkeypatch.setattr(opened_mod, "OPENED_PATH", tmp_path / "opened.json")
    monkeypatch.setattr(opened_mod, "SENT_PATH", tmp_path / "sent.json")


@pytest.fixture(autouse=True)
def _isolated_board_url(monkeypatch, tmp_path):
    """The board's published URL is recorded by the running Head Jeeves;
    the suite must neither read nor overwrite this machine's."""
    from podbay import board as board_mod

    monkeypatch.setattr(board_mod, "URL_PATH", tmp_path / "board-url")


@pytest.fixture(autouse=True)
def _isolated_config(monkeypatch, tmp_path):
    """A test that saves a setting (the name the first start asks for)
    never writes the real ~/.config/podbay/config.json."""
    from podbay import config as config_mod

    monkeypatch.setattr(config_mod, "CONFIG_PATH", tmp_path / "config.json")


@pytest.fixture(autouse=True)
def _isolated_iterm_gate(monkeypatch, tmp_path):
    """The gate and the shared listing live in the temp dir, and the real
    ones are used by the running screen and every session on the machine."""
    from podbay import iterm as iterm_mod

    monkeypatch.setattr(iterm_mod, "GATE_PATH", tmp_path / "iterm.lock")
    monkeypatch.setattr(iterm_mod, "CACHE_PATH", tmp_path / "iterm.json")


@pytest.fixture(autouse=True)
def _isolated_machine(monkeypatch, tmp_path):
    """The history and the inventory snapshot live in the temp dir, the
    retries do not sleep, and `open` does not see this machine's memory
    pressure: a test that wants one patches it again."""
    from podbay import inventory as inventory_mod
    from podbay import iterm as iterm_mod
    from podbay import machine as machine_mod

    monkeypatch.setattr(machine_mod, "HISTORY_PATH", tmp_path / "machine.jsonl")
    monkeypatch.setattr(inventory_mod, "SNAPSHOT_PATH", tmp_path / "inventory.json")
    monkeypatch.setattr(iterm_mod, "RETRY_DELAYS", (0.0, 0.0, 0.0))
    monkeypatch.setattr(machine_mod, "memory_critical", lambda: None)
    monkeypatch.setattr(machine_mod, "overloaded_now", lambda: None)


FAKE_SAMPLE = {
    "at": 1_700_000_000.0, "load": {"1m": 1.2, "5m": 1.0, "15m": 0.9}, "cores": 8, "cpu_pct": 14.0,
    "mem_free_mb": 2048, "mem_total_mb": 16384, "pressure": "normal", "swap_used_mb": 0,
    "top": {"cpu": [{"pid": 1, "name": "node", "cpu": 40.0, "mem_mb": 300}], "mem": [{"pid": 2, "name": "claude", "cpu": 3.0, "mem_mb": 900}]},
}


@pytest.fixture(autouse=True)
def _no_real_machine_sample(monkeypatch):
    """The screen samples the machine on a timer; the suite never runs ps,
    vm_stat or sysctl for it. A test of sample() itself patches
    machine._run."""
    import time

    from podbay import machine as machine_mod

    monkeypatch.setattr(machine_mod, "sample", lambda: {**FAKE_SAMPLE, "at": time.time()})
