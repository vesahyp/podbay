import json
from pathlib import Path

from podbay import reviewer
from podbay.accounts import Account


def _transcript(tmp_path: Path) -> Path:
    records = [
        {"type": "user", "message": {"content": "make the header fit"}, "timestamp": "2026-10-03T10:00:00Z"},
        {"type": "assistant", "message": {"content": [{"type": "text", "text": "I widened it"}, {"type": "tool_use", "id": "t1", "name": "Edit", "input": {"description": "widen header"}}]}, "timestamp": "2026-10-03T10:01:00Z"},
        {"type": "user", "message": {"content": "no, how the fuck is it still too wide"}, "timestamp": "2026-10-03T10:02:00Z"},
    ]
    path = tmp_path / "abc.jsonl"
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n")
    return path


def test_excerpt_labels_roles_and_keeps_order(tmp_path):
    text = reviewer.excerpt(_transcript(tmp_path))
    assert text.splitlines() == [
        "[10:00] USER: make the header fit",
        "[10:01] AGENT: I widened it",
        "[10:01] TOOL: ⚙ Edit: widen header",
        "[10:02] USER: no, how the fuck is it still too wide",
    ]


def test_excerpt_cuts_long_entries_and_the_whole(tmp_path, monkeypatch):
    path = tmp_path / "big.jsonl"
    path.write_text(json.dumps({"type": "user", "message": {"content": "x" * 5000}, "timestamp": "2026-10-03T10:00:00Z"}) + "\n")
    text = reviewer.excerpt(path)
    assert len(text) < 1600 and text.endswith(" …")
    monkeypatch.setattr(reviewer, "EXCERPT_CHARS", 100)
    assert reviewer.excerpt(path).startswith("…\n")


def test_review_paths_and_newest_files(tmp_path):
    d = tmp_path / "reviews"
    assert reviewer.latest("abc", d) is None
    d.mkdir()
    (d / "abc-checkup.md").write_text("one")
    assert reviewer.latest("abc", d) == d / "abc-checkup.md"
    assert set(reviewer.newest_files(d)) == {"abc-checkup.md"}
    assert reviewer.newest_files(tmp_path / "missing") == {}


def test_cmd_excerpt_prints_the_sessions_turns(tmp_path, monkeypatch, capsys):
    from datetime import datetime

    from podbay import app as app_mod
    from tests.test_app import _selection_session

    cwd = "/x/one"
    projects = tmp_path / ".claude" / "projects"
    slug = projects / cwd.replace("/", "-")
    slug.mkdir(parents=True)
    _transcript(tmp_path).rename(slug / "abc.jsonl")
    session = _selection_session("abc", datetime.now(), cwd=cwd)
    session.name = "worker"
    monkeypatch.setattr(app_mod.sources, "gather_sessions", lambda *_a, **_k: [session])
    monkeypatch.setattr(app_mod, "discover", lambda home=None: [Account("claude", tmp_path / ".claude")])

    app_mod.cmd_excerpt("worker", 80)
    out = capsys.readouterr().out
    assert out.splitlines()[0] == "[10:00] USER: make the header fit"
    assert "how the fuck" in out
