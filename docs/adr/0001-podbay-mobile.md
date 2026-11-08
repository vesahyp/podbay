---
adr: 1
title: podbay mobile is a phone web app that replaces the artifact board, with a serverless read path and a serverless text-only write path
date: 2026-11-08
status: Accepted
deciders: owner
---

# 0001. podbay mobile

## Context

The user reads the work on the phone through the board: `podbay board`
writes `~/.local/state/podbay/board.html` and Head Jeeves publishes it as a
claude.ai artifact with the `comments` capability. A tap on a line opens a
composer, and the text arrives at Head Jeeves as an artifact comment. The
comment wake fails often enough that podbay types a comment sweep into Head
Jeeves every 5 minutes while it is idle. The faults of this path:

- An answer waits up to 5 minutes, and a sweep costs a Head Jeeves turn
  even when there is nothing to read.
- The board is only as new as Head Jeeves' last republish. Machine health
  and the nightly runs are not on it, or only in the collapsed machine room.
- The board is a page in another product. podbay cannot control the
  layout, the login, the push target or the error reports.
- The nightly runs are in three places: `cronstatus` on the Mac, the
  11:00 digest push, and the clavesa runs chart at
  `https://jeeves.tienoo.com/runs.html`.

The direction chosen is a phone first web app (PWA) in this repo, under
`mobile/`, that shows the board, machine health and the nightly runs, and
that sends text to Head Jeeves. The vision mock is in `mobile/mock/`, its
screenshots in `mobile/mock/shots/` (`make mobile-shots`).

## Decision

### What the app does

Four tabs, one screen each. The mock shows all four.

1. **Board.** Decisions for you, ready for you to test, in progress,
   finished today, grouped by project. The same sections and the same
   lines as `podbay board`, from the inventory and `headlines.json`.
2. **Machine.** Power and battery, disk free, CPU load and memory pressure
   as a 24 hour chart, Docker up or down, every Claude login with its
   expiry, and every account's quota windows as bars with the reset time.
3. **Nightly.** Every cron job as one row of 7 day cells, one cell per
   night: green ran and passed, red failed, outlined red did not run,
   grey not scheduled that day. A tap on a cell opens that run's exit
   code, duration and log tail. Below it, the clavesa pipeline run times
   as a stacked bar chart per night (the data behind `runs.html`).
4. **Thread.** Every message sent from the app and every reply from Head
   Jeeves, newest first, each with its state: queued, delivered, answered
   or expired.

Every line on every screen opens a reply sheet. A decision line also has
answer buttons, one per option Head Jeeves wrote. The Board has a
"Start a session" button that opens the same sheet with a repo picker.
All of these send text. There is no other action.

### Architecture

```
 Mac (main Mac only)                    AWS eu-north-1                       Phone
 ───────────────────                    ──────────────                       ─────
 podbay loop, every 60 s
   podbay snapshot ── PutObject ──▶  S3 state/state.json (private)
   (only when it changed)                    ▲
                                             │ GetObject
                                   Lambda "read" ◀── GET /state ──┐
                                                                   │
                                   HTTP API (JWT authorizer, ──────┤ access token
                                     Cognito pool, throttled)      │ (Cognito)
                                                                   │
                                   Lambda "write" ◀─ POST /messages┤
                                     checks, rate limit, audit     │
                                        │                          │
                                        ▼                          │
                                   DynamoDB podbay-mobile          │
                                     MSG#…  queued → delivered ────┘ GET /messages
                                     AUDIT#…, RATE#…
                                        ▲
 podbay loop, every 15 s ── Query ──────┘
   queued, not expired → podbay send head-jeeves
     "/head-jeeves phone <id> <target>, on "<quote>": <text>"
   → UpdateItem delivered
 podbay reply <id> "<text>" ── UpdateItem answered ─┘

 scripts/notify (jeeves) ── Web Push ──────────────────────────────▶ service worker
   link https://<app domain>/#line/<id>                             opens the line
```

- **The app shell is static.** `mobile/app/` on S3 behind CloudFront at
  the app domain: HTML, one script, one stylesheet, the manifest, the
  service worker, `tracker.js` and `t.gif`. No build step, same as
  `site/`. The shell holds no data.
