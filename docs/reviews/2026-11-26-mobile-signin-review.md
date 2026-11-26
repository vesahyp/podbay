# Review: phone app sign-in fix (88f0289, 89130b6)

Date: 2026-11-26. Scope: `mobile/app/auth.js`, `mobile/app/app.js`,
`scripts/auth_check.mjs`, `scripts/mobile_app_check.py` (case 1b), the
`make mobile-test` line. Read-only review: nothing changed, committed or
deployed.

## Verdict

The fix is correct and safe to keep. The claimed cause is supported by
CloudTrail, the API access log and the CloudFront tracker log. No other
cause signed the phone out. Nothing is important enough to block. Four low
items and three nits follow.

The deployed files are the committed files: `auth.js`, `app.js`, `sw.js`
and `index.html` at https://app.podbay.tienoo.com are byte-identical to
`mobile/app/` at 89130b6. `node scripts/auth_check.mjs` passes (7 cases).
The live case 1b ran at 2026-11-26 12:00:01 UTC: CloudTrail has a
`REFRESH_TOKEN_AUTH` on the test client with no error.

## 1. Is the cause supported?

Yes. The sign-out at 2026-11-26 05:23 UTC was a token renewal that did not
reach Cognito, during the reload after a deploy.

Timeline (UTC, phone = iPhone OS 18_7, tracker uid `0c24acc0`, app client
`podbay-mobile-app`):

| Time | Source | What happened |
|---|---|---|
| 05:02:41 | CloudTrail | Last renewal before the incident. Access token good to 05:17:41. |
| 05:11:55 | API log | Last API call from the phone (200), from 109.204.176.157. |
| 05:14:44 | CloudFront | Playwright `make mobile-test` against the new deploy (Mac IP). |
| 05:23:29 to 05:23:30 | CloudFront | Phone wakes on 109.204.176.157, fetches the new `sw.js` and shell, `pageview`. |
| 05:23:31 | CloudFront | `session_end`, `dur=2`: the controllerchange reload, 2 s after the page started. |
| 05:23 to 05:28 | CloudTrail, API log | No Cognito call and no API call from the phone. The access token was expired, so the app had to renew first. |
| 05:28:43 to 05:28:50 | CloudFront | Phone back, now on the home IPv6. Same uid `0c24acc0`. |
| 05:28:53, 05:29:06 | CloudTrail, tracker | `USER_AUTH` and `RespondToAuthChallenge`; tracker `signin` `code-sent`, `done`. |

What this rules out:

- **Cognito refused the token.** No Cognito event for the app client has an
  `errorCode` since 2026-10-28. A refusal is logged with one.
- **An API 401.** The access log has no 401 from any address since
  2026-11-08 08:18 (pre-launch tests). The old code's other sign-out path
  did not fire.
- **A revocation.** No `RevokeToken` at all. The `AdminUserGlobalSignOut`
  calls on 2026-11-17, 18 and 23 are on pool `eu-north-1_c0gi1ZwA4`
  (clavesa-ui), not on `podbay-mobile`.
- **Storage loss.** The tracker uid is kept in the same `localStorage`; it
  did not change, so the stored login was deleted by the app.

One qualification: the evidence shows the post-deploy reload case only.
The "network drop at wake" case without a deploy is plausible (the phone
moved from a mobile network to home Wi-Fi in that window) but no incident
shows it alone. The new code covers both the same way.

## 2. Correctness

Checked and correct:

- **Parallel renewals in one page.** One shared `refreshing` promise: three
  calls at start make one Cognito call (test case 1).
- **Renewal against a concurrent sign-out or new sign-in.** On success the
  code stores the new access token only when the stored refresh token is
  still the one it renewed; on refusal it deletes only that same login. A
  late answer cannot bring back a login that was signed out, or overwrite a
  new one.
- **401 loop.** None. `authorized()` renews once and retries once. A second
  401 goes to the screen as "Could not load: Unauthorized", and the login
  stays. Polls run only while the page is visible.
- **Phone clock skew.** `exp` is computed from the phone's own clock, and a
  401 forces a renewal, so a wrong clock costs at most one retry.
- **Real revocation (`podbay mobile signout`).** `AdminUserGlobalSignOut`
  makes Cognito answer the next renewal with `NotAuthorizedException`, which
  is in `REFUSED`, so the app deletes the login and shows the sign-in. The
  access token keeps working at the API Gateway JWT authorizer until it
  expires, up to 15 minutes. That was the same before the fix.
- **Refresh token truly expired.** Cognito answers
  `NotAuthorizedException: Refresh Token has expired`, the login is deleted
  and the sign-in screen shows (test case 4).
- **Captive portal or HTML error page.** A non-JSON answer has no `__type`,
  so the login stays.
- **Lambda 401.** `mobile/api/handler.py` answers 401 only for a token that
  is not an access token. The server never uses 401 to mean "this login is
  over", so dropping the old "401 deletes the login" rule loses nothing.

### Low

**L1. A dead login that Cognito does not call "refused" stays forever.**
`REFUSED` lists three types. `ResourceNotFoundException` (the app client was
replaced, so the stored `clientId` no longer exists) and
`InvalidParameterException` are also permanent, but the app keeps the
login and shows "Could not reach the sign-in service: User pool client ...
does not exist" on every poll. The way out is the Sign out button, which
shows on the error screen too. Trigger: a Terraform change that replaces
`aws_cognito_user_pool_client.mobile`. Fix: add
`ResourceNotFoundException` to `REFUSED`; leave throttling and 5xx as they
are.

