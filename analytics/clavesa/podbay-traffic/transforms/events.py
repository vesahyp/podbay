"""events — CloudFront /t.gif beacon hits from podbay.tienoo.com, one
typed table. keitos' transform with one host: the `site` column stays,
constant 'podbay', so the SQL nodes and the board keep the shared shape.

The beacon query string is DOUBLE URL-encoded. The tracker encodes each
value, then CloudFront encodes the whole field again when it writes the
log, so parse_qs peels one layer and unquote peels the other.

Positional columns of a CloudFront standard log (headerless TSV):
  _c0 date  _c1 time  _c2 x-edge-location  _c7 cs-uri-stem
  _c10 cs(User-Agent)  _c11 cs-uri-query

Deliberately absent: the client IP (_c4). It lives in the raw logs for
their 90 days and it stays there; this table is what outlives them.

Every beacon field rides along in the `q` map rather than being
flattened, so a new event needs no schema change here to be countable.

Bots are flagged, never dropped: human metrics filter on is_bot and the
crawler story stays visible.
"""

from urllib.parse import parse_qs, unquote

from pyspark.sql import DataFrame, functions as F
from pyspark.sql.types import BooleanType, MapType, StringType


def _parse_qs(query):
    """cs-uri-query -> {beacon field: fully decoded value}."""
    out = {}
    if not query or query == "-":
        return out
    for key, vals in parse_qs(query, keep_blank_values=True).items():
        if vals:
            out[key] = unquote(vals[0])
    return out


# Tokens no real browser sends: the automation that does not declare
# itself, which is most of what a small site actually sees.
_HEADLESS = ("headless", "phantomjs", "puppeteer", "playwright", "selenium")


def _is_bot(ua):
    if not ua or ua == "-":
        return True  # a request with no user-agent is not a person
    low = ua.lower()
    if any(token in low for token in _HEADLESS):
        return True
    try:
        from crawlerdetect import CrawlerDetect

        return bool(CrawlerDetect(user_agent=ua).isCrawler())
    except Exception:
        # The library missing must not empty the table.
        return False


def transform(spark, inputs: dict[str, DataFrame]) -> dict[str, DataFrame]:
    parse_udf = F.udf(_parse_qs, MapType(StringType(), StringType()))
    bot_udf = F.udf(_is_bot, BooleanType())

    beacons = (
        inputs["logs"]
        .where(F.col("_c7") == "/t.gif")
        .where(F.col("_c11").isNotNull() & (F.col("_c11") != "") & (F.col("_c11") != "-"))
        .withColumn("site", F.lit("podbay"))
        .withColumn("q", parse_udf(F.col("_c11")))
    )

    events = beacons.select(
        F.col("site"),
        F.col("q")["e"].alias("e"),
        F.col("q")["uid"].alias("uid"),
        F.col("q")["sid"].alias("sid"),
        F.col("q")["p"].alias("path"),
        # The beacon's own millisecond clock; the log's date column is the
        # fallback so a wrong device clock cannot misfile events forever.
        F.expr("timestamp_millis(TRY_CAST(q['t'] AS BIGINT))").alias("event_ts"),
        F.coalesce(
            F.expr("to_date(timestamp_millis(TRY_CAST(q['t'] AS BIGINT)))"),
            F.to_date(F.col("_c0")),
        ).alias("day"),
        F.hour(F.to_timestamp(F.concat_ws(" ", F.col("_c0"), F.col("_c1")))).alias("hour"),
        F.col("_c2").alias("edge"),
        bot_udf(F.col("_c10")).alias("is_bot"),
        F.col("q").alias("q"),
    ).where(F.col("e").isNotNull() & (F.col("e") != ""))

    return {"default": events}
