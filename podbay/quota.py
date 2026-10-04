"""`podbay accounts`: how much room each account has left, and which account
and model a new session should run on.

Nothing new is read here. The overall 5-hour and 7-day windows come from
the status-line snapshots (sources.newest_limits); the per-model weekly
windows (a line such as "week (Fable)") come from the `claude -p /usage`
cache the TUI already keeps (usage.py). The one extra fact is the plan
tier, organizationRateLimitTier in the account's own .claude.json, which
says how large the account's quota is: "default_claude_max_5x" is five
times the base plan.

The suggestion: an account or a model at or above LIMIT_PCT of any window
is never suggested, and neither is a LISTED_ONLY model on an account whose
/usage does not list it right now or has never listed it. Among the rest, the one with the most room left wins,
counted in base-plan units (percent left times the plan multiplier), so a
larger quota wins over a smaller one with the same percentage left.
"""

from __future__ import annotations

import json
import logging
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from .accounts import Account

log = logging.getLogger(__name__)

LIMIT_PCT = 85.0
# The --model aliases claude takes, strongest first. A heavy job starts
# looking at the first one, a routine job at ROUTINE_FROM.
MODELS = ["fable", "opus", "sonnet", "haiku"]
ROUTINE_FROM = "sonnet"
# Models a plan does not include. Claude Code refuses them with "Requires
# usage credits" on an account that lacks them. /usage lists a per-model
# weekly window only for an account that has the model, so these are
# suggested only where that window is listed, or was listed before: the
# list is empty right after a weekly reset, so each account's past models
# are kept in SEEN_PATH. Its own file, not state.json, since the TUI
# rewrites state.json from memory.
LISTED_ONLY = {"fable"}
SEEN_PATH = Path.home() / ".local" / "state" / "podbay" / "models_seen.json"

_MULTIPLIER_RE = re.compile(r"_(\d+)x$")


def state_file(account: Account) -> Path:
    """Claude Code's own settings file for the account: ~/.claude.json for
    the default one, <config dir>/.claude.json for every other one."""
    if account.is_default:
        return account.config_dir.parent / ".claude.json"
    return account.config_dir / ".claude.json"


def plan(account: Account) -> tuple[str | None, int]:
    """(rate-limit tier, quota multiplier against the base plan). The tier
    is None and the multiplier 1 when the file does not say."""
    try:
        data = json.loads(state_file(account).read_text())
    except (OSError, json.JSONDecodeError):
        return None, 1
    oauth = data.get("oauthAccount") or {}
    tier = oauth.get("userRateLimitTier") or oauth.get("organizationRateLimitTier")
    if not isinstance(tier, str):
        return None, 1
    m = _MULTIPLIER_RE.search(tier)
    return tier, int(m.group(1)) if m else 1


def _iso(epoch: float | None) -> str | None:
    if epoch is None:
        return None
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat()


def window(pct: float | None, resets_at: float | None, now: float) -> dict | None:
    """One window. A figure whose reset time has passed is read as 0 used:
    the window started over after the reading was taken."""
    if pct is None:
        return None
    if resets_at is not None and resets_at <= now:
        pct = 0.0
    return {"used_pct": round(float(pct), 1), "left_pct": round(100 - float(pct), 1), "resets_at": _iso(resets_at)}


def model_alias(label: str | None) -> str | None:
    """'Fable' -> 'fable', 'Opus 5.5' -> 'opus': the --model alias."""
    return label.split()[0].lower() if label else None


def read_seen(path: Path) -> dict[str, list[str]]:
    """{account label: model aliases its /usage has ever listed}."""
    try:
        data = json.loads(path.read_text())
    except FileNotFoundError:
        return {}
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("models seen file %s unreadable: %s", path, exc)
        return {}
    return data if isinstance(data, dict) else {}


def remember(path: Path, label: str, aliases: list[str]) -> list[str]:
    """Add `aliases` to the account's remembered models, write the file
    atomically when that changed it, and return the full list."""
    data = read_seen(path)
    known = data.get(label, [])
    merged = sorted(set(known) | {a for a in aliases if a})
    if merged != known:
        data[label] = merged
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name, suffix=".tmp")
            with os.fdopen(fd, "w") as f:
                json.dump(data, f, indent=2)
            os.replace(tmp, path)
        except OSError as exc:
            log.warning("models seen file %s not written: %s", path, exc)
    return merged


