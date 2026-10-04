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
make check      this repo is public: fails if a doc, the skill, a page or
                a test quotes or names the user (scripts/check-quotes)
make install    ~/.local/bin/podbay symlink + statusLine in every
                ~/.claude*/settings.json

make shots      the site's screenshots into site/img/ (Playwright)
make plan       terraform plan for infra/, saved to infra/tfplan
make apply      apply the saved plan
make deploy     sync site/ to the bucket and invalidate
make analytics  the traffic rollup now; cron runs it nightly at 07:30
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
  opened.py         the sessions `podbay open` started and who ran it, and
                      what agents typed into sessions through podbay, so
                      the mood gauge skips it; its own files, since the
                      TUI rewrites state.json
  mood.py           the heat lexicon behind the ⚡ column
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
scripts/shots.py    the site's screenshots: the real TUI and board over
                      made-up sessions, rendered by Playwright
site/               podbay.tienoo.com, static: index, 404, stats/ board,
                      tracker.js (clavesa's, vendored unmodified), t.gif
infra/              its Terraform: S3 + CloudFront + ACM + Route 53
analytics/          clavesa workspace: CloudFront logs to data/analytics.json
```

## The site

podbay.tienoo.com is one static page, no build step. `make deploy` syncs
`site/` with `--delete` but excludes `data/`, which the analytics pipeline
owns, and uploads `t.gif` as `no-store`. Commit before you deploy.

- The screenshots show made-up sessions only. Change the demo in
  `scripts/shots.py`, run `make shots`, commit the PNGs.
- The tienoo.com zone is read as a data source, never declared here.
- The Terraform state bucket is not in the repo: `make plan` passes it as
  `TFSTATE_BUCKET`, by default `<login>-backup-<account id>`.
- Tracking follows jeeves' `practices/web-tracking.md`. Mark a new link
  with `data-track="<surface>-<name>"`; it reaches `/stats/` with no
  pipeline change. `tracker.js` is never edited here: changes go to
  `clavesa-dev/web-tracker/` and are copied in.
- The clavesa workspace is local: never run `clavesa deploy` from
  `analytics/`. The nightly is `clavesa pipeline run podbay-traffic` on
  this machine, against a Delta warehouse under `.clavesa/warehouse/`.

## Hard rules

- Read-only against Claude Code's files. podbay writes only under
  `~/.local/state/podbay/` and, on request, text into an iTerm2 tab.
  `podbay close` is the one command that ends a session and closes a tab.
- `Enter` is the only action that switches to iTerm2. Open and resume start
  Claude in a tab and leave the user in podbay.
- Every user-facing string lives in `voice.py`.
- Nothing personal in the repo: no login names, no home paths, no repo
  names from one machine. Tests build paths from `Path.home()` and set
  the home base in `conftest.py`.
- Commit messages in English.
