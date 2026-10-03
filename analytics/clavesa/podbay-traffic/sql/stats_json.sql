-- One JSON row per day (site is the constant 'podbay'), read by the
-- stats board inside the signed-in editor at /edit/stats (jeeves
-- practices/web-tracking.md: where a repo has an authenticated operator
-- screen, the board goes there).
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
)
SELECT
  substr(CAST(d.day AS STRING), 1, 10) AS date,
  d.site,
  CAST(d.sessions AS INT)  AS sessions,
  CAST(d.visitors AS INT)  AS visitors,
  CAST(COALESCE(r.visitors_7d, 0) AS INT)  AS visitors7d,
  CAST(COALESCE(r.visitors_30d, 0) AS INT) AS visitors30d,
  CAST(d.pageviews AS INT) AS pageviews,
  CAST(d.events AS INT)    AS events,
  CAST(d.crashes AS INT)   AS crashes,
  CAST(d.errors AS INT)    AS errors,
  named_struct('p50', CAST(d.lcp_p50 AS INT), 'p75', CAST(d.lcp_p75 AS INT)) AS lcp,
  named_struct('p50', CAST(d.cls_p50 AS DOUBLE), 'p75', CAST(d.cls_p75 AS DOUBLE)) AS cls,
  named_struct('sessions', CAST(d.bot_sessions AS INT), 'events', CAST(d.bot_events AS INT)) AS bots,
  COALESCE(bm.by, map()) AS by,
  -- The same breakdowns counted by session: the CTA tiers only nest per
  -- session (displayed fires once per element per session, click on
  -- every click), so an event-counted funnel can pass 100%.
  COALESCE(bm.by_sessions, map()) AS bySessions
FROM d
LEFT JOIN r ON d.day = r.day AND d.site = r.site
LEFT JOIN bm ON d.day = bm.day AND d.site = bm.site
ORDER BY date, site
