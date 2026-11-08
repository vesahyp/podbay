"""Screenshots of the podbay mobile vision mock on an iPhone 16, one per
screen, into mobile/mock/shots/ (committed: they are the design that the
build in docs/adr/0001-podbay-mobile.md is checked against).

    make mobile-shots

The mock is one static page with made-up data, opened from disk; the hash
picks the screen. The viewport is the iPhone 16 screen as a Home Screen
web app shows it (393 x 852, no Safari bars), at the device's scale 3.
Where the screen scrolls, the viewport grows to the page height, so
every card shows and the tab bar stays at the bottom.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from playwright.async_api import async_playwright

REPO = Path(__file__).resolve().parent.parent
MOCK = REPO / "mobile" / "mock" / "index.html"
OUT = REPO / "mobile" / "mock" / "shots"

# (file name, hash, full page)
SHOTS = (
    ("1-board", "board", True),
    ("2-decision-reply", "reply", False),
    ("3-machine", "machine", True),
    ("4-nightly", "nightly", True),
    ("5-nightly-log", "log", False),
    ("6-thread", "thread", True),
)


async def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        device = dict(p.devices["iPhone 16"])
        device["viewport"] = {"width": 393, "height": 852}
        context = await browser.new_context(**device)
        page = await context.new_page()
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        for name, screen, full in SHOTS:
            await page.goto(f"{MOCK.as_uri()}#{screen}")
            await page.wait_for_selector(f"body[data-ready='{screen}']")
            path = OUT / f"{name}.png"
            if full:
                # Grow the viewport to the page, so the fixed tab bar sits
                # at the bottom of the shot and not across the middle.
                height = await page.evaluate("document.documentElement.scrollHeight")
                await page.set_viewport_size({"width": 393, "height": max(852, height)})
            await page.screenshot(path=str(path))
            await page.set_viewport_size({"width": 393, "height": 852})
            print(path.relative_to(REPO))
        await browser.close()
        if errors:
            raise SystemExit(f"page errors: {errors}")


if __name__ == "__main__":
    asyncio.run(main())
