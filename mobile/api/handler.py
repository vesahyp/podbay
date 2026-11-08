"""The podbay mobile API (docs/adr/0001-podbay-mobile.md): one Lambda
behind an HTTP API whose JWT authorizer has already checked the Cognito
access token. Every route lives under /api/ on the app's own CloudFront.

    GET  /api/state                     the Mac's last snapshot (gzip)
    GET  /api/stats                     the app's traffic and errors per day
    GET  /api/me                        who, whether this sign-in can send,
                                        passkeys, whether enrolment is open
    GET  /api/messages                  the caller's last 50 messages
    POST /api/messages                  {line, text, key}: queue text for
                                        Head Jeeves
    POST /api/passkey/register/options  only while the Mac opened a window
    POST /api/passkey/register/verify
    POST /api/passkey/auth/options
    POST /api/passkey/auth/verify       a passkey check: a 30 day send
                                        grant for this sign-in
    POST /api/push                      the phone's Web Push subscription

The one action is to queue text. The text is never run here or on the
Mac; podbay types it to Head Jeeves as one /head-jeeves phone event.

Table layout (one table, pk + sk, `ttl` removes old items):
    MSG      <id>            a message: line, target, quote, text, state,
                             created, expires, sub, test, reply
    AUDIT    <id>            every write request, accepted or refused
    RATE     H#<hour> / D#<day>  counters for the rate limit
    IDEM     <key>           the message id an idempotency key made
    PASSKEY  <credential id> public key, sign count, sub
    ENROL    window          open until `expires` (podbay mobile passkey-allow)
    GRANT    <origin_jti>    this sign-in may send until `expires`
    CHAL     <origin_jti>#<reg|auth>  the WebAuthn challenge, 5 minutes
    PUSHSUB  <id>            a push subscription, state new until the Mac
                             registered it
"""

from __future__ import annotations

import base64
import gzip
import hashlib
import json
import os
import secrets
import time
import unicodedata
from decimal import Decimal

import boto3
from boto3.dynamodb.conditions import Key

TABLE = os.environ.get("TABLE", "podbay-mobile")
STATE_BUCKET = os.environ.get("STATE_BUCKET", "podbay-mobile-state")
STATE_KEY = os.environ.get("STATE_KEY", "state/state.json.gz")
RP_ID = os.environ.get("RP_ID", "app.podbay.tienoo.com")
ORIGIN = os.environ.get("ORIGIN", f"https://{RP_ID}")

MAX_TEXT = 1000
EXPIRES_AFTER = 2 * 3600
GRANT_DAYS = 30
PER_HOUR = 30
PER_DAY = 200
MESSAGE_TTL = 90 * 86400
AUDIT_TTL = 365 * 86400
CHALLENGE_TTL = 300
BODY_KEYS = {"line", "text", "key"}

_ddb = None
_s3 = None
_state_cache: dict = {"at": 0.0, "state": None}


def table():
    global _ddb
    if _ddb is None:
        _ddb = boto3.resource("dynamodb").Table(TABLE)
    return _ddb


def s3():
    global _s3
    if _s3 is None:
        _s3 = boto3.client("s3")
    return _s3


# --- pure helpers (tests/test_mobile_api.py) ---------------------------------


def new_id(now: float | None = None) -> str:
    """Sorts by time as a string: milliseconds, then 8 random hex digits."""
    return f"{int((now or time.time()) * 1000):013d}{secrets.token_hex(4)}"


def id_at(seconds: float) -> str:
    """The smallest id made at `seconds`, for a range query."""
    return f"{int(seconds * 1000):013d}"


def clean_text(raw: object) -> str | None:
    """One line of at most MAX_TEXT characters: control and format
    characters removed, every run of whitespace one space. None when
    nothing is left or the input is not a string."""
    if not isinstance(raw, str):
        return None
    out = []
    for ch in raw:
        if ch in "\r\n\t":
            out.append(" ")
        elif unicodedata.category(ch) in ("Cc", "Cf", "Cs", "Co", "Cn"):
            continue
        else:
            out.append(ch)
    text = " ".join("".join(out).split())
    if not text or len(text) > MAX_TEXT:
        return None
    return text