def account_entry(account: Account, limits: dict, usage_data: dict | None, now: float, seen_path: Path | None = None) -> dict:
    """One account: its plan, its overall windows, and every per-model
    weekly window /usage lists for it. The snapshots are the newer source
    for the overall windows; /usage fills in one the snapshots lack."""
    entries = (usage_data or {}).get("entries", [])
    by_key = {e.get("key"): e for e in entries if e.get("key") in ("session", "week_all")}
    five = window(limits.get("five_pct"), limits.get("five_resets_at"), now)
    if five is None and "session" in by_key:
        five = window(by_key["session"].get("pct"), by_key["session"].get("resets_at"), now)
    week = window(limits.get("week_pct"), limits.get("week_resets_at"), now)
    if week is None and "week_all" in by_key:
        week = window(by_key["week_all"].get("pct"), by_key["week_all"].get("resets_at"), now)
    models = []
    for e in entries:
        if e.get("key") != "week_model":
            continue
        w = window(e.get("pct"), e.get("resets_at"), now)
        if w is not None:
            models.append({"model": e.get("label"), "alias": model_alias(e.get("label")), "seven_day": w})
    tier, multiplier = plan(account)
    seen = remember(seen_path, account.label, [m["alias"] for m in models]) if seen_path else [m["alias"] for m in models]
    return {
        "account": account.label,
        "plan": tier,
        "quota_multiplier": multiplier,
        "five_hour": five,
        "seven_day": week,
        "models": models,
        "models_seen": seen,
        "usage_checked_at": _iso((usage_data or {}).get("fetched_at")),
    }


def _left(windows: list[dict | None]) -> float | None:
    """Percent left in the tightest known window; None when a window is at
    or above LIMIT_PCT. With no window known at all, 100."""
    known = [w for w in windows if w is not None]
    if any(w["used_pct"] >= LIMIT_PCT for w in known):
        return None
    return min((w["left_pct"] for w in known), default=100.0)


def options(entries: list[dict]) -> list[dict]:
    """Every account and model that may be suggested, the most room first."""
    found = []
    for e in entries:
        per_model = {m["alias"]: m["seven_day"] for m in e["models"]}
        for alias in MODELS:
            if alias in LISTED_ONLY and alias not in per_model and alias not in e.get("models_seen", []):
                continue
            left = _left([e["five_hour"], e["seven_day"], per_model.get(alias)])
            if left is None:
                continue
            found.append({
                "account": e["account"],
                "model": alias,
                "left_pct": left,
                "room": round(left * e["quota_multiplier"], 1),
            })
    found.sort(key=lambda o: (-o["room"], MODELS.index(o["model"])))
    return found


def suggest(entries: list[dict], start: str) -> dict | None:
    """The strongest model from `start` down that some account has room
    for, on the account with the most room for it. None when every
    account or model is at or above LIMIT_PCT."""
    ranked = options(entries)
    for alias in MODELS[MODELS.index(start):]:
        for o in ranked:
            if o["model"] == alias:
                return o
    return None


def best_model(entries: list[dict], account: str) -> str | None:
    """The strongest model `account` has room for; None when it has room
    for none."""
    fits = {o["model"] for o in options(entries) if o["account"] == account}
    return next((alias for alias in MODELS if alias in fits), None)


def payload(entries: list[dict]) -> dict:
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "limit_pct": LIMIT_PCT,
        "accounts": entries,
        "suggested": {"heavy": suggest(entries, MODELS[0]), "routine": suggest(entries, ROUTINE_FROM)},
        "options": options(entries),
    }


def _pct(w: dict | None) -> str:
    return f"{w['used_pct']:.0f}%" if w else "?"


def render(data: dict) -> str:
    lines = []
    for e in data["accounts"]:
        models = "  ".join(f"{m['model']} {_pct(m['seven_day'])}" for m in e["models"])
        lines.append(
            f"{e['account']} ({e['plan'] or 'plan unknown'}, x{e['quota_multiplier']}): "
            f"5h {_pct(e['five_hour'])}  7d {_pct(e['seven_day'])}" + (f"  {models}" if models else "")
        )
    for kind, s in data["suggested"].items():
        lines.append(f"{kind}: " + (f"--account {s['account']} --model {s['model']} ({s['left_pct']:.0f}% left)" if s else f"nothing under {LIMIT_PCT:.0f}%"))
    return "\n".join(lines)
