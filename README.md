# podbay

One terminal console over every local Claude Code session, across accounts.

Claude Code sessions multiply: one per repo, one per task, one per
subscription. podbay reads the live session registry and transcript tail of
every Claude Code config dir on the machine, joins in its own park/note
state, and maps each session to its iTerm2 tab, so one screen says what every
session is doing, which ones need you, how much of each account's quota is
left, and lets you jump to a session or send it a message without switching
tabs. Themed as a HAL 9000 ship console.

Read-only against Claude Code's own files. podbay writes only its own state
under `~/.local/state/podbay/` and, on request, text into an iTerm2 tab.

```
  STATE          AGE  CTX  MODEL      ACCT     DIR                  TITLE                        #
● ? needs you    42m  27%  Opus 5.5   personal Repositories/jeeves  Feature review (claude)      #5
● ? needs you     6m  21%  Fable 5.1  claude   Repositories/sora    Racing game (claude)         #6
  * working      25m  38%  Fable 5.1  claude   Repositories/jeeves  Home console (claude)        #2
  $ shell        now                           ~                    (-zsh)                       #1
```

## Requirements

macOS, iTerm2 (tab focus and send use `osascript`; on another terminal the
table still works and those two actions fail quietly), [`uv`](https://docs.astral.sh/uv/),
`jq`, Python 3.12 or newer (uv fetches one if missing).

## Install

```
git clone https://github.com/vesahyp/podbay.git
cd podbay
make install
```

`make install` does two things:

1. Symlinks `bin/podbay` into `~/.local/bin/podbay`. The launcher execs
   `uv run --project <checkout> podbay`, so uv creates the venv on first run.
2. Sets `statusLine` in `~/.claude/settings.json` and every
   `~/.claude-*/settings.json` to `sh <checkout>/statusline.sh`. The status
   line is the only place Claude Code exposes the context window and
   rate-limit figures, so this script renders a status line (directory, git
   branch, context, 5h and 7d usage) and first saves the status JSON as a
   snapshot under `~/.local/state/podbay/status/`, which is where podbay's
   CTX column and header quotas come from. If you want to keep your own
   status line, paste the snapshot block from the top of `statusline.sh`
   into it instead, and skip step 2.

Then `podbay` opens the TUI; `podbay list` prints the same rows as text.

```
podbay                 # the TUI
podbay --no-splash     # without the HAL startup and shutdown sequences
podbay list
podbay focus <sessionName|pid>
podbay send <sessionName|pid> <text...>
podbay inventory [--json|--table|--status] [--exclude NAME]...
```

Optional environment:

- `PODBAY_HOME_REPO=<name>`: the repo under `~/Repositories` you launch every
  session from. `o` then offers that directory by default, and a tool call
  that only reads there says nothing about where the work is, so that repo
  counts in the Repos column only when the session edits a file in it.
- `PODBAY_USER=<name>`: how HAL addresses you. Default: your login name.
- `PODBAY_NO_SPLASH=1`: same as `--no-splash`.
- `PODBAY_LOG_LEVEL=DEBUG`: more in `~/.local/state/podbay/podbay.log`.

## Accounts

Claude Code keeps everything per `CLAUDE_CONFIG_DIR`: sessions, transcripts,
login, quota. A second subscription is a second config dir, usually through
an alias such as

```
alias claude-personal='CLAUDE_CONFIG_DIR="$HOME/.claude-personal" command claude'
```

podbay discovers every `~/.claude` and `~/.claude-<label>` directory that
Claude has used (`podbay/accounts.py`) and labels sessions with the suffix:
`claude` for the default, `personal` for the one above.

- the **Acct** column and the detail pane name the account
- the header shows one quota group per account, labelled
- `R` (resume) runs `claude --resume` as the account whose transcript it is
- `o` (open) first asks which account, one row per config dir with its
  live session count, pre-selected to the highlighted row's account; Enter
  or the row's digit picks, then the directory prompt follows

With one config dir nothing changes: no label, no Acct value.

## Keys

- `Enter` focuses the selected session's iTerm2 tab. This is the only action
  that switches to iTerm2; `o` and `R` start Claude in a tab and leave you
  in podbay
- `p` parks (snoozes) the selected session: `+2h`, `+3d`, `today 14`,
  `tomorrow`, `tomorrow 9`, `fri 14`, `2026-09-12`, `2026-09-12 09:00`,
  `14:30`, `14`. A parked session sorts to the bottom until it is due
- `u` unparks
- `n` edits the note
- `t` toggles keyboard focus between the table and the transcript pane
  (arrow keys and PageUp/PageDown scroll it); `Escape` returns to the table
- `m` composes a message and types it into the selected session's tab
  without switching tabs (it queues behind Claude Code when busy)
- `/` filters the table by title and directory (fuzzy), and after three
  characters also by conversation text; `Escape` clears
- `space` selects a row, `c` clears the selection, `A` arranges the selected
  windows side by side on the external display (or this screen)
- `o` starts Claude: in the highlighted terminal when it is a plain shell,
  else in the next free terminal, else in a new window. With more than one
  account it asks which one first
- `R` resumes a past session: the ones live before the last restart first,
  then older history, searchable; `space` ticks several, `Enter` opens them
- `h` shows today's notification history
- `r` refreshes now
- `q` quits through the shutdown eye (again to skip it). When a background
  poll still runs a wait screen lists it; any key there force-quits

`podbay inventory` prints the same session data as JSON for another agent to
read (`--table`, `--status` for text), with each session's account and its
terminal number: iTerm's window number, with `.T` added for a session outside
its window's first tab (`7.2`). "terminal #N" means the session that
`podbay inventory` lists with tab N.

## Status derivation

The transcript is the primary signal, not the stale-prone session registry.
Each session's newest transcript record classifies it as `in_progress` (a
prompt or tool result awaiting the model, or an assistant turn that stopped
on a tool call) or `end_turn`.

