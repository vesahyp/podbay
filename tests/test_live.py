"""The live counter (analytics/live/handler.py): one log file to day sets,
and its bot lists stay the pipeline's."""

import ast
import importlib.util
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
LIVE = REPO / "analytics" / "live" / "handler.py"
EVENTS = REPO / "analytics" / "clavesa" / "podbay-traffic" / "transforms" / "events.py"


def _load():
    spec = importlib.util.spec_from_file_location("live_handler", LIVE)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["live_handler"] = mod
    spec.loader.exec_module(mod)
    return mod


def _tuple_from(path: Path, name: str):
    """A module-level tuple constant, read from source so pyspark is not needed."""
    tree = ast.parse(path.read_text())
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == name for t in node.targets):
            return ast.literal_eval(node.value)
    raise AssertionError(f"{name} not in {path}")


def test_bot_lists_match_the_pipeline():
    for name in ("_NAMED_BOTS", "_HEADLESS"):
        assert _tuple_from(LIVE, name) == _tuple_from(EVENTS, name), f"{name} drifted from events.py"


FIELDS = "#Fields: date time x-edge-location sc-bytes c-ip cs-method cs(Host) cs-uri-stem sc-status cs(Referer) cs(User-Agent) cs-uri-query cs(Cookie) x-edge-result-type\n"
UA = "Mozilla/5.0%20(Macintosh;%20Intel%20Mac%20OS%20X%2010_15_7)%20AppleWebKit/537.36"
BOT = "Mozilla/5.0%20(compatible;%20Googlebot/2.1)"
HEADLESS = "Mozilla/5.0%20HeadlessChrome/120"


def _line(time, ua, query, stem="/t.gif", date="2026-10-18"):
    return f"{date}\t{time}\tARN1-C1\t400\t10.0.0.1\tGET\tpodbay.tienoo.com\t{stem}\t200\t-\t{ua}\t{query}\t-\tMiss\n"


def _q(e, sid, t, **extra):
    # The tracker encodes each value and CloudFront encodes the whole field
    # again: a double-encoded query, as in the real log.
    from urllib.parse import quote, urlencode

    return quote(urlencode({"e": e, "uid": "u1", "sid": sid, "p": "/", "t": str(t), **extra}), safe="=&")


def test_counts_people_by_session_and_helsinki_day():
    mod = _load()
    noon = 1792317600000  # 2026-10-18 10:00:00 UTC, 13:00 Helsinki
    late = 1792362000000  # 2026-10-18 22:20:00 UTC, 01:20 on the 19th Helsinki
    lines = [
        "#Version: 1.0\n",
        FIELDS,
        _line("10:00:00", UA, _q("pageview", "s1", noon)),
        _line("10:00:05", UA, _q("click", "s1", noon + 5000, sel="install-copy")),
        _line("10:00:06", UA, _q("click", "s1", noon + 6000, sel="hero-github")),
        _line("10:00:07", UA, _q("click", "s1", noon + 7000, sel="install-copy")),
        _line("10:01:00", UA, _q("pageview", "s2", noon + 60000)),
        _line("10:01:30", UA, _q("click", "s2", noon + 90000, sel="section-hero")),
        _line("10:02:00", BOT, _q("pageview", "s3", noon + 120000)),
        _line("10:02:00", HEADLESS, _q("pageview", "s4", noon + 120000)),
        _line("10:03:00", BOT, "-", stem="/"),
        _line("10:04:00", UA, "-", stem="/index.html"),
        _line("22:20:00", UA, _q("pageview", "s5", late)),
    ]
    days = mod.count(lines)
    assert sorted(days) == ["2026-10-18", "2026-10-19"]
    d = days["2026-10-18"]
    assert d["visits"] == {"s1", "s2"}
    assert d["copies"] == {"s1"}
    assert d["github"] == {"s1"}
    assert days["2026-10-19"]["visits"] == {"s5"}


def test_log_clock_is_the_fallback_for_a_bad_beacon_clock():
    mod = _load()
    lines = [FIELDS, _line("22:30:00", UA, _q("pageview", "s9", "nope"))]
    assert list(mod.count(lines)) == ["2026-10-19"]
