-- ---------------------------------------------------------------------------
-- Gold: the numbers someone would put in a term sheet.
-- Views over Silver over Bronze. Nothing stored, so nothing goes stale.
-- ---------------------------------------------------------------------------

-- 1.4 Reference data. Versioned and dated — never constants in code.
CREATE TABLE IF NOT EXISTS gold.emission_factor (
    factor_id     bigserial PRIMARY KEY,
    value_tco2e_per_mwh numeric(10, 5) NOT NULL,
    factor_type   text NOT NULL,          -- combined_margin | operating_margin | grid_average
    source        text NOT NULL,
    source_url    text,
    project_types text,                   -- who the published row is applicable to
    vintage       text NOT NULL,
    -- valid_from / valid_to are the window this factor is APPLIED over.
    -- published_valid_to is what the source document itself says, kept so the
    -- two can differ visibly rather than silently.
    valid_from    date NOT NULL,
    valid_to      date,
    published_valid_to date,
    -- Table 1 of a standardized baseline publishes several factors. Only one
    -- applies to this fleet; the rest are recorded so a verifier can see that
    -- they were considered rather than missed.
    active        boolean NOT NULL DEFAULT false,
    -- A factor is unverified until a person has checked it against the source
    -- document named above. The default is false on purpose: a number nobody
    -- has checked must never present itself as one that someone has.
    verified      boolean NOT NULL DEFAULT false,
    verified_by   text,
    verified_at   timestamptz,
    -- Table 1 publishes three combined-margin rows sharing one start date,
    -- distinguished only by which projects they apply to.
    UNIQUE (factor_type, valid_from, project_types)
);

ALTER TABLE gold.emission_factor DROP CONSTRAINT IF EXISTS emission_factor_factor_type_valid_from_key;

ALTER TABLE gold.emission_factor ADD COLUMN IF NOT EXISTS source_url    text;
ALTER TABLE gold.emission_factor ADD COLUMN IF NOT EXISTS project_types text;
ALTER TABLE gold.emission_factor ADD COLUMN IF NOT EXISTS active        boolean NOT NULL DEFAULT false;
ALTER TABLE gold.emission_factor ADD COLUMN IF NOT EXISTS published_valid_to date;
ALTER TABLE gold.emission_factor ADD COLUMN IF NOT EXISTS verified    boolean NOT NULL DEFAULT false;
ALTER TABLE gold.emission_factor ADD COLUMN IF NOT EXISTS verified_by text;
ALTER TABLE gold.emission_factor ADD COLUMN IF NOT EXISTS verified_at timestamptz;

CREATE TABLE IF NOT EXISTS gold.price (
    price_id    bigserial PRIMARY KEY,
    instrument  text NOT NULL CHECK (instrument IN ('vcu')),
    value       numeric(12, 4) NOT NULL,  -- per tCO2e
    currency    text NOT NULL DEFAULT 'USD',
    source      text,
    as_of       date NOT NULL,
    UNIQUE (instrument, as_of)
);

-- Any I-REC rows are left over from an earlier version of this product. VCUs
-- are the only instrument now, so they are removed rather than left to confuse
-- someone reading the portal. The constraint is rebuilt to match.
ALTER TABLE gold.price DROP CONSTRAINT IF EXISTS price_instrument_check;
DELETE FROM gold.price WHERE instrument <> 'vcu';
ALTER TABLE gold.price ADD CONSTRAINT price_instrument_check CHECK (instrument = 'vcu');

-- ---------------------------------------------------------------------------
-- Migrations for databases seeded by an earlier version of this app. The seed
-- only fills an empty table, so a live deployment would otherwise keep the old
-- rows and none of the corrections below would ever reach it.
-- ---------------------------------------------------------------------------

-- 1. The applied window and the publication's own validity used to be the same
--    column. Separate them: ASB0038-2018 is the most recent approved baseline
--    for Armenia, so it keeps applying, and what the document says is retained.
UPDATE gold.emission_factor
   SET published_valid_to = COALESCE(published_valid_to, valid_to),
       valid_to = NULL
 WHERE source LIKE 'CDM Standardized Baseline ASB0038-2018%'
   AND valid_to IS NOT NULL;

