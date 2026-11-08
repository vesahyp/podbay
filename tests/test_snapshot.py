"""podbay snapshot: the phone app's state (snapshot.py)."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

from podbay import snapshot

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


def test_runs_part_groups_by_night_and_workspace():
    runs = {"runs": [
        {"workspace": "a", "started_at": "2026-11-08T04:00:00+02:00", "total_ms": 600000},
        {"workspace": "a", "started_at": "2026-11-07T04:00:00+02:00", "total_ms": 120000},
        {"workspace": "b", "started_at": "2026-11-08T05:00:00+02:00", "total_ms": 60000},
        {"workspace": "c", "started_at": "2026-10-01T05:00:00+02:00", "total_ms": 60000},
    ]}
    out = snapshot.runs_part(runs, NOW)
    assert [s["name"] for s in out["series"]] == ["a", "b"]
    assert out["series"][0]["minutes"][-2:] == [2.0, 10.0]
    assert snapshot.runs_part(None, NOW) is None


def test_parse_power():
    out = "Now drawing from 'Battery Power'\n -InternalBattery-0 (id=1)\t64%; discharging; 3:10 remaining present: true"
    assert snapshot.parse_power(out) == {"source": "battery", "percent": 64, "state": "discharging"}
    assert snapshot.parse_power("") is None


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
