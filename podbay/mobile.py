"""The Mac side of podbay mobile (docs/adr/0001-podbay-mobile.md). Every
call goes out from the Mac; nothing comes in.

- Upload: the podbay screen builds the snapshot (snapshot.py) every
  SNAPSHOT_EVERY seconds and puts it, gzipped, in the state bucket when
  it changed, or when HEARTBEAT seconds have passed, so the app can tell
  a quiet Mac from one that stopped.
- Delivery: every POLL_EVERY seconds the screen reads the messages the
  app queued in the last few hours. One whose `expires` has passed is
  marked expired and never typed. One from the test user is marked
  delivered and never typed. Every other one is typed to Head Jeeves as
  one line, `/head-jeeves phone <id> <target>, on "<quote>": <text>`, and
  marked delivered once the screen confirms the send. The text is only
  ever typed, never run.
- `podbay reply <id> <text>`: Head Jeeves' answer, shown under the
  message in the app.
- `podbay mobile passkey-allow | signout | audit`: open the 15 minute
  window in which the app may add a passkey; end every sign-in and every
  send grant; print the last 7 days of the audit log.

Settings in podbay's config (config.py): `mobile on`, and optionally
mobile-profile, mobile-bucket, mobile-table.
"""

from __future__ import annotations

import gzip
import json
import logging
import time
from datetime import datetime
from typing import Callable

from . import config
from .snapshot import fingerprint

log = logging.getLogger(__name__)

SNAPSHOT_EVERY = 60
HEARTBEAT = 180
POLL_EVERY = 15
LOOK_BACK = 3 * 3600  # longer than the API's 2 hour expiry, so every queued item is seen
ENROL_MINUTES = 15
POOL_NAME = "podbay-mobile"
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


# --- messages ------------------------------------------------------------------


def id_at(seconds: float) -> str:
    """The smallest message id made at `seconds` (the API's new_id)."""
    return f"{int(seconds * 1000):013d}"


def event_line(item: dict) -> str:
    """The one line typed to Head Jeeves for a message."""
    target = " ".join(str(item.get("target") or "?").split())
    quote = " ".join(str(item.get("quote") or "").split()).replace('"', "'")
    text = " ".join(str(item.get("text") or "").split())
    about = f', on "{quote}"' if quote else ""
    return f"/head-jeeves phone {item['sk']} {target}{about}: {text}"


class Store:
    """The message table: the items the poller and `podbay reply` touch."""

    def __init__(self, table=None):
        self._table = table

    @property
    def table(self):
        if self._table is None:
            self._table = session().resource("dynamodb").Table(table_name())
        return self._table

    def recent(self, since: float) -> list[dict]:
        from boto3.dynamodb.conditions import Key

        res = self.table.query(KeyConditionExpression=Key("pk").eq("MSG") & Key("sk").gte(id_at(since)))
        return res.get("Items", [])

    def mark(self, sk: str, expected: str | None, fields: dict) -> bool:
        """Set `fields` on a message, only while its state is `expected`
        (None: only while it exists). False when the condition failed."""
        names = {f"#{k}": k for k in fields}
        values = {f":{k}": v for k, v in fields.items()}
        condition = "attribute_exists(pk)"
        if expected is not None:
            names["#state_now"] = "state"
            values[":expected"] = expected
            condition += " AND #state_now = :expected"
        try:
            self.table.update_item(
                Key={"pk": "MSG", "sk": sk},
                UpdateExpression="SET " + ", ".join(f"#{k} = :{k}" for k in fields),
                ConditionExpression=condition,
                ExpressionAttributeNames=names, ExpressionAttributeValues=values,
            )
            return True
        except self.table.meta.client.exceptions.ConditionalCheckFailedException:
            return False

    def put(self, item: dict) -> None:
        self.table.put_item(Item=item)

    def query(self, pk: str, since: float) -> list[dict]:
        from boto3.dynamodb.conditions import Key

        res = self.table.query(KeyConditionExpression=Key("pk").eq(pk) & Key("sk").gte(id_at(since)))
        return res.get("Items", [])


def poll(store: Store, deliver: Callable[[str], bool], now: float | None = None) -> list[tuple[str, str]]:
    """One delivery pass. `deliver` types one line to Head Jeeves and says
    whether it went. Returns (id, outcome) for every message it touched."""
    now = now or time.time()
    done = []
    for item in store.recent(now - LOOK_BACK):
        if item.get("state") != "queued":
            continue
        sk = item["sk"]
        if int(item.get("expires") or 0) <= now:
            if store.mark(sk, "queued", {"state": "expired", "expired_at": int(now)}):
                done.append((sk, "expired"))
            continue
        if item.get("test"):
            if store.mark(sk, "queued", {"state": "delivered", "delivered_at": int(now), "note": "test user, not typed"}):
                done.append((sk, "test"))
            continue
        # Claim it first, so a second screen or a retry can never type it twice.
        if not store.mark(sk, "queued", {"state": "delivering", "claimed_at": int(now)}):
            continue
        if deliver(event_line(item)):
            store.mark(sk, "delivering", {"state": "delivered", "delivered_at": int(time.time())})
            done.append((sk, "delivered"))
        else:
            store.mark(sk, "delivering", {"state": "queued"})
            done.append((sk, "retry"))
    return done


def reply(store: Store, msg_id: str, text: str) -> bool:
    """Head Jeeves' one-line answer to a message."""
    line = " ".join(text.split())[:1000]
    return store.mark(msg_id, None, {"reply": line, "replied_at": int(time.time()), "state": "answered"})


def passkey_allow(store: Store, minutes: int = ENROL_MINUTES) -> int:
    """Open the window in which the app may register a passkey."""
    until = int(time.time() + minutes * 60)
    store.put({"pk": "ENROL", "sk": "window", "expires": until, "ttl": until + 3600})
    return until


def signout(store: Store) -> tuple[int, int]:
    """End every sign-in in the pool and every send grant. Returns the
    number of users signed out and grants removed."""
    from boto3.dynamodb.conditions import Key

    cognito = session().client("cognito-idp")
    pools = [p for p in cognito.list_user_pools(MaxResults=60)["UserPools"] if p["Name"] == POOL_NAME]
    users = 0
    for pool in pools:
        for user in cognito.list_users(UserPoolId=pool["Id"])["Users"]:
            cognito.admin_user_global_sign_out(UserPoolId=pool["Id"], Username=user["Username"])
            users += 1
    grants = store.table.query(KeyConditionExpression=Key("pk").eq("GRANT"))["Items"]
    for g in grants:
        store.table.delete_item(Key={"pk": "GRANT", "sk": g["sk"]})
    return users, len(grants)


def audit_lines(store: Store, days: int = 7, now: float | None = None) -> list[str]:
    now = now or time.time()
    out = []
    for item in store.query("AUDIT", now - days * 86400):
        at = datetime.fromtimestamp(int(item.get("at") or 0)).strftime("%Y-%m-%d %H:%M:%S")
        text = f' "{item["text"][:80]}"' if item.get("text") else ""
        out.append(f"{at}  {item.get('outcome', '?'):<32} {item.get('ip', ''):<15} {item.get('route', '')}{text}")
    return out
