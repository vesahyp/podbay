"""The site's screenshots: the podbay console and the phone board over
made-up sessions, and the Head Jeeves chat and one push notification as the
phone shows them, quoting a real exchange (the author okayed naming the
real projects on the site; the console and board stay a demo).

The console is the real TUI mounted headless with every source of live data
replaced by the demo below; Textual exports the screen as SVG. The board is
board.render() over the same sessions. The chat and the push are HTML
mockups of what the phone shows (CHAT, PUSH below), drawn here because no
script can screenshot the Claude app. Playwright renders all of them to PNG
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
        s(8, "recipes", "Recipe search", model="Sonnet 5.5", context_pct=33, idle=timedelta(minutes=25),
          recap="Recipe search answers in under 200 ms now. Committed and pushed."),
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
    # Shipped earlier today by a session that has ended since.
    "ended-demo": {
        "kind": "shipped", "text": "Invoice PDFs render on the phone", "repo": "billing", "at": NOW.isoformat(),
        "link": "https://billing.example.com/invoices",
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

    def empty_windows(self, force=False):
        return []


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


# What the `o` account picker shows: the per-model weekly windows that
# quota.best_model reads, made up like everything else here.
QUOTA = [
    {"account": "work", "quota_multiplier": 5, "five_hour": {"used_pct": 36.0, "left_pct": 64.0},
     "seven_day": {"used_pct": 41.0, "left_pct": 59.0},
     "models": [{"model": "Fable", "alias": "fable", "seven_day": {"used_pct": 52.0, "left_pct": 48.0}}]},
    {"account": "personal", "quota_multiplier": 1, "five_hour": {"used_pct": 12.0, "left_pct": 88.0},
     "seven_day": {"used_pct": 18.0, "left_pct": 82.0}, "models": []},
]


async def open_svg(tmp: Path) -> str:
    """The console with the `o` account picker up, the step after the repo."""
    accounts = [Account(label="work", config_dir=tmp / ".claude-work"), Account(label="personal", config_dir=tmp / ".claude-personal")]
    app = app_mod.PodbayApp(
        state_store=StateStore(path=tmp / "state.json"), iterm_lister=NoIterm(), no_splash=True, accounts=accounts
    )
    async with app.run_test(size=(196, 34)) as pilot:
        await app.workers.wait_for_complete()
        await pilot.pause()
        app.push_screen(app_mod.AccountScreen(accounts, QUOTA, "work", app._live_counts()))
        await pilot.resize_terminal(82, 12)
        await pilot.pause(0.3)
        return app.export_screenshot(title="podbay")


# What the Claude app shows while talking to Head Jeeves: one morning's
# real exchange, condensed. The order on the phone, the reply, the line
# that came back twelve minutes later when the work was ready, and the
# message that asked for this landing page. The times are Helsinki time.
CHAT = [
    ("you", "So its just the top text? Was hoping for a graph here.", "08:14"),
    ("hj", "Yes, it's only a line of text, and that's my mistake: my brief asked for a line when your rule is that "
           "numbers on a board arrive as charts. I've started a new session to make it a chart: visits per hour "
           "since midnight, with yesterday's curve behind it. It goes at the top of https://podbay.tienoo.com/stats/.", "08:15"),
    ("gap", "12 minutes later", ""),
    ("hj", "The podbay stats page now opens with an hourly Today chart with yesterday behind it, "
           "ready for you to test at https://podbay.tienoo.com/stats/.", "08:27"),
    ("you", "Podbay copy doesnt mention claude app for mobile and how to talk to head jeeves? "
            "The tech detail etc is great but real people want to see the how. Like what i'm doing now.", "08:36"),
    ("hj", "Agreed. The page explains how podbay works but never shows using it. The thing that sells it is you on "
           "the phone, sending one line and having work happen on the Mac. Here are ideas to change that.", "08:36"),
]

# The push that came at 08:26: the title is the fixed argument of the
# notify command, the sender the site that registered the phone.
PUSH = ("Head Jeeves", "The podbay stats page now opens with an hourly Today chart with yesterday behind it, "
                       "ready for you to test at https://podbay.tienoo.com/stats/.")


def chat_html() -> str:
    """The Head Jeeves session as the Claude app shows it on a phone."""
    rows = []
    for who, text, when in CHAT:
        if who == "gap":
            rows.append(f'<div class="gap">{text}</div>')
            continue
        text = text.replace("https://", "<u>https://").replace("/stats/.", "/stats/</u>.").replace("/demo", "/demo</u>")
        rows.append(f'<div class="msg {who}"><div class="b">{text}</div><div class="t">{when}</div></div>')
    return f"""<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<style>
  html, body {{ margin: 0; background: #0f0f0f; color: #ececec; font: 16px/1.4 -apple-system, "SF Pro Text", system-ui, sans-serif; }}
  .screen {{ width: 393px; height: 852px; display: flex; flex-direction: column; }}
  .status {{ height: 54px; display: flex; align-items: flex-end; justify-content: space-between; padding: 0 30px 6px; font-weight: 600; font-size: 15px; }}
  .status span:last-child {{ letter-spacing: 1px; }}
  .bar {{ display: flex; align-items: center; gap: 10px; padding: 8px 14px 12px; border-bottom: 1px solid #222; }}
  .bar .back {{ color: #d4a574; font-size: 17px; }}
  .bar .name {{ flex: 1; text-align: center; font-weight: 600; font-size: 16px; display: flex; justify-content: center; align-items: center; gap: 7px; }}
  .bar .name i {{ display: inline-block; width: 16px; height: 11px; border: 1.5px solid #bbb; border-radius: 2px; position: relative; }}
  .bar .name i::after {{ content: ""; position: absolute; right: -4px; bottom: -4px; width: 8px; height: 8px; border-radius: 50%; background: #30d158; border: 2px solid #0f0f0f; }}
  .bar .dots {{ color: #888; letter-spacing: 1px; width: 24px; text-align: right; }}
  .list {{ flex: 1; padding: 16px 14px 0; overflow: hidden; display: flex; flex-direction: column; gap: 14px; }}
  .msg {{ max-width: 86%; }}
  .msg.you {{ align-self: flex-end; }}
  .msg.you .b {{ background: #2b2b2b; border-radius: 18px 18px 4px 18px; padding: 10px 14px; }}
  .msg.hj .b {{ padding: 2px 4px; }}
  .msg .t {{ color: #777; font-size: 11px; margin: 4px 6px 0; }}
  .msg.you .t {{ text-align: right; }}
  .gap {{ align-self: center; color: #777; font-size: 12px; }}
  u {{ color: #d4a574; text-decoration: none; }}
  .input {{ margin: 10px 14px 28px; border: 1px solid #333; border-radius: 22px; padding: 12px 16px; color: #777; display: flex; justify-content: space-between; }}
  .input b {{ width: 26px; height: 26px; border-radius: 50%; background: #d4a574; display: inline-block; margin: -3px -6px -3px 0; }}
</style>
<div class="screen">
  <div class="status"><span>08:37</span><span>●●● ▲ ▮</span></div>
  <div class="bar"><span class="back">‹ Code</span><span class="name"><i></i>head-jeeves</span><span class="dots">•••</span></div>
  <div class="list">{"".join(rows)}</div>
  <div class="input"><span>Message head-jeeves</span><b></b></div>
</div>"""


def push_html() -> str:
    """One push notification as the lock screen shows it: the title is the
    fixed argument of the notify command, the body the line Head Jeeves
    sent, and tapping it opens the board."""
    title, body = PUSH
    return f"""<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<style>
  html, body {{ margin: 0; background: #0c0f18; font: 15px/1.35 -apple-system, "SF Pro Text", system-ui, sans-serif; color: #fff; }}
  .screen {{ width: 393px; height: 236px; background: linear-gradient(160deg, #1c2233 0%, #0c0f18 60%, #1a0f10 100%); }}
  .time {{ text-align: center; padding-top: 12px; font-size: 15px; font-weight: 500; opacity: .9; }}
  .time b {{ display: block; font-size: 40px; font-weight: 300; line-height: 1.1; }}
  .n {{ margin: 10px 10px 0; background: rgba(255,255,255,.14); backdrop-filter: blur(20px); border-radius: 22px; padding: 12px 14px 12px 12px; display: flex; gap: 12px; align-items: flex-start; }}
  .n .ic {{ flex: none; width: 38px; height: 38px; border-radius: 9px; background: radial-gradient(circle, #fff3c4 0 3%, #ffb000 9%, #e0201f 28%, #6a0606 58%, #1a0000 76%); box-shadow: inset 0 0 0 1px rgba(255,255,255,.15); }}
  .n .x {{ flex: 1; min-width: 0; }}
  .n .h {{ display: flex; justify-content: space-between; font-weight: 600; }}
  .n .h span:last-child {{ font-weight: 400; font-size: 13px; opacity: .7; }}
  .n .s {{ font-size: 12px; opacity: .7; margin-bottom: 1px; }}
  .n .b {{ font-size: 14px; }}
</style>
<div class="screen">
<div class="time">Monday 19 October<b>08:26</b></div>
<div class="n"><div class="ic"></div><div class="x"><div class="s">jeeves.tienoo.com</div><div class="h"><span>{title}</span><span>now</span></div><div class="b">{body}</div></div></div>
</div>"""


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
        (tmp / "console.svg").write_text(await console_svg(tmp))
        (tmp / "open.svg").write_text(await open_svg(tmp))
        (tmp / "board.html").write_text(board_html())
        (tmp / "chat.html").write_text(chat_html())
        (tmp / "push.html").write_text(push_html())

        async with async_playwright() as p:
            browser = await p.chromium.launch()

            # As an <img> the SVG box is its viewBox, which ends at the
            # window frame; opened directly it fills the viewport.
            page = await browser.new_page(viewport={"width": 1600, "height": 900}, device_scale_factor=2)
            for name, width in (("console", 1600), ("open", 820)):
                (tmp / f"{name}.html").write_text(
                    f'<body style="margin:0;background:transparent"><img src="{name}.svg" style="display:block;width:{width}px">'
                )
                await page.goto((tmp / f"{name}.html").as_uri())
                await page.locator("img").screenshot(path=str(OUT / f"{name}.png"), omit_background=True)

            # Dark, to sit on the dark page. The board itself follows the
            # phone's setting.
            phone = await browser.new_page(
                viewport={"width": 393, "height": 852}, device_scale_factor=3, color_scheme="dark", is_mobile=True
            )
            await phone.goto((tmp / "board.html").as_uri())
            await phone.screenshot(path=str(OUT / "board.png"))
            await phone.goto((tmp / "chat.html").as_uri())
            await phone.screenshot(path=str(OUT / "chat.png"))
            await phone.goto((tmp / "push.html").as_uri())
            await phone.screenshot(path=str(OUT / "push.png"), clip={"x": 0, "y": 0, "width": 393, "height": 236})
            await browser.close()
    for f in sorted(OUT.glob("*.png")):
        print(f.relative_to(REPO))


if __name__ == "__main__":
    asyncio.run(main())
