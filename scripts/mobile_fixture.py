"""The phone app (mobile/app/) on an emulated iPhone 16 over made-up data,
with no AWS: Playwright serves the app's files and answers /api/ from
tests/fixtures/mobile/. The messages there are out of insert order and mix
chat messages, Head Jeeves' own lines and replies to board lines.

    make mobile-fixture                       shots + checks, into .shots/
    ... python scripts/mobile_fixture.py --app DIR --prefix before --no-check

--app points at another copy of the app (an old commit, for a before
shot). Checks: the chat is in time order, each line reply shows in the
chat once and its answer only in the line's sheet, the sheet is in time
order, and the session that waits on a question is orange on the board
and the Machine tab.
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
# podbay/model.py WAITING_ORANGE, the colour of a session that asks.
sys.path.insert(0, str(REPO))
from podbay.model import WAITING_ORANGE as WAITING_HEX  # noqa: E402

WAITING_RGB = "rgb({}, {}, {})".format(*(int(WAITING_HEX[i:i + 2], 16) for i in (1, 3, 5)))


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


# A board line id as podbay notify --line sends it: a session id, the key
# of headlines.json.
SESSION_ID = "07b643a0-5a8b-4b70-814d-fef34079c12b"


async def push_taps(page, state: dict, check) -> None:
    """A tapped push opens its line's sheet with the answer buttons. sw.js
    saves the tap's target in the "podbay-open" cache; this writes the same
    entry and checks that the page takes it in each way iOS brings the app
    up: started at its root with the hash dropped (app closed), made visible
    again (app in the background), and the worker's message. Headless
    Chromium shows no notifications, so scripts/sw_check.mjs proves the
    worker's half."""
    decision = state["board"]["sections"]["decision"][0]
    old = decision["id"]
    decision["id"] = SESSION_ID
    state["lines"][SESSION_ID] = state["lines"].pop(old)

    async def save(hash_: str, age_ms: int = 0) -> None:
        await page.evaluate("""([hash, age]) => caches.open("podbay-open").then(c => c.put("/__open",
            new Response(JSON.stringify({hash, at: Date.now() - age}), {headers: {"Content-Type": "application/json"}})))""",
                            [hash_, age_ms])

    async def sheet_for_line() -> tuple[bool, list[str]]:
        try:
            await page.wait_for_selector(".sheet .opts", timeout=4000)
        except Exception:  # noqa: BLE001
            return False, []
        opts = await page.locator(".sheet .opts button").all_inner_texts()
        quote = await page.locator(".sheet .quote").inner_text() if await page.locator(".sheet .quote").count() else ""
        return quote.strip() != "" and SESSION_ID in await page.evaluate("location.hash"), opts

    hash_ = f"#line/{SESSION_ID}"
    want = decision["options"]

    # App closed: iOS starts it at its root, without the hash.
    await page.goto(f"{ORIGIN}/#board", wait_until="networkidle")
    await save(hash_)
    await page.goto(f"{ORIGIN}/", wait_until="networkidle")
    ok, opts = await sheet_for_line()
    check("push tap, app closed: opens the line's sheet at the root URL", ok and opts == want, (ok, opts))
    await page.screenshot(path=str(OUT / "mobile-fixture-after-push-tap.png"))
    gone = await page.evaluate('caches.open("podbay-open").then(c => c.match("/__open")).then(r => !r)')
    check("push tap: the saved target is used once", gone)

    # App in the background: it becomes visible again.
    await page.goto(f"{ORIGIN}/#machine", wait_until="networkidle")
    await save(hash_)
    await page.evaluate('document.dispatchEvent(new Event("visibilitychange"))')
    ok, opts = await sheet_for_line()
    check("push tap, app in the background: opens the line's sheet", ok and opts == want, (ok, opts))

    # App open: the worker posts the line.
    await page.goto(f"{ORIGIN}/#thread", wait_until="networkidle")
    await page.evaluate(f'navigator.serviceWorker.dispatchEvent(new MessageEvent("message", {{data: {{type: "open", hash: {json.dumps(hash_)}}}}}))')
    ok, opts = await sheet_for_line()
    check("push tap, app open: the worker's message opens the line's sheet", ok and opts == want, (ok, opts))

    # A target from an old tap does not move the app.
    await page.goto(f"{ORIGIN}/#machine", wait_until="networkidle")
    await save(hash_, age_ms=10 * 60 * 1000)
    await page.goto(f"{ORIGIN}/#machine", wait_until="networkidle")
    await page.wait_for_timeout(500)
    check("push tap: an old saved target is ignored", await page.locator(".sheet").count() == 0
          and await page.evaluate("location.hash") == "#machine")


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
            # The session that waits on a question (tests/fixtures/mobile):
            # orange, in the one --waiting token.
            token = await page.evaluate("getComputedStyle(document.documentElement).getPropertyValue('--waiting').trim()")
            waiting = page.locator(".card.waiting")
            check("board: the session that asks is marked", await waiting.count() == 1)
            if await waiting.count():
                colour = await waiting.first.evaluate("e => getComputedStyle(e).borderLeftColor")
                check("board: drawn in the waiting orange", colour == WAITING_RGB and token.lower() == WAITING_HEX, (colour, token))
        # A long line: folded as it is, a tap shows all of it and its link,
        # a second tap folds it. The before app has no fold: the shots show the cut.
        long_card = page.locator("#line-shipped\\:fixture-import")
        folded_text = await long_card.locator(".line").inner_text()
        await long_card.scroll_into_view_if_needed()
        await long_card.screenshot(path=str(OUT / f"mobile-fixture-{args.prefix}-long-folded.png"))
        await long_card.locator(".line").click()
        await page.wait_for_timeout(200)
        await long_card.screenshot(path=str(OUT / f"mobile-fixture-{args.prefix}-long-unfolded.png"))
        if not args.no_check:
            full_text = state["board"]["sections"]["shipped"][-1]["full"]
            check("board: a cut-off line is folded at first", folded_text.endswith("…") and await long_card.locator("a.link").count() == 1)
            check("board: a tap shows the whole text and the link", await long_card.locator(".line").inner_text() == full_text and await long_card.locator("a.link").is_visible())
            await long_card.locator(".line").click()
            await page.wait_for_timeout(200)
            check("board: a second tap folds it again", await long_card.locator(".line").inner_text() == folded_text)
            short = page.locator("#line-shipped\\:fixture-login .fold")
            check("board: a line that is not cut has no fold", await short.count() == 0)
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
        if not args.no_check:
            rows = page.locator(".srow.waiting")
            check("machine: the asking session's row is orange", await rows.count() == 1)
            if await rows.count():
                colour = await rows.first.locator(".k").evaluate("e => getComputedStyle(e).color")
                check("machine: in the waiting orange", colour == WAITING_RGB, colour)
                check("machine: asking sessions first", "waiting" in (await page.locator(".srow").first.get_attribute("class") or ""))
        if not args.no_check:
            await push_taps(page, state, check)
        check("no page errors", not errors, errors) if not args.no_check else None
        await browser.close()

    print(f"→ {OUT}/mobile-fixture-{args.prefix}-*.png")
    if failures:
        print(f"{len(failures)} failed")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
