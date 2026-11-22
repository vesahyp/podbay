# AWS costs in the phone app

Shipped 2026-11-22. The phone app shows the AWS running cost of the home
repo's accounts as charts, from a report that a cron job makes once a month.
Nobody has to sign in to the AWS console to see what the accounts cost.

## Data path

```
 home repo crontab, the 3rd, 19:10     podbay screen, every 60 s          phone
 ─────────────────────────────────     ────────────────────────          ─────
 cronrun aws-costs                      podbay snapshot
   make aws-costs                         reads report.json ──▶ state.json (gzip, S3)
     Cost Explorer, 12 calls                as the "spend" part        │
     (cache: state/aws-costs/raw/)                                     ▼
   ──▶ state/aws-costs/report.json                         GET /state (existing Lambda)
                                                                       │
                                                  Machine tab ▸ AWS costs card ▸ Costs screen
```

- **The fetcher is the home repo's `scripts/aws-costs`** (its skill is
  `.claude/skills/aws-costs/SKILL.md` there). It reads Cost Explorer DAILY
  per account, keeps the raw answers per month, and writes
  `state/aws-costs/report.json`. podbay does not call AWS for this.
- **Schedule: the 3rd of each month at 19:10.** Cost Explorer's daily
  figures for a month settle a day or two after it ends, so a run on the
  1st would show last month unfinished. A missed run is reported by the
  home repo's `cron-digest` the next morning, and a failed run pushes
  through `cronrun`.
- **podbay reads the file like `runs.json`**: `podbay config
  mobile-spend-file <path to report.json>`, then `spend_part()` in
  `podbay/snapshot.py` puts a smaller copy in the snapshot. No new route,
  bucket or Lambda.
- **Size.** Service level, 90 days, three accounts: about 24 KB of JSON,
  under 3 KB gzipped. Usage types, domains and one-off lines stay out of the
  snapshot; the anomaly lines name the usage types.
- **A failed account keeps its last good data.** The report carries
  `fetched` (when Cost Explorer was read) per account, and `stale` (the
  error) when the last run could not read it. The app marks that account
  stale and says from when its figures are.

## The Costs screen

Opened from the **AWS costs** card on the Machine tab, the way the app's
own stats open. No fifth tab.

1. **Run date** at the top, with its age in days. The Machine card turns
   amber when the newest run is more than 35 days old or an account is
   stale.
2. **One tile per account**: the normal daily cost (median of the last 14
   days, anomalies left out) and last month's running cost. The tile is
   the account switch.
3. **Month totals** of the chosen account: the last full month (with the
   part above normal) and the month to date.
4. **Daily stacked bar chart**, one segment per service, the normal level
   as a dashed line, a red mark on a day above normal. **30 d / 90 d**
   picks the range.
5. **Service chips** under the chart, sorted by cost in the range, are the
   legend: a tap hides or shows a service and the axis rescales. A service
   keeps one colour in every account (the 8 that cost most over all
   accounts get the categorical slots, the rest are grey).
6. **Tap a bar**: that day's services as bars, its anomaly lines, and
   "Ask Head Jeeves about this day", which opens the reply sheet on the
   Machine line with the account and day filled in.
7. **As a table**: the same days and services in rows.

The account, the range and the hidden services stay in `localStorage`.
Tracking: the screen open, the account index, the range, a chip turned on
or off and a bar tap; no amounts and no service names.

## Monthly running cost

| Item | Per month |
|---|---|
| Cost Explorer calls: the closed month and the new month's first 2 days, 2 calls each, 3 accounts | 12 |
| **Cost Explorer at 0.01 USD a call** | **0.12 USD** |
| S3 PUT, API Gateway, Lambda, data transfer for the spend part | 0 extra |
| **Total** | **about 0.12 USD** |

- **Cost Explorer API: 0.01 USD per paginated request.**
  <https://aws.amazon.com/aws-cost-management/aws-cost-explorer/pricing/>.
  A closed month is final in the cache once it was fetched on or after the
  3rd of the next month, so older months cost nothing again. Every answer
  so far fits on one page; a second page would cost 0.01 USD more.
- **A run by hand** (Head Jeeves, when asked for newer figures) costs 0.06
  USD the first time on a day and nothing after that on the same day.
- **The app path adds nothing measurable**: the snapshot is uploaded only
  when it changes, and the spend part changes once a month. The app already
  polls `GET /state`; 3 KB more per response is inside the free data
  transfer. <https://aws.amazon.com/s3/pricing/>,
  <https://aws.amazon.com/api-gateway/pricing/>,
  <https://aws.amazon.com/lambda/pricing/>.

## Checks and shots

- `make test`: `spend_part()` in `tests/test_snapshot.py`.
- `make mobile-fixture`: the Costs screen over the made-up spend part in
  `tests/fixtures/mobile/state.json` (card opens it, range, chips, bar tap,
  stale account).
- `make mobile-shots`: `mobile/mock/shots/7-costs.png` and
  `8-costs-day.png`, from the real app over that fixture. This repo is
  public: fixtures and shots use made-up accounts and amounts, never a
  real report.json.
