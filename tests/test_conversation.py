import json

from podbay.sources import read_conversation


def _line(record: dict) -> str:
    return json.dumps(record)


def test_read_conversation_filters_and_renders(tmp_path):
    records = [
        {
            "type": "user",
            "timestamp": "2026-09-09T09:00:00Z",
            "message": {"content": "plain string message"},
        },
        {
            "type": "user",
            "timestamp": "2026-09-09T09:01:00Z",
            "isMeta": True,
            "message": {"content": "should be skipped meta"},
        },
        {
            "type": "user",
            "timestamp": "2026-09-09T09:02:00Z",
            "isSidechain": True,
            "message": {"content": "should be skipped sidechain"},
        },
        {
            "type": "user",
            "timestamp": "2026-09-09T09:03:00Z",
            "message": {
                "content": [
                    {"type": "text", "text": "block text kept"},
                    {"type": "tool_result", "content": "ignored tool result"},
                ]
            },
        },
        {
            "type": "user",
            "timestamp": "2026-09-09T09:04:00Z",
            "message": {
                "content": "<system-reminder>hidden instructions</system-reminder>visible part"
            },
        },
        {
            "type": "user",
            "timestamp": "2026-09-09T09:05:00Z",
            "message": {"content": "<system-reminder>only hidden</system-reminder>"},
        },
        {
            "type": "assistant",
            "timestamp": "2026-09-09T09:06:00Z",
            "message": {"content": [{"type": "text", "text": "assistant reply text"}]},
        },
        {
            "type": "assistant",
            "timestamp": "2026-09-09T09:07:00Z",
            "message": {
                "content": [
                    {"type": "thinking", "thinking": "internal reasoning"},
                    {
                        "type": "tool_use",
                        "name": "Bash",
                        "input": {"description": "list files", "command": "ls -la"},
                    },
                ]
            },
        },
        {
            "type": "assistant",
            "timestamp": "2026-09-09T09:08:00Z",
            "isSidechain": True,
            "message": {"content": [{"type": "text", "text": "sidechain assistant should be skipped"}]},
        },
    ]
    path = tmp_path / "session.jsonl"
    path.write_text("\n".join(_line(r) for r in records) + "\n")

    entries = read_conversation(path, limit=40)

    assert entries == [
        {"role": "user", "timestamp": "2026-09-09T09:00:00Z", "text": "plain string message"},
        {"role": "user", "timestamp": "2026-09-09T09:03:00Z", "text": "block text kept"},
        {"role": "user", "timestamp": "2026-09-09T09:04:00Z", "text": "visible part"},
        {"role": "assistant", "timestamp": "2026-09-09T09:06:00Z", "text": "assistant reply text"},
        {"role": "tool", "timestamp": "2026-09-09T09:07:00Z", "text": "⚙ Bash: list files"},
    ]


def test_read_conversation_respects_limit(tmp_path):
    records = [
        {"type": "user", "timestamp": f"2026-09-09T09:{i:02d}:00Z", "message": {"content": f"msg {i}"}}
        for i in range(10)
    ]
    path = tmp_path / "session.jsonl"
    path.write_text("\n".join(_line(r) for r in records) + "\n")

    entries = read_conversation(path, limit=3)

    assert [e["text"] for e in entries] == ["msg 7", "msg 8", "msg 9"]


def test_read_conversation_tool_use_falls_back_to_input_when_no_description(tmp_path):
    records = [
        {
            "type": "assistant",
            "timestamp": "2026-09-09T09:00:00Z",
            "message": {
                "content": [
                    {"type": "tool_use", "name": "Read", "input": {"file_path": "/tmp/x.py"}},
                ]
            },
        },
    ]
    path = tmp_path / "session.jsonl"
    path.write_text("\n".join(_line(r) for r in records) + "\n")

    entries = read_conversation(path)

    assert len(entries) == 1
    assert entries[0]["role"] == "tool"
    assert entries[0]["text"].startswith("⚙ Read: ")


def test_read_conversation_missing_file_returns_empty(tmp_path):
    assert read_conversation(tmp_path / "nope.jsonl") == []


def test_read_conversation_walks_past_a_huge_record(tmp_path):
    """A single oversized tool result near the end must not hide every turn
    before it: the reader walks backwards until it has `limit` entries."""
    path = tmp_path / "t.jsonl"
    lines = []
    for i in range(6):
        lines.append(json.dumps({"type": "user", "timestamp": f"2026-01-01T00:0{i}:00Z",
                                 "message": {"role": "user", "content": f"old prompt {i}"}}))
        lines.append(json.dumps({"type": "assistant", "timestamp": f"2026-01-01T00:0{i}:30Z",
                                 "message": {"role": "assistant", "content": [{"type": "text", "text": f"old answer {i}"}]}}))
    huge = json.dumps({"type": "user", "timestamp": "2026-01-01T01:00:00Z",
                       "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "x", "content": "y" * 3000}]}})
    lines.append(huge)
    lines.append(json.dumps({"type": "user", "timestamp": "2026-01-01T02:00:00Z",
                             "message": {"role": "user", "content": "latest prompt"}}))
    path.write_text("\n".join(lines) + "\n")

    entries = read_conversation(path, limit=40, max_bytes=10**9)
    texts = [e["text"] for e in entries]
    assert texts[0] == "old prompt 0"
    assert texts[-1] == "latest prompt"
    assert len(texts) == 13

    # A tiny read chunk still yields whole lines in order, and the cap cuts
    # inside the huge record without corrupting what comes after it.
    from podbay.sources import _lines_reversed
    rev = [l for l in _lines_reversed(path, max_bytes=len(lines[-1]) + 1500, chunk_size=64) if l]
    assert json.loads(rev[0])["message"]["content"] == "latest prompt"
    assert len(rev) == 1
    full = [l for l in _lines_reversed(path, max_bytes=10**9, chunk_size=64) if l]
    assert full == list(reversed(lines))
