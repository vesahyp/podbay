-- Long format on purpose: (day, site, dim, key, events, sessions).
-- A new event in the app is one more CASE branch here, not one more
-- column downstream; stats_json folds this into nested objects.
-- Humans only, except the `bot` dim: the bot totals live in `daily`.
--
-- The board reads `sessions` for everything shaped like a share of
-- visits, and `events` only where a request count is the honest number
-- (crawler requests, errors).
WITH human AS (SELECT * FROM ev WHERE NOT is_bot),
-- Where and how each visit began. One row per session, the row every
-- "share of sessions" dim below is counted from, so those dims sum to
-- the day's sessions.
starts AS (
  SELECT day, site, sid, uid, country, os, device, weekday, hour, edge, path,
         parse_url(q['ref'], 'HOST') AS ref_host
  FROM human WHERE e = 'session_start'
),
-- The first day each browser was seen, over the whole history, so a
-- visit can be told apart as the first or a return.
first_seen AS (
  SELECT uid, MIN(day) AS first_day FROM starts GROUP BY uid
),
-- What each visit did, one row per session. The install command copied
-- or GitHub opened is the conversion this page exists for; a visit that
-- stayed half a minute, read half the page or clicked anything was
-- engaged; the rest bounced.
acts AS (
  SELECT site, sid, MIN(day) AS day,
    MAX(CASE WHEN e = 'copy' OR (e = 'click' AND q['sel'] = 'install-copy') THEN 1 ELSE 0 END) AS copied,
    MAX(CASE WHEN e = 'click' AND q['sel'] LIKE '%github%' THEN 1 ELSE 0 END) AS github,
    MAX(CASE WHEN e = 'click' AND q['sel'] = 'install-readme' THEN 1 ELSE 0 END) AS readme,
    -- A click inside a tracked section or card is a text selection, not
    -- an action: only the links and buttons count.
    MAX(CASE WHEN e = 'click' AND NOT (q['sel'] LIKE 'section-%' OR q['sel'] LIKE 'card-%') THEN 1 ELSE 0 END) AS clicked,
    MAX(CASE WHEN e = 'scroll' AND TRY_CAST(q['depth'] AS INT) >= 50 THEN 1 ELSE 0 END) AS half_read,
    MAX(CASE WHEN e = 'session_end' THEN TRY_CAST(q['dur'] AS INT) END) AS dur
  FROM human GROUP BY site, sid
),
outcomes AS (
  SELECT a.site, a.sid, a.day, s.os, a.copied, a.github, a.readme, a.dur,
    CASE
      WHEN a.copied = 1 OR a.github = 1 THEN 'converted'
      WHEN a.clicked = 1 OR a.half_read = 1 OR COALESCE(a.dur, 0) >= 30 THEN 'engaged'
      ELSE 'bounced'
    END AS outcome
  FROM acts a LEFT JOIN starts s ON s.sid = a.sid
),
tagged AS (
  SELECT day, site, sid,
    CASE e
      -- Sections and cards are tracked for impressions only; a click
      -- inside one is a text selection and says nothing.
      WHEN 'click'     THEN CASE WHEN q['sel'] LIKE 'section-%' OR q['sel'] LIKE 'card-%' THEN NULL ELSE 'cta_click' END
      WHEN 'displayed' THEN 'cta_displayed'
      WHEN 'view'      THEN 'cta_view'
      WHEN 'scroll'    THEN 'scroll'
      WHEN 'copy'      THEN 'copy'
      WHEN 'campaign'  THEN 'campaign'
      WHEN 'email_click' THEN 'campaign'
      WHEN 'crash'     THEN 'crash'
      WHEN 'error'     THEN 'error'
    END AS dim,
    CASE
      WHEN e IN ('click', 'displayed', 'view', 'copy') THEN q['sel']
      WHEN e = 'scroll'                        THEN q['depth']
      -- utm_source, with the campaign name when the link carried one.
      WHEN e = 'campaign'                      THEN concat_ws(' / ', q['src'], q['cmp'])
      WHEN e = 'email_click'                   THEN concat_ws(' / ', 'email', q['em'])
      -- Boundary plus message: two crashes at the same place for
      -- different reasons are two different things to fix.
      WHEN e = 'crash'                         THEN concat(q['at'], ' · ', q['msg'])
      WHEN e = 'error'                         THEN q['msg']
    END AS key
  FROM human
  -- A browser extension throwing on a page it rode along to is the
  -- visitor's browser, not the site. The tracker drops these from the
  -- error event but not from the unhandled-rejection one.
  WHERE NOT (e = 'error' AND q['msg'] RLIKE '(?i)extension|runtime\\.sendMessage|runtime\\.connect')
),
other AS (
  SELECT day, site, 'path' AS dim, path AS key, sid FROM starts WHERE path IS NOT NULL AND path <> ''
  UNION ALL
  -- Where the visit came from. `direct` is everything that is not a
  -- source of visitors: no referrer at all (a typed URL, a bookmark, an
  -- app launch), the literal "null" a referrer policy leaves behind, and
  -- our own pages. Dropping those rows leaves a board reading "no
  -- referrer data" on a site where most people arrive directly.
  SELECT day, site, 'referrer' AS dim,
         CASE
           WHEN ref_host IS NULL OR ref_host = '' THEN 'direct'
           WHEN ref_host IN ('podbay.tienoo.com', 'www.podbay.tienoo.com') THEN 'direct'
           ELSE regexp_replace(ref_host, '^www\\.', '')
         END AS key, sid
  FROM starts
  UNION ALL
  -- The three-letter airport code, not the individual edge server.
  SELECT day, site, 'edge' AS dim, substr(edge, 1, 3) AS key, sid FROM starts WHERE edge IS NOT NULL AND edge <> '-'
  UNION ALL
  SELECT day, site, 'hour' AS dim, CAST(hour AS STRING) AS key, sid FROM starts WHERE hour IS NOT NULL
  UNION ALL
  -- Weekday 1..7 and hour, "3-14" is Wednesday at 14, for the grid.
  SELECT day, site, 'weekhour' AS dim, concat(CAST(weekday AS STRING), '-', CAST(hour AS STRING)) AS key, sid
  FROM starts WHERE hour IS NOT NULL
  UNION ALL
  SELECT day, site, 'country' AS dim, country AS key, sid FROM starts
  UNION ALL
  SELECT day, site, 'os' AS dim, os AS key, sid FROM starts
  UNION ALL
  SELECT day, site, 'device' AS dim, device AS key, sid FROM starts
  UNION ALL
  SELECT s.day, s.site, 'visit' AS dim,
         CASE WHEN f.first_day < s.day THEN 'returning' ELSE 'new' END AS key, s.sid
  FROM starts s JOIN first_seen f ON f.uid = s.uid
  UNION ALL
  SELECT day, site, 'outcome' AS dim, outcome AS key, sid FROM outcomes
  UNION ALL
  -- Conversion by OS: the share of Mac visitors who went on to install
  -- is the rate that means something for a Mac-only tool.
  SELECT day, site, 'os_outcome' AS dim, concat(COALESCE(os, 'unknown'), '/', outcome) AS key, sid FROM outcomes
  UNION ALL
  -- One row per action, so a visit that copied and opened GitHub counts
  -- under both. Read with `sessions`.
  SELECT day, site, 'conversion' AS dim, 'install copied' AS key, sid FROM outcomes WHERE copied = 1
  UNION ALL
  SELECT day, site, 'conversion' AS dim, 'GitHub opened' AS key, sid FROM outcomes WHERE github = 1
  UNION ALL
  SELECT day, site, 'conversion' AS dim, 'README opened' AS key, sid FROM outcomes WHERE readme = 1
  UNION ALL
  -- Time on page in bands. A visit with no session_end (the tab was
  -- killed, the beacon lost) is 'unknown', counted so the bands still
  -- sum to the sessions.
  SELECT day, site, 'duration' AS dim,
         CASE
           WHEN dur IS NULL THEN 'unknown'
           WHEN dur < 10 THEN '<10 s'
           WHEN dur < 30 THEN '10-30 s'
           WHEN dur < 60 THEN '30-60 s'
           WHEN dur < 180 THEN '1-3 min'
           ELSE '3 min+'
         END AS key, sid
  FROM outcomes
),
-- Crawlers: the name, counted in requests. They have no session.
bots AS (
  SELECT day, site, 'bot' AS dim, bot_name AS key, sid FROM ev WHERE is_bot
)
SELECT day, site, dim, key, COUNT(*) AS events, COUNT(DISTINCT sid) AS sessions
FROM (
  SELECT day, site, dim, key, sid FROM tagged WHERE dim IS NOT NULL
  UNION ALL SELECT day, site, dim, key, sid FROM other
  UNION ALL SELECT day, site, dim, key, sid FROM bots
)
WHERE key IS NOT NULL AND key <> ''
GROUP BY day, site, dim, key
