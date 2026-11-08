"""The podbay mobile API through the app's CloudFront, as the test user:
no token is refused, a token reads /api/me, /api/state and /api/messages,
and a send without a passkey check is refused.

    make mobile-check

The Makefile passes the Terraform outputs in the environment. The test
user signs in with the test client's admin password flow, which needs
the AWS credentials this Mac already has.
"""

from __future__ import annotations

import gzip
import json
import os
import sys
import time
import urllib.error
import urllib.request

import boto3
from boto3.dynamodb.conditions import Key

URL = os.environ["MOBILE_URL"]


def tokens() -> dict:
    cognito = boto3.client("cognito-idp")
    res = cognito.admin_initiate_auth(
        UserPoolId=os.environ["MOBILE_POOL_ID"], ClientId=os.environ["MOBILE_TEST_CLIENT_ID"],
        AuthFlow="ADMIN_USER_PASSWORD_AUTH",
        AuthParameters={"USERNAME": os.environ["MOBILE_TEST_EMAIL"], "PASSWORD": os.environ["MOBILE_TEST_PASSWORD"]},
    )
    return res["AuthenticationResult"]


def call(method: str, path: str, token: str | None = None, body: dict | None = None) -> tuple[int, object]:
    req = urllib.request.Request(f"{URL}{path}", method=method, data=json.dumps(body).encode() if body is not None else None)
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    if body is not None:
        req.add_header("Content-Type", "application/json")
    req.add_header("Accept-Encoding", "gzip")
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            raw = resp.read()
            if resp.headers.get("Content-Encoding") == "gzip":
                raw = gzip.decompress(raw)
            return resp.status, json.loads(raw)
    except urllib.error.HTTPError as err:
        raw = err.read()
        try:
            return err.code, json.loads(raw)
        except ValueError:
            return err.code, raw.decode(errors="replace")[:200]


def main() -> int:
    failures = []

    def check(name: str, ok: bool, detail: object = "") -> None:
        print(f"{'ok  ' if ok else 'FAIL'} {name}" + (f"  {detail}" if not ok else ""))
        if not ok:
            failures.append(name)

    status, _ = call("GET", "/api/me")
    check("no token is refused", status == 401, status)
    status, _ = call("GET", "/api/me", "not-a-token")
    check("a bad token is refused", status == 401, status)

    access = tokens()["AccessToken"]
    status, me = call("GET", "/api/me", access)
    check("/api/me", status == 200 and isinstance(me, dict) and me.get("test") is True, (status, me))
    status, state = call("GET", "/api/state", access)
    check("/api/state", status == 200 and isinstance(state, dict) and "board" in state, (status, str(state)[:200]))
    status, msgs = call("GET", "/api/messages", access)
    check("/api/messages", status == 200 and isinstance(msgs, dict) and "messages" in msgs, (status, msgs))
    status, sent = call("POST", "/api/messages", access, {"line": "machine", "text": "check", "key": "check-no-passkey-0001"})
    check("a send without a passkey check is refused", status == 403 and isinstance(sent, dict) and sent.get("error") == "passkey", (status, sent))
    status, sent = call("POST", "/api/messages", access, {"line": "machine", "text": "x", "key": "k" * 10, "run": "ls"})
    check("an unknown field is refused", status == 400, (status, sent))
    status, shell = call("GET", "/")
    check("the app shell is served", status == 200, status)
    status, saved = call("POST", "/api/push", access, {"subscription": {"endpoint": "ftp://x", "keys": {}}})
    check("a bad push subscription is refused", status == 400, (status, saved))
    fake = {"endpoint": f"https://push.invalid/check-{int(time.time())}", "keys": {"p256dh": "check", "auth": "check"}}
    status, saved = call("POST", "/api/push", access, {"subscription": fake})
    check("a push subscription is saved", status == 201, (status, saved))
    if status == 201:
        # The Mac's next poll sees it and, since this is the test user, never registers it.
        table = boto3.resource("dynamodb").Table(os.environ["MOBILE_TABLE"])
        state = ""
        for _ in range(40):
            items = table.query(KeyConditionExpression=Key("pk").eq("PUSHSUB"))["Items"]
            mine = [i for i in items if i["subscription"]["endpoint"] == fake["endpoint"]]
            state = mine[0]["state"] if mine else ""
            if state != "new":
                break
            time.sleep(1)
        check("the Mac never registers the test user's subscription", state == "test", state)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
