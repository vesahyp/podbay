"""podbay snapshot: the phone app's state (snapshot.py)."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

from podbay import snapshot, voice

NOW = datetime(2026, 11, 8, 12, 0)

CRONTAB = """\
MAILTO=someone@example.com
# a comment
0 2 * * * /x/scripts/cronrun scraper /x/run.sh
30 7 * * * /x/scripts/cronrun stats /x/stats.sh
15 11 * * 0 /x/scripts/cronrun diskclean /x/diskclean
0 11 * * * /x/scripts/cron-digest
"""


def _run(day: datetime, rc: int = 0, status: str = "OK", log: list[str] | None = None) -> dict:
    return {"label": "x", "started": f"{day:%Y-%m-%d} 02:00:00 +0200", "finished": f"{day:%Y-%m-%d} 02:05:00 +0200",
            "rc": rc, "elapsed_seconds": 300, "tail": "", "status": status, "epoch": int(day.timestamp()),
            "log_tail": log or ["done"]}


def test_scrub_removes_secret_shapes_and_keeps_paths():
    line = "key AKIAABCDEFGHIJKLMNOP token=abc123 at /Users/someone/Repositories/a-long-repo-name/analytics/logs/run.log"
    out = snapshot.scrub(line)
    assert "AKIA" not in out and "abc123" not in out
    assert "token=[removed]" in out
    assert "/Users/someone/Repositories/a-long-repo-name/analytics/logs/run.log" in out
    assert snapshot.scrub("ghp_" + "a" * 30) == "[removed]"


def test_parse_crontab_keeps_only_cronrun_jobs():
    jobs = snapshot.parse_crontab(CRONTAB)
    assert [j["label"] for j in jobs] == ["scraper", "stats", "diskclean"]
    assert jobs[1]["hour"] == 7 and jobs[1]["minute"] == 30


def test_scheduled_on_weekday_field():
    job = snapshot.parse_crontab(CRONTAB)[2]  # Sundays
    assert snapshot.scheduled_on(job, datetime(2026, 11, 8).date())  # a Sunday
    assert not snapshot.scheduled_on(job, datetime(2026, 11, 9).date())


def test_nightly_cells(tmp_path: Path):
    day = lambda n: NOW - timedelta(days=n)  # noqa: E731
    runs = [_run(day(5)), _run(day(4), rc=1, log=["boom", "secret=hunter2"]), _run(day(2), status="SKIPPED, Docker down"), _run(day(0))]
    (tmp_path / "scraper.jsonl").write_text("\n".join(json.dumps(r) for r in runs) + "\n")
    nightly, lines = snapshot.nightly_part(CRONTAB, tmp_path, NOW)
    assert nightly["days"][-1] == "2026-11-08" and len(nightly["days"]) == 7
    scraper = next(j for j in nightly["jobs"] if j["label"] == "scraper")
    states = [c["state"] for c in scraper["cells"]]
    # 6 days ago: before the first run on record. 3 and 1 days ago: missed.
    assert states == ["unknown", "ok", "fail", "miss", "skip", "miss", "ok"]
    failed = scraper["cells"][2]
    assert failed["rc"] == 1 and failed["log"] == ["boom", "secret=[removed]"]
    assert lines["nightly:scraper:2026-11-04"]["quote"] == "scraper on Wed 4 Nov: failed, exit 1"
    stats = next(j for j in nightly["jobs"] if j["label"] == "stats")
    assert [c["state"] for c in stats["cells"]] == ["unknown"] * 7  # no history at all
    disk = next(j for j in nightly["jobs"] if j["label"] == "diskclean")
    assert disk["at"] == "Sun 11:15"
    assert disk["cells"][-1]["state"] == "unknown"  # Sunday 11:15 has passed, and there is no history


def test_nightly_pending_and_none():
    early = datetime(2026, 11, 8, 1, 0)  # a Sunday, 01:00
    nightly, _ = snapshot.nightly_part(CRONTAB, None, early)
    disk = next(j for j in nightly["jobs"] if j["label"] == "diskclean")
    assert disk["cells"][-1]["state"] == "pending"
    assert disk["cells"][-2]["state"] == "none"  # Saturday


def test_downsample_keeps_one_point_per_step():
    t0 = NOW.timestamp() - 3600
    points = [{"at": t0 + i * 60, "load": float(i), "free": 1, "disk": 2} for i in range(60)]
    out = snapshot.downsample(points, NOW.timestamp())
    assert len(out) == 6
    assert out[-1]["load"] == 59.0


def test_run_phases_split_like_the_old_runs_page():
    timed = {"total_ms": 600000, "phases": {"startup": 30000, "image": 30000, "metastore": 60000, "inputs": 60000, "nodes": 300000},
             "nodes": [{"timing": {"phases": {"spark_session": 60000, "catalog": 0, "inputs": 30000, "transform": 30000,
                                              "write": 60000, "stats": 60000}}}]}
    # startup 1, metastore 1, spark 1, reads 1 + 0.5, transform 0.5, writes 1, stats 1, rest bookkeeping
    assert snapshot.run_phases(timed) == [1.0, 1.0, 1.0, 1.5, 0.5, 1.0, 1.0, 0.0, 3.0]
    old = {"total_ms": 120000, "phases": {"nodes": 90000}, "nodes": [{"node": "x"}]}
    assert snapshot.run_phases(old) == [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 1.5, 0.5]


def test_runs_part_nights_and_pipelines():
    def run(ws, pipe, at, minutes, status="SUCCEEDED"):
        return {"workspace": ws, "pipeline": pipe, "started_at": at, "total_ms": minutes * 60000, "status": status, "phases": {}}
    runs = {"generated_at": "2026-11-08T10:45:00+02:00", "runs": [
        run("a", "traffic", "2026-11-08T04:00:00+02:00", 10),
        run("a", "traffic", "2026-11-07T04:00:00+02:00", 2),
        run("a", "traffic", "2026-11-06T04:00:00+02:00", 4),
        run("b", "insights", "2026-11-08T05:00:00+02:00", 3, status="FAILED"),
        run("c", "old", "2026-09-01T05:00:00+02:00", 1),
    ]}
    out = snapshot.runs_part(runs)
    assert len(out["nights"]) == 30 and out["nights"][-1]["d"] == "2026-11-08"
    last = out["nights"][-1]
    assert (last["n"], last["f"], last["p"]) == (2, 1, 2)
    assert last["v"][snapshot.RUN_PHASES.index("bookkeeping")] == 13.0
    assert out["nights"][0]["n"] == 0 and out["nights"][0]["v"] == []
    assert [p["name"] for p in out["pipelines"]] == ["a · traffic", "b · insights", "c · old"]
    traffic = out["pipelines"][0]
    assert traffic["latest_ms"] == 600000 and traffic["median_ms"] == 180000 and traffic["median_of"] == 2
    assert traffic["runs"][-1]["at"] == "2026-11-08 04:00"
    assert out["pipelines"][1]["runs"][0]["f"] == 1 and out["pipelines"][1]["median_ms"] is None
    assert out["count"] == 5
    assert snapshot.runs_part(None) is None


def test_read_runs_json_rereads_only_a_changed_file(tmp_path):
    f = tmp_path / "runs.json"
    assert snapshot.read_runs_json(str(f)) is None
    f.write_text('{"runs": []}')
    assert snapshot.read_runs_json(str(f)) == {"runs": []}
    f.write_text('{"runs": [1, 2]}')
    assert snapshot.read_runs_json(str(f)) == {"runs": [1, 2]}
    assert snapshot.read_runs_json("") is None


def test_parse_power():
    out = "Now drawing from 'Battery Power'\n -InternalBattery-0 (id=1)\t64%; discharging; 3:10 remaining present: true"
    assert snapshot.parse_power(out) == {"source": "battery", "percent": 64, "state": "discharging"}
    assert snapshot.parse_power("") is None


def _spend_report() -> dict:
    """A report.json as aws-costs writes it, with made-up amounts."""
    days = ["2026-11-01", "2026-11-02", "2026-11-03"]
    return {"generated": "2026-11-03 19:10", "accounts": [
        {"profile": "alpha", "fetched": "2026-11-03 19:10", "normal_per_day": 0.4,
         "last_month": {"month": "2026-10", "days": 31, "running": 12.5, "anomalies": 1.0, "one-off": 0, "tax": 3.0, "credit": 0},
         "this_month": {"month": "2026-11", "days": 2, "running": 0.8, "anomalies": 0, "one-off": 0, "tax": 0, "credit": 0, "through": "2026-11-02"},
         "anomalies": [{"kind": "above normal", "service": "S3", "start": "2026-11-02", "end": "2026-11-02", "excess": 0.3,
                        "usage_types": ["Requests"], "peak": 0.5, "normal_per_day": 0.1}],
         "left_out": [{"day": "2026-11-01", "usd": 9}], "domains": [{"domain": "example.test"}],
         "services": [["S3", 1.0]],
         "chart": {"days": days, "series": {"S3": [0.1, 0.51234, 0.2], "Lambda": [0, 0, 0.0001]},
                   "band": [[0.1, 0.2, 0.3], [0.1, 0.25, 0.3], [0.1, 0.2, 0.3]], "excess": [0, 0.3, 0],
                   "oneoff": [0, 0, 0], "tax": [0, 0, 0]}},
        {"profile": "beta", "fetched": "2026-10-03 19:10", "stale": "beta: login failed", "normal_per_day": 0.1,
         "last_month": None, "this_month": None, "anomalies": [],
         "chart": {"days": days, "series": {"KMS": [0.03, 0.03, 0.03]}, "band": [], "excess": [], "oneoff": [], "tax": []}},
        {"profile": "broken"},
    ]}


def test_spend_part_keeps_the_chart_and_drops_the_detail():
    out = snapshot.spend_part(_spend_report())
    assert out["generated"] == "2026-11-03 19:10"
    alpha, beta = out["accounts"]
    assert alpha["series"] == {"S3": [0.1, 0.512, 0.2]}  # an all-zero service is left out
    assert alpha["normal"] == [0.2, 0.25, 0.2]
    assert alpha["last_month"]["running"] == 12.5 and alpha["this_month"]["through"] == "2026-11-02"
    assert alpha["anomalies"][0] == {"kind": "above normal", "service": "S3", "start": "2026-11-02", "end": "2026-11-02",
                                     "excess": 0.3, "usage_types": ["Requests"]}
    assert "left_out" not in alpha and "domains" not in alpha and alpha["stale"] is None
    # A failed account keeps its old data, its old fetch time and why.
    assert beta["stale"] == "beta: login failed" and beta["fetched"] == "2026-10-03 19:10" and beta["normal"] == []
    assert snapshot.spend_part(None) is None and snapshot.spend_part({"accounts": []}) is None


def test_build_carries_the_spend_part():
    state = snapshot.build(payload={"sessions": []}, headlines={}, quota_payload=None, health=None, power=None,
                           docker_up=None, agents=[], crontab_text="", cron_dir=None, runs_json=None,
                           spend_json=_spend_report(), now=NOW)
    assert [a["profile"] for a in state["spend"]["accounts"]] == ["alpha", "beta"]
    assert len(json.dumps(state["spend"])) < 2000


def _release_watch() -> dict:
    """A release-watch.json as the home repo writes it, with made-up items."""
    item = {"id": "a1", "source": "Sail", "group": "Sail", "date": "2026-11-20", "title": "v9.9.9",
            "link": "https://example.com/r", "use": "runner", "part": "Sail runner", "why": "Closes a gap.",
            "breaks": False, "found": "2026-11-21", "pushed": True, "pushed_at": "2026-11-21T10:20+02:00",
            "text": "left out"}
    return {
        "generated": "2026-11-21T10:20:00+02:00", "window_days": 30, "last_push": "2026-11-21",
        "last_run": {"at": "2026-11-21T10:20:00+02:00", "read": 40, "new": 12, "kept": 2, "model": "opus",
                     "model_calls": 1, "seconds": 30.0, "errors": [], "gaps": [], "per_source": {}},
        "runs": [{"per_source": {"Sail": {"judged": 1, "kept": 1, "dropped": 0},
                                 "Databricks": {"judged": 9, "kept": 1, "dropped": 8}}},
                 {"per_source": {"Databricks": {"judged": 3, "kept": 0, "dropped": 3}}}],
        "items": [item,
                  {**item, "id": "b2", "date": "2026-11-21", "use": "odd", "breaks": "yes", "pushed_at": None},
                  {"id": "c3"}],
    }


def test_releases_part_keeps_items_counts_and_push_times():
    out = snapshot.releases_part(_release_watch())
    assert [x["id"] for x in out["items"]] == ["b2", "a1"]
    assert out["items"][0]["use"] == "nothing yet" and out["items"][0]["breaks"] is False
    assert out["items"][1]["pushed_at"] == "2026-11-21T10:20+02:00" and "text" not in out["items"][1]
    assert out["sources"] == [{"source": "Databricks", "kept": 1, "dropped": 11},
                              {"source": "Sail", "kept": 1, "dropped": 0}]
    assert out["last_run"]["model_calls"] == 1 and "per_source" not in out["last_run"]
    assert snapshot.releases_part(None) is None


def test_build_carries_the_releases_part():
    state = snapshot.build(payload={"sessions": []}, headlines={}, quota_payload=None, health=None, power=None,
                           docker_up=None, agents=[], crontab_text="", cron_dir=None, runs_json=None,
                           releases_json=_release_watch(), now=NOW)
    assert state["releases"]["last_push"] == "2026-11-21"


def test_pushes_part_merges_senders_newest_first_with_alarm_state_now():
    t = NOW.timestamp()
    entries = [
        {"at": t - 100, "title": "Head Jeeves", "body": "Ship now?", "url": "https://app.example/#line/s1",
         "tag": "head-jeeves", "source": "Mac", "delivered": 1},
        {"at": t - 50, "title": "OK: api-down", "body": "Back to normal.", "url": "https://app.example/", "tag": "alarm-api-down",
         "source": "aws", "delivered": 1, "alarm": "api-down", "region": "eu-north-1", "state": "OK"},
        {"at": t - 3000, "title": "ALARM: api-down", "body": "key=" + "A" * 45, "tag": "alarm-api-down",
         "source": "aws", "delivered": 1, "alarm": "api-down", "region": "eu-north-1", "state": "ALARM"},
        {"at": t - 20, "title": "nightly-x failed", "tag": "cron-nightly-x", "source": "Mac", "delivered": 0},
        {"at": t - 10, "title": "Release notes", "tag": "release-watch", "source": "Mac"},
        {"at": t - 10, "title": "Release notes", "tag": "release-watch", "source": "Mac"},  # the same push twice
        {"at": t - 31 * 86400, "title": "too old", "tag": "jeeves"},
        {"at": "x", "title": "broken"},
        "not a dict",
    ]
    out = snapshot.pushes_part(entries, {"eu-north-1/api-down": "OK"}, NOW)
    assert [x["kind"] for x in out["items"]] == ["release-watch", "nightly", "alarm", "head-jeeves", "alarm"]
    assert out["items"][1]["delivered"] == 0
    alarm = out["items"][4]
    assert alarm["state"] == "ALARM" and alarm["state_now"] == "OK" and "[removed]" in alarm["body"]
    assert out["items"][3]["url"].endswith("#line/s1")
    assert snapshot.pushes_part([entries[1]], {}, NOW)["items"][0]["state_now"] is None
    retries = [{**entries[2], "at": t - 3000 + k * 60, "message_id": "m1", "delivered": 0} for k in range(3)]
    assert len(snapshot.pushes_part(retries, {}, NOW)["items"]) == 1


def test_read_pushes_file_skips_broken_lines(tmp_path):
    p = tmp_path / "pushes.jsonl"
    p.write_text('{"at": 1, "title": "a"}\nnot json\n[1]\n{"at": 2, "title": "b"}\n')
    assert [r["title"] for r in snapshot.read_pushes_file(str(p))] == ["a", "b"]
    assert snapshot.read_pushes_file(str(tmp_path / "missing.jsonl")) == []
    assert snapshot.read_pushes_table("") == [] and snapshot.read_alarm_states([{"title": "no alarm"}]) == {}


def test_read_spend_json_and_runs_json_cache_apart(tmp_path):
    a, b = tmp_path / "runs.json", tmp_path / "report.json"
    a.write_text('{"runs": []}')
    b.write_text('{"accounts": []}')
    assert snapshot.read_runs_json(str(a)) == {"runs": []}
    assert snapshot.read_spend_json(str(b)) == {"accounts": []}
    assert snapshot.read_runs_json(str(a)) == {"runs": []}


def test_build_lines_and_options():
    session = {"session_id": "s1", "short_id": "s1", "name": "sora-laps", "tab": "6", "repo": "sora", "state": "needs_you",
               "age_seconds": 60, "has_transcript": True, "waiting_on": {"kind": "question_text", "detail": "Start line or checkpoint?"}}
    headlines = {"s1": {"kind": "decision", "text": "Count laps from the start line or the first checkpoint?",
                        "options": ["Start line", "First checkpoint"]}}
    state = snapshot.build(payload={"sessions": [session]}, headlines=headlines, quota_payload=None, health=None,
                           power=None, docker_up=None, agents=[], crontab_text=CRONTAB, cron_dir=None, runs_json=None,
                           repos=["atlas"], now=NOW)
    decision = state["board"]["sections"]["decision"][0]
    assert decision["id"] == "s1" and decision["options"] == ["Start line", "First checkpoint"]
    assert state["lines"]["s1"] == {"target": "#6 sora", "quote": "Count laps from the start line or the first checkpoint?"}
    assert state["lines"]["machine"]["target"] == "machine"
    assert state["repos"] == ["atlas", "sora"]
    assert state["board"]["summary"] == "1 decision waits for you."
    later = snapshot.build(payload={"sessions": [session]}, headlines=headlines, quota_payload=None, health=None,
                           power=None, docker_up=None, agents=[], crontab_text=CRONTAB, cron_dir=None, runs_json=None,
                           repos=["atlas"], now=NOW + timedelta(seconds=30))
    assert snapshot.fingerprint(state) == snapshot.fingerprint(later)


def test_build_marks_a_stale_line_and_lists_it():
    session = {"session_id": "s1", "short_id": "s1", "name": "abac-1", "title": "Access control", "tab": "6", "repo": "clavesa",
               "state": "needs_you", "age_seconds": 3 * 3600, "has_transcript": True}
    headlines = {"s1": {"kind": "progress", "text": "Column hiding, done today"}}
    state = snapshot.build(payload={"sessions": [session]}, headlines=headlines, quota_payload=None, health=None,
                           power=None, docker_up=None, agents=[], crontab_text=CRONTAB, cron_dir=None, runs_json=None, now=NOW)
    line = state["board"]["sections"]["progress"][0]
    assert line["stale"] == "idle" and line["when"] == "stale: the session has been idle for 3h"
    assert [(s["key"], s["why"]) for s in state["board"]["stale"]] == [("s1", "idle")]
    assert voice.hal_stale_line(state["board"]["stale"][0]) == (
        "The board's progress line for Access control is stale: the session has been idle for 3h. "
        "It says: \"Column hiding, done today\" Rewrite or delete the entry s1 in headlines.json.")
