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
    vintage       text NOT NULL,
    valid_from    date NOT NULL,
    valid_to      date,
    -- A factor is unverified until a person has checked it against the source
    -- document named above. The default is false on purpose: a number nobody
    -- has checked must never present itself as one that someone has.
    verified      boolean NOT NULL DEFAULT false,
    verified_by   text,
    verified_at   timestamptz,
    UNIQUE (factor_type, valid_from)
);

ALTER TABLE gold.emission_factor ADD COLUMN IF NOT EXISTS source_url  text;
ALTER TABLE gold.emission_factor ADD COLUMN IF NOT EXISTS verified    boolean NOT NULL DEFAULT false;
ALTER TABLE gold.emission_factor ADD COLUMN IF NOT EXISTS verified_by text;
ALTER TABLE gold.emission_factor ADD COLUMN IF NOT EXISTS verified_at timestamptz;

CREATE TABLE IF NOT EXISTS gold.price (
    price_id    bigserial PRIMARY KEY,
    instrument  text NOT NULL CHECK (instrument IN ('irec', 'vcu')),
    value       numeric(12, 4) NOT NULL,  -- per MWh for I-REC, per tCO2e for VCU
    currency    text NOT NULL DEFAULT 'USD',
    source      text,
    as_of       date NOT NULL,
    UNIQUE (instrument, as_of)
);

-- 4.1 Whole MWh issuable per site per month, with the remainder carried
-- forward rather than rounded away. The ledger is the running total: each
-- month issues the difference between this month's floor and last month's.
CREATE OR REPLACE VIEW gold.irec AS
WITH sm AS (SELECT * FROM silver.site_month),
running AS (
    SELECT plant_name,
           month,
           eligible_kwh,
           worst_flag,
           SUM(eligible_kwh) OVER (
               PARTITION BY plant_name ORDER BY month
               ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
           ) AS cumulative_eligible_kwh
    FROM sm
), ledger AS (
    SELECT plant_name,
           month,
           eligible_kwh,
           worst_flag,
           eligible_kwh / 1000.0                          AS eligible_mwh,
           cumulative_eligible_kwh / 1000.0               AS cumulative_eligible_mwh,
           FLOOR(cumulative_eligible_kwh / 1000.0)        AS cumulative_issued_mwh
    FROM running
)
SELECT plant_name,
       month,
       eligible_kwh,
       eligible_mwh,
       worst_flag,
       cumulative_eligible_mwh,
       cumulative_issued_mwh,
       cumulative_issued_mwh - COALESCE(
           LAG(cumulative_issued_mwh) OVER (PARTITION BY plant_name ORDER BY month), 0
       )                                                   AS irec_issued,
       COALESCE(
           LAG(cumulative_eligible_mwh - cumulative_issued_mwh)
               OVER (PARTITION BY plant_name ORDER BY month), 0
       )                                                   AS carry_in_mwh,
       cumulative_eligible_mwh - cumulative_issued_mwh      AS carry_forward_mwh
FROM ledger;

-- 4.2 Eligible MWh x the emission factor, joined on date so the factor's
-- version travels with the result instead of being baked into it.
-- Project emissions and leakage are explicit zeros so the formula reads complete.
--
-- The factor is resolved once per distinct month rather than once per site-month
-- row: as a correlated lookup this view cost hundreds of milliseconds on a few
-- dozen rows, and everything above it paid that cost again.
CREATE OR REPLACE VIEW gold.factor_by_month AS
SELECT DISTINCT ON (m.month)
       m.month,
       e.factor_id,
       e.value_tco2e_per_mwh,
       e.factor_type,
       e.source,
       e.source_url,
       e.vintage,
       e.valid_from,
       COALESCE(e.verified, false) AS verified
FROM (SELECT DISTINCT month FROM silver.site_month) m
LEFT JOIN gold.emission_factor e
       ON e.valid_from <= (m.month + interval '1 month - 1 day')::date
      AND (e.valid_to IS NULL OR e.valid_to >= m.month)
ORDER BY m.month, e.valid_from DESC;

CREATE OR REPLACE VIEW gold.carbon AS
SELECT sm.plant_name,
       sm.month,
       sm.eligible_kwh,
       sm.eligible_kwh / 1000.0                                   AS eligible_mwh,
       sm.worst_flag,
       ef.factor_id,
       ef.value_tco2e_per_mwh                                     AS emission_factor,
       ef.factor_type,
       ef.source                                                  AS factor_source,
       ef.source_url                                              AS factor_source_url,
       ef.vintage                                                 AS factor_vintage,
       ef.valid_from                                              AS factor_valid_from,
       COALESCE(ef.verified, false)                               AS factor_verified,
       (sm.eligible_kwh / 1000.0) * ef.value_tco2e_per_mwh        AS baseline_emissions_tco2e,
       0::numeric                                                 AS project_emissions_tco2e,
       0::numeric                                                 AS leakage_tco2e,
       (sm.eligible_kwh / 1000.0) * ef.value_tco2e_per_mwh
           - 0::numeric - 0::numeric                              AS net_reduction_tco2e
FROM silver.site_month sm
LEFT JOIN gold.factor_by_month ef ON ef.month = sm.month;