- registry status `waiting`, not older than the newest transcript turn:
  **needs you**, Wait column `dlg`. Claude Code writes an AskUserQuestion or
  a permission-gated tool call to the transcript only after the dialog is
  answered, so while the dialog is up the registry status is the only signal
- `in_progress` for more than 10 minutes: **stalled** (`~`)
- `in_progress`: **working** (`*`)
- `end_turn`, or no transcript signal: parked / due / needs-you, using park
  state first, then the iTerm2 tab-title glyph, then the registry status
- a background subagent still `in_progress` (its own transcript under
  `<session-id>/subagents/`) counts as the session working
- `end_turn` with a background Bash command or Monitor still running:
  **watching** (`o`)
- a turn after the park's due time clears the park, so a resumed session goes
  back to needs-you instead of staying **due**
- no transcript file at all: **empty** (`-`), opened and never typed into
- an iTerm2 pane with no Claude session: **shell** (`$`), with its screen text
  in the right pane

A `●` in the first column marks a finished answer you have not looked at yet.
It clears when you focus the tab or move into the transcript pane.

Rows sort: due first, then needs-you and stalled (oldest idle first), then
working and watching, then empty, then shells, then parked (soonest first).

The **Repos** column lists the repos under `~/Repositories` a session has
touched with a tool call, starred when it edited a file there, with a leading
`⇄` when another live session works in the same repo.

## Header

The header carries each account's rate limits: `5H` and `7D` from the
status-line snapshots, plus one `7D <model>` group per model that
`claude -p /usage` reports (polled every 10 minutes per account, cached in
`~/.local/state/podbay/usage.json` and `usage-<label>.json`). Every `7D`
group ends with a straight-line projection to its reset: `~88% at reset`
when the quota lasts, `out 1d 5h early` when it runs out first. The pace is
measured on weekday time only, and the projection waits until four weekday
hours of the window have passed.

## State and logs

- `~/.local/state/podbay/state.json`: park, note and seen-at per session,
  plus the last working set for Resume. Entries for dead sessions are pruned
  after 14 days. Delete the directory to reset.
- `~/.local/state/podbay/status/*.json`: status-line snapshots.
- `~/.local/state/podbay/podbay.log`: the application log (rotating, 1 MB x 3):
  start, exit, every crash traceback and every error podbay recovers from.
  Textual's own crash output scrolls away with the next run, so read this
  first.
- `~/.local/state/podbay/notifications.log`: the toasts behind `h`.

## Development

```
make test
```

`podbay/voice.py` holds every string HAL says. `podbay/model.py` is the
status derivation, `podbay/sources.py` the file reads, `podbay/iterm.py` the
AppleScript, `podbay/app.py` the Textual TUI.

## License

MIT, see `LICENSE`.