-- 2. Point at the document the client supplied rather than the CDM index page.
UPDATE gold.emission_factor
   SET source_url = 'https://environment.gov.am/api/assets/7e4407a1-eac6-4aed-bc8e-3ce5e1256a40'
 WHERE source LIKE 'CDM Standardized Baseline ASB0038-2018%'
   AND (source_url IS NULL OR source_url LIKE '%cdm.unfccc.int%');

-- 3. Backfill which projects each published margin applies to, matched on the
--    published value, so a verifier can see why one row and not another is used.
UPDATE gold.emission_factor e
   SET project_types = v.project_types
  FROM (VALUES
        (0.4329, 'Wind and solar power generation project activities (first, second and third crediting periods)'),
        (0.4620, 'All project activities (first, second and third crediting periods)'),
        (0.3456, 'All project activities (first, second and third crediting periods)'),
        (0.4038, 'All project activities except wind and solar power generation (first crediting period)'),
        (0.3748, 'All project activities except wind and solar power generation (second and third crediting periods)')
       ) AS v(value, project_types)
 WHERE e.project_types IS NULL
   AND e.value_tco2e_per_mwh = v.value;

-- 4. Older seeds marked nothing active, or marked several. For a solar fleet
--    exactly one row applies: the combined margin for wind and solar.
UPDATE gold.emission_factor
   SET active = (value_tco2e_per_mwh = 0.4329)
 WHERE source LIKE 'CDM Standardized Baseline ASB0038-2018%'
   AND (SELECT COUNT(*) FROM gold.emission_factor WHERE active) <> 1;

-- 5. The published Armenian table was confirmed against its source document by
--    the fleet owner, so it is recorded as checked. Rows an operator entered
--    themselves are left exactly as they set them.
UPDATE gold.emission_factor
   SET verified = true,
       verified_at = COALESCE(verified_at, now())
 WHERE source LIKE 'CDM Standardized Baseline ASB0038-2018%'
   AND verified = false;

-- 6. The VCU price placeholder this app shipped earlier was 8.00 with that
--    exact source string, so matching it replaces our own placeholder without
--    touching a price an operator entered deliberately.
UPDATE gold.price
   SET value = 3.00,
       source = 'Indicative pilot pricing — conservative end of the VCU range'
 WHERE instrument = 'vcu' AND source = 'Indicative pilot pricing';

-- 4.1 The emission factor that applies to a month. A plain date join: the
-- active factor whose applied window covers the month. Where that window runs
-- past the document's own published validity, published_valid_to records it
-- and the portal says so beside every figure — the difference is visible
-- rather than hidden.
CREATE OR REPLACE VIEW gold.factor_by_month AS
SELECT DISTINCT ON (m.month)
       m.month,
       e.factor_id,
       e.value_tco2e_per_mwh,
       e.factor_type,
       e.project_types,
       e.source,
       e.source_url,
       e.vintage,
       e.valid_from,
       e.valid_to,
       e.published_valid_to,
       COALESCE(e.verified, false) AS verified,
       CASE WHEN e.published_valid_to IS NOT NULL
             AND m.month > e.published_valid_to
            THEN true ELSE false END AS beyond_published_validity
FROM (SELECT DISTINCT month FROM silver.site_month) m
LEFT JOIN gold.emission_factor e
       ON e.active
      AND e.valid_from <= (m.month + interval '1 month - 1 day')::date
      AND (e.valid_to IS NULL OR e.valid_to >= m.month)
ORDER BY m.month, e.valid_from DESC;