**L2. A true expiry shows the bare sign-in screen with no reason.** When
`REFUSED` deletes the login, `signin.err` is empty, so the phone looks the
same as after the old bug. A draft in the chat box is lost with the redraw.
Fix: set `signin.err` to a line such as "Your sign-in ended after 30 days.
Sign in again." when the refusal deletes the login. Optional: store the
sign-in time at `RespondToAuthChallenge` and show the end date on the
Machine tab, so the monthly sign-in is expected.

**L3. A persistent 401 makes one Cognito renewal per poll.** If the
authorizer rejects good tokens (issuer or audience wrong after a deploy),
each poll forces a renewal: up to about 6 a minute while the app is open.
It costs nothing (below) and is far below Cognito's quota. Acceptable as
is; noted so the pattern is recognised in CloudTrail.

**L4. The error text on a failed renewal blames the network for all
non-refusals.** "Could not reach the sign-in service: Rate exceeded" is
shown for a Cognito answer that did arrive. Cosmetic; it matters only with
L1 or L3.

### Nits

**N1. The frequency claim is not supported.** The `auth.js` header says the
phone "asked for a new sign-in a few times a week". CloudTrail shows three
sign-ins on the app client since 2026-10-28: two on 2026-11-08 (13:15 and
13:23 UTC, the first sign-ins, most likely Safari and then the home screen
app, which have separate storage) and one on 2026-11-26. Change the line
to what the record shows: one unplanned sign-out in 18 days.

**N2. Keep refresh token rotation off.** With rotation, a renewal that
reaches Cognito but whose answer is lost (the exact reload case) leaves the
phone holding a spent token, and after the grace period that is a
`RefreshTokenReuseException` sign-out. The success branch would also return
"signed out" while a rotated login is stored. A one-line comment at
`refresh_token_validity` in `infra/mobile.tf` would stop someone turning it
on to get a sliding expiry.

**N3. Test gaps.** `auth_check.mjs` has no case for the "signed out in
another tab during the renewal" branch, the "signed in again during the
renewal" branch, or a 200 answer that is not JSON (it rejects with a
`TypeError` text, not `.offline`). The code is right in all three; the
tests do not hold it there.

## 3. Security: is access wider than before?

No, not past what the design already allowed. The refresh token still
lives 30 days, revocation still ends it within 15 minutes, and the API
checks are unchanged. The only change is that a login which used to die
early on a network error now lives to the 30 days the ADR
(`docs/adr/0001-podbay-mobile.md`, Tokens) always intended. Token storage
(`localStorage`, CSP `connect-src` limited to self and Cognito) is
unchanged.

## 4. Running cost

About zero. Cognito Essentials bills per monthly active user, not per
renewal, and one user is inside the free tier. The phone renews 3 to 15
times a day (CloudTrail). The 401 retry adds at most one API call per 401,
and there were none in the last 18 days. The live test case 1b adds one
renewal per `make mobile-test`. CloudTrail management events are free.

## 5. Is there a simpler native option?

No simpler one for the fix. There is no browser API that renews a Cognito
token. The Amplify Auth library does the same rule (keep tokens on a
network error, sign out on `NotAuthorizedException`) but adds a build step
and a large dependency to an app that is static with no build. The old
code needed a three-line change; moving it to `auth.js` is what made it
testable in Node, and that is worth the extra file.

Two cheap additions, not needed now:

- `navigator.storage.persist()` asks the browser not to evict the app's
  storage. iOS already exempts home screen apps from the 7-day storage
  limit, so it is only insurance.
- The reload after a deploy could wait until the page is hidden. After the
  fix the reload is harmless, so this is not worth the code.

## Recommendation: `refresh_token_validity`

**Keep 30 days.** Reasons:

- **What a longer limit widens is the send path.** The send grant is keyed
  by the sign-in's `origin_jti`, which stays the same for every renewal of
  one refresh token. A copied refresh token can read for its whole life,
  and it can send whenever the real phone has renewed the grant with a
  passkey on that same sign-in. The ADR treats a send as a shell on the
  Mac. So `refresh_token_validity` is the longest time a copied token can
  type into Head Jeeves. At 30 days that window ends by itself every month;
  at 365 days only `podbay mobile signout` ends it, and only if Vesa
  notices.
- **The monthly cost is now small and on a known date.** Without rotation
  the expiry is fixed from the sign-in, not from the last use. The current
  sign-in ends about 2026-12-26 05:29 UTC. The cost is one email code and
  one passkey confirm. L2 makes that sign-out say why.
- **Check the fix in December.** If the only sign-in on the app client
  before 2026-12-27 is the one at the 30-day mark, the fix holds. Any
  other `USER_AUTH` in CloudTrail means a cause this review did not find.

If a longer limit is still wanted, 90 days is the most I would set, and it
is a change to the threat model row "Phone stolen while unlocked" in the
ADR, so it needs an ADR update in the same commit. I'm unsure whether
Cognito's refresh token rotation gives a sliding expiry; check the AWS
documentation before using rotation for that, and see N2.

## Outside scope, one line

Every tracker beacon (`POST /t.gif`) gets a 403 from CloudFront, while
`tracker.js` says it answers 200. The pipeline reads the access log and
does not filter on status, so no data is lost.
