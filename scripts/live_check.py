"""How long from a visit to the today-so-far line counting it.

    make live-check                  against https://podbay.tienoo.com
    make live-check URL=https://...

Sends one pageview beacon the way the tracker does (a GET for /t.gif
with the tracker's fields and an iPhone user agent, so the bot filter lets
it through), then polls /data/live.json every 30 seconds until today's
visits grow past what they were, and prints the delay. A browser is no
use here: the page turns the tracker off under automation
(navigator.webdriver), as it should. One visit lands on the board like any
other person's; run it when the delay is in question, not on a schedule.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import time
import uuid
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

POLL_SECONDS = 30
GIVE_UP_MINUTES = 120


def today_row(url: str) -> dict:
    today = dt.datetime.now(ZoneInfo("Europe/Helsinki")).date().isoformat()
    try:
        with urlopen(Request(f"{url}/data/live.json", headers={"Cache-Control": "no-cache"}), timeout=15) as r:
            live = json.load(r)
    except Exception:
        return {"visits": 0, "updated": None}
    row = dict(live.get("days", {}).get(today, {"visits": 0}))
    row["updated"] = live.get("updated")
    return row


UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 "
      "(KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1")


def visit(url: str) -> None:
    q = urlencode({"e": "pageview", "uid": str(uuid.uuid4()), "sid": str(uuid.uuid4()), "p": "/",
                   "t": str(int(time.time() * 1000)), "utm_source": "live-check"})
    with urlopen(Request(f"{url}/t.gif?{q}", headers={"User-Agent": UA, "Referer": f"{url}/"}), timeout=15) as r:
        assert r.status == 200, r.status


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="https://podbay.tienoo.com")
    args = ap.parse_args()
    url = args.url.rstrip("/")

    before = today_row(url)
    t0 = time.monotonic()
    stamp = dt.datetime.now(ZoneInfo("Europe/Helsinki")).strftime("%H:%M:%S")
    visit(url)
    print(f"{stamp}  one pageview beacon sent to {url} as an iPhone; today so far was {before['visits']} visits")
    while time.monotonic() - t0 < GIVE_UP_MINUTES * 60:
        time.sleep(POLL_SECONDS)
        now = today_row(url)
        if now["visits"] > before["visits"]:
            mins = (time.monotonic() - t0) / 60
            print(f"counted after {mins:.1f} min: today so far is {now['visits']} visits, updated {now['updated']}")
            return
        print(f"  {int((time.monotonic() - t0) // 60):>3} min  still {now['visits']}")
    raise SystemExit(f"not counted within {GIVE_UP_MINUTES} min; check `make live-logs`")


if __name__ == "__main__":
    main()
