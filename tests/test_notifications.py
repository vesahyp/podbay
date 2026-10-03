from datetime import datetime

from podbay import notifications


def test_record_and_read_since_newest_first_and_one_line(tmp_path):
    path = tmp_path / "n.log"
    notifications.record(path, datetime(2026, 9, 22, 23, 0), "information", "yesterday")
    notifications.record(path, datetime(2026, 9, 23, 9, 0), "sent", "to a: first\nsecond")
    notifications.record(path, datetime(2026, 9, 23, 10, 0), "warning", "later")

    entries = notifications.read_since(path, datetime(2026, 9, 23))

    assert entries == [
        (datetime(2026, 9, 23, 10, 0), "warning", "later"),
        (datetime(2026, 9, 23, 9, 0), "sent", "to a: first second"),
    ]


def test_record_rolls_over_and_read_includes_the_old_file(tmp_path, monkeypatch):
    path = tmp_path / "n.log"
    monkeypatch.setattr(notifications, "MAX_BYTES", 10)
    notifications.record(path, datetime(2026, 9, 23, 9, 0), "information", "old enough to roll")
    notifications.record(path, datetime(2026, 9, 23, 9, 1), "information", "new")

    assert (tmp_path / "n.log.1").exists()
    assert [e[2] for e in notifications.read_since(path, datetime(2026, 9, 23))] == ["new", "old enough to roll"]


def test_read_since_skips_bad_lines_and_missing_file(tmp_path):
    path = tmp_path / "n.log"
    assert notifications.read_since(path, datetime(2026, 9, 23)) == []
    path.write_text("garbage\nnot-a-date\tx\ty\n2026-09-23T09:00:00\tinformation\tok\n")
    assert [e[2] for e in notifications.read_since(path, datetime(2026, 9, 23))] == ["ok"]
