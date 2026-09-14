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
    vintage       text NOT NULL,
    valid_from    date NOT NULL,
    valid_to      date,
    UNIQUE (factor_type, valid_from)
);

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
WITH running AS (
    SELECT plant_name,
           month,
           eligible_kwh,
           worst_flag,
           SUM(eligible_kwh) OVER (
               PARTITION BY plant_name ORDER BY month
               ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
           ) AS cumulative_eligible_kwh
    FROM silver.site_month
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
       ef.vintage                                                 AS factor_vintage,
       ef.valid_from                                              AS factor_valid_from,
       (sm.eligible_kwh / 1000.0) * ef.value_tco2e_per_mwh        AS baseline_emissions_tco2e,
       0::numeric                                                 AS project_emissions_tco2e,
       0::numeric                                                 AS leakage_tco2e,
       (sm.eligible_kwh / 1000.0) * ef.value_tco2e_per_mwh
           - 0::numeric - 0::numeric                              AS net_reduction_tco2e
FROM silver.site_month sm
LEFT JOIN LATERAL (
    SELECT *
    FROM gold.emission_factor e
    WHERE e.valid_from <= (sm.month + interval '1 month - 1 day')::date
      AND (e.valid_to IS NULL OR e.valid_to >= sm.month)
    ORDER BY e.valid_from DESC
    LIMIT 1
) ef ON true;

-- 4.3 Credits x price.
CREATE OR REPLACE VIEW gold.revenue AS
SELECT i.plant_name,
       i.month,
       i.irec_issued,
       c.net_reduction_tco2e,
       ip.value        AS irec_price_per_mwh,
       ip.currency     AS currency,
       ip.as_of        AS irec_price_as_of,
       vp.value        AS vcu_price_per_tco2e,
       vp.as_of        AS vcu_price_as_of,
       i.irec_issued * COALESCE(ip.value, 0)                AS irec_revenue,
       c.net_reduction_tco2e * COALESCE(vp.value, 0)        AS vcu_revenue,
       i.irec_issued * COALESCE(ip.value, 0)
           + c.net_reduction_tco2e * COALESCE(vp.value, 0)  AS total_revenue
FROM gold.irec i
JOIN gold.carbon c ON c.plant_name = i.plant_name AND c.month = i.month
LEFT JOIN LATERAL (
    SELECT * FROM gold.price p
    WHERE p.instrument = 'irec' AND p.as_of <= (i.month + interval '1 month - 1 day')::date
    ORDER BY p.as_of DESC LIMIT 1
) ip ON true
LEFT JOIN LATERAL (
    SELECT * FROM gold.price p
    WHERE p.instrument = 'vcu' AND p.as_of <= (i.month + interval '1 month - 1 day')::date
    ORDER BY p.as_of DESC LIMIT 1
) vp ON true;

-- Fleet headline: one row, the numbers on the front screen.
CREATE OR REPLACE VIEW gold.fleet_summary AS
SELECT (SELECT COUNT(*) FROM bronze.site)                               AS site_count,
       (SELECT COUNT(*) FROM bronze.source_file)                        AS file_count,
       (SELECT window_start FROM silver.loaded_window)                  AS window_start,
       (SELECT window_end   FROM silver.loaded_window)                  AS window_end,
       COALESCE((SELECT SUM(generation_kwh) FROM silver.eligibility), 0) AS generation_kwh,
       COALESCE((SELECT SUM(eligible_kwh)   FROM silver.eligibility), 0) AS eligible_kwh,
       COALESCE((SELECT SUM(excluded_kwh)   FROM silver.eligibility), 0) AS excluded_kwh,
       COALESCE((SELECT SUM(irec_issued)    FROM gold.irec), 0)          AS irec_issued,
       COALESCE((SELECT SUM(carry_forward_mwh) FROM (
            SELECT DISTINCT ON (plant_name) plant_name, carry_forward_mwh
            FROM gold.irec ORDER BY plant_name, month DESC) last_month), 0) AS carry_forward_mwh,
       COALESCE((SELECT SUM(net_reduction_tco2e) FROM gold.carbon), 0)   AS net_reduction_tco2e,
       COALESCE((SELECT SUM(total_revenue) FROM gold.revenue), 0)        AS total_revenue,
       COALESCE((SELECT currency FROM gold.revenue WHERE currency IS NOT NULL LIMIT 1), 'USD') AS currency;

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
       COALESCE(irec.irec_issued, 0)                         AS irec_issued,
       COALESCE(carbon.net_reduction_tco2e, 0)               AS net_reduction_tco2e
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
LEFT JOIN (SELECT plant_name, SUM(irec_issued) AS irec_issued FROM gold.irec GROUP BY plant_name) irec
       ON irec.plant_name = s.plant_name
LEFT JOIN (SELECT plant_name, SUM(net_reduction_tco2e) AS net_reduction_tco2e FROM gold.carbon GROUP BY plant_name) carbon
       ON carbon.plant_name = s.plant_name;

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
