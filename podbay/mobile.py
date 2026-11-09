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
- Push: when the app saves a Web Push subscription, the next poll hands
  it as JSON on stdin to `podbay config mobile-push-command`, the command
  that registers a device with whatever sends the pushes. `podbay notify
  --line <id>` then sends a push whose tap opens that line in the app.
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
from .board import TESTED_TEXT
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

    def mark(self, sk: str, expected: str | None, fields: dict, pk: str = "MSG") -> bool:
        """Set `fields` on a message (or another item under `pk`), only
        while its state is `expected` (None: only while it exists). False
        when the condition failed."""
        names = {f"#{k}": k for k in fields}
        values = {f":{k}": v for k, v in fields.items()}
        condition = "attribute_exists(pk)"
        if expected is not None:
            names["#state_now"] = "state"
            values[":expected"] = expected
            condition += " AND #state_now = :expected"
        try:
            self.table.update_item(
                Key={"pk": pk, "sk": sk},
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


THREAD_DAYS = 7


def thread(store: Store, now: float | None = None) -> list[dict]:
    """The phone thread of the last THREAD_DAYS days, newest first, as
    plain values for the board screen (boardview.py): the user's messages
    and Head Jeeves' own lines (`podbay say`). The test user's are left
    out."""
    now = now or time.time()
    keep = ("sk", "from", "line", "target", "quote", "text", "state", "created", "reply", "replied_at", "delivered_at")
    out = []
    for item in store.recent(now - THREAD_DAYS * 86400):
        if item.get("test"):
            continue
        out.append({k: (float(v) if hasattr(v, "as_tuple") else v) for k, v in item.items() if k in keep})
    return sorted(out, key=lambda m: str(m.get("sk")), reverse=True)


# A check run (scripts/mobile_app_check.py) plays a fake Head Jeeves for the
# test user's messages: the screen leaves a test message alone this long, so
# the check can claim it first with poll(..., fake=True).
TEST_GRACE = 10


def poll(store: Store, deliver: Callable[[str], bool], now: float | None = None, fake: bool = False,
         tested: Callable[[str], bool] | None = None) -> list[tuple[str, str]]:
    """One delivery pass. `deliver` types one line to Head Jeeves and says
    whether it went. Returns (id, outcome) for every message it touched.
    The screen marks a test user's message delivered without typing it;
    with `fake`, only the test user's messages are taken, and `deliver` is
    the check's fake Head Jeeves. `tested` takes a board line id and marks
    that shipped line tested (board.mark_tested); a message that only says
    "Tested" is handled by it and not typed, since Head Jeeves sees the
    `tested` field in headlines.json. When it returns False the message is
    typed like any other."""
    now = now or time.time()
    done = []
    for item in store.recent(now - LOOK_BACK):
        if item.get("state") != "queued" or (fake and not item.get("test")):
            continue
        sk = item["sk"]
        if int(item.get("expires") or 0) <= now:
            if store.mark(sk, "queued", {"state": "expired", "expired_at": int(now)}):
                done.append((sk, "expired"))
            continue
        if item.get("test") and not fake:
            if now - int(item.get("created") or 0) < TEST_GRACE:
                continue
            if store.mark(sk, "queued", {"state": "delivered", "delivered_at": int(now), "note": "test user, not typed"}):
                done.append((sk, "test"))
            continue
        if tested and not fake and str(item.get("text") or "").strip() == TESTED_TEXT and tested(str(item.get("line") or "")):
            if store.mark(sk, "queued", {"state": "delivered", "delivered_at": int(now), "note": "marked tested"}):
                done.append((sk, "tested"))
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


def register_pushes(store: Store, run: Callable[[str], bool], now: float | None = None) -> list[tuple[str, str]]:
    """Hand each new push subscription the app saved to `run`, which gets
    the subscription as JSON (podbay config mobile-push-command, for
    example the home repo's push-subscribe --stdin). A test user's
    subscription is never registered."""
    now = now or time.time()
    done = []
    for item in store.query("PUSHSUB", now - 30 * 86400):
        if item.get("state") != "new":
            continue
        if item.get("test"):
            store.mark(item["sk"], "new", {"state": "test"}, pk="PUSHSUB")
            done.append((item["sk"], "test"))
            continue
        if run(json.dumps(item["subscription"])):
            store.mark(item["sk"], "new", {"state": "registered", "registered_at": int(now)}, pk="PUSHSUB")
            done.append((item["sk"], "registered"))
        else:
            store.mark(item["sk"], "new", {"state": "failed"}, pk="PUSHSUB")
            done.append((item["sk"], "failed"))
    return done


def run_push_command(subscription_json: str) -> bool:
    """Run `podbay config mobile-push-command` with the subscription on
    stdin. False when none is set or it fails."""
    import os
    import shlex
    import subprocess

    command = config.mobile_setting("mobile_push_command")
    if not command:
        return False
    try:
        parts = shlex.split(command)
        parts[0] = os.path.expanduser(parts[0])
        res = subprocess.run(parts, input=subscription_json, capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError, ValueError):
        log.warning("mobile push command failed to start", exc_info=True)
        return False
    if res.returncode != 0:
        log.warning("mobile push command exited %s: %s", res.returncode, (res.stderr or res.stdout)[-300:])
    return res.returncode == 0


def reply(store: Store, msg_id: str, text: str) -> bool:
    """Head Jeeves' one-line answer to a message."""
    line = " ".join(text.split())[:1000]
    return store.mark(msg_id, None, {"reply": line, "replied_at": int(time.time()), "state": "answered"})


def say(store: Store, text: str, now: float | None = None) -> str:
    """A line Head Jeeves writes to the app's thread himself, not as an
    answer to a message: what he tells the user about an event, a result
    or a fault. Returns its id."""
    import secrets

    now = now or time.time()
    sk = f"{id_at(now)}{secrets.token_hex(4)}"
    store.put({"pk": "MSG", "sk": sk, "from": "head-jeeves", "line": "head-jeeves", "target": "you",
               "text": " ".join(text.split())[:1000], "state": "said", "created": int(now),
               "ttl": int(now + 90 * 86400)})
    return sk


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
