from podbay import mood


def test_calm_prompts_score_nothing():
    assert mood.score(["please add a test for the parser", "looks good, ship it"]) == 0
    assert not mood.is_hot(["why does the build use node 18?"])  # a real question, not a complaint


def test_one_swear_word_makes_a_session_hot_in_either_language():
    assert mood.is_hot(["how the fuck is this still not working"])
    assert mood.is_hot(["no perkele, taas sama virhe"])


def test_soft_signals_need_company():
    assert not mood.is_hot(["this is still wrong"])  # 0.3
    assert mood.is_hot(["this is STILL wrong?! again!!"])  # shout + two clusters + two retries


def test_code_and_urls_do_not_count():
    assert mood.score(["run `make BUILD_FAST=1` and see https://x.test/BROKEN?!"]) == 0
    assert mood.score(["```\nERROR BROKEN again!!\n```"]) == 0


def test_newer_prompts_weigh_more_and_old_heat_fades():
    heated_then_calm = ["what the fuck", "ok", "thanks", "add a test", "and docs", "nice", "ship it"]
    assert not mood.is_hot(heated_then_calm)  # the swearing fell out of the window
    recent = ["ok", "fine", "what the fuck"]
    assert mood.is_hot(recent)
    older = ["what the fuck", "ok", "fine"]
    assert mood.score(older) == 0.25  # the same word three prompts back


def test_prompts_are_collected_from_the_transcript_tail(tmp_path):
    import json

    from podbay.sources import tail_read_transcript

    records = [
        {"type": "user", "message": {"content": "first prompt"}, "timestamp": "2026-10-03T10:00:00Z"},
        {"type": "user", "isMeta": True, "message": {"content": "meta noise"}, "timestamp": "2026-10-03T10:00:01Z"},
        {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "t1", "content": "out"}]}, "timestamp": "2026-10-03T10:00:02Z"},
        {"type": "user", "message": {"content": "<command-name>/clear</command-name>"}, "timestamp": "2026-10-03T10:00:03Z"},
        {"type": "user", "isSidechain": True, "message": {"content": "subagent prompt"}, "timestamp": "2026-10-03T10:00:04Z"},
        {"type": "user", "message": {"content": [{"type": "text", "text": "second prompt"}]}, "timestamp": "2026-10-03T10:00:05Z"},
    ]
    path = tmp_path / "t.jsonl"
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n")

    assert tail_read_transcript(path)["user_prompts"] == ["first prompt", "second prompt"]
