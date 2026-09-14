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
    valid_from    date NOT NULL,
    valid_to      date,
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

-- 4.1 The emission factor that applies to a month.
--
-- A standardized baseline is published with a validity window. If the loaded
-- period falls outside it there is no applicable factor, and by default this
-- view returns none rather than reaching for a lapsed one — no carbon number
-- is better than an indefensible carbon number. Setting the allow-expired
-- parameter applies the lapsed factor and marks it, so every figure derived
-- from it can say so on screen.
CREATE OR REPLACE VIEW gold.factor_by_month AS
WITH months AS (SELECT DISTINCT month FROM silver.site_month),
allow AS (
    SELECT COALESCE((SELECT txt FROM silver.parameter
                     WHERE key = 'allow_expired_emission_factor'), 'false') = 'true' AS ok
),
in_window AS (
    SELECT DISTINCT ON (m.month) m.month, e.*, false AS expired
    FROM months m
    JOIN gold.emission_factor e
      ON e.active
     AND e.valid_from <= (m.month + interval '1 month - 1 day')::date
     AND (e.valid_to IS NULL OR e.valid_to >= m.month)
    ORDER BY m.month, e.valid_from DESC
),
lapsed AS (
    SELECT DISTINCT ON (m.month) m.month, e.*, true AS expired
    FROM months m
    CROSS JOIN allow a
    JOIN gold.emission_factor e ON e.active
    WHERE a.ok
      AND NOT EXISTS (SELECT 1 FROM in_window w WHERE w.month = m.month)
    ORDER BY m.month, e.valid_to DESC NULLS FIRST, e.valid_from DESC
)
SELECT m.month,
       f.factor_id,
       f.value_tco2e_per_mwh,
       f.factor_type,
       f.project_types,
       f.source,
       f.source_url,
       f.vintage,
       f.valid_from,
       f.valid_to,
       COALESCE(f.verified, false) AS verified,
       COALESCE(f.expired, false)  AS expired
FROM months m
LEFT JOIN (SELECT * FROM in_window UNION ALL SELECT * FROM lapsed) f ON f.month = m.month;

-- 4.2 Eligible MWh x the emission factor, joined on date so the factor's
-- version travels with the result instead of being baked into it.
-- Project emissions and leakage are explicit zeros so the formula reads complete.
CREATE OR REPLACE VIEW gold.carbon AS
SELECT sm.plant_name,
       sm.month,
       sm.eligible_kwh,
       sm.eligible_kwh / 1000.0                                   AS eligible_mwh,
       sm.worst_flag,
       ef.factor_id,
       ef.value_tco2e_per_mwh                                     AS emission_factor,
       ef.factor_type,
       ef.project_types                                           AS factor_project_types,
       ef.source                                                  AS factor_source,
       ef.source_url                                              AS factor_source_url,
       ef.vintage                                                 AS factor_vintage,
       ef.valid_from                                              AS factor_valid_from,
       ef.valid_to                                                AS factor_valid_to,
       COALESCE(ef.verified, false)                               AS factor_verified,
       COALESCE(ef.expired, false)                                AS factor_expired,
       (sm.eligible_kwh / 1000.0) * ef.value_tco2e_per_mwh        AS baseline_emissions_tco2e,
       0::numeric                                                 AS project_emissions_tco2e,
       0::numeric                                                 AS leakage_tco2e,
       (sm.eligible_kwh / 1000.0) * ef.value_tco2e_per_mwh
           - 0::numeric - 0::numeric                              AS net_reduction_tco2e
FROM silver.site_month sm
LEFT JOIN gold.factor_by_month ef ON ef.month = sm.month;

-- 4.3 VCUs. A verified carbon unit is one tonne of CO2e, so a month issues
-- whole tonnes and the remainder is carried forward to the next month rather
-- than being rounded away. The ledger is the running total: each month issues
-- the difference between this month's floor and last month's.
CREATE OR REPLACE VIEW gold.vcu AS
WITH running AS (
    SELECT plant_name,
           month,
           eligible_kwh,
           eligible_mwh,
           worst_flag,
           emission_factor,
           factor_verified,
           factor_expired,
           net_reduction_tco2e,
           SUM(COALESCE(net_reduction_tco2e, 0)) OVER (
               PARTITION BY plant_name ORDER BY month
               ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
           ) AS cumulative_tco2e
    FROM gold.carbon
), ledger AS (
    SELECT *, FLOOR(cumulative_tco2e) AS cumulative_issued FROM running
)
SELECT plant_name,
       month,
       eligible_kwh,
       eligible_mwh,
       worst_flag,
       emission_factor,
       factor_verified,
       factor_expired,
       net_reduction_tco2e,
       cumulative_tco2e,
       cumulative_issued,
       cumulative_issued - COALESCE(
           LAG(cumulative_issued) OVER (PARTITION BY plant_name ORDER BY month), 0
       )                                                    AS vcu_issued,
       COALESCE(
           LAG(cumulative_tco2e - cumulative_issued)
               OVER (PARTITION BY plant_name ORDER BY month), 0
       )                                                    AS carry_in_tco2e,
       cumulative_tco2e - cumulative_issued                 AS carry_forward_tco2e
FROM ledger;

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
       v.vcu_issued,
       v.net_reduction_tco2e,
       v.carry_forward_tco2e,
       v.factor_verified,
       v.factor_expired,
       p.vcu_price_per_tco2e,
       p.currency,
       p.vcu_price_as_of,
       p.vcu_price_source,
       v.vcu_issued * COALESCE(p.vcu_price_per_tco2e, 0) AS total_revenue
