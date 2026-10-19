"""Today so far: the live counter behind the stats board's first line.

The nightly clavesa pipeline is the record, and it adds yesterday. This
Lambda is the second source the board reads for today: S3 starts it for
every CloudFront log file that lands (legacy standard logging, files
arrive a few minutes after the requests they hold), it parses the file,
keeps the /t.gif beacons of people, and adds the session ids to one
DynamoDB row per Helsinki day. Then it writes data/live.json to the site
bucket, which /stats/ reads.

Every number is a set of session ids, never a counter: visits are the
sessions seen, copies the sessions that copied the install command,
github the sessions that opened GitHub. ADD on a string set is a union,
so a file processed twice changes nothing. On top of that a marker row
per file makes the second run return early; the marker is written after
the day rows, so a run that failed halfway is retried in full, and the
sets make the retry harmless.

The bot filter is the pipeline's: the named crawlers and the headless
tokens in analytics/clavesa/podbay-traffic/transforms/events.py, then
crawlerdetect. tests/test_live.py fails when the two lists drift.

A day row holds every session id of the day, so a day of ten thousand
human visits would pass DynamoDB's item size. That day the nightly run
still has the full answer; this line only says "so far".

Environment: TABLE, SITE_BUCKET, LIVE_KEY (default data/live.json).
"""

from __future__ import annotations

import datetime as dt
import gzip
import json
import os
import io
import sys
from urllib.parse import parse_qs, unquote, unquote_plus

SITE = "podbay"
TZ_NAME = "Europe/Helsinki"
LIVE_KEY = os.environ.get("LIVE_KEY", "data/live.json")
# Rows outlive their use by three months, like the raw logs.
TTL_DAYS = 100

# --- the pipeline's bot filter, verbatim ------------------------------------
_HEADLESS = ("headless", "phantomjs", "puppeteer", "playwright", "selenium")

_NAMED_BOTS = (
    ("googlebot", "Googlebot"),
    ("google-inspectiontool", "Googlebot"),
    ("bingbot", "Bingbot"),
    ("duckduckbot", "DuckDuckBot"),
    ("yandex", "Yandex"),
    ("baiduspider", "Baidu"),
    ("applebot", "Applebot"),
    ("gptbot", "GPTBot"),
    ("oai-searchbot", "GPTBot"),
    ("chatgpt-user", "GPTBot"),
    ("claudebot", "ClaudeBot"),
    ("claude-web", "ClaudeBot"),
    ("anthropic-ai", "ClaudeBot"),
    ("perplexitybot", "PerplexityBot"),
    ("bytespider", "Bytespider"),
    ("ahrefsbot", "Ahrefs"),
    ("semrushbot", "Semrush"),
    ("facebookexternalhit", "Facebook"),
    ("twitterbot", "X"),
    ("linkedinbot", "LinkedIn"),
    ("slackbot", "Slack"),
    ("discordbot", "Discord"),
    ("telegrambot", "Telegram"),
    ("mastodon", "Mastodon"),
)

_warned = False


def bot_name(ua: str) -> str:
    """The crawler's name, '' for a person. Same answer as the pipeline."""
    global _warned
    if not ua:
        return "no user agent"
    low = ua.lower()
    for token, name in _NAMED_BOTS:
        if token in low:
            return name
    if any(token in low for token in _HEADLESS):
        return "headless browser"
    try:
        from crawlerdetect import CrawlerDetect

        detector = CrawlerDetect(user_agent=ua)
        if detector.isCrawler():
            match = detector.getMatches() or "other crawler"
            return str(match)[:40]
    except ImportError:
        if not _warned:
            print("warn: crawlerdetect missing, only the named crawlers are filtered", file=sys.stderr)
            _warned = True
    except Exception:
        pass
    return ""


# --- the log file ------------------------------------------------------------
def parse_query(query: str) -> dict[str, str]:
    """cs-uri-query, double encoded, to {field: value}."""
    out: dict[str, str] = {}
    if not query or query == "-":
        return out
    for key, vals in parse_qs(query, keep_blank_values=True).items():
        if vals:
            out[key] = unquote(vals[0])
    return out


def local_day(q: dict[str, str], date: str, time: str) -> str:
    """The beacon's own clock, the log's as the fallback, as a Helsinki date."""
    from zoneinfo import ZoneInfo

    ts = None
    try:
        ms = int(q.get("t", ""))
        if 10**11 < ms < 10**13:
            ts = dt.datetime.fromtimestamp(ms / 1000, dt.timezone.utc)
    except ValueError:
        pass
    if ts is None:
        ts = dt.datetime.fromisoformat(f"{date}T{time}+00:00")
    return ts.astimezone(ZoneInfo(TZ_NAME)).date().isoformat()


