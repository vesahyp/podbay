"""The site's screenshots: the podbay console and the phone board, from
made-up sessions, never this machine's.

The console is the real TUI mounted headless with every source of live data
replaced by the demo below; Textual exports the screen as SVG. The board is
board.render() over the same sessions. Playwright then renders both to PNG
under site/img/, which are committed.

    make shots
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

# Read at import by voice.py and config.py, so they go first.
os.environ["PODBAY_USER"] = "Frank"
os.environ["PODBAY_HOME_REPO"] = "base"
os.environ["PODBAY_NO_SPLASH"] = "1"

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from playwright.async_api import async_playwright  # noqa: E402

from podbay import app as app_mod  # noqa: E402
from podbay import board, iterm, sources, usage  # noqa: E402
from podbay.accounts import Account  # noqa: E402
from podbay.inventory import inventory_payload  # noqa: E402
from podbay.model import Session  # noqa: E402
from podbay.state import StateStore  # noqa: E402

OUT = REPO / "site" / "img"
NOW = datetime.now().replace(second=0, microsecond=0)
REPOS = Path.home() / "code"


def ago(**kw) -> datetime:
    return NOW - timedelta(**kw)


def demo_sessions() -> list[Session]:
    def s(n: int, repo: str, title: str, *, account="work", state="end_turn", idle=timedelta(minutes=5), **kw) -> Session:
        base = dict(
            session_id=f"{n:08x}-demo-0000-0000-000000000000",
            pid=90000 + n,
            cwd=str(REPOS / repo),
            name=title.lower().replace(" ", "-"),
            name_source="user",
            status="idle",
            status_updated_at=ago(minutes=1) - idle,
            updated_at=NOW - idle,
            started_at=ago(hours=3),
            account=account,
            last_turn=state,
            last_turn_ts=NOW - idle,
            iterm_title=title,
            window_number=n,
            tab_index=1,
            repos_touched=[repo],
            repos_edited=[repo],
            seen_at=ago(hours=4),
        )
        base.update(kw)
        return Session(**base)

    return [
        s(1, "base", "head-jeeves", account="personal", model="Opus 5.5", context_pct=9, name="head-jeeves",
          idle=timedelta(minutes=2), remote_session_id="session_demo", repos_edited=[],
          recap="Reported the checkout tests finishing to the phone."),
        s(5, "checkout", "Checkout flow tests", model="Opus 5.5", context_pct=41, idle=timedelta(minutes=12),
          recap="All 48 checkout tests pass. Ready to merge the payment retry branch?",
          waiting_on={"kind": "question_text", "detail": "Merge the payment retry branch now?"}),
        s(3, "atlas", "Map tile cache", model="Fable 5.1", context_pct=63, status="waiting", idle=timedelta(minutes=4),
          recap="Asked which cache size to use for the tile server.",
          waiting_on={"kind": "ask_user_question", "detail": "Cache size: 512 MB or 2 GB?"}),
        s(2, "atlas", "Tile server metrics", model="Fable 5.1", context_pct=28, state="in_progress",
          idle=timedelta(minutes=1), status="busy", repos_edited=[],
          recap="Adding latency histograms to the tile server."),
        s(4, "ledger", "Invoice export", account="personal", model="Sonnet 5.5", context_pct=74, state="in_progress",
          idle=timedelta(seconds=30), status="busy", recap="Writing the CSV export for invoices."),
        s(6, "docs", "API reference pass", model="Opus 5.5", context_pct=22, idle=timedelta(hours=2),
          parked_until=NOW + timedelta(hours=18), note="after the release",
          recap="First half of the API reference is rewritten."),
        s(7, "ledger", "Ledger migration", account="personal", model="Opus 5.5", context_pct=52,
          idle=timedelta(minutes=38), seen_at=ago(hours=5),
          recap="The migration ran on staging. 3 rows need a manual fix."),
    ]


TRANSCRIPT = [
    {"role": "user", "timestamp": None, "text": "Run the checkout tests again, then tell me if the retry branch is safe to merge."},
    {"role": "tool", "timestamp": None, "text": "⚙ Bash: Run the checkout test suite"},
    {"role": "assistant", "timestamp": None, "text": (
        "All **48** checkout tests pass, including the 6 new ones for payment retry.\n\n"
        "- the retry waits 2 s, then 8 s, then gives up\n"
        "- a declined card is never retried\n\n"
        "Ready to merge the payment retry branch?")},
]

# What Head Jeeves writes to headlines.json for the demo sessions above.
HEADLINES = {
    f"{7:08x}-demo-0000-0000-000000000000": {
        "kind": "shipped", "text": "Ledger migration is live on staging",
        "link": "https://staging.ledger.example.com/accounts",
        "steps": ["Open an account from before 2024", "Check that its balance matches last month's statement"],
    },
    f"{2:08x}-demo-0000-0000-000000000000": {
        "kind": "progress", "text": "Latency charts for the tile server, ready in about an hour",
    },
}

LIMITS = {
    "work": {"five_pct": 36, "five_resets_at": None, "week_pct": 41, "week_resets_at": None},
    "personal": {"five_pct": 12, "five_resets_at": None, "week_pct": 18, "week_resets_at": None},
}


class NoIterm:
    """Stands in for iTerm2: no tabs, no windows, no AppleScript."""

    _windows: dict = {}

    def tabs_for_pids(self, pids):
        return {}

    def windows(self):
        return {}


def patch_sources(tmp: Path) -> None:
    sessions = demo_sessions()
    transcript = tmp / "demo.jsonl"
    transcript.write_text("{}\n")
    sources.gather_sessions = lambda *a, **k: list(sessions)
    sources.read_status_snapshots = lambda *a, **k: {}
    sources.newest_limits = lambda _snaps, label=None: LIMITS.get(label, {})
    sources.transcript_path_for = lambda *a, **k: transcript
    sources.read_conversation = lambda *a, **k: list(TRANSCRIPT)
    usage.read_cache = lambda *a, **k: {"entries": []}
    usage.fetch = lambda *a, **k: None
    iterm.focus_tty = lambda *a, **k: False
    iterm.send_text = lambda *a, **k: False


async def console_svg(tmp: Path) -> str:
    accounts = [Account(label="work", config_dir=tmp / ".claude-work"), Account(label="personal", config_dir=tmp / ".claude-personal")]
    app = app_mod.PodbayApp(
        state_store=StateStore(path=tmp / "state.json"), iterm_lister=NoIterm(), no_splash=True, accounts=accounts
    )
    async with app.run_test(size=(196, 34)) as pilot:
        await app.workers.wait_for_complete()
        await pilot.pause()
        await pilot.press("down")
        await pilot.pause(0.3)
        return app.export_screenshot(title="podbay")


def board_html() -> str:
    payload = inventory_payload(demo_sessions(), set())
    page = board.render(payload, NOW, LIMITS, HEADLINES)
    # The board is published as an artifact, which supplies the document
    # around it; here it is a page of its own.
    return f'<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">{page}'


async def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        patch_sources(tmp)
        svg = await console_svg(tmp)
        (tmp / "console.svg").write_text(svg)
        (tmp / "board.html").write_text(board_html())

        async with async_playwright() as p:
            browser = await p.chromium.launch()

            # As an <img> the SVG box is its viewBox, which ends at the
            # window frame; opened directly it fills the viewport.
            (tmp / "console.html").write_text(
                '<body style="margin:0;background:transparent"><img src="console.svg" style="display:block;width:1600px">'
            )
            page = await browser.new_page(viewport={"width": 1600, "height": 900}, device_scale_factor=2)
            await page.goto((tmp / "console.html").as_uri())
            await page.locator("img").screenshot(path=str(OUT / "console.png"), omit_background=True)

            # Dark, to sit on the dark page. The board itself follows the
            # phone's setting.
            phone = await browser.new_page(
                viewport={"width": 393, "height": 852}, device_scale_factor=3, color_scheme="dark", is_mobile=True
            )
            await phone.goto((tmp / "board.html").as_uri())
            await phone.screenshot(path=str(OUT / "board.png"))
            await browser.close()
    for f in sorted(OUT.glob("*.png")):
        print(f.relative_to(REPO))


if __name__ == "__main__":
    asyncio.run(main())
