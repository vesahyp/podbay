"""The Mac side of podbay mobile (docs/adr/0001-podbay-mobile.md). Every
call goes out from the Mac; nothing comes in.

- Upload: the podbay screen builds the snapshot (snapshot.py) every
  SNAPSHOT_EVERY seconds and puts it, gzipped, in the state bucket when
  it changed, or when HEARTBEAT seconds have passed, so the app can tell
  a quiet Mac from one that stopped.

Settings in podbay's config (config.py): `mobile on`, and optionally
mobile-profile, mobile-bucket, mobile-table.
"""

from __future__ import annotations

import gzip
import json
import logging
import time

from . import config
from .snapshot import fingerprint

log = logging.getLogger(__name__)

SNAPSHOT_EVERY = 60
HEARTBEAT = 180
STATE_KEY = "state/state.json.gz"
DEFAULT_BUCKET = "podbay-mobile-state"
DEFAULT_TABLE = "podbay-mobile"

_session = None


def session():
    """One boto3 session for the configured profile; eu-north-1 unless the
    profile names a region."""
    global _session
    if _session is None:
        import boto3

        profile = config.mobile_setting("mobile_profile") or None
        _session = boto3.Session(profile_name=profile)
        if not _session.region_name:
            _session = boto3.Session(profile_name=profile, region_name="eu-north-1")
    return _session


def bucket() -> str:
    return config.mobile_setting("mobile_bucket", DEFAULT_BUCKET)


def table_name() -> str:
    return config.mobile_setting("mobile_table", DEFAULT_TABLE)


class Uploader:
    """Puts the snapshot when it changed or the heartbeat is due."""

    def __init__(self, put=None, clock=time.time):
        self._put = put or self._s3_put
        self._clock = clock
        self._last_print: str | None = None
        self._last_at = 0.0

    @staticmethod
    def _s3_put(body: bytes) -> None:
        session().client("s3").put_object(Bucket=bucket(), Key=STATE_KEY, Body=body,
                                          ContentType="application/json", ContentEncoding="gzip")

    def tick(self, state: dict) -> bool:
        """True when the snapshot was uploaded."""
        now = self._clock()
        mark = fingerprint(state)
        if mark == self._last_print and now - self._last_at < HEARTBEAT:
            return False
        self._put(gzip.compress(json.dumps(state, default=str).encode(), mtime=0))
        self._last_print = mark
        self._last_at = now
        return True
