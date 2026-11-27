# AWS spend in the phone app: what is left to do

Status: idea, 2026-11-27. Nothing built.

## Most of it already ships

The phone app has shown AWS spend as charts since 2026-11-22:
[`docs/aws-costs.md`](../aws-costs.md) is the authority on the data path,
the Costs screen and its cost. In short: Machine tab, **AWS costs** card, a
daily stacked bar chart per account (urbangreen, webbaa, personal), one
segment per service, service chips that hide or show a service, 30 d / 90 d,
tap a day for its services. The data comes from the home repo's
`make aws-costs` through `report.json` and the snapshot, the way the Nightly
tab reads `runs.json`. Running cost: 0.12 USD a month.

The real gap is freshness. The report runs once a month (the 3rd, 19:10),
so on the 2nd of a month the newest bar in the "daily" chart is about 30
days old. A daily view that is a month late does not answer "what did AWS
cost yesterday".

## The change

1. **Run `aws-costs` every day** in the home repo's `crontab`, for example
   at 10:40, before `runs-export` at 10:45. The script needs no change: its
   cache already fetches the current month at most once a day and keeps a
   closed month once it is settled (fetched on or after the 3rd of the next
   month).
2. **The Machine card turns amber after 2 days, not 35** (`late` in
   `mobile/app/app.js`, the spend part). Update `docs/aws-costs.md` and the
   home repo's `aws-costs` skill ("When it runs") in the same change.
3. Nothing changes in podbay's data path: `spend_part()` reads the newer
   `report.json` on the next snapshot.

Cost Explorer data refreshes at least once every 24 hours, and the newest
day is always incomplete
(<https://docs.aws.amazon.com/cost-management/latest/userguide/ce-exploring-data.html>).
A daily run gets all there is to get.

### Optional, client side, no AWS cost

These bring the screen nearer to Cost Explorer. Each is a change to the
app only.

- **All accounts** as a fourth tile: the three accounts stacked, one
  segment per service.
- **A date range** of any start and end inside the 90 days, in addition to
  30 d / 90 d.
- **Usage types** under a service (tap a chip twice). This needs the usage
  types in the snapshot, which `docs/aws-costs.md` keeps out for size: more
  than 3 KB gzipped. Do this one only if asked for.

## Monthly running cost

Cost Explorer API: **0.01 USD per paginated request**
(<https://aws.amazon.com/aws-cost-management/aws-cost-explorer/pricing/>).
`aws-costs` makes 2 requests per account and month fetched (`record` and
`usage`), and every answer so far fits on one page.

| Schedule | Requests a month, 3 accounts | Per month |
|---|---|---|
| Monthly (now) | 12 | 0.12 USD |
| **Daily** | days 1 to 3: 12 a day (this month and the unsettled last month); days 4 to 30: 6 a day. 36 + 162 = 198 | **about 2.00 USD** |
| Hourly | 4752 (24 times the daily numbers). Needs a cache change: today the cache is fresh for the whole day, so hourly runs make 0 calls after the first | about 47.50 USD |

The app path adds nothing measurable:

- **S3**: the snapshot goes up only when it changes. A daily report adds
  about 30 PUTs a month at 0.005 USD per 1000
  (<https://aws.amazon.com/s3/pricing/>): under 0.001 USD.
- **Lambda and API Gateway**: the app already polls `GET /state`. A daily
  report adds no requests
  (<https://aws.amazon.com/lambda/pricing/>,
  <https://aws.amazon.com/api-gateway/pricing/>).
- **Data transfer**: the spend part stays under 3 KB gzipped per response,
  inside the free data transfer out.

## Recommendation

**Run the existing report daily: about 2 USD a month.** It turns the chart
that exists into a daily view, with one crontab line and one threshold in
the app. Hourly costs 24 times more for data that AWS refreshes once a day.
Add "All accounts" and the date range as a second slice if more is wanted
of Cost Explorer after he has used the daily view.