FROM gold.vcu v
LEFT JOIN gold.price_by_month p ON p.month = v.month;

-- Fleet headline: one row, the numbers on the front screen.
-- The CTEs are plain, not MATERIALIZED: forcing materialisation here blocked
-- the planner and made this query several times slower.
CREATE OR REPLACE VIEW gold.fleet_summary AS
WITH energy AS (
    SELECT COALESCE(SUM(generation_kwh), 0) AS generation_kwh,
           COALESCE(SUM(eligible_kwh), 0)   AS eligible_kwh,
           COALESCE(SUM(excluded_kwh), 0)   AS excluded_kwh
    FROM silver.site_month
), ledger AS (
    SELECT plant_name, month, vcu_issued, carry_forward_tco2e, net_reduction_tco2e,
           factor_verified, factor_expired,
           ROW_NUMBER() OVER (PARTITION BY plant_name ORDER BY month DESC) AS recency
    FROM gold.vcu
), money AS (
    SELECT COALESCE(SUM(total_revenue), 0) AS total_revenue, MAX(currency) AS currency
    FROM gold.revenue
)
SELECT (SELECT COUNT(*) FROM bronze.site)        AS site_count,
       (SELECT COUNT(*) FROM bronze.source_file) AS file_count,
       w.window_start,
       w.window_end,
       e.generation_kwh,
       e.eligible_kwh,
       e.excluded_kwh,
       COALESCE((SELECT SUM(vcu_issued) FROM ledger), 0)                          AS vcu_issued,
       COALESCE((SELECT SUM(carry_forward_tco2e) FROM ledger WHERE recency = 1), 0) AS carry_forward_tco2e,
       (SELECT SUM(net_reduction_tco2e) FROM ledger)                              AS net_reduction_tco2e,
       COALESCE((SELECT bool_and(factor_verified) FROM ledger), false)            AS factor_verified,
       COALESCE((SELECT bool_or(factor_expired)  FROM ledger), false)             AS factor_expired,
       m.total_revenue,
       COALESCE(m.currency, 'USD')               AS currency
FROM energy e
CROSS JOIN money m
CROSS JOIN silver.loaded_window w;

-- 5.4 The fleet table.
CREATE OR REPLACE VIEW gold.fleet_table AS
SELECT s.plant_name,
       s.installed_kwp,
       s.plant_type,
       s.grid_connection_date,
       s.plant_status,
       s.address,
       COALESCE(agg.days_expected, 0)                        AS days_expected,
       COALESCE(agg.days_with_data, 0)                       AS days_covered,
       COALESCE(agg.days_missing, 0)                         AS days_missing,
       COALESCE(agg.days_suspect, 0)                         AS days_suspect,
       COALESCE(agg.generation_kwh, 0)                       AS generation_kwh,
       COALESCE(agg.eligible_kwh, 0)                         AS eligible_kwh,
       CASE WHEN s.installed_kwp > 0 AND COALESCE(agg.days_with_data, 0) > 0
            THEN agg.generation_kwh / s.installed_kwp / agg.days_with_data
       END                                                   AS specific_yield_per_day,
       CASE WHEN COALESCE(agg.days_expected, 0) > 0
            THEN 100.0 * agg.days_ok / agg.days_expected
            ELSE 0 END                                       AS quality_score,
       COALESCE(credits.vcu_issued, 0)                       AS vcu_issued,
       credits.net_reduction_tco2e                           AS net_reduction_tco2e
FROM bronze.site s
LEFT JOIN (
    SELECT plant_name,
           SUM(days_expected)  AS days_expected,
           SUM(days_with_data) AS days_with_data,
           SUM(days_missing)   AS days_missing,
           SUM(days_suspect)   AS days_suspect,
           SUM(days_ok)        AS days_ok,
           SUM(generation_kwh) AS generation_kwh,
           SUM(eligible_kwh)   AS eligible_kwh
    FROM silver.site_month GROUP BY plant_name
) agg ON agg.plant_name = s.plant_name
LEFT JOIN (
    SELECT plant_name,
           SUM(vcu_issued)          AS vcu_issued,
           SUM(net_reduction_tco2e) AS net_reduction_tco2e
    FROM gold.vcu GROUP BY plant_name
) credits ON credits.plant_name = s.plant_name;

-- 4.6 Lineage. Any gold figure resolves to the bronze cells underneath it:
-- gold row -> silver site-days -> bronze readings -> file, hash, row, column.
CREATE OR REPLACE VIEW gold.lineage AS
SELECT g.plant_name,
       g.month,
       g.vcu_issued,
       g.net_reduction_tco2e,
       e.reading_date,
       e.generation_kwh       AS day_generation_kwh,
       e.eligible_kwh         AS day_eligible_kwh,
       e.flag                 AS day_flag,
       e.exclusion_reason,
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
JOIN silver.eligibility e
  ON e.plant_name = g.plant_name
 AND date_trunc('month', e.reading_date)::date = g.month
LEFT JOIN bronze.reading r
  ON r.file_id = e.file_id
 AND r.plant_name = e.plant_name
 AND r.reading_date = e.reading_date
 AND r.metric = 'daily_yield'
LEFT JOIN bronze.source_file f ON f.file_id = r.file_id;
