-- One JSON row per Helsinki day for the phone app's Stats screen
-- (app.podbay.tienoo.com/#stats) and `make mobile-errors`. Every number is
-- aggregated here; the app only draws it.
--
-- The tracker's events: pageview, tab (q.name), reply-open (q.kind), send
-- (q.kind), passkey (q.step), signin (q.step), push (q.step), log-open and
-- error (q.msg, q.type: error, promise or boundary). No message text ever
-- reaches a beacon (mobile/app/track-config.js).
WITH h AS (
  SELECT * FROM ev WHERE NOT is_bot AND e <> 'crawl'
),
days AS (
  SELECT
    day,
    COUNT(DISTINCT sid)                               AS sessions,
    COUNT(*)                                          AS events,
    SUM(CASE WHEN e = 'pageview' THEN 1 ELSE 0 END)   AS pageviews,
    SUM(CASE WHEN e = 'send' THEN 1 ELSE 0 END)       AS sends,
    SUM(CASE WHEN e = 'reply-open' THEN 1 ELSE 0 END) AS replies_opened,
    SUM(CASE WHEN e = 'error' THEN 1 ELSE 0 END)      AS errors
  FROM h
  GROUP BY day
),
tabs AS (
  SELECT day, map_from_entries(collect_list(struct(name, CAST(n AS INT)))) AS tabs
  FROM (SELECT day, COALESCE(q['name'], '?') AS name, COUNT(*) AS n FROM h WHERE e = 'tab' GROUP BY day, COALESCE(q['name'], '?'))
  GROUP BY day
),
errs AS (
  SELECT day, collect_list(named_struct('msg', msg, 'type', type, 'path', path, 'n', CAST(n AS INT))) AS error_list
  FROM (
    SELECT day, COALESCE(q['msg'], '') AS msg, COALESCE(q['type'], 'error') AS type, COALESCE(path, '') AS path, COUNT(*) AS n
    FROM h WHERE e = 'error'
    GROUP BY day, COALESCE(q['msg'], ''), COALESCE(q['type'], 'error'), COALESCE(path, '')
  )
  GROUP BY day
)
SELECT
  substr(CAST(d.day AS STRING), 1, 10) AS date,
  CAST(d.sessions AS INT)       AS sessions,
  CAST(d.events AS INT)         AS events,
  CAST(d.pageviews AS INT)      AS pageviews,
  CAST(d.sends AS INT)          AS sends,
  CAST(d.replies_opened AS INT) AS repliesOpened,
  CAST(d.errors AS INT)         AS errors,
  COALESCE(t.tabs, map())       AS tabs,
  COALESCE(x.error_list, array()) AS errorList
FROM days d
LEFT JOIN tabs t ON t.day = d.day
LEFT JOIN errs x ON x.day = d.day
ORDER BY date
