---
adr: 2
title: app.podbay.tienoo.com is the only phone app; jeeves.tienoo.com and the artifact board are removed now
date: 2026-11-08
status: Accepted
deciders: owner
---

# 0002. One phone app

Supersedes two parts of [0001](0001-podbay-mobile.md): "`runs.html` stays
on jeeves.tienoo.com" (Architecture, the push paragraph) and the one month
fallback of the artifact board (build slice 8 and choice 4). The rest of
0001 stays as written.

## Context

After slice 7 of 0001 the phone had three surfaces for one job:

- the app at `https://app.podbay.tienoo.com`: board, machine, nightly
  cells, thread, stats;
- `https://jeeves.tienoo.com`: the push sign-up page and its push
  subscription, and `runs.html`, the clavesa run timings chart;
- the artifact board on claude.ai, published by Head Jeeves, with the
  comment sweep every 5 minutes behind it.

The app's Nightly tab had only a summary of the runs chart: minutes per
night by workspace for 7 nights, and a button to `runs.html`. The phone
had two push subscriptions on two origins, so a push could arrive twice or
open the wrong page. The owner decided on 2026-11-08 to keep one app for
all of it, at once.

## Decision

### Runs

The app shows everything `runs.html` showed, on the Nightly tab:

- **All pipelines, per night.** The last 30 nights, one bar per night,
  stacked by phase (startup, metastore, spark, reads, transform, writes,
  stats, nodes for runs without phase data, bookkeeping), a red mark on a
  night with a failed run, the run count and failures per night.
- **One chart per pipeline**, longest latest run first: the last 30 runs,
  stacked by phase, the latest run time and the median of the 7 runs
  before it.
- A tap on a bar shows that night's or run's breakdown in minutes and
  percent below the chart. Each chart has its table twin.

The phase split is computed on the Mac in `podbay/snapshot.py`, from the
same fields and rules `runs.html` used, so the snapshot carries about
20 KB of numbers in place of the 800 KB raw history.

**The data path is the Mac snapshot.** jeeves `scripts/runs-export` runs
at 10:45 from cron, as `runs-publish` did, and writes
`jeeves/state/runs.json` on the Mac. It no longer uploads anything. podbay
reads that file (`podbay config mobile-runs-file`) when its modification
time changes and puts the `runs` part in `state.json`. The history stays
private behind the app's login, like the rest of the state; before, it was
a public object.

### Push

- The app's subscription is the only one. The jeeves.tienoo.com sign-up
  page goes away, and its subscription comes out of `/jeeves/push`.
- jeeves `scripts/notify` and `jeeves_push.py` stay the one send point.
  `jeeves_push.notify` makes every link absolute on the app origin: a
  relative link such as the default `/` becomes
  `https://app.podbay.tienoo.com/`, and a link to another site is kept in
  the body and the tap opens the app's board. The app's service worker
  already opens only links on its own origin.
- `podbay notify` links to the app always. The published board URL is no
  longer a fallback.

### jeeves.tienoo.com

The site is removed completely, with no redirect: jeeves `site/`, `make
site-deploy` and `make site-test`, and through Terraform the S3 bucket,
the CloudFront distribution, the certificate and the DNS records. jeeves
`infra/` keeps only what is not the site: the `jeeves-push` SNS topic and
its Lambda (`alarmpush.tf`) and the Bedrock budget (`budget.tf`).

### The artifact board

Slice 8 of 0001 is done now, without the month of fallback:

- The comment sweep is removed from the podbay loop, with its config key.
- The head-jeeves skill no longer publishes the board, reads artifact
  comments or answers them. It answers only `/head-jeeves phone` events
  from the app.
- The board page loses its composer code. `podbay board` stays as the
  local HTML view of the same lines.

## Consequences

- One place on the phone for the board, the machine, the nightly runs,
  the run timings, the thread and the pushes.
- No fallback: when the app or its API is down, the phone has nothing
  until it is fixed. The board on the Mac (`podbay board`, the TUI) is the
  only other view.
- Until the phone turns on notifications in the app, no push reaches it:
  the old subscription is removed in the same change. The phone also
  keeps the old origin's notification permission until it is turned off
  by hand in iOS Settings.
- The run timings are as old as the last 10:45 export, and the app shows
  the export time. Before, the same was true of `runs.html`.
- An old jeeves.tienoo.com bookmark, Home Screen icon or push link now
  fails to load. The old Home Screen icon must be deleted by hand.
- A public run history page is gone. Nothing linked to it except the app.
