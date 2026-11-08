"""events — CloudFront hits of app.podbay.tienoo.com, the phone app, one
typed table: the /t.gif beacons, plus the page requests of crawlers that
never run the tracker. A copy of podbay-traffic's transform; the `site`
column is the constant 'podbay-mobile'.

The beacon query string is DOUBLE URL-encoded. The tracker encodes each
value, then CloudFront encodes the whole field again when it writes the
log, so parse_qs peels one layer and unquote peels the other.

Positional columns of a CloudFront standard log (headerless TSV):
  _c0 date  _c1 time  _c2 x-edge-location  _c4 c-ip  _c7 cs-uri-stem
  _c10 cs(User-Agent)  _c11 cs-uri-query

The client IP (_c4) is read here and dropped: it feeds the country lookup
(a bisect over DB-IP's free country ranges, the reference data the
clavesa.dev site builds monthly and keeps in its log bucket) and nothing
of it survives into the table. The raw logs keep it for their 90 days and
that is where it stays.

The user agent gives three things and is then dropped too: whether the
request is a crawler, and which one, so the board can say "Googlebot came
twice"; the OS; and the device class. podbay only runs on macOS, so the
OS is the one qualifying fact about a visitor this site can know.

Crawlers mostly fetch the page and never execute tracker.js, so a beacon
table alone would say "no crawlers". Their requests for the page,
robots.txt and the sitemap come in as the event `crawl`, with no session.

Days and hours are Helsinki time. The person reading the board lives
there, and "who comes at nine in the morning" is a question about their
clock.

Every beacon field rides along in the `q` map rather than being
flattened, so a new event needs no schema change here to be countable.

Bots are flagged, never dropped: human metrics filter on is_bot and the
crawler story stays visible.
"""

import bisect
import ipaddress
import os
from urllib.parse import parse_qs, unquote

from pyspark.sql import DataFrame, functions as F
from pyspark.sql.types import MapType, StringType


def _parse_qs(query):
    """cs-uri-query -> {beacon field: fully decoded value}."""
    out = {}
    if not query or query == "-":
        return out
    for key, vals in parse_qs(query, keep_blank_values=True).items():
        if vals:
            out[key] = unquote(vals[0])
    return out


def _ua(raw):
    """cs(User-Agent) is URL-encoded once by CloudFront; '-' is none."""
    if not raw or raw == "-":
        return ""
    return unquote(raw)


# Tokens no real browser sends: the automation that does not declare
# itself, which is most of what a small site actually sees.
_HEADLESS = ("headless", "phantomjs", "puppeteer", "playwright", "selenium")

# The crawlers worth naming on the board, matched before crawlerdetect so
# the name is stable across library versions.
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


# podbay's own checks (make mobile-test) are not use of the app. Since
# 2026-11-08 they add this token to the user agent. Before that they sent
# Playwright's iPhone 16 string as it was, a pair of iOS 18_7 and Safari
# 27.2 that no real iPhone sends; that exact string is matched too, so the
# day's earlier check runs leave the counts as well.
_CHECK_TOKEN = "podbay-check"
_CHECK_UAS_BEFORE_TOKEN = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 18_7 like Mac OS X) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/27.2 Mobile/15E148 Safari/604.1",
)


def _bot_name(ua):
    """The crawler's name, '' for a person. Doubles as the bot flag."""
    if not ua:
        return "no user agent"
    if _CHECK_TOKEN in ua or ua in _CHECK_UAS_BEFORE_TOKEN:
        return _CHECK_TOKEN
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
    except Exception:
        # The library missing must not empty the table.
        pass
    return ""


def _os(ua):
    """Coarse OS. iPad before Mac: an iPad on the desktop site says Macintosh."""
    if not ua:
        return "unknown"
    if "iPhone" in ua or "iPod" in ua:
        return "iOS"
    if "iPad" in ua:
        return "iPadOS"
    if "Android" in ua:
        return "Android"
    if "Windows" in ua:
        return "Windows"
    if "Macintosh" in ua or "Mac OS X" in ua:
        return "macOS"
    if "CrOS" in ua:
        return "ChromeOS"
    if "Linux" in ua or "X11" in ua:
        return "Linux"
    return "other"


def _device(ua):
    if not ua:
        return "unknown"
    if "iPad" in ua or ("Android" in ua and "Mobile" not in ua) or "Tablet" in ua:
        return "tablet"
    if "Mobile" in ua or "iPhone" in ua or "Android" in ua:
        return "phone"
    return "desktop"


# --- Country: a bisect over DB-IP's free country ranges ---------------------
# Flat "start end CC" files, one per IP family, from
# clavesa-dev/site/analytics/reference-data/. Downloaded once per Python
# worker; anything missing leaves the lookup answering "ZZ" rather than
# failing the night's run.
_REF_BUCKET = os.environ.get("GEO_REF_BUCKET", "clavesa-cloudfront-logs")
_REF_PREFIX = os.environ.get("GEO_REF_PREFIX", "reference-data")
_cc_loaded = False
_cc = {4: ([], [], []), 6: ([], [], [])}


