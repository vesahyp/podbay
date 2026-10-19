"""Screenshots of the stats board at /stats/, desktop and iPhone, so a
change to its charts is proven on both before it ships.

    make stats-shots                     the live board, into .shots/
    make stats-shots URL=http://localhost:8765 DATA=path/to/analytics.json LIVE=path/to/live.json

DATA replaces /data/analytics.json in the page and LIVE /data/live.json,
so a board can be checked against a local JSON before the pipeline or the
Lambda publishes it. The PNGs are not
committed: they are the proof for one change, sent to whoever asked.
"""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from playwright.async_api import async_playwright

REPO = Path(__file__).resolve().parent.parent
OUT = REPO / ".shots"


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="https://podbay.tienoo.com")
    ap.add_argument("--data", default=None, help="a local analytics.json to serve instead of the site's")
    ap.add_argument("--live", default=None, help="a local live.json to serve instead of the site's")
    args = ap.parse_args()
    OUT.mkdir(exist_ok=True)

    async with async_playwright() as p:
        browser = await p.chromium.launch()
        shots = (
            ("stats-desktop", dict(viewport={"width": 1280, "height": 900}, device_scale_factor=2)),
            ("stats-iphone", p.devices["iPhone 15 Pro"]),
        )
        for name, opts in shots:
            context = await browser.new_context(**opts)
            if args.data:
                body = Path(args.data).read_bytes()
                await context.route("**/data/analytics.json", lambda route: route.fulfill(body=body, content_type="application/json"))
            if args.live:
                live_body = Path(args.live).read_bytes()
                await context.route("**/data/live.json", lambda route: route.fulfill(body=live_body, content_type="application/json"))
            page = await context.new_page()
            errors: list[str] = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            await page.goto(f"{args.url}/stats/", wait_until="networkidle")
            await page.wait_for_selector(".card svg", timeout=15000)
            # Today so far, when live.json is there; the card's heading is the proof.
            live = await page.locator("#live h3").text_content() if await page.locator("#live h3").count() else "(no today card)"
            await page.screenshot(path=str(OUT / f"{name}.png"), full_page=True)
            if errors:
                raise SystemExit(f"{name}: page errors: {errors}")
            cards = await page.locator(".card").count()
            axes = await page.locator(".card svg text.lbl").count()
            print(f"{OUT.relative_to(REPO)}/{name}.png  {cards} cards, {axes} axis labels")
            print(f"  {live.strip()[:120]}")
            await context.close()
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
