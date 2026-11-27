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
and the Machine tab, and the tab bar stays at the screen's bottom edge
when scrolled and after a reply sheet with the keyboard up closes (see
stuck_viewport).
"""

from __future__ import annotations

import argparse
import asyncio
import base64
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
    now = int(time.time()) - 20
    for p in (state.get("pushes") or {}).get("items") or []:
        p["at"] += now - state["generated_at"]
    state["generated_at"] = now
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


def fixture_me() -> dict:
    return {"email": "owner@example.com", "test": False, "can_send_until": int(time.time()) + 86400, "passkeys": 1, "enrol_open": False}


async def fixture_context(p, app_dir: Path, state: dict, msgs: dict, me: dict):
    """A signed-in iPhone 16 browser context that serves the app from
    `app_dir` and answers /api/ from the given fixture objects."""
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

    browser = await p.chromium.launch()
    device = dict(p.devices["iPhone 16"])
    ctx = await browser.new_context(**device, service_workers="block")
    auth = {"access": "fixture", "refresh": "fixture", "exp": int(time.time() * 1000) + 3600_000, "email": "owner@example.com", "clientId": "fixture"}
    await ctx.add_init_script(f"localStorage.setItem('podbay.auth', {json.dumps(json.dumps(auth))});")
    await ctx.route(f"{ORIGIN}/**", serve)
    await ctx.route("https://cognito-idp.**", lambda r: r.fulfill(status=400, json={}))
    return browser, ctx


async def costs_checks(page, check) -> None:
    """The Costs screen over the fixture's made-up spend part: the Machine
    tab's card opens it, the account tiles switch, 30 days draws 30 bars,
    a service chip hides its segments, a bar tap shows that day, and the
    stale account says so."""
    await page.goto(f"{ORIGIN}/#machine", wait_until="networkidle")
    await page.evaluate("localStorage.removeItem('podbay.costs')")
    await page.click("[data-costs]")
    await page.wait_for_selector("#costs-chart svg")
    check("costs: the Machine card opens the Costs screen", "#costs" in page.url)
    check("costs: the run date shows", "Tue 3 Nov" in (await page.inner_text("#costs-run")))
    await page.click("[data-crange='30']")
    check("costs: 30 days draws 30 bars", await page.locator("#costs-chart [data-cbar]").count() == 30)
    colour = await page.get_attribute("[data-csvc='CloudFront'] svg rect", "stroke")
    before = await page.locator(f"#costs-chart rect[fill='{colour}']").count()
    await page.click("[data-csvc='CloudFront']")
    after = await page.locator(f"#costs-chart rect[fill='{colour}']").count()
    check("costs: a service chip hides its segments", before > 0 and after == 0, (before, after))
    await page.click("[data-csvc='CloudFront']")
    await page.locator("#costs-chart [data-cbar='25']").dispatch_event("click")
    detail = await page.inner_text("#costs-chart .rdetail")
    check("costs: a bar tap shows the day's services", "All services" in detail and "S3" in detail, detail[:120])
    await page.click("[data-cacct='home']")
    check("costs: a stale account says so", "home is stale" in (await page.inner_text("#main")))
    await page.click("[data-cacct='shop']")


async def releases_checks(page, check) -> None:
    """The Release watch screen over the fixture's made-up releases part:
    the Nightly tab's card opens it, a push link (#releases) opens it
    straight, the pushed item says when, and the per-source bars draw."""
    await page.goto(f"{ORIGIN}/#nightly", wait_until="networkidle")
    await page.click("[data-releases]")
    await page.wait_for_selector(".rwitem")
    check("releases: the Nightly card opens the screen", "#releases" in page.url)
    await page.goto(f"{ORIGIN}/#board", wait_until="networkidle")
    await page.goto(f"{ORIGIN}/#releases", wait_until="networkidle")
    await page.wait_for_selector(".rwitem")
    main_text = (await page.inner_text("#main")).lower()
    check("releases: a push link opens the screen", "could remove a part" in main_text and "another runner" in main_text, main_text[:160])
    pushed = await page.inner_text(".rwitem.pushed")
    check("releases: the pushed item says when", "pushed 21.11 10:20" in pushed, pushed[:160])
    n_items = await page.locator(".rwitem").count()
    check("releases: each title links to its source in a new tab",
          await page.locator(".rwitem h3 a[target=_blank][href^='http']").count() == n_items
          and await page.locator(".rwitem a.rwsrc").count() == n_items, n_items)
    check("releases: one bar per source", await page.locator(".bar b.rwall").count() == 2)
    check("releases: the Nightly tab is on", await page.get_attribute(".tab.on", "data-tab") == "nightly")


async def pushes_checks(page, check) -> None:
    """The Pushes tab over the fixture's made-up pushes: newest first, an
    open alarm says so on the tab and the card, a fixed one says OK now,
    a push that reached no device says so, a Head Jeeves push opens its
    board line, a release-watch push its screen, and an outside link in a
    body is a link."""
    await page.goto(f"{ORIGIN}/#board", wait_until="networkidle")
    await page.click("[data-tab='pushes']")
    await page.wait_for_selector(".pitem")
    check("pushes: the tab opens the list", "#pushes" in page.url)
    titles = await page.locator(".pitem h3").all_inner_texts()
    check("pushes: newest first", titles[:1] == ["fixture-mac: Head Jeeves"] and len(titles) == 6, titles)
    check("pushes: the tab badge counts open alarms", (await page.inner_text("[data-tab='pushes'] .badge")) == "1")
    text = (await page.inner_text("#main")).lower()
    check("pushes: open and fixed alarms", "still in alarm: shop-api-down" in text and "ok now" in text, text[:200])
    check("pushes: a push no device took", "no device took it" in text)
    check("pushes: the outside link is a link", await page.locator(".pitem .pbody a[href='https://example.com/runbook']").count() == 1)
    check("pushes: the per-day chart draws", await page.locator(".chartcard svg rect").count() >= 1)
    await page.click("[data-open-push='releases']")
    await page.wait_for_selector(".rwitem")
    check("pushes: a release-watch push opens its screen", "#releases" in page.url)
    await page.goto(f"{ORIGIN}/#pushes", wait_until="networkidle")
    await page.click("[data-open-push='line/fixture-decision']")
    await page.wait_for_selector(".sheet")
    check("pushes: a Head Jeeves push opens its board line", "#line/fixture-decision" in page.url and await page.get_attribute(".tab.on", "data-tab") == "board")
    await page.goto(f"{ORIGIN}/#pushes", wait_until="networkidle")
    await page.click("[data-open-push='board'] h3")
    await page.wait_for_timeout(300)
    check("pushes: a push linked to the board opens the board", await page.get_attribute(".tab.on", "data-tab") == "board", page.url)
    await page.goto(f"{ORIGIN}/#pushes", wait_until="networkidle")
    check("pushes: an alarm push has nothing to open", await page.locator(".pitem:not(.tap)").count() == 3)


# The home-screen app on an iPhone 16: the whole 852 pt screen, no Safari bars.
SCREEN_H = 852
# The iOS keyboard with its suggestion bar, about this tall.
KEYBOARD_H = 300


async def stuck_viewport(p, app_dir: Path, state: dict, msgs: dict, prefix: str, check) -> None:
    """The tab bar at the bottom edge of the screen at every scroll
    position, and after a reply sheet with the keyboard up is closed.
    iOS 26 in standalone mode can keep the keyboard's shorter layout
    viewport after the keyboard is gone (WebKit bug 297779), and anything at
    bottom: 0 then floats mid-screen with the board drawn under it. Here
    that state is made by shrinking the viewport by the keyboard's height
    and leaving it short after the sheet closes; the shot is taken past the
    viewport's end (captureBeyondViewport), so it shows the whole screen as
    the phone does."""
    browser, ctx = await fixture_context(p, app_dir, state, msgs, fixture_me())
    await ctx.add_init_script("Object.defineProperty(navigator, 'standalone', {get: () => true});")
    page = await ctx.new_page()
    await page.set_viewport_size({"width": 393, "height": SCREEN_H})
    cdp = await ctx.new_cdp_session(page)

    async def screen(name: str) -> float:
        """Shot of the whole screen; returns where the tab bar's bottom
        edge is drawn, in pt from the top of the screen."""
        await page.wait_for_timeout(150)
        # Measured first: the capture resizes the page for a moment.
        y, bottom = await page.evaluate("[scrollY, document.querySelector('#tabs').getBoundingClientRect().bottom]")
        shot = await cdp.send("Page.captureScreenshot", {"captureBeyondViewport": True, "clip": {"x": 0, "y": y, "width": 393, "height": SCREEN_H, "scale": 1}})
        (OUT / f"mobile-fixture-{prefix}-screen-{name}.png").write_bytes(base64.b64decode(shot["data"]))
        await page.wait_for_timeout(100)
        return bottom

    await page.goto(f"{ORIGIN}/#board", wait_until="networkidle")
    await page.wait_for_timeout(300)
    end = await page.evaluate("document.documentElement.scrollHeight - innerHeight")
    for name, y in (("top", 0), ("middle", end // 2), ("end", end)):
        await page.evaluate(f"window.scrollTo(0, {y})")
        bottom = await screen(f"board-{name}")
        check(f"tab bar: at the screen's bottom edge, board scrolled to the {name}", abs(bottom - SCREEN_H) <= 1, bottom)
    await page.evaluate(f"window.scrollTo(0, {end // 2})")
    # A Reply button already on screen, so the board keeps its place.
    await page.evaluate("""() => [...document.querySelectorAll('button[data-reply], button.thr')]
        .find(b => { const r = b.getBoundingClientRect(); return r.top > 80 && r.bottom < innerHeight - 120; }).click()""")
    await page.wait_for_selector(".sheet textarea")
    await page.locator(".sheet textarea").focus()
    # A resize here puts the page back at its top; the keyboard does not.
    y = await page.evaluate("scrollY")
    await page.set_viewport_size({"width": 393, "height": SCREEN_H - KEYBOARD_H})
    await page.evaluate(f"window.scrollTo(0, {y})")
    await screen("sheet-keyboard")
    await page.locator(".scrim").click(position={"x": 200, "y": 40})
    await page.wait_for_timeout(700)
    bottom = await screen("sheet-closed")
    check("tab bar: at the screen's bottom edge after the sheet closes, viewport left short", abs(bottom - SCREEN_H) <= 1, bottom)
    await page.evaluate("window.scrollBy(0, 300)")
    bottom = await screen("sheet-closed-scrolled")
    check("tab bar: still at the bottom edge after scrolling on", abs(bottom - SCREEN_H) <= 1, bottom)
    y = await page.evaluate("scrollY")
    await page.set_viewport_size({"width": 393, "height": SCREEN_H})
    await page.evaluate(f"window.scrollTo(0, {y})")
    bottom = await screen("viewport-back")
    check("tab bar: at the bottom edge once the viewport is whole again", abs(bottom - SCREEN_H) <= 1, bottom)
    await browser.close()


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--app", default=str(REPO / "mobile" / "app"))
    ap.add_argument("--prefix", default="after")
    ap.add_argument("--no-check", action="store_true")
    args = ap.parse_args()
    app_dir = Path(args.app)
    OUT.mkdir(exist_ok=True)
    state, msgs = shifted()
    me = fixture_me()
    failures: list[str] = []
    errors: list[str] = []

    def check(name: str, ok: bool, detail: object = "") -> None:
        print(f"{'ok  ' if ok else 'FAIL'} {name}" + ("" if ok else f"  {detail}"))
        if not ok:
            failures.append(name)

    async with async_playwright() as p:
        browser, ctx = await fixture_context(p, app_dir, state, msgs, me)
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
        if not args.no_check and state.get("spend"):
            await costs_checks(page, check)
            await full("costs")
        if not args.no_check and state.get("releases"):
            await releases_checks(page, check)
            await full("releases")
        if not args.no_check and state.get("pushes"):
            await pushes_checks(page, check)
            await full("pushes")
        check("no page errors", not errors, errors) if not args.no_check else None
        await browser.close()
        await stuck_viewport(p, app_dir, state, msgs, args.prefix, check)

    print(f"→ {OUT}/mobile-fixture-{args.prefix}-*.png")
    if failures:
        print(f"{len(failures)} failed")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
