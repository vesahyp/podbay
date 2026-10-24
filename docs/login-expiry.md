# When a Claude login expires

Finding of 2026-10-24. The expiry is predictable. podbay reads it from the
keychain and warns before it (`podbay/logins.py`).

## What the keychain item holds

Each account has one generic password item, `Claude Code-credentials` for
the default account and `Claude Code-credentials-<8 hex of the config dir
hash>` for the others. The secret is JSON. Under `claudeAiOauth`:

| field | what it is |
|---|---|
| `accessToken` | the token each request sends; it lasts 8 hours |
| `expiresAt` | when the access token ends, epoch milliseconds |
| `refreshToken` | renews the access token; each refresh replaces it |
| `refreshTokenExpiresAt` | when the refresh token ends, epoch milliseconds |
| `scopes`, `subscriptionType`, `rateLimitTier` | the plan; no secret |

`/login` sets `refreshTokenExpiresAt` about 30 days ahead (27.6 and 29.6
days on the two logins seen). A refresh gives a new access token and a new
refresh token, but it does not move `refreshTokenExpiresAt`.

When the login is gone, Claude Code keeps the item with empty tokens and
`expiresAt` 0. `refreshTokenExpiresAt` stays in it.

## What makes Claude Code say "Login expired"

The refresh token reaching its fixed end. The default account on 2026-10-23,
all times UTC:

- 04:47, the last refresh (inferred: 8 hours before 12:47).
- 06:09, `refreshTokenExpiresAt`. No refresh works after this.
- 08:36, the keychain item is written with empty tokens.
- 10:49, Remote Control disconnects: "OAuth token unavailable".
- 12:47, the access token ends and the session answers "Login expired ·
  Please run /login".

So the sessions stop at the end of the last access token issued before
`refreshTokenExpiresAt`: from that time up to 8 hours after it.
`accounts.login_deadline` uses `refreshTokenExpiresAt` while it is ahead,
and the access token's end after it.

The personal account on 2026-10-24 fits the same pattern. Two sessions
answered "Login expired" at 19:34:19 and 19:34:30 UTC, at the end of an
8-hour access token, and `/login` at 19:34:56 gave a new
`refreshTokenExpiresAt`. Its old value was not kept, so this case is not
proven.

The Claude Code message is a synthetic assistant record (model
`<synthetic>`) in the session transcript. Its text is "Login expired ·
Please run /login", or "Failed to authenticate: OAuth session expired and
could not be refreshed" from `claude -p`.

## What podbay does with it

- `podbay accounts`, `podbay inventory` (`login_ends`, and a `logins:` line
  in `--status`) and the board's machine room show the time left on each
  login. The board shows it in red under 24 hours.
- The screen checks once a minute and pushes through `podbay notify`: 24
  hours before the end and 1 hour before. No push between 23:00 and 07:00
  unless under 2 hours are left; a warning held at night goes at 07:00.
- When a session answers "Login expired", one push names the account at
  once, and the board marks the session "login expired". One push per
  account until the next `/login`.

The fix is always the same: run `/login` in a session on that account.
Nothing in podbay prints, logs or stores a token.