-- 4.2 Emission reduction. Every megawatt-hour generated displaces a
-- megawatt-hour the grid would have supplied, so the reduction is simply
-- generation times the factor. Project emissions and leakage are explicit
-- zeros so the formula reads complete.
CREATE OR REPLACE VIEW gold.carbon AS
SELECT sm.plant_name,
       sm.month,
       sm.generation_kwh,
       sm.generation_kwh / 1000.0                                 AS generation_mwh,
       sm.days_with_data,
       ef.factor_id,
       ef.value_tco2e_per_mwh                                     AS emission_factor,
       ef.factor_type,
       ef.project_types                                           AS factor_project_types,
       ef.source                                                  AS factor_source,
       ef.source_url                                              AS factor_source_url,
       ef.vintage                                                 AS factor_vintage,
       ef.valid_from                                              AS factor_valid_from,
       ef.valid_to                                                AS factor_valid_to,
       ef.published_valid_to                                      AS factor_published_valid_to,
       COALESCE(ef.verified, false)                               AS factor_verified,
       COALESCE(ef.beyond_published_validity, false)              AS factor_beyond_validity,
       (sm.generation_kwh / 1000.0) * ef.value_tco2e_per_mwh      AS baseline_emissions_tco2e,
       0::numeric                                                 AS project_emissions_tco2e,
       0::numeric                                                 AS leakage_tco2e,
       (sm.generation_kwh / 1000.0) * ef.value_tco2e_per_mwh
           - 0::numeric - 0::numeric                              AS net_reduction_tco2e
FROM silver.site_month sm
LEFT JOIN gold.factor_by_month ef ON ef.month = sm.month;

-- 4.3 VCUs. One tonne avoided is one unit, so the two are the same number.
-- Nothing is rounded and nothing is carried forward.
CREATE OR REPLACE VIEW gold.vcu AS
SELECT plant_name,
       month,
       generation_kwh,
       generation_mwh,
       days_with_data,
       emission_factor,
       factor_verified,
       factor_beyond_validity,
       net_reduction_tco2e,
       net_reduction_tco2e AS vcu_issued
FROM gold.carbon;

-- 4.4 Price per month, resolved once per distinct month.
CREATE OR REPLACE VIEW gold.price_by_month AS
SELECT m.month,
       vp.value    AS vcu_price_per_tco2e,
       vp.currency AS currency,
       vp.as_of    AS vcu_price_as_of,
       vp.source   AS vcu_price_source
FROM (SELECT DISTINCT month FROM silver.site_month) m
LEFT JOIN LATERAL (
    SELECT * FROM gold.price p
    WHERE p.instrument = 'vcu' AND p.as_of <= (m.month + interval '1 month - 1 day')::date
    ORDER BY p.as_of DESC LIMIT 1
) vp ON true;

-- 4.5 VCUs x price.
CREATE OR REPLACE VIEW gold.revenue AS
SELECT v.plant_name,
       v.month,
       v.generation_kwh,
       v.vcu_issued,
       v.net_reduction_tco2e,
       v.factor_verified,
       v.factor_beyond_validity,
       p.vcu_price_per_tco2e,
       p.currency,
       p.vcu_price_as_of,
       p.vcu_price_source,
       v.vcu_issued * COALESCE(p.vcu_price_per_tco2e, 0) AS total_revenue
FROM gold.vcu v
LEFT JOIN gold.price_by_month p ON p.month = v.month;

-- The headline row on the front screen.
CREATE OR REPLACE VIEW gold.fleet_summary AS
SELECT (SELECT COUNT(*) FROM bronze.site)                              AS site_count,
       (SELECT COUNT(*) FROM bronze.source_file)                       AS file_count,
       (SELECT COALESCE(SUM(installed_kwp), 0) FROM bronze.site)       AS installed_kwp,
       (SELECT COUNT(*) FROM bronze.site WHERE installed_kwp IS NULL)  AS sites_without_capacity,
       w.window_start,
       w.window_end,
       COALESCE(e.generation_kwh, 0)                                   AS generation_kwh,
       COALESCE(e.days_with_data, 0)                                   AS days_with_data,
       m.net_reduction_tco2e,
       COALESCE(m.vcu_issued, 0)                                       AS vcu_issued,
       COALESCE(m.total_revenue, 0)                                    AS total_revenue,
       COALESCE(m.currency, 'USD')                                     AS currency,
       COALESCE(m.factor_verified, false)                              AS factor_verified,
       COALESCE(m.factor_beyond_validity, false)                       AS factor_beyond_validity
FROM (SELECT SUM(generation_kwh) AS generation_kwh, SUM(days_with_data) AS days_with_data
      FROM silver.site_month) e
