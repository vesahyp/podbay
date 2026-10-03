# jeeves-podbay

A terminal pod bay for Vesa's many local Claude Code sessions. Reads the live
session registry (`~/.claude/sessions/*.json`) and each session's transcript
recap, joins in podbay's own park/note state, and maps sessions to iTerm2
tabs so you can jump straight to one, or send a message without switching to
it. Themed as a HAL 9000 ship console (see `podbay/voice.py` for every
user-facing string).

Read-only against Claude Code's own files (and against the transcripts it
tails for the conversation pane); the only things this tool writes are its
own state file and, on request, text into an iTerm2 tab via `write text`.

## Run

```
uv run --project <checkout>/tools/podbay podbay          # TUI
uv run --project <checkout>/tools/podbay podbay list     # plain text
uv run --project <checkout>/tools/podbay podbay focus <sessionName|pid>
uv run --project <checkout>/tools/podbay podbay send <sessionName|pid> <text...>
```

A launcher at `bin/podbay` execs the same `uv run` command and resolves the
project from its own location, so symlink it into `~/.local/bin` from any
checkout path.

The TUI opens on a HAL 9000 startup splash while the first session scan
runs behind it: the eye materialises over a dimmed copy of the terminal text
you were just looking at (read from iTerm2 before Textual takes the screen;
plain black elsewhere), the text dissolves away, the "open the pod bay doors"
exchange types out, then the eye dissolves and the main screen fades in from
black. Any key skips to the dissolve. `q` plays the reverse: the main screen
fades to black, the eye returns with HAL's "my mind is going" line and
dissolves, then the app exits; a second key exits at once. `podbay
--no-splash` (or `PODBAY_NO_SPLASH=1`) starts straight on the main screen and
quits instantly. `list`, `focus` and `send` never show either sequence.
The Park and Message prompts carry their own HAL glyphs: an amber dormant
eye for Park, a red open eye for Message.

## Setup on a new machine (agent runbook)

Everything below is safe to run unattended; nothing touches Claude Code's own
files except one key in `~/.claude/settings.json`.

Requirements: macOS, iTerm2 (tab focus and `m`/send use `osascript`; on any
other terminal the table still works and those two actions fail quietly),
`uv`, `jq` (used by the status line script), Python >= 3.12 (uv fetches it if
missing).

1. Clone the jeeves repo (any path; `<checkout>` below). Do not copy `.venv`
   or `.pytest_cache`; they are gitignored and uv recreates the venv on first
   run.
2. Launcher: `ln -sfn <checkout>/tools/podbay/bin/podbay ~/.local/bin/podbay`
   (make sure `~/.local/bin` is on PATH).
3. Status line snapshots (context window and rate-limit columns): set
   `statusLine` in `~/.claude/settings.json` to
   `{"type": "command", "command": "bash <checkout>/.claude/statusline-command.sh"}`.
   The script writes one JSON snapshot per session to
   `~/.local/state/podbay/status/` and then renders the normal status line.
   Skip this step if you want to keep an existing status line; podbay then
   shows those columns empty. The snapshot block at the top of the script is
   self-contained and can be pasted into another status line script instead.
4. Verify: `podbay list` prints the live sessions; `podbay` opens the TUI.
   `uv run --project <checkout>/tools/podbay pytest` runs the tests.

State this tool creates: `~/.local/state/podbay/state.json` (park/notes) and
`~/.local/state/podbay/status/*.json` (snapshots). Delete the directory to
reset.

## Logs

`~/.local/state/podbay/podbay.log` (rotating, 1 MB x 3) is the application
log: one line per start and exit, a full traceback for any crash of the TUI
or of a subcommand, and a warning for every error podbay recovers from (a
state save the OS refused, a failed history search, a coordinator tick that
raised). Textual prints a crash traceback to the terminal too, but the next
`podbay` in the same tab scrolls it away, so read the log first when the TUI
disappeared. `PODBAY_LOG_LEVEL=DEBUG` raises the level (default INFO).

