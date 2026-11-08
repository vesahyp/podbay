"""The live podbay mobile app on an emulated iPhone 16 (Playwright), as the
test user: the sign-in screen, then each tab, a run's log, the reply
sheet, the tracker's beacons and its error report. Screenshots of every
step go to .shots/mobile-app-*.png (not committed).

    make mobile-test

The test user's tokens come from the test client's admin sign-in and
are put in the app's storage before it loads, so no mail is sent.
--send also adds a virtual passkey and sends one message; it needs a
passkey window open (the Makefile target opens one) and checks that the
message reaches the table as queued and marked test.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import os
import sys
import time
from pathlib import Path

import boto3
from boto3.dynamodb.conditions import Key
from playwright.async_api import async_playwright

REPO = Path(__file__).resolve().parent.parent
OUT = REPO / ".shots"
URL = os.environ["MOBILE_URL"]


def test_tokens() -> dict:
    res = boto3.client("cognito-idp").admin_initiate_auth(
        UserPoolId=os.environ["MOBILE_POOL_ID"], ClientId=os.environ["MOBILE_TEST_CLIENT_ID"],
        AuthFlow="ADMIN_USER_PASSWORD_AUTH",
        AuthParameters={"USERNAME": os.environ["MOBILE_TEST_EMAIL"], "PASSWORD": os.environ["MOBILE_TEST_PASSWORD"]},
    )["AuthenticationResult"]
    return {"access": res["AccessToken"], "refresh": res["RefreshToken"], "exp": int(time.time() * 1000) + res["ExpiresIn"] * 1000,
            "email": os.environ["MOBILE_TEST_EMAIL"], "clientId": os.environ["MOBILE_TEST_CLIENT_ID"]}


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--send", action="store_true", help="add a virtual passkey and send one test message")
    args = ap.parse_args()
    OUT.mkdir(exist_ok=True)
    failures: list[str] = []

    async def shot(page, name: str, full: bool = False) -> None:
        """Where the screen scrolls, grow the viewport to the page so every
        card shows and the fixed tab bar stays at the bottom."""
        if full:
            height = await page.evaluate("document.documentElement.scrollHeight")
            await page.set_viewport_size({"width": 393, "height": max(659, height)})
        await page.screenshot(path=str(OUT / name))
        if full:
            await page.set_viewport_size({"width": 393, "height": 659})

    def check(name: str, ok: bool, detail: object = "") -> None:
        print(f"{'ok  ' if ok else 'FAIL'} {name}" + ("" if ok else f"  {detail}"))
        if not ok:
            failures.append(name)

    async with async_playwright() as p:
        browser = await p.chromium.launch()
        device = dict(p.devices["iPhone 16"])
        beacons: list[str] = []
        errors: list[str] = []

        # 1. Signed out: the sign-in form, with the autofill the house rules ask for.
        ctx = await browser.new_context(**device)
        page = await ctx.new_page()
        page.on("pageerror", lambda e: errors.append(str(e)))
        await page.goto(URL, wait_until="networkidle")
        email = page.locator("input#email")
        check("signed out shows the email form", await email.count() == 1)
        check("the email field invites autofill", await email.get_attribute("autocomplete") == "email" and await email.get_attribute("type") == "email")
        await page.screenshot(path=str(OUT / "mobile-app-0-signin.png"))
        await ctx.close()

        # 2. Signed in as the test user.
        ctx = await browser.new_context(**device)
        auth = test_tokens()
        await ctx.add_init_script(f"localStorage.setItem('podbay.auth', {json.dumps(json.dumps(auth))});")
        page = await ctx.new_page()
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("request", lambda r: beacons.append(r.url) if "/t.gif" in r.url else None)
        await page.goto(f"{URL}/#board", wait_until="networkidle")
        await page.wait_for_selector("main h1", timeout=15000)
        check("board: the decisions section", await page.locator("h2.ask").count() == 1)
        fresh = (await page.locator("#fresh").text_content() or "").strip()
        check("header: the snapshot's age", "ago" in fresh, fresh)
        await shot(page, "mobile-app-1-board.png", full=True)

        await page.locator("button[data-tab=machine]").click()
        await page.wait_for_selector("text=CPU load")
        charts = await page.locator(".chartcard svg polyline").count()
        check("machine: three 24 hour charts", charts == 3, charts)
        check("machine: the account bars have widths", await page.locator(".bar b[style*=width]").count() > 0)
        await shot(page, "mobile-app-2-machine.png", full=True)

        await page.locator("button[data-tab=nightly]").click()
        await page.wait_for_selector(".ng")
        jobs = await page.locator(".ng .j").count()
        cells = await page.locator(".ng .cell").count()
        check("nightly: 7 cells a job", jobs > 0 and cells == jobs * 7, (jobs, cells))
        await shot(page, "mobile-app-3-nightly.png", full=True)
        ran = page.locator(".ng .cell.ok, .ng .cell.fail").first
        if await ran.count():
            await ran.click()
            await page.wait_for_selector(".sheet")
            check("nightly: a run's sheet opens", await page.locator(".sheet .kv").count() == 1)
            await page.screenshot(path=str(OUT / "mobile-app-4-log.png"))
            await page.locator(".sheet button[data-close]").first.click()

        await page.locator("button[data-tab=board]").click()
        reply = page.locator("button[data-reply]").first
        if not await reply.count():
            await page.locator("button[data-new-session]").click()
        else:
            await reply.click()
        await page.wait_for_selector(".sheet textarea")
        check("reply: the sheet opens with a text box", await page.locator(".sheet textarea").count() == 1)
        await page.screenshot(path=str(OUT / "mobile-app-5-reply.png"))
        await page.locator(".sheet button[data-close]").first.click()

        await page.locator("button[data-tab=thread]").click()
        await page.wait_for_selector("text=Everything you send")
        await shot(page, "mobile-app-6-thread.png", full=True)

        # The tracker: page views went out, and an uncaught error is reported.
        await page.evaluate("setTimeout(function () { throw new Error('podbay mobile test error'); }, 0)")
        for _ in range(30):
            if any("e=error" in b for b in beacons):
                break
            await page.wait_for_timeout(200)
        check("tracker: page views leave the page", any("e=pageview" in b or "e=view" in b or "e=tab" in b for b in beacons), beacons[:3])
        check("tracker: an uncaught error is reported", any("e=error" in b for b in beacons), beacons[-3:])

        if args.send:
            client = await ctx.new_cdp_session(page)
            await client.send("WebAuthn.enable")
            await client.send("WebAuthn.addVirtualAuthenticator", {"options": {
                "protocol": "ctap2", "transport": "internal", "hasResidentKey": True,
                "hasUserVerification": True, "isUserVerified": True}})
            await page.goto(f"{URL}/#line/machine", wait_until="networkidle")
            await page.wait_for_selector(".sheet textarea")
            add = page.locator("button[data-add-passkey]")
            if await add.count():
                await add.click()
                await page.wait_for_selector("button[data-send]", timeout=15000)
            text = f"podbay mobile test {int(time.time())}"
            await page.fill(".sheet textarea", text)
            await page.locator("button[data-send]").click()
            await page.wait_for_selector(f"text={text}", timeout=15000)
            await shot(page, "mobile-app-7-sent.png", full=True)
            table = boto3.resource("dynamodb").Table(os.environ["MOBILE_TABLE"])
            items = table.query(KeyConditionExpression=Key("pk").eq("MSG"), ScanIndexForward=False, Limit=20)["Items"]
            sent = next((i for i in items if i.get("text") == text), None)
            check("send: the message is in the table, marked test", bool(sent and sent.get("test") is True), sent)
            check("send: queued with a 2 hour expiry", bool(sent and int(sent["expires"]) - int(sent["created"]) == 7200), sent)
            # The screen marks a test message delivered without typing it.
            row: dict = {}
            for _ in range(45):
                row = table.get_item(Key={"pk": "MSG", "sk": sent["sk"]}).get("Item", {}) if sent else {}
                if row.get("state") == "delivered":
                    break
                await page.wait_for_timeout(1000)
            check("send: the Mac takes it and does not type it", row.get("note") == "test user, not typed", row)
            # The virtual passkey goes with the browser; remove its record.
            sub = json.loads(base64.urlsafe_b64decode(auth["access"].split(".")[1] + "==")).get("sub")
            for item in table.query(KeyConditionExpression=Key("pk").eq("PASSKEY"))["Items"]:
                if item.get("sub") == sub:
                    table.delete_item(Key={"pk": "PASSKEY", "sk": item["sk"]})

        check("no page errors other than the planted one", all("podbay mobile test error" in e for e in errors), errors)
        await browser.close()
    for name in sorted(OUT.glob("mobile-app-*.png")):
        print(f"  {name.relative_to(REPO)}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