- **The state is never public.** The brief proposed reading the snapshot
  through CloudFront. A public object would put the log tails, the
  decisions and the login expiries at a guessable URL, and CloudFront
  cannot check a Cognito token without Lambda@Edge. The HTTP API has a
  built-in JWT authorizer for Cognito, so `GET /state` goes through it and
  a small Lambda returns the S3 object. Cost is the same order (cents a
  month), and one authorizer guards both read and write.
- **The Mac only makes outbound calls.** It writes `state.json` and polls
  the table. No port, no tunnel, no webhook into the Mac.
- **The snapshot is written when it changes**, checked every 60 s, so the
  app is at most about a minute behind. `state.json` holds `generated_at`;
  the app shows the age in the header and turns it amber after 5 minutes
  and red after 15 (the Mac is asleep, off or offline).
- **Delivery is polled every 15 s.** A message is at Head Jeeves within
  about 15 s of the tap, against up to 5 minutes for the comment sweep.
  The poll is one DynamoDB Query on a sparse index of queued messages,
  about 170 000 reads a month, inside the free tier.
- **Push stays in jeeves.** `scripts/notify` and `jeeves_push.py` are
  the one send point. Change: a notification that carries a URL on the
  app's origin opens the app at that line. A Web Push subscription belongs
  to one origin, and the current `sw.js` on jeeves.tienoo.com does not open
  outside URLs on iOS. So the app registers its own subscription, `make
  push-subscribe` stores it in `/jeeves/push` beside the others, and the
  jeeves.tienoo.com subscription on the phone is removed, or every push
  arrives twice. `runs.html` stays on jeeves.tienoo.com.

### State snapshot

`podbay snapshot` writes one JSON object, about 100 KB:

- `board`: the sections and lines `podbay board` renders now, each line
  with a stable `id` (the session id, plus the headline `at` for a
  shipped line), `target`, `quote`, `text`, `link`, `steps`, and for a
  decision the new optional headline field `options`.
- `machine`: the last 24 hours of `machine.jsonl` at 10 minute steps,
  battery percent and power source, disk free, Docker up, the launchd
  jobs (powerwatch, keepawake, dockerup) and when each last wrote its log.
- `accounts`: what `podbay accounts --json` prints, plus the login end
  times from `logins.py`.
- `nightly`: per job, the last 8 nights: start, end, exit code, and the
  last 40 log lines. `cronrun` keeps only the last run today
  (`jeeves/state/cron/<job>.json`), so `cronrun` must also append each run
  to `jeeves/state/cron/<job>.jsonl` and keep 8 days. The schedule comes
  from `jeeves/crontab`, which is how a missed night is told from a night
  with nothing scheduled.
- `runs`: the clavesa run history that `make runs-publish` exports.

Before the upload, every log line goes through a scrubber that removes
anything shaped like a key or token (AWS keys, `sk-`, `ghp_`, JWTs, long
base64 runs). Nothing in these repos is confidential, but a log tail can
hold a credential printed by mistake.

### Messages

One item per message in the `podbay-mobile` table:

| field | value |
|---|---|
| `pk` | `MSG#<ulid>` |
| `line` | the board line id the reply is about, or `new-session` |
| `target`, `quote` | copied from the snapshot by the Lambda, never from the request |
| `text` | at most 1000 characters, one line, control characters removed |
| `state` | `queued`, `delivered`, `answered`, `expired` |
| `created`, `expires` | epoch seconds; `expires` is `created` + 2 hours |
| `reply` | Head Jeeves' answer, from `podbay reply` |
| `ttl` | 90 days, then DynamoDB removes it |

- **Text only.** The request body has `line`, `text` and an idempotency
  key, nothing else. The Lambda rejects any other field. The Mac side
  types it with `podbay send head-jeeves` as one `/head-jeeves phone`
  event; there is no code path from the table to a shell.
- **Expiry.** The poller delivers only `queued` items with `expires` in
  the future and marks the rest `expired`. A message queued while the Mac
  slept does not arrive hours later as an order nobody remembers; the
  app shows it as expired, with a "Send again" button.
- **Rate limit.** API stage throttle 1 request a second, burst 5. In the
  Lambda, an atomic counter per hour and per day: 30 messages an hour,
  200 a day. Over the limit the request is refused with 429 and audited.
- **Audit.** Every request to the write Lambda, accepted or refused, gets
  an `AUDIT#<ulid>` item: time, Cognito `sub`, source IP, user agent,
  outcome, the text's SHA-256 and the text. Kept 1 year. The poller adds
  the delivery time and the `podbay send` result. A `podbay mobile audit`
  command prints the last 7 days.
