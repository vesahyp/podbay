from datetime import datetime

from podbay.voice import _projection_tone, _seven_day_projection, header_segments, limits_segments, scan_status

NOW = datetime(2026, 9, 9, 12, 0)  # a Wednesday
NOW_TS = NOW.timestamp()


def _texts(segments):
    return "".join(text for text, _pct in segments)


def _pcts(segments):
    """Every toned segment: live figures as numbers, projections as tones."""
    return [pct for _text, pct in segments if pct is not None]


def test_limits_segments_five_hour_and_week_with_countdowns():
    segments = limits_segments(53, NOW_TS + 9 * 60, 61, NOW_TS + 3 * 86400 + 4 * 3600, NOW)

    # window Sat 16:00 -> Sat 16:00: 61% spent in 60 weekday hours of 120
    # runs out Fri ~02:20, 1d 13h before the reset
    assert _texts(segments) == "5H 53% (resets 0h 9m)  ·  7D 61% (resets 3d 4h, out 1d 13h early)"
    assert _pcts(segments) == [53, 61, "alert"]


def test_limits_segments_only_five_hour():
    segments = limits_segments(32, NOW_TS + 3600, None, None, NOW)
    assert _texts(segments) == "5H 32% (resets 1h 0m)"
    assert _pcts(segments) == [32]


def test_limits_segments_only_week():
    segments = limits_segments(None, None, 61, NOW_TS + 86400, NOW)
    # window Thu 12:00 -> Thu 12:00: 96 of 120 weekday hours elapsed
    assert _texts(segments) == "7D 61% (resets 1d 0h, ~76% at reset)"
    assert _pcts(segments) == [61, "ok"]


def test_limits_segments_blank_when_nothing_known():
    assert limits_segments(None, None, None, None, NOW) == []


def test_limits_segments_shows_pct_without_countdown_when_resets_at_missing():
    segments = limits_segments(32, None, None, None, NOW)
    assert _texts(segments) == "5H 32%"


def test_limits_segments_omits_countdown_when_already_past():
    segments = limits_segments(32, NOW_TS - 10, None, None, NOW)
    assert _texts(segments) == "5H 32%"


def test_header_segments_includes_ship_identity_and_quotas():
    segments = header_segments(53, None, 8, None, NOW)
    text = _texts(segments)
    assert text.startswith("● POD BAY  ·  HAL 9000")
    assert "5H 53%" in text
    assert "7D 8%" in text
    # counts (pods / awaiting input / unread) are on screen already -- the
    # header no longer repeats them.
    assert "POD" not in text.replace("POD BAY", "")
    assert "AWAITING" not in text
    assert "UNREAD" not in text


def test_header_segments_blank_limits_omitted():
    segments = header_segments(None, None, None, None, NOW)
    text = _texts(segments)
    assert text == "● POD BAY  ·  HAL 9000"
    assert "5H" not in text
    assert "7D" not in text


def test_limits_segments_appends_per_model_weekly_entry():
    segments = limits_segments(
        24, NOW_TS + 3 * 3600 + 31 * 60, 13, NOW_TS + 6 * 86400 + 3600, NOW,
        model_entries=[{"key": "week_model", "label": "Fable", "pct": 8.0, "resets_text": "x"}],
    )

    # the Fable entry has no reset time of its own and borrows the account-wide one
    # window Tue 13:00 -> Tue 13:00: 23 of 120 weekday hours elapsed
    assert _texts(segments) == "5H 24% (resets 3h 31m)  ·  7D 13% (resets 6d 1h, ~68% at reset)  ·  7D Fable 8% (~42% at reset)"
    assert _pcts(segments) == [24, 13, "ok", 8.0, "ok"]


def test_limits_segments_multiple_model_entries_each_get_their_own_group():
    segments = limits_segments(
        None, None, None, None, NOW,
        model_entries=[
            {"key": "week_model", "label": "Fable", "pct": 8.0, "resets_text": "x"},
            {"key": "week_model", "label": "Opus", "pct": 41.0, "resets_text": "x"},
        ],
    )

    assert _texts(segments) == "7D Fable 8%  ·  7D Opus 41%"
    assert _pcts(segments) == [8.0, 41.0]


def test_limits_segments_model_entry_uses_its_own_reset_time():
    segments = limits_segments(
        None, None, None, None, NOW,
        model_entries=[{"key": "week_model", "label": "Fable", "pct": 50.0, "resets_text": "x", "resets_at": NOW_TS + 5 * 86400}],
    )

    # window Mon 12:00 -> Mon 12:00: 50% in 48 of 120 weekday hours runs out
    # Fri 12:00, a whole weekend (3d) before the Monday reset
    assert _texts(segments) == "7D Fable 50% (out 3d 0h early)"
    assert _pcts(segments) == [50.0, "alert"]


def test_seven_day_projection_skips_a_window_that_only_just_opened():
    assert _seven_day_projection(2.0, NOW_TS + 7 * 86400 - 3600, NOW) is None


def test_seven_day_projection_skips_a_past_reset():
    assert _seven_day_projection(50.0, NOW_TS - 10, NOW) is None


def test_seven_day_projection_states_the_margin_in_days_and_hours():
    # window Sun 12:00 -> Sun 12:00: 90% in 60 weekday hours runs out Wed
    # ~18:40, 3d 17h before the reset
    text, pct = _seven_day_projection(90.0, NOW_TS + 4 * 86400, NOW)
    assert (text, pct) == ("out 3d 17h early", "alert")


def test_seven_day_projection_counts_weekday_time_only():
    # window Fri 08:00 -> Fri 08:00, now Mon 08:00: 24 weekday hours of 120
    # have passed, the weekend in between counts for nothing
    monday = datetime(2026, 9, 14, 8, 0)
    text, pct = _seven_day_projection(10.0, monday.timestamp() + 4 * 86400, monday)
    assert (text, pct) == ("~50% at reset", "ok")


def test_projection_tone_only_warns_near_the_line():
    assert _projection_tone(80.0) == "ok"
    assert _projection_tone(85.0) == "warn"
    assert _projection_tone(99.0) == "warn"
    assert _projection_tone(100.0) == "alert"


def test_seven_day_projection_skips_a_window_that_has_only_seen_a_weekend():
    sunday = datetime(2026, 9, 13, 12, 0)
    assert _seven_day_projection(5.0, sunday.timestamp() + 6 * 86400, sunday) is None


def test_limits_segments_ignores_session_and_week_all_entries():
    segments = limits_segments(
        None, None, None, None, NOW,
        model_entries=[
            {"key": "session", "label": None, "pct": 29.0, "resets_text": "x"},
            {"key": "week_all", "label": None, "pct": 13.0, "resets_text": "x"},
        ],
    )

    assert segments == []


def test_header_segments_includes_per_model_weekly_entry():
    segments = header_segments(
        None, None, None, None, NOW,
        model_entries=[{"key": "week_model", "label": "Fable", "pct": 8.0, "resets_text": "x"}],
    )

    assert _texts(segments) == "● POD BAY  ·  HAL 9000  ·  7D Fable 8%"


def test_scan_status_keeps_width_and_flips_glyph():
    idle = scan_status(False, NOW)
    busy = scan_status(True, NOW)
    assert idle.startswith("●") and busy.startswith("◌")
    assert len(idle) == len(busy)
    assert scan_status(False, None) == "● --:--:--"