-- 4.3 Credits x price. Prices resolve per distinct month, for the same reason.
CREATE OR REPLACE VIEW gold.price_by_month AS
SELECT m.month,
       ip.value    AS irec_price_per_mwh,
       ip.currency AS currency,
       ip.as_of    AS irec_price_as_of,
       vp.value    AS vcu_price_per_tco2e,
       vp.as_of    AS vcu_price_as_of
FROM (SELECT DISTINCT month FROM silver.site_month) m
LEFT JOIN LATERAL (
    SELECT * FROM gold.price p
    WHERE p.instrument = 'irec' AND p.as_of <= (m.month + interval '1 month - 1 day')::date
    ORDER BY p.as_of DESC LIMIT 1
) ip ON true
LEFT JOIN LATERAL (
    SELECT * FROM gold.price p
    WHERE p.instrument = 'vcu' AND p.as_of <= (m.month + interval '1 month - 1 day')::date
    ORDER BY p.as_of DESC LIMIT 1
) vp ON true;

CREATE OR REPLACE VIEW gold.revenue AS
SELECT i.plant_name,
       i.month,
       i.irec_issued,
       c.net_reduction_tco2e,
       p.irec_price_per_mwh,
       p.currency,
       p.irec_price_as_of,
       p.vcu_price_per_tco2e,
       p.vcu_price_as_of,
       i.irec_issued * COALESCE(p.irec_price_per_mwh, 0)                AS irec_revenue,
       c.net_reduction_tco2e * COALESCE(p.vcu_price_per_tco2e, 0)       AS vcu_revenue,
       i.irec_issued * COALESCE(p.irec_price_per_mwh, 0)
           + COALESCE(c.net_reduction_tco2e, 0) * COALESCE(p.vcu_price_per_tco2e, 0) AS total_revenue
FROM gold.irec i
JOIN gold.carbon c ON c.plant_name = i.plant_name AND c.month = i.month
LEFT JOIN gold.price_by_month p ON p.month = i.month;

-- Fleet headline: one row, the numbers on the front screen.
-- The CTEs are MATERIALIZED deliberately: written as independent scalar
-- subqueries, each one re-walked Bronze through the whole view stack, and the
-- single most-looked-at query in the app took seconds rather than milliseconds.
CREATE OR REPLACE VIEW gold.fleet_summary AS
WITH energy AS (
    SELECT COALESCE(SUM(generation_kwh), 0) AS generation_kwh,
           COALESCE(SUM(eligible_kwh), 0)   AS eligible_kwh,
           COALESCE(SUM(excluded_kwh), 0)   AS excluded_kwh
    FROM silver.site_month
), ledger AS (
    SELECT plant_name, month, irec_issued, carry_forward_mwh,
           ROW_NUMBER() OVER (PARTITION BY plant_name ORDER BY month DESC) AS recency
    FROM gold.irec
), money AS (
    SELECT COALESCE(SUM(net_reduction_tco2e), 0) AS net_reduction_tco2e,
           COALESCE(SUM(total_revenue), 0)       AS total_revenue,
           MAX(currency)                         AS currency
    FROM gold.revenue
)
SELECT (SELECT COUNT(*) FROM bronze.site)              AS site_count,
       (SELECT COUNT(*) FROM bronze.source_file)       AS file_count,
       w.window_start,
       w.window_end,
       e.generation_kwh,
       e.eligible_kwh,
       e.excluded_kwh,
       COALESCE((SELECT SUM(irec_issued) FROM ledger), 0)                        AS irec_issued,
       COALESCE((SELECT SUM(carry_forward_mwh) FROM ledger WHERE recency = 1), 0) AS carry_forward_mwh,
       m.net_reduction_tco2e,
       m.total_revenue,
       COALESCE(m.currency, 'USD')                     AS currency
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
       COALESCE(credits.irec_issued, 0)                      AS irec_issued,
       COALESCE(credits.net_reduction_tco2e, 0)              AS net_reduction_tco2e
FROM bronze.site s
LEFT JOIN (
    SELECT plant_name,
           SUM(days_expected) AS days_expected,
           SUM(days_with_data) AS days_with_data,
           SUM(days_missing)  AS days_missing,
           SUM(days_suspect)  AS days_suspect,
           SUM(days_ok)       AS days_ok,
           SUM(generation_kwh) AS generation_kwh,
           SUM(eligible_kwh)   AS eligible_kwh
    FROM silver.site_month GROUP BY plant_name
) agg ON agg.plant_name = s.plant_name
LEFT JOIN (
    SELECT plant_name,
           SUM(irec_issued)          AS irec_issued,
           SUM(net_reduction_tco2e)  AS net_reduction_tco2e
    FROM gold.revenue GROUP BY plant_name
) credits ON credits.plant_name = s.plant_name;

-- 4.4 Lineage. Any gold figure resolves to the bronze cells underneath it:
-- gold row -> silver site-days -> bronze readings -> file, hash, row, column.
CREATE OR REPLACE VIEW gold.lineage AS
SELECT g.plant_name,
       g.month,
       g.irec_issued,
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
FROM gold.irec g
JOIN silver.eligibility e
  ON e.plant_name = g.plant_name
 AND date_trunc('month', e.reading_date)::date = g.month
LEFT JOIN bronze.reading r
  ON r.file_id = e.file_id
 AND r.plant_name = e.plant_name
 AND r.reading_date = e.reading_date
 AND r.metric = 'daily_yield'
LEFT JOIN bronze.source_file f ON f.file_id = r.file_id;
