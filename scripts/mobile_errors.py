"""The phone app's errors of the last 7 days, from the nightly rollup the
podbay-mobile-traffic pipeline publishes (s3://<state bucket>/stats/
analytics.json), grouped by message, kind and page.

    make mobile-errors
"""

from __future__ import annotations

import json
import os
import sys
from datetime import date, timedelta

import boto3


def main() -> int:
    bucket = os.environ.get("MOBILE_STATE_BUCKET", "podbay-mobile-state")
    try:
        raw = boto3.client("s3").get_object(Bucket=bucket, Key="stats/analytics.json")["Body"].read()
    except boto3.client("s3").exceptions.NoSuchKey:
        print("No rollup yet: the pipeline has not run.")
        return 0
    rows = json.loads(raw)
    since = (date.today() - timedelta(days=6)).isoformat()
    grouped: dict[tuple, dict] = {}
    for row in rows:
        if row["date"] < since:
            continue
        for e in row.get("errorList") or []:
            key = (e.get("msg") or "", e.get("type") or "", e.get("path") or "")
            g = grouped.setdefault(key, {"n": 0, "last": row["date"]})
            g["n"] += e.get("n") or 0
            g["last"] = max(g["last"], row["date"])
    last_day = rows[-1]["date"] if rows else "none"
    print(f"Errors since {since} (the rollup's last day: {last_day})")
    if not grouped:
        print("  none")
        return 0
    for (msg, kind, path), g in sorted(grouped.items(), key=lambda kv: -kv[1]["n"]):
        print(f"  {g['n']:>4}  last {g['last']}  {kind:<8} {path:<12} {msg}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
