"""Taps every line of the board on a phone and checks what reaches Head
Jeeves: each line opens its own composer, and a sent message is addressed
to that line's session and quotes the line. The board is the one the site
screenshot shows (scripts/shots.py), made-up sessions only. The comments
capability is stubbed, so nothing is sent anywhere.

    make board-check
"""

from __future__ import annotations

import asyncio
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import shots  # noqa: E402  (sets the demo environment before podbay loads)
from playwright.async_api import async_playwright  # noqa: E402

STUB = """
window.__sent = [];
window.claude = { use: async () => ({
  anchorFor: async () => ({}),
  sendToClaude: async ({ text }) => { window.__sent.push(text); },
}) };
"""


async def main() -> int:
    failures = []
    with tempfile.TemporaryDirectory() as d:
        page_path = Path(d) / "board.html"
        page_path.write_text(shots.board_html())
        async with async_playwright() as p:
            browser = await p.chromium.launch()
            phone = await browser.new_page(
                viewport={"width": 393, "height": 852}, device_scale_factor=3, is_mobile=True, has_touch=True
            )
            await phone.add_init_script(STUB)
            await phone.goto(page_path.as_uri())
            await phone.click("details.room > summary")
            taps = phone.locator(".tap[data-target]")
            count = await taps.count()
            sends = 0
            for i in range(count):
                tap = taps.nth(i)
                target = await tap.get_attribute("data-target")
                quote = await tap.get_attribute("data-quote")
                section = await tap.evaluate("el => el.closest('section, details').querySelector('h2, summary').textContent")
                await tap.locator(".text, .title").first.tap()
                form = tap.locator("form.say")
                if not await form.is_visible():
                    failures.append(f"{target}: no composer after a tap")
                    continue
                await form.locator("input").fill("does this work")
                await form.locator("input").press("Enter")
                sends += 1
                await phone.wait_for_function("n => window.__sent.length === n", arg=sends)
                sent = await phone.evaluate("window.__sent[window.__sent.length - 1]")
                expected = f'{target}, on "{quote}": does this work' if quote else f"{target}: does this work"
                status = await tap.locator(".status").text_content()
                if sent != expected or status != "Sent to Head Jeeves.":
                    failures.append(f"{target}: sent {sent!r}, status {status!r}")
                print(f"{section.split('·')[0].rstrip('0123456789 ')[:24]:<24} {sent}")
                await tap.locator(".text, .title").first.tap()  # close it again
            await browser.close()
    for f in failures:
        print("FAIL", f, file=sys.stderr)
    print(f"{count} lines tapped, {len(failures)} failed")
    return 1 if failures or count == 0 else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
