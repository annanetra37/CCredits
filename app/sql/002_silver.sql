-- ---------------------------------------------------------------------------
-- Silver: the rules. Views only — nothing is stored, so a new upload or a
-- changed threshold recomputes everything on the next query.
-- Every rule below is one CASE expression that can be read aloud to a verifier.
-- ---------------------------------------------------------------------------

-- Helper: the parameter values, as one row, so views read them by name.
CREATE OR REPLACE VIEW silver.rules AS
SELECT
    (SELECT num FROM silver.parameter WHERE key = 'implausible_kwh_per_kwp')     AS implausible_kwh_per_kwp,
    (SELECT txt FROM silver.parameter WHERE key = 'zero_day_policy')             AS zero_day_policy,
    (SELECT txt FROM silver.parameter WHERE key = 'missing_day_policy')          AS missing_day_policy,
    (SELECT txt FROM silver.parameter WHERE key = 'trust_grid_connection_date')  AS trust_grid_connection_date;

-- The window of dates the uploaded files actually cover.
CREATE OR REPLACE VIEW silver.loaded_window AS
SELECT MIN(period_start) AS window_start,
       MAX(period_end)   AS window_end
FROM bronze.source_file
WHERE period_start IS NOT NULL AND period_end IS NOT NULL;

-- Every file's own account of a site-day. Inverter-grain files are summed
-- across devices; plant-grain files carry the plant row straight through.
CREATE OR REPLACE VIEW silver.file_site_day AS
SELECT r.file_id,
       f.grain,
       f.uploaded_at,
       f.filename,
       r.plant_name,
       r.reading_date,
       SUM(r.value)                                                          AS generation_kwh,
       COUNT(DISTINCT r.device_sn) FILTER (WHERE r.device_sn IS NOT NULL)     AS device_count,
       MIN(r.source_row)                                                      AS source_row,
       COUNT(*)                                                               AS reading_count
FROM bronze.reading r
JOIN bronze.source_file f ON f.file_id = r.file_id
WHERE r.metric = 'daily_yield'
  AND r.value IS NOT NULL
GROUP BY r.file_id, f.grain, f.uploaded_at, f.filename, r.plant_name, r.reading_date;

-- 3.1 One row per site per day. Where two files cover the same site-day the
-- most recently uploaded one wins; the loser is kept and shown as superseded.
CREATE OR REPLACE VIEW silver.generation_daily AS
SELECT DISTINCT ON (plant_name, reading_date)
       plant_name,
       reading_date,
       generation_kwh,
       device_count,
       grain,
       file_id,
       filename,
       source_row,
       reading_count
FROM silver.file_site_day
ORDER BY plant_name,
         reading_date,
         uploaded_at DESC,
         CASE grain WHEN 'plant' THEN 0 ELSE 1 END,
         file_id DESC;

-- The rows that lost. Superseded, not deleted, and visible on request.
CREATE OR REPLACE VIEW silver.superseded_site_day AS
SELECT fsd.*
FROM silver.file_site_day fsd
JOIN silver.generation_daily gd
  ON gd.plant_name = fsd.plant_name
 AND gd.reading_date = fsd.reading_date
WHERE gd.file_id <> fsd.file_id;

-- 3.2 A row per site per day in the loaded window, whether or not data exists.
CREATE OR REPLACE VIEW silver.site_day_expected AS
SELECT s.plant_name,
       d.day::date AS reading_date
FROM bronze.site s
CROSS JOIN silver.loaded_window w
CROSS JOIN LATERAL generate_series(w.window_start, w.window_end, interval '1 day') AS d(day);

-- 3.3 One boolean per rule, each rule its own CASE expression.
CREATE OR REPLACE VIEW silver.data_quality AS
SELECT e.plant_name,
       e.reading_date,
       g.generation_kwh,
       s.installed_kwp,
       s.grid_connection_date,
       s.plant_status,
       g.file_id,
       g.source_row,

       CASE WHEN g.plant_name IS NULL THEN true ELSE false END                      AS is_missing,

       CASE WHEN g.generation_kwh = 0 THEN true ELSE false END                      AS is_zero,

       CASE WHEN g.generation_kwh < 0 THEN true ELSE false END                      AS is_negative,

       CASE WHEN s.installed_kwp > 0 THEN g.generation_kwh / s.installed_kwp END    AS specific_yield,

       CASE WHEN s.installed_kwp > 0
             AND g.generation_kwh / s.installed_kwp > r.implausible_kwh_per_kwp
            THEN true ELSE false END                                                AS is_implausible,

       CASE WHEN s.grid_connection_date IS NOT NULL
             AND e.reading_date < s.grid_connection_date
            THEN true ELSE false END                                                AS is_before_grid_connection,

       dup.file_count,
       dup.spread_kwh,

       -- Two files covering the same site-day is normal: the later upload wins
       -- and the earlier is kept as superseded. It is only a quality problem
       -- when the two sources disagree about the number.
       CASE WHEN dup.file_count > 1
             AND dup.spread_kwh > GREATEST(0.005 * NULLIF(dup.max_kwh, 0), 0.01)
            THEN true ELSE false END                                                AS is_duplicate,

       CASE WHEN s.plant_status IS NOT NULL
             AND lower(s.plant_status) NOT IN ('normal', 'active', 'online', 'running', 'ok')
            THEN true ELSE false END                                                AS is_site_inactive
FROM silver.site_day_expected e
CROSS JOIN silver.rules r
JOIN bronze.site s              ON s.plant_name = e.plant_name
LEFT JOIN silver.generation_daily g
       ON g.plant_name = e.plant_name AND g.reading_date = e.reading_date