def _download_once(s3, key, dest):
    if not os.path.exists(dest):
        tmp = f"{dest}.{os.getpid()}.tmp"
        s3.download_file(_REF_BUCKET, key, tmp)
        os.replace(tmp, dest)
    return dest


def _load_ranges(path):
    starts, ends, codes = [], [], []
    with open(path) as f:
        for line in f:
            parts = line.split()
            if len(parts) == 3 and not parts[0].startswith("#"):
                starts.append(int(parts[0]))
                ends.append(int(parts[1]))
                codes.append(parts[2])
    return starts, ends, codes


def _ensure_ranges():
    global _cc_loaded, _cc
    if _cc_loaded:
        return
    try:
        import boto3

        s3 = boto3.client("s3")
        v4 = _download_once(s3, f"{_REF_PREFIX}/country_ranges_v4.txt", "/tmp/cc_v4.txt")
        v6 = _download_once(s3, f"{_REF_PREFIX}/country_ranges_v6.txt", "/tmp/cc_v6.txt")
        _cc = {4: _load_ranges(v4), 6: _load_ranges(v6)}
    except Exception:
        _cc = {4: ([], [], []), 6: ([], [], [])}
    _cc_loaded = True


def _country(ip):
    """ISO alpha-2 code, 'ZZ' when unknown. Never raises."""
    if not ip or ip == "-":
        return "ZZ"
    try:
        _ensure_ranges()
        addr = ipaddress.ip_address(ip)
        starts, ends, codes = _cc[addr.version]
        if not starts:
            return "ZZ"
        x = int(addr)
        i = bisect.bisect_right(starts, x) - 1
        if i >= 0 and x <= ends[i]:
            return codes[i] or "ZZ"
        return "ZZ"
    except Exception:
        return "ZZ"


# The page requests that say "a crawler was here". Images, CSS and the
# tracker itself are left out: one visit fetches them all, and the count
# would be a count of assets, not of visits.
_CRAWL_PATHS = ("/", "/index.html", "/robots.txt", "/sitemap.xml")


def transform(spark, inputs: dict[str, DataFrame]) -> dict[str, DataFrame]:
    parse_udf = F.udf(_parse_qs, MapType(StringType(), StringType()))
    ua_udf = F.udf(_ua, StringType())
    bot_udf = F.udf(_bot_name, StringType())
    os_udf = F.udf(_os, StringType())
    device_udf = F.udf(_device, StringType())
    country_udf = F.udf(_country, StringType())

    has_query = F.col("_c11").isNotNull() & (F.col("_c11") != "") & (F.col("_c11") != "-")
    beacon = (F.col("_c7") == "/t.gif") & has_query
    crawl = (F.col("bot_name") != "") & F.col("_c7").isin(*_CRAWL_PATHS)

    hits = (
        inputs["logs"]
        .withColumn("ua", ua_udf(F.col("_c10")))
        .withColumn("bot_name", bot_udf(F.col("ua")))
        .where(beacon | crawl)
        .withColumn("site", F.lit("podbay-mobile"))
        .withColumn("q", parse_udf(F.col("_c11")))
    )

    # The beacon's own millisecond clock; the log's time is the fallback so
    # a wrong device clock cannot misfile events forever.
    ts_utc = F.coalesce(
        F.expr("timestamp_millis(TRY_CAST(q['t'] AS BIGINT))"),
        F.to_timestamp(F.concat_ws(" ", F.col("_c0"), F.col("_c1"))),
    )
    ts_local = F.from_utc_timestamp(ts_utc, "Europe/Helsinki")

    events = hits.select(
        F.col("site"),
        F.when(F.col("_c7") == "/t.gif", F.col("q")["e"]).otherwise(F.lit("crawl")).alias("e"),
        F.col("q")["uid"].alias("uid"),
        F.col("q")["sid"].alias("sid"),
        F.when(F.col("_c7") == "/t.gif", F.col("q")["p"]).otherwise(F.col("_c7")).alias("path"),
        ts_utc.alias("event_ts"),
        F.to_date(ts_local).alias("day"),
        F.hour(ts_local).alias("hour"),
        # 1 = Monday .. 7 = Sunday, so a weekday-by-hour grid sorts itself.
        (((F.dayofweek(ts_local) + 5) % 7) + 1).alias("weekday"),
        F.col("_c2").alias("edge"),
        (F.col("bot_name") != "").alias("is_bot"),
        F.col("bot_name"),
        os_udf(F.col("ua")).alias("os"),
        device_udf(F.col("ua")).alias("device"),
        country_udf(F.col("_c4")).alias("country"),
        F.col("q").alias("q"),
    ).where(F.col("e").isNotNull() & (F.col("e") != ""))

    return {"default": events}
