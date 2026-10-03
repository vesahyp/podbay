-- One row per day and site: the numbers that do not need a breakdown.
-- Bots are counted separately rather than dropped, so the crawler story stays
-- visible while every human number filters them out.
WITH human AS (SELECT * FROM ev WHERE NOT is_bot)
SELECT
  d.day,
  d.site,
  d.sessions,
  d.visitors,
  d.events,
  d.pageviews,
  d.crashes,
  d.errors,
  d.lcp_p50,
  d.lcp_p75,
  d.cls_p50,
  d.cls_p75,
  d.visitors_hll,
  COALESCE(b.bot_sessions, 0) AS bot_sessions,
  COALESCE(b.bot_events, 0)   AS bot_events
FROM (
  SELECT
    day,
    site,
    COUNT(DISTINCT sid) AS sessions,
    COUNT(DISTINCT uid) AS visitors,
    COUNT(*)            AS events,
    COUNT(CASE WHEN e = 'pageview' THEN 1 END) AS pageviews,
    COUNT(CASE WHEN e = 'crash' THEN 1 END)         AS crashes,
    COUNT(CASE WHEN e = 'error' THEN 1 END)         AS errors,
    -- TRY_CAST for the same reason `events` uses it on q['t']: the beacon
    -- value is whatever arrived over the wire. A single malformed one
    -- ('#', 2026-08-20) failed the whole `daily` node under a plain CAST,
    -- and percentile_approx ignores NULLs, so a bad beacon now drops out
    -- of the percentile instead of taking the night's run down.
    CAST(percentile_approx(CASE WHEN e = 'lcp' THEN TRY_CAST(q['v'] AS DOUBLE) END, 0.5) AS INT)  AS lcp_p50,
    CAST(percentile_approx(CASE WHEN e = 'lcp' THEN TRY_CAST(q['v'] AS DOUBLE) END, 0.75) AS INT) AS lcp_p75,
    ROUND(percentile_approx(CASE WHEN e = 'cls' THEN TRY_CAST(q['v'] AS DOUBLE) END, 0.5), 3)  AS cls_p50,
    ROUND(percentile_approx(CASE WHEN e = 'cls' THEN TRY_CAST(q['v'] AS DOUBLE) END, 0.75), 3) AS cls_p75,
    -- Sketches, not counts: per-day distincts cannot be summed into a week,
    -- so the trailing windows in `rollup` union these instead.
    hll_sketch_agg(uid) AS visitors_hll
  FROM human GROUP BY day, site
) d
LEFT JOIN (
  SELECT day, site, COUNT(DISTINCT sid) AS bot_sessions, COUNT(*) AS bot_events
  FROM ev WHERE is_bot GROUP BY day, site
) b ON d.day = b.day AND d.site = b.site