LEFT JOIN LATERAL (
       SELECT COUNT(*)                                        AS file_count,
              MAX(f.generation_kwh)                           AS max_kwh,
              MAX(f.generation_kwh) - MIN(f.generation_kwh)   AS spread_kwh
       FROM silver.file_site_day f
       WHERE f.plant_name = e.plant_name AND f.reading_date = e.reading_date
) dup ON true;

-- 3.4 Collapse the booleans into one flag. Missing stays missing: a gap is
-- never interpolated and never quietly becomes a zero.
CREATE OR REPLACE VIEW silver.quality_flag AS
SELECT q.*,
       CASE
           WHEN q.is_missing AND r.missing_day_policy = 'zero' THEN 'ok'
           WHEN q.is_missing                                   THEN 'missing'
           WHEN q.is_negative                                  THEN 'suspect'
           WHEN q.is_implausible                               THEN 'suspect'
           WHEN q.is_duplicate                                 THEN 'suspect'
           WHEN q.is_zero AND r.zero_day_policy = 'suspect'    THEN 'suspect'
           ELSE 'ok'
       END AS flag,
       CASE
           WHEN q.is_missing AND r.missing_day_policy = 'zero' THEN 'missing day counted as zero'
           WHEN q.is_missing                                   THEN 'no row for this site on this day'
           WHEN q.is_negative                                  THEN 'negative generation'
           WHEN q.is_implausible                               THEN 'specific yield above ' || r.implausible_kwh_per_kwp || ' kWh/kWp/day'
           WHEN q.is_duplicate                                 THEN q.file_count || ' files disagree about this site-day by ' || round(q.spread_kwh, 2) || ' kWh'
           WHEN q.is_zero AND r.zero_day_policy = 'suspect'    THEN 'zero generation — winter zero and broken inverter look identical'
           ELSE NULL
       END AS flag_reason
FROM silver.data_quality q
CROSS JOIN silver.rules r;

-- 3.5 The bridge. Every kWh is either eligible or excluded with a reason;
-- nothing disappears between generation and credits.
CREATE OR REPLACE VIEW silver.eligibility AS
SELECT f.plant_name,
       f.reading_date,
       COALESCE(f.generation_kwh, 0)                       AS generation_kwh,
       f.flag,
       f.specific_yield,
       f.installed_kwp,
       f.file_id,
       f.source_row,

       CASE WHEN f.is_before_grid_connection THEN 0
            WHEN f.is_site_inactive          THEN 0
            WHEN f.flag = 'missing'          THEN 0
            WHEN f.flag = 'suspect'          THEN 0
            ELSE COALESCE(f.generation_kwh, 0)
       END AS eligible_kwh,

       CASE WHEN f.is_before_grid_connection THEN COALESCE(f.generation_kwh, 0)
            WHEN f.is_site_inactive          THEN COALESCE(f.generation_kwh, 0)
            WHEN f.flag = 'missing'          THEN 0
            WHEN f.flag = 'suspect'          THEN COALESCE(f.generation_kwh, 0)
            ELSE 0
       END AS excluded_kwh,

       CASE WHEN f.is_before_grid_connection THEN 'before_grid_connection'
            WHEN f.is_site_inactive          THEN 'site_inactive'
            WHEN f.flag = 'missing'          THEN 'data_gap'
            WHEN f.flag = 'suspect'          THEN 'quality_suspect'
            ELSE NULL
       END AS exclusion_reason
FROM silver.quality_flag f;

-- 3.6 Aggregates that carry the worst child quality, never the average.
CREATE OR REPLACE VIEW silver.site_month AS
SELECT e.plant_name,
       date_trunc('month', e.reading_date)::date          AS month,
       SUM(e.generation_kwh)                              AS generation_kwh,
       SUM(e.eligible_kwh)                                AS eligible_kwh,
       SUM(e.excluded_kwh)                                AS excluded_kwh,
       COUNT(*)                                           AS days_expected,
       COUNT(*) FILTER (WHERE e.flag <> 'missing')        AS days_with_data,
       COUNT(*) FILTER (WHERE e.flag = 'missing')         AS days_missing,
       COUNT(*) FILTER (WHERE e.flag = 'suspect')         AS days_suspect,
       COUNT(*) FILTER (WHERE e.flag = 'ok')              AS days_ok,
       CASE
           WHEN COUNT(*) FILTER (WHERE e.flag = 'missing') > 0 THEN 'missing'
           WHEN COUNT(*) FILTER (WHERE e.flag = 'suspect') > 0 THEN 'suspect'
           ELSE 'ok'
       END                                                AS worst_flag
FROM silver.eligibility e
GROUP BY e.plant_name, date_trunc('month', e.reading_date);

CREATE OR REPLACE VIEW silver.fleet_month AS
SELECT month,
       SUM(generation_kwh)                                AS generation_kwh,
       SUM(eligible_kwh)                                  AS eligible_kwh,
       SUM(excluded_kwh)                                  AS excluded_kwh,
       SUM(days_expected)                                 AS days_expected,
       SUM(days_missing)                                  AS days_missing,
       SUM(days_suspect)                                  AS days_suspect,
       COUNT(DISTINCT plant_name)                         AS site_count,
       CASE
           WHEN SUM(days_missing) > 0 THEN 'missing'
           WHEN SUM(days_suspect) > 0 THEN 'suspect'
           ELSE 'ok'
       END                                                AS worst_flag
FROM silver.site_month
GROUP BY month;

-- Exclusions grouped by reason — the grey streams peeling off the river.
CREATE OR REPLACE VIEW silver.exclusion_summary AS
SELECT exclusion_reason,
       SUM(excluded_kwh) AS excluded_kwh,
       COUNT(*)          AS day_count,
       COUNT(DISTINCT plant_name) AS site_count
FROM silver.eligibility
WHERE exclusion_reason IS NOT NULL
GROUP BY exclusion_reason;
