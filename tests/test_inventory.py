from datetime import datetime, timedelta

from podbay.inventory import inventory_payload, render_status, render_table
from podbay.model import Session, repo_groups
from pathlib import Path

REPOS = str(Path.home() / "Repositories")

NOW = datetime(2026, 9, 18, 12, 0)


def _session(
    name,
    *,
    cwd=f"{REPOS}/jeeves",
    repos_touched=None,
    repos_edited=None,
    waiting_on=None,
    turn_ended=None,
    last_turn_ts=None,
    recap=None,
    status="idle",
    is_shell=False,
    has_transcript=True,
) -> Session:
    return Session(
        session_id=name,
        pid=1,
        cwd=cwd,
        name=name,
        name_source="derived",
        status=status,
        status_updated_at=NOW,
        updated_at=NOW,
        started_at=NOW,
        repos_touched=repos_touched or [],
        repos_edited=repos_edited or [],
        waiting_on=waiting_on,
        turn_ended=turn_ended,
        last_turn_ts=last_turn_ts,
        recap=recap,
        is_shell=is_shell,
        has_transcript=has_transcript,
    )


# -- repo_groups: jeeves groups only via edits, others via any touch --------


def test_repo_groups_jeeves_only_groups_via_edits():
    a = _session("a", repos_touched=["jeeves", "kafka-infra"], repos_edited=["kafka-infra"])
    b = _session("b", repos_touched=["jeeves"], repos_edited=[])

    groups = repo_groups([a, b])

    assert groups == []  # jeeves touched by both but edited by neither


def test_repo_groups_jeeves_groups_when_both_edit_it():
    a = _session("a", repos_touched=["jeeves"], repos_edited=["jeeves"])
    b = _session("b", repos_touched=["jeeves"], repos_edited=["jeeves"])

    groups = repo_groups([a, b])

    assert groups == [{"repo": "jeeves", "sessions": ["a", "b"]}]


def test_repo_groups_needs_at_least_two_sessions():
    a = _session("a", repos_touched=["kafka-infra"])

    assert repo_groups([a]) == []


def test_repo_groups_team_repo_groups_on_any_touch():
    a = _session("a", repos_touched=["kafka-infra"], repos_edited=[])
    b = _session("b", repos_touched=["kafka-infra"], repos_edited=[])

    assert repo_groups([a, b]) == [{"repo": "kafka-infra", "sessions": ["a", "b"]}]


# -- inventory_payload schema -------------------------------------------------


def test_inventory_payload_excludes_shell_sessions():
    shell = _session("shellsess", is_shell=True, has_transcript=False)

    payload = inventory_payload([shell], set())

    assert payload["sessions"] == []


def test_inventory_payload_exclude_by_name_and_short_id():
    a = _session("a")
    b = _session("bbbbbb-long-session-id")

    payload = inventory_payload([a, b], {"a", "bbbbbb"})

    assert payload["sessions"] == []


def test_inventory_payload_sorted_by_repo_then_name():
    a = _session("zeta", cwd=f"{REPOS}/kafka-infra")
    b = _session("alpha", cwd=f"{REPOS}/data-platform")
    c = _session("beta", cwd=f"{REPOS}/data-platform")

    payload = inventory_payload([a, b, c], set())

    assert [s["name"] for s in payload["sessions"]] == ["alpha", "beta", "zeta"]


# -- render_status: line shapes -----------------------------------------------


def test_render_status_line_shapes():
    waiting_session = _session(
        "waiter",
        repos_touched=["data-platform"],
        waiting_on={"kind": "permission", "detail": "Bash: ls /tmp"},
        last_turn_ts=NOW - timedelta(minutes=3),
    )
    done_session = _session(
        "doner",
        repos_touched=["jeeves"],
        repos_edited=["jeeves"],
        turn_ended=True,
        recap="all finished here",
    )
    busy_session = _session("busier", repos_touched=["kafka-infra"], turn_ended=False)
    grouped_a = _session("ga", cwd=f"{REPOS}/kafka-infra", repos_touched=["kafka-infra"])
    grouped_b = _session("gb", cwd=f"{REPOS}/kafka-infra", repos_touched=["kafka-infra"])

    payload = inventory_payload([waiting_session, done_session, busy_session, grouped_a, grouped_b], set())
    status = render_status(payload)
    lines = status.split("\n")

    assert any(l.startswith("waiter ·") and "waiting on: permission" in l and "Bash: ls /tmp" in l for l in lines)
    assert any(l.startswith("doner ·") and "done: all finished here" in l for l in lines)
    assert any(l.startswith("busy:") and "busier" in l for l in lines)
    assert any(l.startswith("same repo: kafka-infra") and "ga, gb" in l for l in lines)


def test_render_status_done_line_uses_jeeves_fallback_when_only_jeeves_touched():
    done_session = _session("doner", repos_touched=["jeeves"], turn_ended=True, recap="wrapped up")

    payload = inventory_payload([done_session], set())
    status = render_status(payload)

    assert "doner · jeeves · done: wrapped up" in status


def test_render_status_empty_when_no_sessions():
    assert render_status(inventory_payload([], set())) == ""


# -- render_table --------------------------------------------------------------


def test_render_table_marks_edited_repo_and_lists_same_repo_group():
    a = _session(
        "a",
        cwd=f"{REPOS}/kafka-infra",
        repos_touched=["kafka-infra"],
        repos_edited=["kafka-infra"],
    )
    b = _session("b", cwd=f"{REPOS}/kafka-infra", repos_touched=["kafka-infra"], repos_edited=[])

    payload = inventory_payload([a, b], set())
    table = render_table(payload)

    assert "kafka-infra*" in table
    assert "same repo: kafka-infra -> a, b" in table


# -- terminal number ("terminal #N") ---------------------------------------------


def test_session_terminal_is_window_number_plus_tab_outside_tab_one():
    s = _session("a")
    assert s.terminal is None
    s.window_number, s.tab_index = 7, 1
    assert s.terminal == "7"
    s.tab_index = 2
    assert s.terminal == "7.2"


def test_inventory_lists_the_terminal_number_in_json_and_table():
    a = _session("alpha")
    a.window_number, a.tab_index = 7, 1
    b = _session("beta")

    payload = inventory_payload([a, b], set())
    assert {s["name"]: s["tab"] for s in payload["sessions"]} == {"alpha": "7", "beta": None}
    table = render_table(payload).splitlines()
    assert table[0].startswith("TAB")
    assert any(line.startswith("#7 ") and "alpha" in line for line in table)


def test_work_repo_prefers_an_edited_project_repo_over_the_home_base():
    from podbay.inventory import work_repo

    assert work_repo(_session("a", repos_touched=["jeeves", "sora"], repos_edited=["jeeves"])) == "sora"
    assert work_repo(_session("b", repos_touched=["jeeves", "sora", "podbay"], repos_edited=["podbay"])) == "podbay"
    assert work_repo(_session("c", repos_touched=["jeeves"], repos_edited=["jeeves"])) == "jeeves"
    assert work_repo(_session("d", cwd=f"{REPOS}/keitos")) == "keitos"