- **Delivery to Head Jeeves.** The skill's "Messages from the board"
  section moves to the new event, `/head-jeeves phone <id> <target>, on
  "<quote>": <text>`. Head Jeeves answers with `podbay reply <id>
  "<line>"`, which the app shows under the message. The comment sweep and
  the artifact board are removed in the last slice.

### Login

From jeeves `practices/auth.md`: Cognito, `EMAIL_OTP` first factor and
Google sign-in, no password UI, SES sending as `no-reply@<app domain>`,
pre-signup Lambda with an allowlist of one address (a Terraform variable,
not in this public repo). Plus, for this app:

- **No self sign-up.** `allow_admin_create_user_only`, and the one user is
  created by Terraform. The allowlist is a second check.
- **A send needs a passkey sign-in in the last 30 days.** An email code
  or Google sign-in can read; it cannot send. The reason is in the threat
  model: the write path is as strong as the Mac, and email OTP and Google
  sign-in are both only as strong as the Gmail account.
- **The API checks the passkey itself, not Cognito.** A Cognito token does
  not say which factor signed in, so Cognito's own `WEB_AUTHN` factor
  cannot carry this rule. The write Lambda verifies a WebAuthn assertion
  (`py_webauthn`, relying party `app.podbay.tienoo.com`) and stores a send
  grant keyed by the token's `origin_jti`, which stays the same for one
  sign-in across refreshes. The grant ends 30 days after the passkey
  check or when that sign-in ends. Another sign-in, with its own
  `origin_jti`, has no grant.
- **A passkey is added only from the Mac.** `podbay mobile passkey-allow`
  opens a 15 minute window, and only then does the API register a new
  passkey. Someone with the Gmail account can sign in and read, but
  cannot add their own passkey.
- **Tokens.** Access token 15 minutes, refresh token 30 days, stored in
  `localStorage` on the app origin. Refresh token revocation is on, so a
  sign-out on the Mac (`podbay mobile signout`, AdminUserGlobalSignOut)
  ends every session at once.
- **Tests sign in as a second user** in the `test` group, through an app
  client that allows only `ADMIN_USER_PASSWORD_AUTH`, which needs AWS
  credentials. The write Lambda marks its messages `test`, and the poller
  marks them delivered without typing them.

### Threat model

The asset is the Mac. Head Jeeves is an agent that runs shell commands and
starts sessions that run shell commands. **Text that reaches Head Jeeves is
the same as a shell on the Mac**, so "the app only sends text" limits what
the code does, but not what the text can cause. The design treats the write
path with the care of a remote shell.

| Threat | Control |
|---|---|
| Someone signs in as the user | One user, admin-created, allowlist; passkey bound to the phone; OTP and Google need the Gmail account |
| Gmail account taken over | Read only: a send needs a passkey check in the same sign-in, and a new passkey is added only in a window opened from the Mac |
| Phone stolen while unlocked | 30 day refresh token; `podbay mobile signout` revokes it; the audit log and the Thread tab show every message sent |
| A stolen access token | 15 minute life; JWT authorizer checks issuer, audience, expiry |
| Many messages, or a script in a loop | Stage throttle; 30 an hour, 200 a day; each refusal audited |
| An old message delivered late | 2 hour expiry; expired items are never typed |
| A crafted message breaks out of the event | One line, control characters removed, 1000 character limit; `target` and `quote` come from the snapshot; `podbay send` already types text without a shell |
| Text injected into another session's prompt (prompt injection) | Head Jeeves sees every phone message as an event marked `phone`; nothing new, the same text arrives today as a comment |
| The state leaks | Private bucket, read only through the authorizer; log tails scrubbed before upload |
| XSS in the app runs a send | Strict CSP (`default-src 'self'`, `connect-src` the API only), all text set with `textContent`, no third-party script |
| Replay or double send | Idempotency key per message, conditional put |
| AWS credentials on the Mac | The poller uses the AWS profile the nightly jobs already use. A narrow IAM user would protect nothing while that profile is on the same Mac |
| The AWS account itself | Out of scope: whoever holds it holds the Mac's secrets bundle too |

### Tracking and error reporting