def resolve_line(state: dict | None, line: object) -> dict | None:
    """The target and quote for a line id, from the Mac's snapshot only.
    `new-session:<repo>` is valid for a repo the snapshot lists."""
    if not isinstance(line, str) or not state or len(line) > 200:
        return None
    if line.startswith("new-session:"):
        repo = line.split(":", 1)[1]
        if repo in (state.get("repos") or []):
            return {"target": f"new session in {repo}", "quote": ""}
        return None
    found = (state.get("lines") or {}).get(line)
    if isinstance(found, dict) and found.get("target"):
        return {"target": str(found["target"]), "quote": str(found.get("quote") or "")}
    return None


def is_test(claims: dict) -> bool:
    return "test" in str(claims.get("cognito:groups") or "")


# --- responses ---------------------------------------------------------------


def respond(status: int, body: object) -> dict:
    return {"statusCode": status, "headers": {"content-type": "application/json", "cache-control": "no-store"},
            "body": json.dumps(body, default=_jsonable)}


def _jsonable(value):
    if isinstance(value, Decimal):  # every DynamoDB number
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, set):
        return sorted(value)
    raise TypeError(type(value))


# --- state -------------------------------------------------------------------


def load_state(max_age: float = 20) -> dict | None:
    """The snapshot, kept in memory for `max_age` seconds per Lambda."""
    if time.time() - _state_cache["at"] < max_age and _state_cache["state"] is not None:
        return _state_cache["state"]
    try:
        raw = s3().get_object(Bucket=STATE_BUCKET, Key=STATE_KEY)["Body"].read()
    except s3().exceptions.NoSuchKey:
        return None
    _state_cache.update(at=time.time(), state=json.loads(gzip.decompress(raw)), raw=raw)
    return _state_cache["state"]


STATS_KEY = os.environ.get("STATS_KEY", "stats/analytics.json")


def get_stats() -> dict:
    """The app's own traffic and errors, one row per day, as the nightly
    podbay-mobile-traffic pipeline publishes it."""
    try:
        raw = s3().get_object(Bucket=STATE_BUCKET, Key=STATS_KEY)["Body"].read()
    except s3().exceptions.NoSuchKey:
        return respond(200, {"days": []})
    return {"statusCode": 200, "headers": {"content-type": "application/json", "cache-control": "no-store"},
            "body": '{"days": ' + raw.decode() + "}"}


def get_state() -> dict:
    if load_state(max_age=5) is None:
        return respond(404, {"error": "no snapshot from the Mac yet"})
    return {"statusCode": 200, "isBase64Encoded": True,
            "headers": {"content-type": "application/json", "content-encoding": "gzip", "cache-control": "no-store"},
            "body": base64.b64encode(_state_cache["raw"]).decode()}


# --- grants and audit ----------------------------------------------------------


def grant_until(origin_jti: str | None, now: float) -> int | None:
    if not origin_jti:
        return None
    item = table().get_item(Key={"pk": "GRANT", "sk": origin_jti}).get("Item")
    if item and int(item["expires"]) > now:
        return int(item["expires"])
    return None


def audit(event: dict, claims: dict, outcome: str, text: str | None = None, extra: dict | None = None) -> None:
    now = time.time()
    http = (event.get("requestContext") or {}).get("http") or {}
    item = {"pk": "AUDIT", "sk": new_id(now), "at": int(now), "ttl": int(now + AUDIT_TTL),
            "sub": claims.get("sub") or "", "origin_jti": claims.get("origin_jti") or "",
            "ip": http.get("sourceIp") or "", "ua": (http.get("userAgent") or "")[:300],
            "route": event.get("routeKey") or "", "outcome": outcome}
    if text is not None:
        item["text"] = text
        item["sha256"] = hashlib.sha256(text.encode()).hexdigest()
    if extra:
        item.update(extra)
    table().put_item(Item=item)


def _count(sk: str, limit: int, ttl: int) -> bool:
    """Add one to a counter unless it is at `limit`. False when it is."""
    try:
        table().update_item(
            Key={"pk": "RATE", "sk": sk},
            UpdateExpression="ADD n :one SET #t = if_not_exists(#t, :ttl)",
            ConditionExpression="attribute_not_exists(n) OR n < :limit",
            ExpressionAttributeNames={"#t": "ttl"},
            ExpressionAttributeValues={":one": 1, ":limit": limit, ":ttl": ttl},
        )
        return True
    except table().meta.client.exceptions.ConditionalCheckFailedException:
        return False


