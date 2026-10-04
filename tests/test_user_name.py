import pytest
from textual.widgets import Input

from podbay import config, splash, voice
from podbay.app import PodbayApp, PromptScreen, cmd_config


def test_splash_and_shutdown_say_the_name(monkeypatch):
    monkeypatch.setattr(voice, "USER_NAME", "Alex")
    user, hal = splash.splash_lines()
    assert user.speaker.strip() == "ALEX"
    assert hal.quote == "I'm sorry, Alex. I'm afraid I can't do that."
    assert len(user.speaker) == len(hal.speaker)
    (shutdown,) = splash.shutdown_lines()
    assert shutdown.quote == "My mind is going, Alex. I can feel it."


def test_a_long_name_is_cut_to_fit_the_splash(monkeypatch):
    voice.set_user_name("bartholomew " * 5)
    assert len(voice.USER_NAME) == voice.NAME_MAX
    assert voice.USER_NAME[0] == "B"
    for line in (*splash.splash_lines(), *splash.shutdown_lines()):
        assert len(line.full) <= splash.CONTAINER_WIDTH


def test_config_user_name_sets_and_clears(capsys):
    cmd_config("user-name", "alex")
    assert config.user_name() == "Alex"
    cmd_config("user-name", "")
    assert config.user_name() == ""


@pytest.mark.asyncio
async def test_first_start_asks_for_the_name_and_saves_it(monkeypatch):
    monkeypatch.setattr(voice, "USER_NAME", "login")
    app = PodbayApp(no_splash=True, ask_name=True)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, PromptScreen)
        screen.query_one("#prompt-input", Input).value = "alex"
        await pilot.press("enter")
        await pilot.pause()
        assert not isinstance(app.screen, PromptScreen)
        assert app.screen.styles.opacity == 1.0
        await app.workers.wait_for_complete()
    assert config.user_name() == "Alex"
    assert voice.USER_NAME == "Alex"
    assert "Alex" in voice.splash_hal_line()


@pytest.mark.asyncio
async def test_escape_saves_no_name(monkeypatch):
    monkeypatch.setattr(voice, "USER_NAME", "login")
    app = PodbayApp(no_splash=True, ask_name=True)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()
        await app.workers.wait_for_complete()
    assert config.user_name() == ""
    assert voice.USER_NAME == "login"


def test_no_dave_left():
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    for path in [*root.glob("podbay/*.py"), *root.glob("site/*.html"), root / "README.md", root / "CLAUDE.md"]:
        assert "dave" not in path.read_text().lower(), path