CROSS JOIN (SELECT SUM(net_reduction_tco2e) AS net_reduction_tco2e,
                   SUM(vcu_issued)          AS vcu_issued,
                   SUM(total_revenue)       AS total_revenue,
                   MAX(currency)            AS currency,
                   bool_and(factor_verified)        AS factor_verified,
                   bool_or(factor_beyond_validity)  AS factor_beyond_validity
            FROM gold.revenue) m
CROSS JOIN silver.loaded_window w;

-- One row per site.
CREATE OR REPLACE VIEW gold.fleet_table AS
SELECT s.plant_name,
       s.installed_kwp,
       s.plant_type,
       s.grid_connection_date,
       s.plant_status,
       s.address,
       COALESCE(agg.days_with_data, 0)   AS days_with_data,
       COALESCE(agg.generation_kwh, 0)   AS generation_kwh,
       agg.first_day,
       agg.last_day,
       CASE WHEN s.installed_kwp > 0 AND COALESCE(agg.days_with_data, 0) > 0
            THEN agg.generation_kwh / s.installed_kwp / agg.days_with_data
       END                               AS specific_yield_per_day,
       COALESCE(credits.vcu_issued, 0)   AS vcu_issued,
       credits.net_reduction_tco2e       AS net_reduction_tco2e,
       COALESCE(credits.total_revenue, 0) AS total_revenue
FROM bronze.site s
LEFT JOIN (
    SELECT plant_name,
           SUM(days_with_data) AS days_with_data,
           SUM(generation_kwh) AS generation_kwh,
           MIN(first_day)      AS first_day,
           MAX(last_day)       AS last_day
    FROM silver.site_month GROUP BY plant_name
) agg ON agg.plant_name = s.plant_name
LEFT JOIN (
    SELECT plant_name,
           SUM(vcu_issued)          AS vcu_issued,
           SUM(net_reduction_tco2e) AS net_reduction_tco2e,
           SUM(total_revenue)       AS total_revenue
    FROM gold.revenue GROUP BY plant_name
) credits ON credits.plant_name = s.plant_name;

-- 4.6 The Gold layer as one flat table — every column the calculation used,
-- one row per site per month, ready to read or export.
CREATE OR REPLACE VIEW gold.ledger AS
SELECT i.label                       AS site,
       i.site_code,
       i.region,
       i.country,
       i.installed_kwp,
       i.grid_connection_date,
       r.month,
       v.days_with_data,
       v.generation_kwh,
       v.generation_mwh,
       v.emission_factor,
       c.factor_source,
       c.factor_vintage,
       v.net_reduction_tco2e,
       v.vcu_issued,
       r.vcu_price_per_tco2e,
       r.currency,
       r.total_revenue
FROM gold.revenue r
JOIN gold.vcu v    ON v.plant_name = r.plant_name AND v.month = r.month
JOIN gold.carbon c ON c.plant_name = r.plant_name AND c.month = r.month
JOIN silver.site_identity i ON i.plant_name = r.plant_name;

-- 4.7 Lineage: any gold figure resolves to the bronze cells underneath it.
CREATE OR REPLACE VIEW gold.lineage AS
SELECT g.plant_name,
       g.month,
       g.vcu_issued,
       g.net_reduction_tco2e,
       d.reading_date,
       d.generation_kwh       AS day_generation_kwh,
       r.reading_id,
       r.device_sn,
       r.metric,
       r.metric_label,
       r.value                AS cell_value,
       r.unit,
       r.source_row,
       r.source_col,
       f.file_id,
       f.filename,
       f.sha256,
       f.grain,
       f.uploaded_at
FROM gold.vcu g
JOIN silver.generation_daily d
  ON d.plant_name = g.plant_name
 AND date_trunc('month', d.reading_date)::date = g.month
LEFT JOIN bronze.reading r
  ON r.file_id = d.file_id
 AND r.plant_name = d.plant_name
 AND r.reading_date = d.reading_date
 AND r.metric = 'daily_yield'
LEFT JOIN bronze.source_file f ON f.file_id = r.file_id;
