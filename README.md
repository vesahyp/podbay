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
podbay notify <text...>
podbay inventory [--json|--table|--status] [--exclude NAME]...
podbay excerpt <sessionName|pid|id> [--turns N]
podbay open <dir> [--account LABEL] [--name NAME] [--wait SECONDS] [first prompt...]
podbay close <sessionName|#N|title> [--force]
podbay board [--out PATH]
```

Settings live in `~/.config/podbay/config.json`, set from the command line
and read at the next start:

```
podbay config home-repo jeeves   # the repo under ~/Repositories you launch every session from
podbay config voice off          # HAL stops remarking on what changed (on | off; default on)
podbay config head-jeeves on     # keep a Head Jeeves session running and send it work (default off)
podbay config head-jeeves-account personal   # the account he runs as (default: the default account)
podbay config review-model sonnet   # the model Head Jeeves runs on (default: the account's own)
podbay config notify-command "~/bin/push --tag ''"   # what `podbay notify` runs; empty (default) means off
podbay config                    # show every setting
```

`podbay open` starts claude in a terminal sitting at an empty shell prompt
(one stuck on a half-typed line does not count), or in a new window. With a
home repo set the session starts there and its first prompt names `<dir>`.
The prompt reaches claude through a file under `~/.local/state/podbay/prompts/`,
so its length does not matter. It waits (180 s by default) until the session
is up, and otherwise exits 1 with the end of that terminal's screen. Each
launch is recorded in `~/.local/state/podbay/opened.json` with the session
that ran it and the first prompt, and `podbay inventory` shows the former as
`opened_by`.

`podbay close` ends a finished session (SIGTERM, as closing its terminal
would) and closes its iTerm2 tab, or its window when that tab was the only
one. A session that is working, watching a background task or stalled stays
open unless `--force`; Head Jeeves is never closed.

With a home repo set, `o` offers that directory by default, and a tool call
that only reads there says nothing about where the work is, so that repo
counts in the Repos column only when the session edits a file in it.

Optional environment:

- `PODBAY_HOME_REPO=<name>`: overrides the home repo for one run.
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
- `x` toggles Remote Control for the highlighted session by typing
  `/remote-control` into it; on, the session appears in the Claude mobile
  app and on claude.ai/code, and the RC column shows `⇅`
- `E` sends the highlighted session to Head Jeeves for its exit interview
  (see Head Jeeves); `v` shows the newest review of it
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
- `end_turn` while a subagent it started still runs: **working**; with a
  background Bash command or Monitor still running: **watching** (`o`)
- a turn after the park's due time clears the park, so a resumed session goes
  back to needs-you instead of staying **due**
- no transcript file at all: **empty** (`-`), opened and never typed into
- an iTerm2 pane with no Claude session: **shell** (`$`), with its screen text
  in the right pane

Only a window's first tab is listed. Tabs after it count as that session's
helper terminals (a login, a log tail) and stay out of the table.

The **RC** column shows `⇅` while Remote Control is on for a session (the
registry reports its claude.ai session id); the detail pane shows the link.

The **⚡** column marks a session whose last prompts read heated: a lexicon
over your newest typed prompts (swearing in English and Finnish, shouting,
`!?` clusters, the words of a third attempt such as "still" and "again"),
the newest prompt counting most, code spans and URLs left out. No model, no
tokens. Only your own words count: the first prompt of a session that
`podbay open` started, and anything a Claude session typed into another with
`podbay send`, are recorded at the source (`opened.json`, `sent.json`) and
left out. HAL remarks once when a session turns hot (see HAL speaks).

A `●` in the first column marks a finished answer you have not looked at yet.
It clears when you focus the tab or move into the transcript pane.

Rows sort: due first, then needs-you and stalled (oldest idle first), then
working and watching, then empty, then shells, then parked (soonest first).

The **Repos** column lists the repos under `~/Repositories` a session has
touched with a tool call, starred when it edited a file there, with a leading
`⇄` when another live session works in the same repo.

## HAL speaks

HAL remarks when something changed, never at random: a session finished and
waits for you, asked a question, stalled, or a park came due; an account's
five-hour window or week passed 85%; your prompts in a session turned
heated. One remark per event, none for a
session whose answer you have already seen. When nothing has needed you for
two hours he says so once. Remarks are toasts and go into the `h` history.
`podbay config voice off` silences him. He never speaks aloud.

## Head Jeeves

When a session goes south, the agent in it is the wrong one to ask why. Head
Jeeves is a standing Claude Code session of his own, named `head-jeeves`,
that reads the other sessions and says what he sees. podbay starts him from
the home repo when none is live (`podbay config head-jeeves on`, on the
account's default model unless `podbay config review-model` says otherwise)
and sends him work as prompts:

- the moment a session's prompts turn heated: `/head-jeeves checkup`, at
  most 150 words on what the friction is and the one sentence that gets the
  session back on track. If the agent is the problem and one sentence would
  fix it, he sends that sentence to the agent himself
- `E` on a row: `/head-jeeves exit`, the exit interview before you replace
  an agent: what was asked, where and when it went south, which of your
  prompts were ambiguous or short of a fact the agent needed (quoted), the
  agent's own failures, and a handover prompt for the next agent

He is also the operator you talk to from elsewhere. Run him as the account
that bridges at startup (`podbay config head-jeeves-account personal` with
`remoteControlAtStartup` on for that account) and his session is the one
the Claude app shows on your phone. podbay forwards him every event it
toasts (a session finished, asked, stalled, came due) and he reports it to
you in one line there; you can ask him what is going on, tell him to start
a session somewhere with a task (`podbay open`), or to pass a message to a
session (`podbay send`), and he does it and reports back.

`podbay board` writes the status board to
`~/.local/state/podbay/board.html`: one page that says, by project, what
needs a decision from you, what is ready for you to test (with its URL and
the steps), what finished today and what is in progress. The sessions
themselves, with terminal numbers, states, ages, context use and each
account's five-hour and weekly usage, sit in a collapsed machine room at
the bottom. The lines come from Head Jeeves, who writes them to
`~/.local/state/podbay/headlines.json` (format in his skill); a session
without one shows the first sentence of its recap. He publishes the page as
an artifact with the `comments` capability, so it costs him no context, and
you read it on the phone. Tapping a question or a session opens a composer;
what you type reaches him as a comment addressed `#6 sora: <text>`, he
passes it on with `podbay send` and answers in the thread.