# Positional fallback for a file without a #Fields line.
_DEFAULT_FIELDS = ("date time x-edge-location sc-bytes c-ip cs-method cs(Host) cs-uri-stem "
                   "sc-status cs(Referer) cs(User-Agent) cs-uri-query").split()


def count(lines) -> dict[str, dict[str, set[str]]]:
    """{day: {"visits": sids, "copies": sids, "github": sids}} of the people
    in one log file's lines. Crawls have no session and are not here."""
    fields = list(_DEFAULT_FIELDS)
    days: dict[str, dict[str, set[str]]] = {}
    for raw in lines:
        line = raw.rstrip("\n")
        if not line:
            continue
        if line.startswith("#"):
            if line.startswith("#Fields:"):
                fields = line[len("#Fields:"):].split()
            continue
        col = line.split("\t")
        if len(col) < len(fields):
            continue
        row = dict(zip(fields, col))
        if row.get("cs-uri-stem") != "/t.gif":
            continue
        q = parse_query(row.get("cs-uri-query", ""))
        sid = q.get("sid")
        if not q.get("e") or not sid:
            continue
        ua = row.get("cs(User-Agent)", "")
        if bot_name(unquote(ua) if ua != "-" else ""):
            continue
        day = days.setdefault(local_day(q, row["date"], row["time"]), {"visits": set(), "copies": set(), "github": set()})
        day["visits"].add(sid)
        sel = q.get("sel", "")
        if q["e"] == "copy" or (q["e"] == "click" and sel == "install-copy"):
            day["copies"].add(sid)
        if q["e"] == "click" and "github" in sel:
            day["github"].add(sid)
    return days


# --- AWS ---------------------------------------------------------------------
def _table():
    import boto3

    return boto3.resource("dynamodb").Table(os.environ["TABLE"])


def _ttl() -> int:
    return int(dt.datetime.now(dt.timezone.utc).timestamp()) + TTL_DAYS * 86400


def process(bucket: str, key: str) -> str:
    """One log file into the day rows. Returns what happened, for the log."""
    import boto3

    table = _table()
    marker = {"pk": f"file#{key}"}
    if "pk" in table.get_item(Key=marker, ProjectionExpression="pk").get("Item", {}):
        return "skipped, seen before"
    body = boto3.client("s3").get_object(Bucket=bucket, Key=key)["Body"].read()
    with gzip.open(io.BytesIO(body), "rt", encoding="utf-8", errors="replace") as f:
        days = count(f)
    now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    for day, sets in sorted(days.items()):
        adds = {name: sids for name, sids in sets.items() if sids}
        if not adds:
            continue
        names = {f"#{n}": n for n in adds}
        values = {f":{n}": sids for n, sids in adds.items()}
        table.update_item(
            Key={"pk": f"{SITE}#{day}"},
            UpdateExpression="ADD " + ", ".join(f"#{n} :{n}" for n in adds) + ", files :one SET updated = :now, #ttl = :ttl",
            ExpressionAttributeNames={**names, "#ttl": "ttl"},
            ExpressionAttributeValues={**values, ":one": 1, ":now": now, ":ttl": _ttl()},
        )
    table.put_item(Item={**marker, "done": now, "ttl": _ttl()})
    return "counted " + ", ".join(f"{d}: {len(s['visits'])} visits" for d, s in sorted(days.items())) if days else "no people in it"


def publish() -> dict:
    """data/live.json: today and yesterday, Helsinki time."""
    import boto3
    from zoneinfo import ZoneInfo

    table = _table()
    today = dt.datetime.now(ZoneInfo(TZ_NAME)).date()
    out = {"site": SITE, "updated": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), "days": {}}
    for day in (today - dt.timedelta(days=1), today):
        item = table.get_item(Key={"pk": f"{SITE}#{day.isoformat()}"}).get("Item", {})
        out["days"][day.isoformat()] = {
            "visits": len(item.get("visits", ())),
            "copies": len(item.get("copies", ())),
            "github": len(item.get("github", ())),
            "files": int(item.get("files", 0)),
        }
    boto3.client("s3").put_object(
        Bucket=os.environ["SITE_BUCKET"], Key=LIVE_KEY, Body=json.dumps(out).encode(),
        ContentType="application/json", CacheControl="public, max-age=60",
    )
    return out


def handler(event, context):
    for rec in event.get("Records", []):
        bucket = rec["s3"]["bucket"]["name"]
        key = unquote_plus(rec["s3"]["object"]["key"])
        print(f"{key}: {process(bucket, key)}")
    out = publish()
    print("published " + json.dumps(out["days"]))
    return out
