-- Long format on purpose: (day, site, dim, key, events, sessions).
-- A new event in the app is one more CASE branch here, not one more
-- column downstream; stats_json folds this into nested objects.
-- Humans only: the bot totals live in `daily`.
WITH human AS (SELECT * FROM ev WHERE NOT is_bot),
tagged AS (
  SELECT day, site, sid,
    CASE e
      WHEN 'click'     THEN 'cta_click'
      WHEN 'displayed' THEN 'cta_displayed'
      WHEN 'view'      THEN 'cta_view'
      WHEN 'scroll'    THEN 'scroll'
      WHEN 'crash'     THEN 'crash'
      WHEN 'error'     THEN 'error'
    END AS dim,
    CASE
      WHEN e IN ('click', 'displayed', 'view') THEN q['sel']
      WHEN e = 'scroll'                        THEN q['depth']
      -- Boundary plus message: two crashes at the same place for
      -- different reasons are two different things to fix.
      WHEN e = 'crash'                         THEN concat(q['at'], ' · ', q['msg'])
      WHEN e = 'error'                         THEN q['msg']
    END AS key
  FROM human
),
other AS (
  SELECT day, site, 'path' AS dim, path AS key, sid
  FROM human WHERE e = 'session_start' AND path IS NOT NULL AND path <> ''
  UNION ALL
  -- Where the visit came from, one row per session_start, so the dim sums to
  -- the day's sessions.
  --
  -- `direct` is everything that is not a source of visitors: no referrer at
  -- all (a typed URL, a bookmark, an app launch), the literal "null" that a
  -- referrer policy leaves behind, our own pages, and the sign-in hops a
  -- visit walks back in from. Dropping those rows, which is what this did,
  -- leaves a board reading "no referrer data" on a site where most people
  -- arrive directly.
  SELECT day, site, 'referrer' AS dim,
         CASE
           WHEN ref_host IS NULL OR ref_host = '' THEN 'direct'
           WHEN ref_host IN (own_host, concat('www.', own_host)) THEN 'direct'
           WHEN ref_host = 'accounts.google.com' THEN 'direct'
           WHEN ref_host LIKE '%.amazoncognito.com' THEN 'direct'
           ELSE ref_host
         END AS key,
         sid
  FROM (
    SELECT day, site, sid,
           parse_url(q['ref'], 'HOST') AS ref_host,
           'podbay.tienoo.com' AS own_host
    FROM human WHERE e = 'session_start'
  ) AS refs
  UNION ALL
  -- The three-letter airport code, not the individual edge server.
  SELECT day, site, 'edge' AS dim, substr(edge, 1, 3) AS key, sid
  FROM human WHERE edge IS NOT NULL AND edge <> '-'
  UNION ALL
  SELECT day, site, 'hour' AS dim, CAST(hour AS STRING) AS key, sid
  FROM human WHERE hour IS NOT NULL
)
SELECT day, site, dim, key, COUNT(*) AS events, COUNT(DISTINCT sid) AS sessions
FROM (
  SELECT day, site, dim, key, sid FROM tagged WHERE dim IS NOT NULL
  UNION ALL SELECT day, site, dim, key, sid FROM other
)
WHERE key IS NOT NULL AND key <> ''
GROUP BY day, site, dim, key
