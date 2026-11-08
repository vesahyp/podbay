"""The phone app (mobile/app/) on an emulated iPhone 16 over made-up data,
with no AWS: Playwright serves the app's files and answers /api/ from
tests/fixtures/mobile/. The messages there are out of insert order and mix
chat messages, Head Jeeves' own lines and replies to board lines.

    make mobile-fixture                       shots + checks, into .shots/
    ... python scripts/mobile_fixture.py --app DIR --prefix before --no-check

--app points at another copy of the app (an old commit, for a before
shot). Checks: the chat is in time order, each line reply shows in the
chat once and its answer only in the line's sheet, the sheet is in time
order.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import mimetypes
import sys
import time
from pathlib import Path

from playwright.async_api import async_playwright

REPO = Path(__file__).resolve().parent.parent
FIX = REPO / "tests" / "fixtures" / "mobile"
OUT = REPO / ".shots"
ORIGIN = "https://app.podbay.test"
TIME_KEYS = ("created", "delivered_at", "replied_at")


def shifted() -> tuple[dict, dict]:
    """The fixture moved so its newest message is 3 minutes old, so the
    day labels read Today and Yesterday on every run."""
    state = json.loads((FIX / "state.json").read_text())
    msgs = json.loads((FIX / "messages.json").read_text())
    newest = max(m[k] for m in msgs["messages"] for k in TIME_KEYS if m.get(k))
    delta = int(time.time()) - 180 - newest
    for m in msgs["messages"]:
        for k in TIME_KEYS:
            if m.get(k):
                m[k] += delta
    state["generated_at"] = int(time.time()) - 20
    return state, msgs


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--app", default=str(REPO / "mobile" / "app"))
    ap.add_argument("--prefix", default="after")
    ap.add_argument("--no-check", action="store_true")
    args = ap.parse_args()
    app_dir = Path(args.app)
    OUT.mkdir(exist_ok=True)
    state, msgs = shifted()
    me = {"email": "owner@example.com", "test": False, "can_send_until": int(time.time()) + 86400, "passkeys": 1, "enrol_open": False}
    failures: list[str] = []
    errors: list[str] = []

    def check(name: str, ok: bool, detail: object = "") -> None:
        print(f"{'ok  ' if ok else 'FAIL'} {name}" + ("" if ok else f"  {detail}"))
        if not ok:
            failures.append(name)

    async def serve(route):
        url = route.request.url[len(ORIGIN):].split("?")[0].split("#")[0]
        if url.startswith("/api/"):
            body = {"/api/state": state, "/api/me": me, "/api/messages": msgs, "/api/stats": {"days": []}}.get(url)
            if body is None:
                return await route.fulfill(status=404, json={"error": "fixture"})
            return await route.fulfill(json=body)
        if url == "/config.js":
            return await route.fulfill(body='window.PODBAY = {"region": "eu-north-1", "clientId": "fixture"};', content_type="text/javascript")
        path = app_dir / (url.lstrip("/") or "index.html")
        if not path.is_file():
            return await route.fulfill(status=404, body="")
        return await route.fulfill(body=path.read_bytes(), content_type=mimetypes.guess_type(path.name)[0] or "application/octet-stream")

    async with async_playwright() as p:
        browser = await p.chromium.launch()
        device = dict(p.devices["iPhone 16"])
        ctx = await browser.new_context(**device, service_workers="block")
        auth = {"access": "fixture", "refresh": "fixture", "exp": int(time.time() * 1000) + 3600_000, "email": "owner@example.com", "clientId": "fixture"}
        await ctx.add_init_script(f"localStorage.setItem('podbay.auth', {json.dumps(json.dumps(auth))});")
        await ctx.route(f"{ORIGIN}/**", serve)
        await ctx.route("https://cognito-idp.**", lambda r: r.fulfill(status=400, json={}))
        page = await ctx.new_page()
        page.on("pageerror", lambda e: errors.append(str(e)))

        async def full(name: str) -> None:
            height = await page.evaluate("document.documentElement.scrollHeight")
            await page.set_viewport_size({"width": 393, "height": max(659, height)})
            await page.screenshot(path=str(OUT / f"mobile-fixture-{args.prefix}-{name}.png"))
            await page.set_viewport_size({"width": 393, "height": 659})

        await page.goto(f"{ORIGIN}/#thread", wait_until="networkidle")
        await page.wait_for_timeout(400)
        await page.screenshot(path=str(OUT / f"mobile-fixture-{args.prefix}-thread.png"))
        bottom = await page.evaluate("window.innerHeight + window.scrollY >= document.documentElement.scrollHeight - 4")
        await full("thread-full")

        if not args.no_check:
            ats = await page.locator("#thread-list [data-at]").evaluate_all("els => els.map(e => Number(e.dataset.at))")
            check("chat: every item is in time order", len(ats) > 0 and ats == sorted(ats), ats)
            line_msgs = [m for m in msgs["messages"] if m.get("line") not in (None, "head-jeeves")]
            entries = await page.locator("#thread-list .lref").count()
            check("chat: one compact entry per line reply", entries == len(line_msgs), (entries, len(line_msgs)))
            answers = [m["reply"] for m in line_msgs if m.get("reply") and not m["line"].startswith("new-session:")]
            listed = await page.locator("#thread-list").inner_text()
            check("chat: a line's answer is not in the chat", not any(a in listed for a in answers))
            says = [m["text"] for m in msgs["messages"] if m.get("from") == "head-jeeves"]
            hj = await page.locator("#thread-list .b.hj").all_inner_texts()
            check("chat: say lines are Head Jeeves messages", all(any(s in h for h in hj) for s in says))
            check("chat: opens at the newest message", bottom)

        # A tap on the compact entry opens the line's sheet with its history.
        entry = page.locator("#thread-list .lref").first
        if await entry.count():
            await entry.click()
            await page.wait_for_selector(".sheet")
            await page.wait_for_timeout(200)
            await page.screenshot(path=str(OUT / f"mobile-fixture-{args.prefix}-sheet-from-chat.png"))
            if not args.no_check:
                ats = await page.locator(".sheet [data-at]").evaluate_all("els => els.map(e => Number(e.dataset.at))")
                check("sheet: the line's replies in time order", len(ats) >= 2 and ats == sorted(ats), ats)
            await page.locator(".sheet button[data-close]").first.click()

        await page.locator("button[data-tab=board]").click()
        await page.wait_for_selector("main h1")
        await full("board")
        if not args.no_check:
            check("board: a line shows its replies", await page.locator("button.thr").count() >= 2)
        reply = page.locator("button.thr").first
        if await reply.count():
            await reply.click()
            await page.wait_for_selector(".sheet")
            await page.wait_for_timeout(200)
            await page.screenshot(path=str(OUT / f"mobile-fixture-{args.prefix}-sheet.png"))
            await page.locator(".sheet button[data-close]").first.click()

        await page.locator("button[data-tab=machine]").click()
        await page.wait_for_timeout(300)
        await full("machine")
        check("no page errors", not errors, errors) if not args.no_check else None
        await browser.close()

    print(f"→ {OUT}/mobile-fixture-{args.prefix}-*.png")
    if failures:
        print(f"{len(failures)} failed")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
