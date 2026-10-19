"""Full-page screenshots of the landing page, desktop and iPhone 16, so a
change to site/ is proven on both before it ships.

    make page-shots                       site/ served locally, into .shots/
    make page-shots URL=https://podbay.tienoo.com

Without URL the script serves site/ itself on a free port. The PNGs are
not committed: they are the proof for one change, sent to whoever asked.
"""

from __future__ import annotations

import argparse
import asyncio
import functools
import http.server
import threading
from pathlib import Path

from playwright.async_api import async_playwright

REPO = Path(__file__).resolve().parent.parent
SITE = REPO / "site"
OUT = REPO / ".shots"


class Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args) -> None:
        pass


def serve(directory: Path) -> tuple[http.server.ThreadingHTTPServer, str]:
    handler = functools.partial(Quiet, directory=str(directory))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}"


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default=None, help="the page to shoot; default: site/ served locally")
    args = ap.parse_args()
    OUT.mkdir(exist_ok=True)
    server = None
    url = args.url
    if not url:
        server, url = serve(SITE)

    async with async_playwright() as p:
        browser = await p.chromium.launch()
        shots = (
            ("page-desktop", dict(viewport={"width": 1440, "height": 900}, device_scale_factor=2)),
            ("page-iphone16", p.devices["iPhone 16"]),
        )
        for name, opts in shots:
            context = await browser.new_context(**opts)
            page = await context.new_page()
            errors: list[str] = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            await page.goto(f"{url}/", wait_until="networkidle")
            await page.evaluate("document.fonts.ready")
            # Lazy images below the fold load as the page scrolls; a full-page
            # shot does not scroll, so load them all first.
            await page.evaluate("document.querySelectorAll('img[loading=lazy]').forEach(i => i.loading = 'eager')")
            await page.wait_for_function("Array.from(document.images).every(i => i.complete)")
            await page.screenshot(path=str(OUT / f"{name}.png"), full_page=True)
            if errors:
                raise SystemExit(f"{name}: page errors: {errors}")
            width = await page.evaluate("document.documentElement.scrollWidth")
            inner = await page.evaluate("window.innerWidth")
            sections = await page.locator("[data-track^=section-]").count()
            print(f"{OUT.relative_to(REPO)}/{name}.png  {sections} tracked sections, page width {width} of {inner}")
            if width > inner:
                raise SystemExit(f"{name}: the page scrolls sideways ({width} > {inner})")
            await context.close()
        await browser.close()
    if server:
        server.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
