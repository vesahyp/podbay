"""The Claude Code accounts on this machine, one per config directory.

Claude Code keeps everything per CLAUDE_CONFIG_DIR: the session registry,
the transcripts, the login. The default is ~/.claude; the `claude-personal`
alias points CLAUDE_CONFIG_DIR at ~/.claude-personal. Podbay reads every
such directory, so one console shows the sessions of both subscriptions
and tells them apart by the label, which is the suffix of the directory
name ("claude" for the default one, "personal" for ~/.claude-personal).
"""

from __future__ import annotations

import getpass
import hashlib
import json
import os
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

DEFAULT_DIRNAME = ".claude"
DEFAULT_LABEL = "claude"
KEYCHAIN_SERVICE = "Claude Code-credentials"


@dataclass(frozen=True)
class Account:
    label: str
    config_dir: Path

    @property
    def sessions_dir(self) -> Path:
        return self.config_dir / "sessions"

    @property
    def projects_dir(self) -> Path:
        return self.config_dir / "projects"

    @property
    def is_default(self) -> bool:
        return self.config_dir.name == DEFAULT_DIRNAME

    def env(self) -> dict[str, str]:
        """The environment that makes `claude` run as this account; empty
        for the default one, which needs no variable."""
        return {} if self.is_default else {"CLAUDE_CONFIG_DIR": str(self.config_dir)}

    def run_env(self) -> dict[str, str]:
        """The whole environment for a `claude` child process run as this
        account. The caller's own CLAUDE_CONFIG_DIR is removed first: a
        podbay started from a personal session inherits it, and without
        the removal the default account ran as the personal one and its
        usage looked fresh after its login had expired (2026-10-24)."""
        base = {k: v for k, v in os.environ.items() if k != "CLAUDE_CONFIG_DIR"}
        return {**base, **self.env()}

    @property
    def keychain_service(self) -> str:
        """The macOS keychain item that holds the account's login: Claude
        Code names it after a hash of the config dir for every account but
        the default one."""
        if self.is_default:
            return KEYCHAIN_SERVICE
        return f"{KEYCHAIN_SERVICE}-{hashlib.sha256(str(self.config_dir).encode()).hexdigest()[:8]}"

    def command_prefix(self) -> str:
        """What goes in front of `claude` in a shell command typed into a
        terminal: nothing for the default account, the env assignment
        for every other one. The home path is spelled with ~ so the line
        reads the same as the alias in .zshrc."""
        if self.is_default:
            return ""
        home = str(Path.home())
        shown = str(self.config_dir)
        if shown.startswith(home + "/"):
            shown = "~" + shown[len(home):]
        return f"CLAUDE_CONFIG_DIR={shown} "


def label_for(config_dir: Path) -> str:
    name = config_dir.name
    if name == DEFAULT_DIRNAME:
        return DEFAULT_LABEL
    prefix = DEFAULT_DIRNAME + "-"
    return name[len(prefix):] if name.startswith(prefix) else name


def discover(home: Path | None = None) -> list[Account]:
    """Every config directory under `home` that Claude Code has used: the
    default first, then ~/.claude-<label> in name order. A directory
    counts once it has a sessions or projects folder, so a stray
    ~/.claude-something that is not a Claude Code home is skipped."""
    home = home or Path.home()
    found: list[Account] = []
    candidates = [home / DEFAULT_DIRNAME] + sorted(
        p for p in home.glob(DEFAULT_DIRNAME + "-*") if p.is_dir() and p.name != DEFAULT_DIRNAME
    )
    for path in candidates:
        if (path / "sessions").is_dir() or (path / "projects").is_dir():
            found.append(Account(label=label_for(path), config_dir=path))
    if not found:
        found.append(Account(label=DEFAULT_LABEL, config_dir=home / DEFAULT_DIRNAME))
    return found


def by_label(accounts: list[Account], label: str | None) -> Account | None:
    if label is None:
        return accounts[0] if accounts else None
    return next((a for a in accounts if a.label == label), None)


def _keychain_user() -> str:
    """The keychain account Claude Code files its login under: the login
    user's name."""
    try:
        return getpass.getuser()
    except (OSError, KeyError):
        return ""


