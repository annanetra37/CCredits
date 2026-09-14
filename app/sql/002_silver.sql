-- ---------------------------------------------------------------------------
-- Silver: what the files add up to. Views only — nothing is stored, so a new
-- upload recomputes everything on the next query.
--
-- There is no eligibility split. Every kilowatt-hour a site generated counts.
-- Days with no reading are simply absent: they are not invented, not zeroed,
-- and not reported as a gap.
-- ---------------------------------------------------------------------------

-- How a site is identified on screen. One definition, read by every query, so
-- the pseudonym is the same everywhere. Real names are disclosed only when the
-- show_real_site_names parameter says so.
CREATE OR REPLACE VIEW silver.site_identity AS
SELECT s.plant_name,
       s.site_code,
       CASE WHEN (SELECT txt FROM silver.parameter WHERE key = 'show_real_site_names') = 'true'
            THEN s.plant_name
            ELSE s.site_code
       END                              AS label,
       s.region,
       s.country,
       s.address,
       s.plant_type,
       s.installed_kwp,
       s.grid_connection_date,
       s.plant_status
FROM bronze.site s;

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
       SUM(r.value)                                                      AS generation_kwh,
       COUNT(DISTINCT r.device_sn) FILTER (WHERE r.device_sn IS NOT NULL) AS device_count,
       MIN(r.source_row)                                                  AS source_row,
       COUNT(*)                                                           AS reading_count
FROM bronze.reading r
JOIN bronze.source_file f ON f.file_id = r.file_id
WHERE r.metric = 'daily_yield'
  AND r.value IS NOT NULL
GROUP BY r.file_id, f.grain, f.uploaded_at, f.filename, r.plant_name, r.reading_date;

-- One row per site per day that actually has data. Where two files cover the
-- same site-day the most recently uploaded one wins; the loser is kept and
-- shown as superseded, never deleted.
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

-- Monthly totals per site: the sum of the days that reported.
CREATE OR REPLACE VIEW silver.site_month AS
SELECT g.plant_name,
       date_trunc('month', g.reading_date)::date AS month,
       SUM(g.generation_kwh)                     AS generation_kwh,
       COUNT(*)                                  AS days_with_data,
       MIN(g.reading_date)                       AS first_day,
       MAX(g.reading_date)                       AS last_day
FROM silver.generation_daily g
GROUP BY g.plant_name, date_trunc('month', g.reading_date);

CREATE OR REPLACE VIEW silver.fleet_month AS
SELECT month,
       SUM(generation_kwh)        AS generation_kwh,
       SUM(days_with_data)        AS days_with_data,
       COUNT(DISTINCT plant_name) AS site_count
FROM silver.site_month
GROUP BY month;
