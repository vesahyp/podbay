"""Rebuild the today-so-far rows of chosen days from the log files on S3.

    make live-backfill                        today and yesterday, Helsinki
    make live-backfill DAYS="2026-10-18"

For when the Lambda's row shape changes (the hour sets arrived on
2026-10-19) or a day looks wrong: deletes the DynamoDB row of each day,
runs the Lambda's own process() over every log file that can hold the
day (force=True, so the per-file markers are ignored; only_days, so the
other days' rows are untouched), then publishes data/live.json. The
markers stay, so the S3 event keeps skipping files it has seen. A file
that lands while this runs is counted by the Lambda as usual; the sets
make the overlap harmless.

Needs TABLE, SITE_BUCKET and LOGS_BUCKET in the environment, and AWS
credentials that can read the log bucket and write the table and the
site bucket: the Makefile supplies them from Terraform.
"""

from __future__ import annotations

import argparse
import datetime as dt
import importlib.util
import os
import re
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

import boto3

HANDLER = Path(__file__).resolve().parent.parent / "analytics" / "live" / "handler.py"
STAMP = re.compile(r"\.(\d{4}-\d{2}-\d{2})-(\d{2})\.[0-9a-f]+\.gz$")


def load_handler():
    spec = importlib.util.spec_from_file_location("live_handler", HANDLER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def log_files(bucket: str, since: dt.date) -> list[str]:
    """Keys under cloudfront/ whose UTC stamp is on or after `since`."""
    keys = []
    for page in boto3.client("s3").get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix="cloudfront/"):
        for obj in page.get("Contents", []):
            m = STAMP.search(obj["Key"])
            if m and m.group(1) >= since.isoformat():
                keys.append(obj["Key"])
    return sorted(keys)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("days", nargs="*", help="Helsinki dates; default today and yesterday")
    args = ap.parse_args()
    today = dt.datetime.now(ZoneInfo("Europe/Helsinki")).date()
    days = args.days or [(today - dt.timedelta(days=1)).isoformat(), today.isoformat()]
    mod = load_handler()
    table = mod._table()
    logs = os.environ["LOGS_BUCKET"]
    # A Helsinki day starts at 21:00 or 22:00 UTC the day before; a file's
    # stamp is the UTC hour its requests fall in, so start one day early.
    since = dt.date.fromisoformat(min(days)) - dt.timedelta(days=1)
    keys = log_files(logs, since)
    print(f"{len(keys)} log files since {since}, rebuilding {', '.join(days)}")
    for day in days:
        table.delete_item(Key={"pk": f"{mod.SITE}#{day}"})
    for key in keys:
        print(f"  {key}: {mod.process(logs, key, force=True, only_days=set(days))}")
    out = mod.publish()
    for day in days:
        d = out["days"].get(day)
        print(f"{day}: " + (f"{d['visits']} visits, {d['copies']} copies, {d['github']} github, {sum(h[0] for h in d['hours'])} in hour sets" if d else "not in live.json (not today or yesterday)"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