### Pushes to your phone

podbay has no push transport of its own. Give it one with `podbay config
notify-command <command>`: a script, with any fixed arguments, that takes
the message as its last argument. `podbay notify "<text>"` then runs it
with the text appended, waits up to 30 seconds, and prints one line: `sent`,
or on stderr why not (exit 1). A failing or missing command is logged in
`podbay.log`, never a traceback. With no command set, nothing is sent and
`podbay notify` says so.

Head Jeeves uses it for two things only: a decision only you can make (the
question with its options) and shipped work ready for you to test (what to
test, with its full URL). One push per item, nothing for progress or
routine events, and none between 23:00 and 07:00 local time unless it is
urgent. The rules are in his skill.

His instructions are the skill in `skills/head-jeeves/`; `make install-skill
HOME_REPO=~/Repositories/jeeves` links it into the home repo's
`.claude/skills/`, since every session, his included, starts there. He reads
a session with `podbay excerpt <name>` (the last 80 turns as plain text) and
writes under `~/.local/state/podbay/reviews/`; podbay watches that directory,
HAL says when a file lands, `v` shows the newest one for the highlighted
session. His row has its own colour, sorts first, and is never scored.

## Header

The header carries each account's rate limits on one line, in the compact
form `claude 5H 36% ↻2h15m  7D 17% ↻1d7h →~17%  Fable 32% →~32%`:

- `5H` and `7D` come from the status-line snapshots; `↻` is the time until
  that window resets
- one `<model> N%` group per model that `claude -p /usage` reports, a weekly
  figure like `7D` (polled every 10 minutes per account, cached in
  `~/.local/state/podbay/usage.json` and `usage-<label>.json`)
- `→` is the straight-line projection to the reset: `→~88%` when the quota
  lasts, `→out 1d5h` when it runs out that long before the reset. The pace
  is measured on weekday time only, and the projection waits until four
  weekday hours of the window have passed. Green under 85%, yellow to 99%,
  red when it runs out

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
