"""`podbay accounts`: the windows come from the snapshots and the /usage
cache, and the suggestion never goes past 85 percent of any window."""

import json

from podbay import quota
from podbay.accounts import Account

NOW = 1_000_000.0
LATER = NOW + 3600


def _account(tmp_path, label="claude", tier=None):
    directory = tmp_path / (".claude" if label == "claude" else f".claude-{label}")
    directory.mkdir()
    account = Account(label, directory)
    if tier:
        quota.state_file(account).write_text(json.dumps({"oauthAccount": {"organizationRateLimitTier": tier}}))
    return account


def _limits(five, week):
    return {"five_pct": five, "five_resets_at": LATER, "week_pct": week, "week_resets_at": LATER}


def _usage(*models):
    return {"fetched_at": NOW, "entries": [
        {"key": "week_model", "label": label, "pct": pct, "resets_at": LATER} for label, pct in models
    ]}


def test_the_plan_tier_sets_the_quota_multiplier(tmp_path):
    assert quota.plan(_account(tmp_path, "claude", "default_claude_max_5x")) == ("default_claude_max_5x", 5)
    assert quota.plan(_account(tmp_path, "personal", "default_claude_ai")) == ("default_claude_ai", 1)
    assert quota.plan(_account(tmp_path, "other")) == (None, 1)


def test_a_window_past_its_reset_reads_as_empty():
    assert quota.window(90, NOW - 1, NOW)["used_pct"] == 0
    assert quota.window(90, LATER, NOW)["used_pct"] == 90
    assert quota.window(None, LATER, NOW) is None


def test_usage_fills_an_overall_window_the_snapshots_lack(tmp_path):
    usage = {"fetched_at": NOW, "entries": [{"key": "session", "label": None, "pct": 30.0, "resets_at": LATER}]}
    entry = quota.account_entry(_account(tmp_path), _limits(None, 40), usage, NOW)
    assert entry["five_hour"]["used_pct"] == 30
    assert entry["seven_day"]["used_pct"] == 40


def test_heavy_work_gets_the_strongest_model_on_the_account_with_most_room(tmp_path):
    big = quota.account_entry(_account(tmp_path, "claude", "default_claude_max_5x"), _limits(50, 40), _usage(("Fable", 70)), NOW)
    small = quota.account_entry(_account(tmp_path, "personal", "default_claude_ai"), _limits(10, 10), None, NOW)
    data = quota.payload([big, small])
    # 30 percent of a 5x quota is more room than 90 percent of the base plan.
    assert data["suggested"]["heavy"] == {"account": "claude", "model": "fable", "left_pct": 30.0, "room": 150.0}
    assert data["suggested"]["routine"]["model"] == "sonnet"


def test_the_larger_quota_wins_at_the_same_percentage(tmp_path):
    big = quota.account_entry(_account(tmp_path, "claude", "default_claude_max_5x"), _limits(20, 20), None, NOW)
    small = quota.account_entry(_account(tmp_path, "personal", "default_claude_ai"), _limits(20, 20), None, NOW)
    assert quota.payload([small, big])["suggested"]["heavy"]["account"] == "claude"


def test_a_model_at_85_percent_is_never_suggested(tmp_path):
    entry = quota.account_entry(_account(tmp_path), _limits(10, 10), _usage(("Fable", 85)), NOW)
    data = quota.payload([entry])
    assert data["suggested"]["heavy"]["model"] == "opus"
    assert all(o["model"] != "fable" for o in data["options"])


def test_an_account_at_85_percent_of_any_window_is_never_suggested(tmp_path):
    full = quota.account_entry(_account(tmp_path, "claude", "default_claude_max_5x"), _limits(86, 10), None, NOW)
    other = quota.account_entry(_account(tmp_path, "personal"), _limits(60, 60), None, NOW)
    data = quota.payload([full, other])
    assert {o["account"] for o in data["options"]} == {"personal"}
    assert quota.payload([full])["suggested"] == {"heavy": None, "routine": None}
    assert "nothing under 85%" in quota.render(quota.payload([full]))


def test_fable_is_never_suggested_on_an_account_that_does_not_list_it(tmp_path):
    listed = quota.account_entry(_account(tmp_path, "claude", "default_claude_max_5x"), _limits(90, 10), _usage(("Fable", 10)), NOW)
    plain = quota.account_entry(_account(tmp_path, "personal", "default_claude_ai"), _limits(5, 5), None, NOW)
    data = quota.payload([listed, plain])
    # personal has more room, but only claude may run Fable; claude is full at 5h, so opus on personal.
    assert data["suggested"]["heavy"]["account"] == "personal"
    assert data["suggested"]["heavy"]["model"] == "opus"
    assert {o["account"] for o in data["options"] if o["model"] == "fable"} == set()
    assert quota.best_model([plain], "personal") == "opus"


def test_fable_stays_available_where_it_is_listed(tmp_path):
    listed = quota.account_entry(_account(tmp_path, "claude", "default_claude_max_5x"), _limits(10, 10), _usage(("Fable", 10)), NOW)
    plain = quota.account_entry(_account(tmp_path, "personal", "default_claude_ai"), _limits(1, 1), None, NOW)
    heavy = quota.payload([listed, plain])["suggested"]["heavy"]
    assert (heavy["account"], heavy["model"]) == ("claude", "fable")
