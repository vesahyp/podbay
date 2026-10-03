import json
import subprocess
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from podbay import usage

SAMPLE_OUTPUT = """You are currently using your subscription to power your Claude Code usage

Current session: 29% used · resets Sep 18 at 11:20am (Europe/Helsinki)
Current week (all models): 13% used · resets Sep 24 at 9am (Europe/Helsinki)
Current week (Fable): 8% used · resets Sep 24 at 8:59am (Europe/Helsinki)

What's contributing to your limits usage?
"""


class _Result:
    def __init__(self, stdout="", returncode=0):
        self.stdout = stdout
        self.returncode = returncode


def test_fetch_parses_sample_output_all_three_lines(monkeypatch):
    monkeypatch.setattr(usage.subprocess, "run", lambda *_a, **_k: _Result(SAMPLE_OUTPUT))

    data = usage.fetch()

    assert data is not None
    assert isinstance(data["fetched_at"], float)
    entries = data["entries"]
    by_key = {(e["key"], e["label"]): e for e in entries}
    assert by_key[("session", None)]["pct"] == 29.0
    assert by_key[("session", None)]["resets_text"] == "Sep 18 at 11:20am (Europe/Helsinki)"
    assert by_key[("week_all", None)]["pct"] == 13.0
    assert by_key[("week_all", None)]["resets_text"] == "Sep 24 at 9am (Europe/Helsinki)"
    assert by_key[("week_model", "Fable")]["pct"] == 8.0
    assert by_key[("week_model", "Fable")]["resets_text"] == "Sep 24 at 8:59am (Europe/Helsinki)"
    hel = ZoneInfo("Europe/Helsinki")
    for entry in entries:
        assert isinstance(entry["resets_at"], float)
    assert datetime.fromtimestamp(by_key[("week_model", "Fable")]["resets_at"], hel).strftime("%b %d %H:%M") == "Sep 24 08:59"


def test_fetch_parses_a_fourth_model_line(monkeypatch):
    output = SAMPLE_OUTPUT.replace(
        "Current week (Fable): 8% used · resets Sep 24 at 8:59am (Europe/Helsinki)",
        "Current week (Fable): 8% used · resets Sep 24 at 8:59am (Europe/Helsinki)\n"
        "Current week (Opus): 41% used · resets Sep 24 at 8:59am (Europe/Helsinki)",
    )
    monkeypatch.setattr(usage.subprocess, "run", lambda *_a, **_k: _Result(output))

    data = usage.fetch()

    week_models = {e["label"]: e["pct"] for e in data["entries"] if e["key"] == "week_model"}
    assert week_models == {"Fable": 8.0, "Opus": 41.0}


def test_fetch_parses_model_name_with_a_space(monkeypatch):
    output = "Current week (Claude Opus): 41% used · resets Sep 24 at 9am (Europe/Helsinki)\n"
    monkeypatch.setattr(usage.subprocess, "run", lambda *_a, **_k: _Result(output))

    data = usage.fetch()

    assert len(data["entries"]) == 1
    entry = data["entries"][0]
    assert (entry["key"], entry["label"], entry["pct"]) == ("week_model", "Claude Opus", 41.0)


def test_parse_resets_at_reads_the_account_clock():
    hel = ZoneInfo("Europe/Helsinki")
    now = datetime(2026, 9, 18, 10, 0, tzinfo=hel).timestamp()

    at = usage._parse_resets_at("Sep 24 at 9am (Europe/Helsinki)", now)

    assert datetime.fromtimestamp(at, hel) == datetime(2026, 9, 24, 9, 0, tzinfo=hel)
    assert usage._parse_resets_at("Sep 18 at 11:20pm (Europe/Helsinki)", now) == datetime(2026, 9, 18, 23, 20, tzinfo=hel).timestamp()
    assert usage._parse_resets_at("Sep 18 at 12am (Europe/Helsinki)", now) == datetime(2026, 9, 18, 0, 0, tzinfo=hel).timestamp()


def test_parse_resets_at_rolls_a_past_date_into_next_year():
    hel = ZoneInfo("Europe/Helsinki")
    now = datetime(2026, 12, 30, 10, 0, tzinfo=hel).timestamp()

    at = usage._parse_resets_at("Jan 3 at 9am (Europe/Helsinki)", now)

    assert datetime.fromtimestamp(at, hel) == datetime(2027, 1, 3, 9, 0, tzinfo=hel)


def test_parse_resets_at_returns_none_for_unknown_shapes():
    now = time.time()
    assert usage._parse_resets_at(None, now) is None
    assert usage._parse_resets_at("tomorrow", now) is None
    assert usage._parse_resets_at("Sep 24 at 9am (Mars/Olympus)", now) is None


def test_fetch_returns_none_when_no_parsable_lines(monkeypatch):
    monkeypatch.setattr(usage.subprocess, "run", lambda *_a, **_k: _Result("nothing useful here\n"))

    assert usage.fetch() is None


def test_fetch_returns_none_on_nonzero_exit(monkeypatch):
    monkeypatch.setattr(usage.subprocess, "run", lambda *_a, **_k: _Result(SAMPLE_OUTPUT, returncode=1))

    assert usage.fetch() is None


def test_fetch_returns_none_on_timeout(monkeypatch):
    def _boom(*_a, **_k):
        raise subprocess.TimeoutExpired(cmd="claude", timeout=30)

    monkeypatch.setattr(usage.subprocess, "run", _boom)

    assert usage.fetch() is None


def test_fetch_returns_none_when_claude_binary_missing(monkeypatch):
    def _boom(*_a, **_k):
        raise FileNotFoundError("claude")

    monkeypatch.setattr(usage.subprocess, "run", _boom)

    assert usage.fetch() is None


def test_write_cache_then_read_cache_roundtrip(tmp_path):
    path = tmp_path / "usage.json"
    data = {"fetched_at": time.time(), "entries": [{"key": "week_model", "label": "Fable", "pct": 8.0, "resets_text": "x"}]}

    usage.write_cache(data, path=path)
    result = usage.read_cache(path=path)

    assert result == data
    assert json.loads(path.read_text()) == data


def test_read_cache_missing_file_returns_none(tmp_path):
    assert usage.read_cache(path=tmp_path / "nope.json") is None


def test_read_cache_malformed_json_returns_none(tmp_path):
    path = tmp_path / "usage.json"
    path.write_text("not json")

    assert usage.read_cache(path=path) is None


def test_read_cache_max_age_rejects_stale_cache(tmp_path):
    path = tmp_path / "usage.json"
    usage.write_cache({"fetched_at": time.time() - 1000, "entries": []}, path=path)

    assert usage.read_cache(path=path, max_age=600) is None
    assert usage.read_cache(path=path, max_age=2000) is not None