def within_rate(now: float) -> bool:
    hour = time.strftime("%Y%m%d%H", time.gmtime(now))
    day = hour[:8]
    return _count(f"H#{hour}", PER_HOUR, int(now + 7200)) and _count(f"D#{day}", PER_DAY, int(now + 2 * 86400))


# --- messages ----------------------------------------------------------------


def _message_view(item: dict) -> dict:
    keys = ("line", "target", "quote", "text", "state", "created", "expires", "delivered_at", "reply", "replied_at", "test")
    return {"id": item["sk"], **{k: item.get(k) for k in keys if k in item}}


def list_messages(claims: dict) -> dict:
    items = table().query(KeyConditionExpression=Key("pk").eq("MSG"), ScanIndexForward=False, Limit=200)["Items"]
    mine = [_message_view(i) for i in items if i.get("sub") == claims.get("sub")][:50]
    return respond(200, {"messages": mine})


def post_message(event: dict, claims: dict, body: dict) -> dict:
    now = time.time()
    if set(body) - BODY_KEYS:
        audit(event, claims, "refused: unknown fields")
        return respond(400, {"error": "unknown fields"})
    text = clean_text(body.get("text"))
    if text is None:
        audit(event, claims, "refused: text")
        return respond(400, {"error": f"the text must be 1 to {MAX_TEXT} characters"})
    key = body.get("key")
    if not isinstance(key, str) or not 8 <= len(key) <= 64:
        audit(event, claims, "refused: key", text)
        return respond(400, {"error": "missing idempotency key"})
    if grant_until(claims.get("origin_jti"), now) is None:
        audit(event, claims, "refused: no passkey", text)
        return respond(403, {"error": "passkey", "message": "Confirm with your passkey to send."})
    resolved = resolve_line(load_state(), body.get("line"))
    if resolved is None:
        audit(event, claims, "refused: line", text, {"line": str(body.get("line"))[:200]})
        return respond(400, {"error": "that line is no longer on the board"})
    # Idempotency first, so a retry of an accepted message is not counted again.
    idem = table().get_item(Key={"pk": "IDEM", "sk": key}).get("Item")
    if idem:
        msg = table().get_item(Key={"pk": "MSG", "sk": idem["msg"]}).get("Item")
        return respond(200, {"message": _message_view(msg) if msg else {"id": idem["msg"]}})
    if not within_rate(now):
        audit(event, claims, "refused: rate", text)
        return respond(429, {"error": f"at most {PER_HOUR} messages an hour and {PER_DAY} a day"})
    msg_id = new_id(now)
    try:
        table().put_item(Item={"pk": "IDEM", "sk": key, "msg": msg_id, "ttl": int(now + 86400)},
                         ConditionExpression="attribute_not_exists(pk)")
    except table().meta.client.exceptions.ConditionalCheckFailedException:
        return respond(409, {"error": "sent twice at once; reload"})
    item = {"pk": "MSG", "sk": msg_id, "line": body["line"], "target": resolved["target"], "quote": resolved["quote"],
            "text": text, "state": "queued", "created": int(now), "expires": int(now + EXPIRES_AFTER),
            "sub": claims.get("sub") or "", "test": is_test(claims), "ttl": int(now + MESSAGE_TTL)}
    table().put_item(Item=item)
    audit(event, claims, "queued", text, {"msg": msg_id, "line": body["line"]})
    return respond(201, {"message": _message_view(item)})


# --- passkeys ----------------------------------------------------------------


def _webauthn():
    import webauthn
    from webauthn.helpers import structs
    return webauthn, structs


