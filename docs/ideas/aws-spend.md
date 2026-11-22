# Idea: AWS spend in the phone app

Status: idea, nothing built. 2026-11-22.

The request: "figure out a way to get podbay mobile to show aws account spend
on a daily level/graphs with some way to do something like the cost explorer
ie: show/remove services etc. before implementing an estimate of how much this
feature would cost monthly."

**Recommendation: refresh once a day.** A jeeves cron job runs the existing
`make aws-costs` each morning, podbay puts its `report.json` into the state
snapshot as a `spend` part, and the app draws everything client side. Running
cost: about **2.20 USD a month**, all of it Cost Explorer API calls. The app
path adds nothing measurable. Hourly refresh costs about **53 USD a month**
and shows no newer data, because AWS updates cost data only a few times a day.

## Data path

```
 jeeves crontab, 10:50 daily          podbay screen, every 60 s          phone
 ───────────────────────────          ────────────────────────          ─────
 cronrun aws-costs                     podbay snapshot
   make aws-costs                        reads report.json ──▶ state.json (gzip, S3)
     Cost Explorer, 6 calls                as the "spend" part        │
     (cache: state/aws-costs/raw/)                                    ▼
   ──▶ state/aws-costs/report.json                        GET /state (existing Lambda)
                                                                      │
                                                          Spend tab: charts, toggles
```

- **No new fetcher.** `jeeves/scripts/aws-costs` already reads Cost
  Explorer DAILY for urbangreen, webbaa and personal, keeps the raw answers
  per month in `jeeves/state/aws-costs/raw/`, and writes
  `state/aws-costs/report.json`. Each account in that file already has what
  the app needs: `chart.days`, `chart.series` (running cost per service per
  day), `chart.band` (the normal level), `chart.oneoff`, `chart.tax`,
  `anomalies`, `normal_per_day`, `last_month`, `this_month`.
- **No new route, bucket or Lambda.** podbay reads the file the same way it
  reads `runs.json` (`snapshot.read_runs_json`, `podbay config
  mobile-runs-file`): a new `podbay config mobile-spend-file` and a
  `spend_part()` in `podbay/snapshot.py`. The snapshot is already uploaded
  only when it changes, and the spend part changes once a day.
- **Size.** Service level, 105 days, all three accounts: 32 KB of JSON,
  3.5 KB gzipped (measured on the 2026-11-15 cache). Usage types stay out of
  the snapshot. They are 5 to 10 times bigger and the anomaly lines already
  name them.
- **Schedule.** `aws-costs` is not in the crontab today. Add one line to
  `jeeves/crontab`: `cronrun aws-costs make aws-costs` at 10:50, after
  `runs-export`. A failure pushes through `cronrun` like every other job.
- **Each account shows its own data age** (the `fetched` time of its newest
  raw file). The personal profile signs in with `aws login`; when that login
  has expired the script exits 2 and the other two accounts still update.
  I am unsure how long that login lasts unattended; if it is shorter than a
  day, personal goes stale until Vesa signs in again, and the tab shows it
  amber.

## How often it refreshes

Once a day. The script's cache already allows only one fetch of the current
month per day (`fresh()` in `scripts/aws-costs`), so a manual `make
aws-costs` by Head Jeeves on the same day costs nothing extra. AWS updates
Cost Explorer data at least once every 24 hours, so a faster refresh mostly
fetches the same numbers again. Yesterday is complete by the morning run;
today is always partial, and the chart draws today's bar hatched.

## The UI

A fifth tab, **Spend**. Charts only, no tables of totals.

1. **Header tiles**: normal daily cost per account and their sum, from
   `normal_per_day`. A tap on a tile picks that account.
2. **Account switch**: urbangreen, webbaa, personal, All. All stacks by
   account; a single account stacks by service.
3. **Range chips**: 7, 30, 90 days. A drag across the chart picks a custom
   range.
4. **Daily stacked bar chart.** One bar per day, one colour per service.
   The normal level (`band`) as a line over the bars. Anomaly days get a
   marker above the bar. One-offs, credits and tax are off by default, and
   a chip turns them on as separate hatched segments.
5. **Service chips under the chart**, sorted by cost in the range. Tap to
   hide or show a service, long press for "only this". The axis rescales to
   what is shown. The top 8 services get their own colour; the rest are
   "Other", which also expands into chips. A service keeps the same colour in
   every account. The chosen account, range and hidden services are kept in
   `localStorage`.
6. **Tap a bar**: a sheet with that day's cost as horizontal bars per
   service, the anomaly lines for that day, and a reply button that sends the
   day to Head Jeeves like any other line ("why was CloudFront high on
   11-04?").

Tracking: the tab, the account and the range chip go into the beacon; no
amounts and no service names.

## Monthly running cost

| Item | Daily refresh | Hourly refresh |
|---|---|---|
| Cost Explorer calls, current month (2 per account per run) | 6 x 30 = 180 | 6 x 24 x 30 = 4 320 |
| Cost Explorer calls, last month until it is final (days 1 to 7) | 6 x 7 = 42 | 6 x 24 x 7 = 1 008 |
| Calls a month | 222 | 5 328 |
| **Cost Explorer at 0.01 USD a call** | **2.22 USD** | **53.28 USD** |
| S3 PUT for state.json | 0 extra | 0 extra |
| API Gateway, Lambda requests | 0 extra | 0 extra |
| Data transfer, +3.5 KB per state poll | about 25 MB, free tier | same |
| **Total** | **about 2.20 USD** | **about 53 USD** |

How the numbers are made:

- **Cost Explorer API: 0.01 USD per paginated request.**
  <https://aws.amazon.com/aws-cost-management/aws-cost-explorer/pricing/>.
  The script makes two calls per account per month it fetches (`record`
  and `usage`), so 6 calls per run on a warm cache. Every cached answer so
  far has one result per day of the month, which is one page. If an answer
  ever needs a second page, that page costs 0.01 USD too; at worst this
  doubles the figure to about 4.40 USD.
- **Hourly would also need a code change**: `fresh()` allows one fetch a
  day, so an hourly cron alone makes 0 extra calls and shows nothing new.
- **S3**: the snapshot is already put only when it changes, at most once a
  minute. The spend part changes once a day, so it adds no PUTs.
  <https://aws.amazon.com/s3/pricing/>.
- **API Gateway HTTP API and Lambda**: the app already polls `GET /state`
  every 30 s while it is open. No new requests; 3.5 KB more per response
  adds no measurable Lambda time.
  <https://aws.amazon.com/api-gateway/pricing/>,
  <https://aws.amazon.com/lambda/pricing/>.
- **Data transfer**: 2 hours a day with the app open is 240 polls, 0.84 MB
  a day, about 25 MB a month. The first 100 GB a month out to the internet
  is free. <https://aws.amazon.com/ec2/pricing/on-demand/#Data_Transfer>.
- Cost Explorer also sells hourly granularity, priced per usage record. It
  is not needed for a daily chart and is not part of this estimate.

## Slices, when built

1. jeeves: the crontab line for `aws-costs`. Commit, `make crontab-install`.
2. podbay: `mobile-spend-file` config, `spend_part()` with tests, in the
   snapshot.
3. podbay: the Spend tab in `mobile/app/` with the mock and `make
   mobile-shots`.
4. ADR 0001: the fifth tab and the `spend` part, as an amendment.
