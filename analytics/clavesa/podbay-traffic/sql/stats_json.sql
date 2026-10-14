-- One JSON row per day (site is the constant 'podbay'), read by the
-- stats board at /stats/. The board only sums days and formats; every
-- number here is already aggregated.
WITH b AS (
  SELECT day, site, dim,
    map_from_entries(collect_list(struct(key, CAST(events AS INT)))) AS m,
    map_from_entries(collect_list(struct(key, CAST(sessions AS INT)))) AS ms
  FROM (SELECT day, site, dim, key, events, sessions FROM bd)
  GROUP BY day, site, dim
),
bm AS (
  SELECT day, site,
    map_from_entries(collect_list(struct(dim, m)))  AS by,
    map_from_entries(collect_list(struct(dim, ms))) AS by_sessions
  FROM b GROUP BY day, site
),
-- A day with only crawler traffic has no `daily` row for humans, but the
-- crawls are still worth a point on the chart.
days AS (
  SELECT day, site FROM d
  UNION
  SELECT day, site FROM bm
)
SELECT
  substr(CAST(x.day AS STRING), 1, 10) AS date,
  x.site,
  CAST(COALESCE(d.sessions, 0) AS INT)  AS sessions,
  CAST(COALESCE(d.visitors, 0) AS INT)  AS visitors,
  CAST(COALESCE(r.visitors_7d, 0) AS INT)  AS visitors7d,
  CAST(COALESCE(r.visitors_30d, 0) AS INT) AS visitors30d,
  CAST(COALESCE(d.pageviews, 0) AS INT) AS pageviews,
  CAST(COALESCE(d.events, 0) AS INT)    AS events,
  CAST(COALESCE(d.crashes, 0) AS INT)   AS crashes,
  CAST(COALESCE(d.errors, 0) AS INT)    AS errors,
  named_struct('p50', CAST(d.lcp_p50 AS INT), 'p75', CAST(d.lcp_p75 AS INT)) AS lcp,
  named_struct('p50', CAST(d.cls_p50 AS DOUBLE), 'p75', CAST(d.cls_p75 AS DOUBLE)) AS cls,
  named_struct('p75', CAST(d.inp_p75 AS INT)) AS inp,
  named_struct('p50', CAST(d.dur_p50 AS INT), 'p75', CAST(d.dur_p75 AS INT)) AS dur,
  named_struct('sessions', CAST(COALESCE(d.bot_sessions, 0) AS INT), 'events', CAST(COALESCE(d.bot_events, 0) AS INT)) AS bots,
  COALESCE(bm.by, map()) AS by,
  -- The same breakdowns counted by session: the CTA tiers only nest per
  -- session (displayed fires once per element per session, click on
  -- every click), so an event-counted funnel can pass 100%.
  COALESCE(bm.by_sessions, map()) AS bySessions
FROM days x
LEFT JOIN d ON d.day = x.day AND d.site = x.site
LEFT JOIN r ON r.day = x.day AND r.site = x.site
LEFT JOIN bm ON bm.day = x.day AND bm.site = x.site
ORDER BY date, site