## Keys (TUI)

- `Enter` — focus the selected session's iTerm2 tab. This is the only action
  that switches to iTerm2; `o` (open) and resume start Claude in the tab and
  leave you in podbay
- `p` — park (snooze) the selected session; accepts `+2h`, `+3d`, `today 14`,
  `tomorrow`, `tomorrow 9`, `fri 14`, `2026-09-12`, `2026-09-12 09:00`, `14:30`, `14`, or `pr` to park until one of the session's PRs changes state or gets a new comment or review (checked at each PR poll, capped at 14 days; the Parked column shows `PR change`, and the session turns due with a toast when it fires), or `slack` to park until someone else replies in one of the Slack threads the session posted in (checked at each Slack poll, capped at 14 days; the Parked column shows `Slack reply`). `slack <link>` adds the thread of a Slack message link to the session first (a link with `?thread_ts=` uses that thread), then parks on it
- `u` — unpark
- `n` — edit the note
- `t` — toggle keyboard focus between the session table and the transcript
  pane (arrow keys / PageUp / PageDown then scroll the transcript); `Escape`
  returns focus to the table
- `m` — compose a message and deliver it into the selected session's iTerm2
  tab via `write text`, without switching tabs (queues behind Claude Code if
  it's busy)
- `r` — refresh now
- `q` — quit through the shutdown eye (press again to skip it). When a background poll still runs (session scan, usage, PRs, tickets, Slack), a wait screen lists each one with how long it has run against its limit and a progress bar, and podbay exits when the last one ends. Any key there force-quits: podbay stops its own child processes and exits without waiting for the poll threads, so that poll writes no cache. No new poll starts once quit is pressed
- `h` — show today's notification history, newest first: every toast and every warning the collision watch typed into a session. The history is kept in `~/.local/state/podbay/notifications.log` (1 MB, one rolled-over copy).
- `g` — list pull requests (the top line says when the list was last fetched): new ones first (`✦`), then open by last update, then merged or closed in the last 24 hours. It opens on the selected session's PRs when it has any, and `Tab` switches to all PRs and back. Each row shows the session that opened it, repo#number, state, title and the last activity by someone else. `Enter` opens the PR in the browser in the session view and jumps to the iTerm2 tab of the owning session in the all view; `o` always opens the PR. Both mark it seen, `a` marks all seen, `Escape` or `g` closes
- `j` — list the DATAENG tickets your sessions work on, those with a gap first. It opens on the selected session's tickets when it has any, and `Tab` switches to all tickets and back. Each row shows the owning session, ticket, status, assignee, gaps and summary. `Enter` opens the ticket in the browser in the session view and jumps to the iTerm2 tab of the owning session in the all view; `o` always opens the ticket. `Escape` or `j` closes
- `s` — list the Slack threads your sessions posted in: new ones first (`✦`, someone else replied), then by latest activity. It opens on the selected session's threads when it has any, and `Tab` switches to all threads and back. Each row shows the owning session, channel, the first line of the thread, the reply count and the last reply by someone else. `Enter` opens the thread in the browser in the session view and jumps to the iTerm2 tab of the owning session in the all view; `o` always opens the thread. Both mark it seen, `a` marks all seen, `Escape` or `s` closes
- `C` — send `/coordinator conflicts <repo>` to the coordinator session for the first repo the selected session shares with another session, or `/coordinator status` when it shares none
- `x` — send `/coordinator route --clipboard` to the coordinator session; podbay never reads the clipboard itself, the skill does that with `pbpaste`

`C` or `x` start a coordinator session from jeeves when none exists. The coordinator row always renders in its own colour, whatever its status.

`podbay inventory` prints the same session data the coordinator skill reads (including each session's `prs`, with the state from the last poll, its `tickets`, with status and gaps from the last check, and its `slack_threads`, with title and reply figures from the last poll), without starting the TUI: `--json` (default), `--table`, `--status`, and repeatable `--exclude NAME` (name or short id) to leave sessions out.

Each session carries its terminal number (`tab` in `--json`, `TAB` in `--table`, `#N` in the TUI): iTerm's own window number, the one in the title bar, with `.T` added for a session outside its window's first tab (`7.2`). "terminal #N" or "#N" means the session `podbay inventory` lists with tab N: resolve the number there, then send to that session's name with SendMessage. Window numbers are unique, so a bare `#7` always resolves to one window.

## Pull requests

One `gh api graphql` call (30 s timeout) lists your open PRs (`author:@me`, not archived) and those closed in the last 24 hours. A background worker repeats it every 180 s and once at start; the result is cached in `~/.local/state/podbay/prs.json`, and a failed call is logged to `podbay.log` and leaves the header as it was. The header shows `PR 12 open · 2 ✓ · 1 ✗ · 1 merged · 3 new`: `✓` is review decision APPROVED among the open PRs, `✗` is CHANGES_REQUESTED, and zero counts are left out. GitHub leaves the review decision empty for some approved PRs whose merge is blocked by something else (such as unresolved conversations); podbay then takes it from each reviewer's latest approve or request-changes review (`latestOpinionatedReviews`, the reviewer ticks on the PR page). The `g` screen adds `· N unresolved` to an open PR's state when review threads are still unresolved.

A PR is **new** when anyone except you and bots (`[bot]` logins, bot accounts, logins ending `-bot` or `_bot`) approved it, requested changes, or commented (review, PR comment or review-thread comment) after the PR's `seen_at`. `seen_at` is kept per PR in `state.json`: it starts at the moment podbay first sees the PR, so existing history is not new, and it moves when you jump to its session or open the PR from the `g` screen or press `a` there. Entries for PRs that no poll has listed for 14 days are dropped. When a PR turns new, a toast names who did what; it lands in the `h` history too. The first poll after start sets the baseline and shows no toast.

A session is linked to a PR when its transcript holds a Bash call with `gh pr create` and the tool result prints a PR URL, or a `pr-link` record, which Claude Code writes for a PR the session or one of its subagents created or pushed to. Only your own PRs show, because only those are polled. The transcript is read once and then only its new lines, so the check is cheap. The detail pane lists the session's PRs with a state glyph and a `✦` when new.

## Tickets

Podbay checks that every DATAENG ticket a session works on is assigned to you, in the open sprint and has Story Points. The header shows `TIX 5 · 2 ⚠` (worked tickets, tickets with a gap; the `⚠` part is amber and left out at zero, and the whole group is left out when no ticket is worked). The detail pane lists the session's tickets with gap markers such as `DATAENG-3120 · no sprint · no pts`.

A session works a ticket when a DATAENG key appears in the title, body or branch name of one of its PRs, in its git branch name, or in one of its Jira write calls: a Bash call with `acli jira workitem` and `transition`, `comment create`, `edit`, `assign` or `create` (key from `--key`), or an Atlassian MCP call to `transitionJiraIssue`, `editJiraIssue`, `addCommentToJiraIssue` or `createJiraIssue` (key from `issueIdOrKey`). For a create call the key comes from the tool result. A key in plain prose does not count. A key that several sessions touched belongs to the session with the newest Jira write; a branch or PR mention ranks below any write.

For the union of worked keys, five `acli jira workitem search` queries run in a background worker every 300 s, and at once when the set of worked keys changes (a new ticket does not wait for the interval). The base query lists the keys whose status category is In Progress, leaving out Epics (they never go into a sprint), so a ticket a session only filed or commented on for team pickup is left alone; the four others add the predicates `sprint not in openSprints() OR sprint is EMPTY`, `cf[10150] is EMPTY`, `assignee is EMPTY` and `assignee is not EMPTY AND assignee != currentUser()`. A key that no longer exists makes the JQL fail: the keys are then tried one by one and the failing ones are dropped. The result is cached in `~/.local/state/podbay/tickets.json`; a failed check is logged and leaves the header as it was. The check only runs in the real TUI, not in `podbay list` or `inventory`, which read the cache.

Fixes, each once per ticket (recorded in `state.json`, so a restart does not repeat them):

- unassigned: podbay runs `acli jira workitem edit --key K --assignee @me --yes` itself and shows a toast `assigned DATAENG-N to you` (also in the `h` history)
- assigned to someone else: flagged only, never reassigned
- not in the open sprint, or no Story Points: one message is typed into the owning session, through the same path as the collision warnings (it queues behind Claude Code when the session is busy). The message is marked as sent only after it was typed, so a session with no tab is tried again at the next check

## Slack threads

Podbay tracks the Slack threads your sessions posted in and shows when someone else replied. The header shows `SLK 4 · 1 new` (tracked threads, threads with a new reply; the `new` part is amber and left out at zero, and the whole group is left out until a poll has succeeded or when no thread is tracked). The detail pane lists the session's threads with the channel id, the first line of the thread, the reply count and a `✦` when new.

A session is linked to a thread when its transcript holds a successful `slack_send_message` call (any MCP server prefix; a draft does not count). A call with `thread_ts` is a reply in that thread, a call without it starts a thread. An error result, or a result without a message ts, links nothing. The transcript is read once and then only its new lines. A thread added with `p` then `slack <link>` is stored per session in `state.json`, so it survives a restart.

One headless Claude run (Haiku, 120 s timeout) reads all tracked threads with the Slack `slack_read_thread` tool, one call each, in parallel. The prompt goes on stdin, and every other tool is denied: `--disallowedTools` carries a built-in list of MCP servers plus every other tool of the Slack server, so the run can read and never post. A run without that list costs about 150k tokens, with it about 12k. The init event of each run lists the tools the run loaded; any tool beyond the read tool adds its server (or, for a Slack tool, its name) to the deny list, which is kept in the cache and logged as a warning.

A background worker runs the poll every 300 s, and at once when the set of tracked threads changes. It is skipped when no live session has a thread. Only the real TUI polls; `podbay list` and `inventory` read the cache. The result is cached in `~/.local/state/podbay/slack.json`; a failed run is logged to `podbay.log` and leaves the header as it was, and a thread whose read failed keeps its last data. The text of each thread is parsed with regexes, with no model involved. Your own Slack user id is learned from the author of any message the sessions posted, and kept in the cache as `viewer`.

A thread is **new** when anyone except you and bots (no author id, or an id starting with `B`) replied after the thread's `seen_at`. `seen_at` is kept per thread in `state.json` like the PR one: it starts when podbay first sees the thread, and it moves when you focus the session's tab, open the thread from the `s` screen, or press `a` there. Entries for threads that no poll has listed for 14 days are dropped. When a thread turns new, a toast names who replied and the session; it lands in the `h` history too. The first poll after start sets the baseline and shows no toast.

Park on `slack` stores, for each thread of the session, the timestamp of the newest reply by someone else, or the park time when there is none. The park ends when a reply with a newer timestamp shows up, so a reply that was already there never ends it.

## Hook warnings

Claude Code prints a hook's message only on the screen of the session that started, so a warning there is easy to miss. A hook can also drop a JSON file into `~/.local/state/podbay/warnings/` (`{"title": "...", "message": "...", "fix": "..."}`; the file name is the warning's id) and delete it when its check is clean again. Podbay reads the directory at every session scan: each warning shows as a red `⚠ <title>` group in the header and gets one toast with the message and fix (also in the `h` history); a changed message toasts again. jeeves' `update-dataeng-tools.sh` SessionStart hook uses this for `skills stale`, when the data-engineering-tools checkout is not on clean master and its skills differ from origin/master.

## Status derivation

The transcript is the primary signal, not the (stale-prone) session
registry. Each session's newest transcript record classifies it as
`in_progress` (a prompt or tool result awaiting the model, or an assistant
turn that stopped on a tool call) or `end_turn` (the assistant finished).

- registry status `waiting`, not older than the newest transcript turn →
  **needs you**, Wait column `dlg`. Claude Code writes an AskUserQuestion or
  a permission-gated tool call to the transcript only after the dialog is
  answered, so while the dialog is up the registry status is the only signal.
- `in_progress` for more than 10 minutes → **stalled** (`?`, likely a hung
  session)
- `in_progress` → **working** (`*`)
- `end_turn`, or no transcript signal at all → falls through to parked / due
  / needs-you, using park state first, then the iTerm2 tab-title glyph, then
  (last resort) the registry status
- a background subagent still `in_progress` (its own transcript under
  `<session-id>/subagents/`, modified since the main turn ended) counts as
  the session working, since a background Agent call ends the main turn
- `end_turn` with a background Bash command or Monitor still running → **watching** (`o`, dark green). A task counts from its launch result until its `<task-notification>` or TaskStop, only if this process started it, and for at most its Monitor timeout or 6 hours
- a turn after the park's due time clears the park automatically, so a
  resumed session goes back to needs-you instead of staying **due**

- no transcript file at all → **empty** (`-`, dim blue): a session opened
  and never typed into, waiting to be used

A `●` in the first column marks a session with a finished answer you have
not looked at yet. It clears when you focus the session's tab (Enter, or
selecting the tab in iTerm2 itself) or move into the chat-log pane in
podbay; the header counts them as UNREAD.

