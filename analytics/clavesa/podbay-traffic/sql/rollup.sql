-- Trailing unique visitors, per site.
--
-- Per-day distinct counts cannot be added into a week, so `daily` keeps an
-- HLL sketch per day and this node unions the trailing sketches and estimates
-- once. The sketches never leave Spark: Athena and browsers cannot read them,
-- so what gets served is a plain integer.
SELECT
  a.day,
  a.site,
  hll_sketch_estimate(hll_union_agg(CASE WHEN b.day >= date_add(a.day, -6)  THEN b.visitors_hll END)) AS visitors_7d,
  hll_sketch_estimate(hll_union_agg(CASE WHEN b.day >= date_add(a.day, -29) THEN b.visitors_hll END)) AS visitors_30d
FROM (SELECT day, site, visitors_hll FROM d) a
JOIN (SELECT day, site, visitors_hll FROM d) b
  ON b.site = a.site AND b.day BETWEEN date_add(a.day, -29) AND a.day
GROUP BY a.day, a.site