The hard rule in jeeves `practices/web-tracking.md` applies.
`tracker.js` unmodified from `clavesa-dev/web-tracker/`, `spa: true`, a
`t.gif` on the app's CloudFront with logging to a private bucket and a
90 day expiry, `TRACKER_CONFIG.sanitize` that removes every value except
the tab, the line kind and the button name, so no message text and no
board line ever leaves in a beacon. Uncaught errors and rejections come
from `tracker.js`; the app's top-level `try` around rendering sends
`type: "boundary"`. A second pipeline, `podbay-mobile-traffic`, in the
existing `analytics/` workspace, and a Stats tab inside the app (behind
the login) with an errors panel. `make mobile-errors` prints the last 7
days.

### Domain

`app.podbay.tienoo.com`, its own origin. The public landing page stays on
podbay.tienoo.com with its own CloudFront, so the app's service worker,
storage and CSP are separate from a page anyone can open. The API is on
the same CloudFront under `/api/`, so the app makes no cross-origin call
except to Cognito.

### Build slices, in order

Each slice ends with `make ship` and a check on the phone.

1. **Run history.** jeeves: `cronrun` appends each run to
   `state/cron/<job>.jsonl`, 8 days; `cronstatus` unchanged.
2. **Snapshot.** podbay: `podbay snapshot` writes `state.json` locally
   (board, machine, accounts, nightly, runs), stable line ids, the log
   scrubber, tests. No AWS yet.
3. **Infra.** `infra/mobile.tf`: Cognito (OTP, Google, passkey), SES for
   the domain, app bucket, CloudFront, ACM, Route 53, HTTP API with the
   JWT authorizer, the read and write Lambdas, the table, the poller's
   IAM user, the tracker log bucket.
4. **Read-only app.** `mobile/app/`: sign-in, Board, Machine, Nightly
   from `GET /state`; manifest and service worker; tracker and errors;
   the podbay loop uploads the snapshot. A Playwright test signs in
   against a test pool and reads all three tabs.
5. **Write path.** `POST /messages`, rate limit, audit, expiry; the
   poller in the podbay loop; `podbay reply`, `podbay mobile audit`,
   `podbay mobile signout`; the Thread tab; the skill's new
   `/head-jeeves phone` section. A Playwright test sends a message and
   sees it reach a fake Head Jeeves tab.
6. **Push into the app.** The app's own subscription; `notify` links on
   the app origin open the line; the jeeves.tienoo.com subscription
   comes off the phone.
7. **Stats.** `podbay-mobile-traffic` pipeline, the Stats tab,
   `make mobile-errors`.
8. **Retire the artifact board.** For one month after the app is live,
   Head Jeeves keeps publishing the artifact board as a fallback. Then
   remove the comment sweep, the artifact publish steps in the skill and
   the board's composer code. `podbay board` stays as the local HTML view.

## Consequences

- An answer reaches Head Jeeves in about 15 s, and an idle Head Jeeves no
  longer spends a turn every 5 minutes on a sweep.
- The board, the machine and the nightly runs are one app, at most about
  a minute old, with the age shown.
- podbay owns a login, an API and a table. That is new surface to keep
  correct in a public repo: Terraform, two Lambdas, a pool, an SES
  identity, a second tracker pipeline. Before this, podbay's AWS was a
  static site and one Lambda.
- The write path makes the phone a way into the Mac. The controls lower
  the risk; they do not remove it. A taken-over Gmail account plus no
  passkey gate on writes is a remote shell.
- While the Mac sleeps, the app shows stale state and messages expire.
  That is the intended behaviour, and it means a message sent at night
  must be sent again in the morning.
- The phone needs a new push subscription, and the jeeves.tienoo.com one
  must go, or pushes arrive twice for as long as both exist.
- `cronrun` gains a history file, which is a change in jeeves, not here.
- Running cost: about 1 to 2 USD a month (Cognito ESSENTIALS for one
  user is in its free tier; the rest is requests and storage).

## Choices made

1. **Domain:** `app.podbay.tienoo.com`. Other options were
   `bay.tienoo.com` and `podbay.tienoo.com/app/`.
2. **What a send needs:** a passkey sign-in in the last 30 days; an email
   code or Google sign-in can only read. Other options were any sign-in,
   or Face ID on every send.
3. **Message expiry:** 2 hours. Other options were 30 minutes, or until
   08:00 the next morning.
4. **The artifact board:** published beside the app for one month as a
   fallback, then removed. The other option was to remove it at once.