The **PR** column shows one glyph per PR the session opened, most actionable first: `✗` changes requested (red), `✓` approved (green), then `○` open and `◌` draft waiting on a reviewer, and `⊕` merged (dim). `+` means more than three PRs. A separate amber `✦` after the circles means someone commented or reviewed since you last looked.

The **Tix** column shows the number of the session's first In Progress ticket: amber with `⚠` when any of its tickets misses the sprint, Story Points or your assignment, dim when all are in order; `+` means more tickets.

The **Slk** column shows how many Slack threads the session posted in: followed by an amber `✦` when someone else replied since you last looked.

Rows are sorted: due (parking expired) first, then needs-you and stalled
together (oldest idle first), then working and watching, then empty, then parked
(soonest first).

## Layout (TUI)

The session table takes the top ~45% of the screen. Below it, the left pane
is the existing session detail; the right pane shows the highlighted
session's recent conversation (last 40 entries), re-read only when the
transcript file's size or mtime changes, and auto-scrolled to the bottom
unless you've scrolled up yourself.

The header bar carries the account rate limits: `5H` and `7D` from the
status-line snapshots, plus one `7D <model>` group per model that `claude -p
/usage` reports (polled every 10 minutes, cached in
`~/.local/state/podbay/usage.json`, which the status-line script also reads).
Every `7D` group ends with a straight-line projection from the window's start
to its reset: `~88% at reset` when the quota lasts, `out 1d 5h early` when it
runs out first (the margin is calendar time before the reset). The pace is
measured on weekday time only, Monday to Friday on the local clock, because
no quota is spent at the weekend: a window that has just crossed a weekend is
not diluted by it, and a weekend still ahead does not inflate the figure. The
projection is skipped until four weekday hours of the window have passed,
where it would swing with every prompt.

## Refresh

The session gather (registry + transcripts + the iTerm2 AppleScript listing)
runs in a background worker thread, not on the UI loop, so a slow scan never
freezes the TUI. The header sub-title shows the last scan time; its glyph
flips from `●` to `◌` while a scan is in flight. Both the periodic timer and `r`
trigger the same worker; a scan already in flight makes either a no-op.

## State

Podbay's own state (per-session `parked_until` and `note`) lives at
`~/.local/state/podbay/state.json`, written atomically. Entries for
sessions that are no longer live are pruned after 14 days.

The Slack thread cache is `~/.local/state/podbay/slack.json` (threads, your Slack user id, the deny list of the headless run).

## Tests

```
uv run --project <checkout>/tools/podbay pytest
```
