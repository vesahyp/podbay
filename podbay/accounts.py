"""The Claude Code accounts on this machine, one per config directory.

Claude Code keeps everything per CLAUDE_CONFIG_DIR: the session registry,
the transcripts, the login. The default is ~/.claude; the `claude-personal`
alias points CLAUDE_CONFIG_DIR at ~/.claude-personal. Podbay reads every
such directory, so one console shows the sessions of both subscriptions
and tells them apart by the label, which is the suffix of the directory
name ("claude" for the default one, "personal" for ~/.claude-personal).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

DEFAULT_DIRNAME = ".claude"
DEFAULT_LABEL = "claude"


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