def _b64u(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64u(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _passkeys(sub: str) -> list[dict]:
    items = table().query(KeyConditionExpression=Key("pk").eq("PASSKEY"))["Items"]
    return [i for i in items if i.get("sub") == sub]


def _enrol_open(now: float) -> bool:
    item = table().get_item(Key={"pk": "ENROL", "sk": "window"}).get("Item")
    return bool(item and int(item["expires"]) > now)


def _save_challenge(claims: dict, kind: str, challenge: bytes, now: float) -> None:
    table().put_item(Item={"pk": "CHAL", "sk": f"{claims.get('origin_jti')}#{kind}", "challenge": _b64u(challenge),
                           "ttl": int(now + CHALLENGE_TTL), "expires": int(now + CHALLENGE_TTL)})


def _take_challenge(claims: dict, kind: str, now: float) -> bytes | None:
    key = {"pk": "CHAL", "sk": f"{claims.get('origin_jti')}#{kind}"}
    item = table().get_item(Key=key).get("Item")
    if not item:
        return None
    table().delete_item(Key=key)
    return _unb64u(item["challenge"]) if int(item["expires"]) > now else None


def _grant(claims: dict, now: float) -> int:
    until = int(now + GRANT_DAYS * 86400)
    table().put_item(Item={"pk": "GRANT", "sk": claims["origin_jti"], "sub": claims.get("sub") or "",
                           "expires": until, "ttl": until})
    return until


def register_options(event: dict, claims: dict) -> dict:
    now = time.time()
    if not _enrol_open(now):
        audit(event, claims, "refused: passkey enrolment closed")
        return respond(403, {"error": "enrol", "message": "Open a passkey window on the Mac first: podbay mobile passkey-allow"})
    webauthn, structs = _webauthn()
    opts = webauthn.generate_registration_options(
        rp_id=RP_ID, rp_name="podbay", user_id=(claims.get("sub") or "").encode(),
        user_name=claims.get("username") or claims.get("sub") or "user",
        authenticator_selection=structs.AuthenticatorSelectionCriteria(
            resident_key=structs.ResidentKeyRequirement.PREFERRED,
            user_verification=structs.UserVerificationRequirement.REQUIRED),
        exclude_credentials=[structs.PublicKeyCredentialDescriptor(id=_unb64u(p["sk"])) for p in _passkeys(claims.get("sub"))],
    )
    _save_challenge(claims, "reg", opts.challenge, now)
    return respond(200, json.loads(webauthn.options_to_json(opts)))


def register_verify(event: dict, claims: dict, body: dict) -> dict:
    now = time.time()
    if not _enrol_open(now):
        audit(event, claims, "refused: passkey enrolment closed")
        return respond(403, {"error": "enrol"})
    challenge = _take_challenge(claims, "reg", now)
    if challenge is None:
        return respond(400, {"error": "the passkey request timed out; try again"})
    webauthn, _ = _webauthn()
    try:
        verified = webauthn.verify_registration_response(
            credential=body.get("credential"), expected_challenge=challenge, expected_rp_id=RP_ID,
            expected_origin=ORIGIN, require_user_verification=True)
    except Exception as exc:  # noqa: BLE001 -- any verification failure is a refusal
        audit(event, claims, f"refused: passkey registration ({exc.__class__.__name__})")
        return respond(400, {"error": "the passkey did not verify"})
    table().put_item(Item={"pk": "PASSKEY", "sk": _b64u(verified.credential_id), "sub": claims.get("sub") or "",
                           "public_key": _b64u(verified.credential_public_key), "sign_count": verified.sign_count,
                           "created": int(now), "device": str(body.get("device") or "")[:80]})
    table().delete_item(Key={"pk": "ENROL", "sk": "window"})
    until = _grant(claims, now)
    audit(event, claims, "passkey added", extra={"credential": _b64u(verified.credential_id)})
    return respond(200, {"ok": True, "can_send_until": until})


def auth_options(event: dict, claims: dict) -> dict:
    now = time.time()
    keys = _passkeys(claims.get("sub"))
    if not keys:
        return respond(404, {"error": "no passkey yet"})
    webauthn, structs = _webauthn()
    opts = webauthn.generate_authentication_options(
        rp_id=RP_ID, user_verification=structs.UserVerificationRequirement.REQUIRED,
        allow_credentials=[structs.PublicKeyCredentialDescriptor(id=_unb64u(p["sk"])) for p in keys])
    _save_challenge(claims, "auth", opts.challenge, now)
    return respond(200, json.loads(webauthn.options_to_json(opts)))


def auth_verify(event: dict, claims: dict, body: dict) -> dict:
    now = time.time()
    challenge = _take_challenge(claims, "auth", now)
    if challenge is None:
        return respond(400, {"error": "the passkey request timed out; try again"})
    credential = body.get("credential") or {}
    raw_id = credential.get("rawId") or credential.get("id") or ""
    stored = table().get_item(Key={"pk": "PASSKEY", "sk": raw_id}).get("Item") if raw_id else None
    if not stored or stored.get("sub") != claims.get("sub"):
        audit(event, claims, "refused: unknown passkey")
        return respond(400, {"error": "unknown passkey"})
    webauthn, _ = _webauthn()
    try:
        verified = webauthn.verify_authentication_response(
            credential=credential, expected_challenge=challenge, expected_rp_id=RP_ID, expected_origin=ORIGIN,
            credential_public_key=_unb64u(stored["public_key"]), credential_current_sign_count=int(stored.get("sign_count") or 0),
            require_user_verification=True)
    except Exception as exc:  # noqa: BLE001
        audit(event, claims, f"refused: passkey check ({exc.__class__.__name__})")
        return respond(400, {"error": "the passkey did not verify"})
    table().update_item(Key={"pk": "PASSKEY", "sk": raw_id}, UpdateExpression="SET sign_count = :n, used = :t",
                        ExpressionAttributeValues={":n": verified.new_sign_count, ":t": int(now)})
    until = _grant(claims, now)
    audit(event, claims, "passkey check")
    return respond(200, {"ok": True, "can_send_until": until})


def save_push(event: dict, claims: dict, body: dict) -> dict:
    """The phone's Web Push subscription. The podbay screen hands it to the
    configured register command (mobile.py), so notify reaches the app."""
    sub = body.get("subscription") or {}
    endpoint = str(sub.get("endpoint") or "")
    keys = sub.get("keys") or {}
    if not endpoint.startswith("https://") or len(endpoint) > 1000 or not keys.get("p256dh") or not keys.get("auth"):
        return respond(400, {"error": "not a push subscription"})
    now = time.time()
    table().put_item(Item={"pk": "PUSHSUB", "sk": new_id(now), "sub": claims.get("sub") or "", "test": is_test(claims),
                           "subscription": {"endpoint": endpoint, "keys": {"p256dh": str(keys["p256dh"])[:200], "auth": str(keys["auth"])[:100]}},
                           "state": "new", "ttl": int(now + 30 * 86400)})
    audit(event, claims, "push subscription saved")
    return respond(201, {"ok": True})


def me(claims: dict) -> dict:
    now = time.time()
    return respond(200, {
        "email": claims.get("username") or "",
        "test": is_test(claims),
        "can_send_until": grant_until(claims.get("origin_jti"), now),
        "passkeys": len(_passkeys(claims.get("sub"))),
        "enrol_open": _enrol_open(now),
    })


# --- entry -------------------------------------------------------------------


def handler(event: dict, context=None) -> dict:
    claims = (((event.get("requestContext") or {}).get("authorizer") or {}).get("jwt") or {}).get("claims") or {}
    if not claims.get("sub") or claims.get("token_use") != "access":
        return respond(401, {"error": "sign in"})
    route = event.get("routeKey") or ""
    body: dict = {}
    if route.startswith("POST "):
        raw = event.get("body") or ""
        if event.get("isBase64Encoded"):
            raw = base64.b64decode(raw).decode()
        if len(raw) > 20000:
            return respond(413, {"error": "too large"})
        try:
            body = json.loads(raw or "{}")
        except ValueError:
            return respond(400, {"error": "not JSON"})
        if not isinstance(body, dict):
            return respond(400, {"error": "not an object"})
    if route == "GET /api/state":
        return get_state()
    if route == "GET /api/stats":
        return get_stats()
    if route == "GET /api/me":
        return me(claims)
    if route == "GET /api/messages":
        return list_messages(claims)
    if route == "POST /api/messages":
        return post_message(event, claims, body)
    if route == "POST /api/passkey/register/options":
        return register_options(event, claims)
    if route == "POST /api/passkey/register/verify":
        return register_verify(event, claims, body)
    if route == "POST /api/passkey/auth/options":
        return auth_options(event, claims)
    if route == "POST /api/passkey/auth/verify":
        return auth_verify(event, claims, body)
    if route == "POST /api/push":
        return save_push(event, claims, body)
    return respond(404, {"error": "no such route"})