def _read_keychain(service: str) -> str | None:
    """The keychain item's secret, or None when there is no such item or
    no `security` command (not macOS). Never raises.

    Several items can share one service name, told apart by their account
    attribute, and a lookup by service alone returns whichever comes
    first. On 2026-10-24 a second "Claude Code-credentials" item with
    account "unknown" and only MCP logins in it appeared beside the real
    one (account "vesa"), and podbay read it and called the default
    account logged out through a fresh /login. So the item under the
    login user's name is read first, and the service-only lookup is the
    fallback."""
    user = _keychain_user()
    lookups = ([["-a", user]] if user else []) + [[]]
    for extra in lookups:
        try:
            result = subprocess.run(
                ["security", "find-generic-password", "-s", service, *extra, "-w"],
                capture_output=True, text=True, timeout=5,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        if result.returncode == 0:
            return result.stdout
    return None


def credential_logged_out(credential: dict | None, now: float | None = None) -> bool:
    """True when the stored login can no longer start a session. /logout and
    an expired login both leave the item in place with an empty access
    token, an empty refresh token and expiresAt 0 (seen 2026-10-24); an
    access token past its expiry with no refresh token to renew it is the
    same state."""
    oauth = (credential or {}).get("claudeAiOauth")
    if not isinstance(oauth, dict) or not oauth.get("accessToken"):
        return True
    now = time.time() if now is None else now
    expires = oauth.get("expiresAt")
    expired = isinstance(expires, (int, float)) and expires / 1000 <= now
    return expired and not oauth.get("refreshToken")


def read_credential(account: Account, read_keychain=_read_keychain) -> dict | None:
    """The account's stored login: the keychain item on macOS, else
    <config dir>/.credentials.json (Linux). None when neither exists or
    parses. The tokens in it are never printed or logged."""
    raw = read_keychain(account.keychain_service)
    if raw is None:
        try:
            raw = (account.config_dir / ".credentials.json").read_text()
        except OSError:
            return None
    try:
        credential = json.loads(raw)
    except ValueError:
        return None
    return credential if isinstance(credential, dict) else None


def logged_in(account: Account, read_keychain=_read_keychain) -> bool | None:
    """Whether the account still has a working login. None when the login
    cannot be read, so a machine podbay cannot read is never called logged
    out."""
    credential = read_credential(account, read_keychain)
    if credential is None:
        return None
    return not credential_logged_out(credential)


def login_deadline(credential: dict | None, now: float | None = None) -> float | None:
    """When the login stops working, as epoch seconds; None when it is
    already gone or the item does not say.

    The refresh token has a fixed end, refreshTokenExpiresAt, which /login
    sets about 30 days ahead and a refresh does not move. Until then Claude
    Code renews the 8-hour access token as it runs out. After it, nothing
    can renew it, and the sessions answer "Login expired" when the last
    access token runs out (2026-10-23: refresh token ended 06:09 UTC, the
    sessions died 12:47 UTC). So the deadline is the refresh token's end,
    and once that has passed, the access token's end."""
    oauth = (credential or {}).get("claudeAiOauth")
    if not isinstance(oauth, dict) or credential_logged_out(credential, now):
        return None
    now = time.time() if now is None else now
    refresh_end = oauth.get("refreshTokenExpiresAt")
    if not isinstance(refresh_end, (int, float)) or not refresh_end:
        return None
    if refresh_end / 1000 > now:
        return refresh_end / 1000
    access_end = oauth.get("expiresAt")
    if isinstance(access_end, (int, float)) and access_end / 1000 > now:
        return access_end / 1000
    return None


def login_stamp(credential: dict | None) -> int | None:
    """What tells one login from the next without touching a token: the
    refresh token's end, which every /login sets anew and a refresh does
    not move. None when the account is logged out or the item does not
    say."""
    if credential_logged_out(credential):
        return None
    end = credential["claudeAiOauth"].get("refreshTokenExpiresAt")
    return int(end) if isinstance(end, (int, float)) and end else None


def login_deadlines(accounts: list[Account], read_keychain=_read_keychain, now: float | None = None) -> dict[str, float]:
    """Each logged-in account's login_deadline, by label; an account whose
    deadline is unknown is left out."""
    found = {}
    for a in accounts:
        deadline = login_deadline(read_credential(a, read_keychain), now)
        if deadline is not None:
            found[a.label] = deadline
    return found


def logged_out_labels(accounts: list[Account], read_keychain=_read_keychain) -> set[str]:
    """The labels of the accounts whose login has expired or was logged out."""
    return {a.label for a in accounts if logged_in(a, read_keychain) is False}
