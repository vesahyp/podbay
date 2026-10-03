from datetime import datetime

import pytest

from podbay.state import parse_when, ParseError

NOW = datetime(2026, 9, 9, 10, 0)  # a Wednesday


def test_relative_hours():
    assert parse_when("+2h", NOW) == datetime(2026, 9, 9, 12, 0)


def test_relative_days():
    assert parse_when("+3d", NOW) == datetime(2026, 9, 12, 10, 0)


def test_relative_minutes():
    assert parse_when("+30m", NOW) == datetime(2026, 9, 9, 10, 30)


def test_tomorrow_bare():
    assert parse_when("tomorrow", NOW) == datetime(2026, 9, 10, 9, 0)


def test_tomorrow_with_hour():
    assert parse_when("tomorrow 9", NOW) == datetime(2026, 9, 10, 9, 0)


def test_tomorrow_with_hour_minute():
    assert parse_when("tomorrow 14:30", NOW) == datetime(2026, 9, 10, 14, 30)


def test_weekday_future_this_week():
    # NOW is Wednesday 2026-09-09; Friday is 2 days ahead
    assert parse_when("fri 14", NOW) == datetime(2026, 9, 11, 14, 0)


def test_weekday_bare_uses_default_hour():
    assert parse_when("fri", NOW) == datetime(2026, 9, 11, 9, 0)


def test_weekday_rolls_to_next_week_if_passed():
    # asking for "wed 9" when it's already Wednesday 10:00 should roll a week
    assert parse_when("wed 9", NOW) == datetime(2026, 9, 16, 9, 0)


def test_date_only():
    assert parse_when("2026-09-12", NOW) == datetime(2026, 9, 12, 9, 0)


def test_date_and_time():
    assert parse_when("2026-09-12 09:00", NOW) == datetime(2026, 9, 12, 9, 0)


def test_bare_time_later_today():
    assert parse_when("14:30", NOW) == datetime(2026, 9, 9, 14, 30)


def test_bare_time_already_passed_rolls_to_tomorrow():
    assert parse_when("09:00", NOW) == datetime(2026, 9, 10, 9, 0)


def test_today_with_hour():
    assert parse_when("today 14", NOW) == datetime(2026, 9, 9, 14, 0)


def test_today_with_hour_minute():
    assert parse_when("today 14:30", NOW) == datetime(2026, 9, 9, 14, 30)


def test_today_bare_uses_default_hour_if_ahead():
    assert parse_when("today", datetime(2026, 9, 9, 7, 0)) == datetime(2026, 9, 9, 9, 0)


def test_today_already_passed_raises():
    with pytest.raises(ParseError):
        parse_when("today 9", NOW)


def test_bare_hour_later_today():
    assert parse_when("14", NOW) == datetime(2026, 9, 9, 14, 0)


def test_garbage_raises():
    with pytest.raises(ParseError):
        parse_when("whenever", NOW)


def test_empty_raises():
    with pytest.raises(ParseError):
        parse_when("   ", NOW)


def test_out_of_range_hour_raises_parse_error():
    with pytest.raises(ParseError):
        parse_when("today 25", NOW)


def test_out_of_range_date_raises_parse_error():
    with pytest.raises(ParseError):
        parse_when("2026-02-30", NOW)


def test_out_of_range_weekday_minute_raises_parse_error():
    with pytest.raises(ParseError):
        parse_when("fri 14:75", NOW)


def test_relative_accepts_spelled_out_units_and_no_plus():
    """"+30min", "30m" and "30 minutes" all mean half an hour from now."""
    from datetime import datetime
    from podbay.state import parse_when

    now = datetime(2026, 9, 18, 8, 12)
    for text in ("+30m", "+30min", "30m", "+30 min", "30 minutes", "30mins"):
        assert parse_when(text, now) == datetime(2026, 9, 18, 8, 42), text
    assert parse_when("2 hours", now) == datetime(2026, 9, 18, 10, 12)
    assert parse_when("+1 day", now) == datetime(2026, 9, 19, 8, 12)
    # a bare number is still a clock time, not a duration
    assert parse_when("14", now) == datetime(2026, 9, 18, 14, 0)
