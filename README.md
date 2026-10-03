# podbay

One console over every Claude Code session on this machine, across both
subscriptions. It reads each account's live session registry
(`~/.claude/sessions/*.json`, `~/.claude-personal/sessions/*.json`) and each
session's transcript tail, joins in podbay's own park/note state, and maps
sessions to iTerm2 tabs so you can jump to one, or send it a message without
switching to it. Themed as a HAL 9000 ship console (`podbay/voice.py` holds
every user-facing string).

Read-only against Claude Code's own files. The only things this tool writes
are its own state under `~/.local/state/podbay/` and, on request, text into
an iTerm2 tab via `write text`.

Forked from the podbay used at work, minus the parts that only made sense
there (pull request, Jira and Slack tracking, the coordinator session).

## Run

```
make podbay            # from the jeeves root: the TUI
make podbay-list       # the same rows as plain text
podbay                 # anywhere, after make podbay-install
podbay list
podbay focus <sessionName|pid>
podbay send <sessionName|pid> <text...>
podbay inventory [--json|--table|--status] [--exclude NAME]...
```

`bin/podbay` execs `uv run --project <this dir> podbay`, resolving the
project from its own location, so the `~/.local/bin/podbay` symlink works
from any checkout path. `podbay --no-splash` (or `PODBAY_NO_SPLASH=1`) skips
the HAL startup and shutdown sequences.

## Setup

`make podbay-install` from the jeeves root does both steps:

1. `ln -sfn <jeeves>/tools/podbay/bin/podbay ~/.local/bin/podbay`
2. Sets `statusLine` in `~/.claude/settings.json` and every
   `~/.claude-*/settings.json` to `sh <jeeves>/tools/podbay/statusline.sh`.
   That script renders the status line as before (with an `[account]`
   prefix for every account but the default) and first saves the status
   JSON as a snapshot under `~/.local/state/podbay/status/`, which is where
   podbay's CTX column and the header quotas come from. Without it those
   stay empty.

Requirements: macOS, iTerm2 (tab focus and send use `osascript`; on another
terminal the table still works and those actions fail quietly), `uv`, `jq`,
Python 3.12 or newer (uv fetches one if missing). `uv` creates the venv on
first run.

## Accounts

Claude Code keeps everything per `CLAUDE_CONFIG_DIR`: sessions, transcripts,
login, quota. podbay discovers every `~/.claude` and `~/.claude-<label>`
directory that Claude has used (`podbay/accounts.py`) and labels sessions
with the suffix: `claude` for the default, `personal` for the
`claude-personal` alias.

- the **Acct** column and the detail pane name the account
- the header shows one quota group per account, labelled
- `R` (resume) runs `claude --resume` as the account whose transcript it is
- `o` (open) takes the account as a leading `@label` in the directory
  prompt, pre-filled from the highlighted row: `@personal ~/Repositories/x`

## Keys (TUI)

- `Enter` focuses the selected session's iTerm2 tab. This is the only action
  that switches to iTerm2; `o` and `R` start Claude in a tab and leave you
  in podbay
- `p` parks (snoozes) the selected session: `+2h`, `+3d`, `today 14`,
  `tomorrow`, `tomorrow 9`, `fri 14`, `2026-09-12`, `2026-09-12 09:00`,
  `14:30`, `14`
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
  else in the next free terminal, else in a new window
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
`⇄` when another live session works in the same repo. jeeves counts only
when edited, because every session starts there.

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
  first. `PODBAY_LOG_LEVEL=DEBUG` raises the level.
- `~/.local/state/podbay/notifications.log`: the toasts behind `h`.

## Tests

```
make podbay-test
```
