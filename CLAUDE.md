# podbay

One terminal console over every local Claude Code session, across accounts.
`README.md` is the user page: what it does, install, keys. This file is for
agents working on the code.

## Commands

```
make run        the TUI (uv run --project . podbay)
make list       the same rows as plain text
make test       pytest, about a minute; the app tests mount the real TUI
                headless and read the real session registry read-only
make install    ~/.local/bin/podbay symlink + statusLine in every
                ~/.claude*/settings.json
```

## Layout

```
bin/podbay          launcher: execs uv run from its own location
statusline.sh       the Claude Code status line for every account; saves
                      the status JSON as a snapshot podbay reads
podbay/
  app.py            the Textual TUI and the CLI subcommands
  accounts.py       one Account per ~/.claude and ~/.claude-<label> dir
  sources.py        every read of Claude Code's files: registry,
                      transcripts, snapshots; the iTerm2 join
  model.py          Session and the status derivation; repo groups
  iterm.py          AppleScript: list tabs, focus, send text, open window
  state.py          park/note/seen state and the resume snapshot;
                      parse_when for park expressions
  usage.py          `claude -p /usage` per account, cached
  history.py        past sessions to resume, with transcript search
  inventory.py      the JSON/table/status views for other agents
  voice.py          every string HAL says; the header segments
  splash.py         the HAL eye: startup and shutdown sequences
  glyphs.py         the small eyes on the Park and Message prompts
  layout.py         window arrangement geometry (pure)
  screens.py        display bounds from CoreGraphics
  logs.py           the rotating application log
  notifications.py  the history behind `h`
tests/              pytest; conftest pins one account and a home base
```

## Hard rules

- Read-only against Claude Code's files. podbay writes only under
  `~/.local/state/podbay/` and, on request, text into an iTerm2 tab.
- `Enter` is the only action that switches to iTerm2. Open and resume start
  Claude in a tab and leave the user in podbay.
- Every user-facing string lives in `voice.py`.
- Nothing personal in the repo: no login names, no home paths, no repo
  names from one machine. Tests build paths from `Path.home()` and set
  the home base in `conftest.py`.
- Commit messages in English.
