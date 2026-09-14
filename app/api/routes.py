"""HTTP API.

Every endpoint is a thin wrapper over a Silver or Gold view. There is no
calculation in Python: if a number appears on screen, a SQL statement produced
it, and that statement is the one shown to a verifier.
"""
from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, File, Header, HTTPException, Query, UploadFile

from app.config import settings
from app.db import query, query_one
from app.ingest.loader import load_bytes

router = APIRouter(prefix="/api")


def _factor_warning(carbon: dict) -> str | None:
    """Say plainly what a reader should know about this carbon figure."""
    if carbon.get("emission_factor") is None:
        return (
            "No emission factor is on file, so no emission reduction and no VCUs "
            "are claimed. Nothing stands in for it."
        )
    notes = []
    if carbon.get("factor_beyond_validity"):
        notes.append(
            "This is the most recent approved grid factor for Armenia, and it is "
            "applied to months after the publication's own stated validity "
            f"({carbon.get('factor_published_valid_to')}). A verifier will want a "
            "successor baseline once one is approved."
        )
    if not carbon.get("factor_verified"):
        notes.append(
            "Nobody has signed off that this factor was checked against the source "
            "document. Set EMISSION_FACTOR_VERIFIED_BY to record who did."
        )
    return " ".join(notes) or None


# How a site is named on screen. A pseudonym by default: opening the portal
# should not disclose which client a site belongs to.
SITE_LABEL = (
    "s.plant_name" if settings.show_real_site_names
    else "COALESCE(s.site_code, 'SITE-' || upper(substr(md5(s.plant_name), 1, 6)))"
)


def require_token(x_admin_token: str | None = Header(default=None)) -> None:
    """Open by default. Set ADMIN_TOKEN to require a token on writes."""
    if not settings.admin_token:
        return
    if x_admin_token != settings.admin_token:
        raise HTTPException(status_code=401, detail="Missing or invalid X-Admin-Token header")


# --- Context ---------------------------------------------------------------

@router.get("/health")
def health() -> dict:
    try:
        query_one("SELECT 1 AS ok")
        db_ok = True
    except Exception as exc:  # surfaced so a failing deploy says why
        return {"status": "degraded", "database": False, "detail": str(exc)}
    return {"status": "ok", "database": db_ok, "env": settings.app_env}


@router.get("/context")
def context() -> dict:
    """Banner, assumptions and reference data — shown on every screen."""
    assumptions = settings.assumptions()
    active = query_one(
        """
        SELECT value_tco2e_per_mwh, factor_type, source, vintage, valid_from, valid_to,
               project_types, verified
        FROM gold.emission_factor WHERE active ORDER BY valid_from DESC LIMIT 1
        """
    )
    for item in assumptions:
        if item["key"] != "emission_factor":
            continue
        if not active:
            break
        item["value"] = f"{float(active['value_tco2e_per_mwh']):g} tCO2e/MWh"
        item["note"] = (
            f"{active['source']} — {active['factor_type'].replace('_', ' ')}, "
            f"valid {active['valid_from']} to {active['valid_to'] or 'open'}"
            + ("" if active["verified"] else ". Unverified: nobody has checked it "
                                             "against that document yet.")
        )
    return {
        "banner": settings.banner_text,
        "fleet_name": settings.fleet_name,
        "env": settings.app_env,
        "real_names": settings.show_real_site_names,
        "assumptions": assumptions,
        "emission_factors": query(
            "SELECT * FROM gold.emission_factor ORDER BY valid_from DESC"
        ),
        "prices": query("SELECT * FROM gold.price ORDER BY instrument, as_of DESC"),
    }


@router.get("/summary")
def summary() -> dict:
    row = query_one("SELECT * FROM gold.fleet_summary") or {}
    coverage = query_one(
        """
        SELECT COUNT(*) AS site_days,
               COUNT(*) FILTER (WHERE flag = 'ok')      AS days_ok,
               COUNT(*) FILTER (WHERE flag = 'suspect') AS days_suspect,
               COUNT(*) FILTER (WHERE flag = 'missing') AS days_missing
        FROM silver.quality_flag
        """
    ) or {}
    site_days = coverage.get("site_days") or 0
    return {
        **row,
        **coverage,
        "coverage_pct": round(100.0 * (site_days - (coverage.get("days_missing") or 0)) / site_days, 1)
        if site_days else 0.0,
    }


# --- 5.2 The river ---------------------------------------------------------

@router.get("/river")
def river() -> dict:
    total = query_one(
        "SELECT COALESCE(SUM(generation_kwh),0) AS generation_kwh, "
        "COALESCE(SUM(eligible_kwh),0) AS eligible_kwh FROM silver.eligibility"
    ) or {}
    streams = query("SELECT * FROM silver.exclusion_summary ORDER BY excluded_kwh DESC")
    credits = query_one(
        "SELECT COALESCE(SUM(vcu_issued),0) AS vcu_issued FROM gold.vcu"
    ) or {}
    carbon = query_one(
        "SELECT COALESCE(SUM(net_reduction_tco2e),0) AS net_reduction_tco2e FROM gold.carbon"
    ) or {}
    revenue = query_one(
        "SELECT COALESCE(SUM(total_revenue),0) AS total_revenue, "
        "MAX(currency) AS currency FROM gold.revenue"
    ) or {}
    return {
        "generation_kwh": total.get("generation_kwh", 0),
        "eligible_kwh": total.get("eligible_kwh", 0),
        "exclusions": streams,
        "vcu_issued": credits.get("vcu_issued", 0),
        "net_reduction_tco2e": carbon.get("net_reduction_tco2e", 0),
        "total_revenue": revenue.get("total_revenue", 0),
        "currency": revenue.get("currency") or settings.price_currency,
    }


# --- 5.3 Drill-down: credits -> months -> sites -> days -> bronze cells -----

@router.get("/drill/months")
def drill_months() -> list[dict]:
    return query(
        """
        SELECT f.month,
               f.generation_kwh, f.eligible_kwh, f.excluded_kwh,
               f.site_count, f.days_expected, f.days_missing, f.days_suspect, f.worst_flag,
               CASE WHEN f.days_expected > 0
                    THEN 100.0 * (f.days_expected - f.days_missing) / f.days_expected
                    ELSE 0 END AS coverage_pct,
               COALESCE(g.vcu_issued, 0)           AS vcu_issued,
               COALESCE(g.net_reduction_tco2e, 0)  AS net_reduction_tco2e,
               COALESCE(g.total_revenue, 0)        AS total_revenue
        FROM silver.fleet_month f
        -- gold.revenue already carries the credits and the carbon, so this is
        -- one pass over the chain rather than three.
        LEFT JOIN (
            SELECT month,
                   SUM(vcu_issued)          AS vcu_issued,
                   SUM(net_reduction_tco2e) AS net_reduction_tco2e,
                   SUM(total_revenue)       AS total_revenue
            FROM gold.revenue GROUP BY month
        ) g ON g.month = f.month
        ORDER BY f.month
        """
    )


@router.get("/drill/months/{month}/sites")
def drill_sites(month: dt.date) -> list[dict]:
    return query(
        """
        SELECT i.label AS site, i.site_code, i.region, i.installed_kwp,
               sm.generation_kwh, sm.eligible_kwh, sm.excluded_kwh,
               sm.days_expected, sm.days_with_data, sm.days_missing,
               sm.days_suspect, sm.worst_flag,
               CASE WHEN sm.days_expected > 0
                    THEN 100.0 * (sm.days_expected - sm.days_missing) / sm.days_expected
                    ELSE 0 END AS coverage_pct,
               COALESCE(g.vcu_issued, 0)          AS vcu_issued,
               COALESCE(g.net_reduction_tco2e, 0) AS net_reduction_tco2e
        FROM silver.site_month sm
        JOIN silver.site_identity i ON i.plant_name = sm.plant_name
        LEFT JOIN gold.revenue g
               ON g.plant_name = sm.plant_name AND g.month = sm.month
        WHERE sm.month = %s
        ORDER BY sm.eligible_kwh DESC
        """,
        (month,),
    )


def _plant_for(site_code: str) -> str:
    """Resolve a pseudonym back to the real key. The code never leaves the URL."""
    row = query_one(
        "SELECT plant_name FROM silver.site_identity WHERE site_code = %s OR plant_name = %s",
        (site_code, site_code),
    )
    if not row:
        raise HTTPException(status_code=404, detail="No such site")
    return row["plant_name"]


@router.get("/drill/sites/{site_code}/days")
def drill_days(site_code: str, month: dt.date | None = None) -> list[dict]:
    plant_name = _plant_for(site_code)
    return query(
        """
        SELECT e.reading_date, e.generation_kwh, e.eligible_kwh, e.excluded_kwh,
               e.exclusion_reason, e.flag, e.specific_yield, e.installed_kwp,
               q.flag_reason, q.is_missing, q.is_zero, q.is_implausible,
               q.is_before_grid_connection, q.is_duplicate, q.is_site_inactive,
               e.file_id, e.source_row
        FROM silver.eligibility e
        JOIN silver.quality_flag q
          ON q.plant_name = e.plant_name AND q.reading_date = e.reading_date
        WHERE e.plant_name = %s
          AND (%s::date IS NULL OR date_trunc('month', e.reading_date)::date = %s)
        ORDER BY e.reading_date
        """,
        (plant_name, month, month),
    )


@router.get("/drill/sites/{site_code}/days/{day}/readings")
def drill_readings(site_code: str, day: dt.date) -> dict:
    """The bottom of the drill: the actual cells, with file, hash, row, column."""
    plant_name = _plant_for(site_code)
    rows = query(
        """
        SELECT r.reading_id, r.device_sn, r.metric, r.metric_label, r.value, r.unit,
               r.source_row, r.source_col,
               f.file_id, f.filename, f.sha256, f.grain, f.uploaded_at, f.sheet_name,
               CASE WHEN g.file_id = f.file_id THEN true ELSE false END AS is_winner
        FROM bronze.reading r
        JOIN bronze.source_file f ON f.file_id = r.file_id
        LEFT JOIN silver.generation_daily g
               ON g.plant_name = r.plant_name AND g.reading_date = r.reading_date
        WHERE r.plant_name = %s AND r.reading_date = %s
        ORDER BY is_winner DESC, r.metric, r.device_sn NULLS FIRST, r.source_row
        """,
        (plant_name, day),
    )
    return {"site_code": site_code, "reading_date": day.isoformat(), "readings": rows}


# --- 4.4 Lineage -----------------------------------------------------------

@router.get("/lineage")
def lineage(
    plant_name: str = Query(...),
    month: dt.date = Query(...),
    limit: int = Query(500, le=5000),
) -> dict:
    """Any gold figure to the cells underneath it, in one call."""
    gold_row = query_one(
        """
        SELECT v.plant_name, v.month, v.eligible_kwh, v.eligible_mwh, v.vcu_issued,
               v.carry_in_tco2e, v.carry_forward_tco2e, v.net_reduction_tco2e,
               v.emission_factor, v.factor_verified, v.factor_beyond_validity,
               c.factor_source, c.factor_source_url, c.factor_vintage,
               c.factor_valid_from, c.factor_valid_to, c.factor_project_types,
               r.total_revenue, r.currency
        FROM gold.vcu v
        LEFT JOIN gold.carbon  c ON c.plant_name = v.plant_name AND c.month = v.month
        LEFT JOIN gold.revenue r ON r.plant_name = v.plant_name AND r.month = v.month
        WHERE v.plant_name = %s AND v.month = %s
        """,
        (plant_name, month),
    )
    if not gold_row:
        raise HTTPException(status_code=404, detail="No gold row for that site and month")
    cells = query(
        """
        SELECT reading_date, day_generation_kwh, day_eligible_kwh, day_flag,
               exclusion_reason, reading_id, device_sn, metric, cell_value, unit,
               source_row, source_col, file_id, filename, sha256, grain
        FROM gold.lineage
        WHERE plant_name = %s AND month = %s
        ORDER BY reading_date, source_row
        LIMIT %s
        """,
        (plant_name, month, limit),
    )
    files = query(
        """
        SELECT DISTINCT f.file_id, f.filename, f.sha256, f.grain, f.uploaded_at,
               f.storage_path, f.header_row, f.sheet_name
        FROM gold.lineage l
        JOIN bronze.source_file f ON f.file_id = l.file_id
        WHERE l.plant_name = %s AND l.month = %s
        """,
        (plant_name, month),
    )
    return {"gold": gold_row, "cells": cells, "files": files, "cell_count": len(cells)}


# --- 5.4 Fleet table -------------------------------------------------------

@router.get("/fleet")
def fleet() -> list[dict]:
    return query(
        """
        SELECT i.label AS site, i.site_code, i.region, i.country, i.address,
               i.plant_type, f.installed_kwp, f.grid_connection_date, f.plant_status,
               f.days_expected, f.days_covered, f.days_missing, f.days_suspect,
               f.generation_kwh, f.eligible_kwh, f.specific_yield_per_day,
               f.quality_score, f.vcu_issued, f.net_reduction_tco2e
        FROM gold.fleet_table f
        JOIN silver.site_identity i ON i.plant_name = f.plant_name
        ORDER BY i.label
        """
    )


# --- 5.5 Calculation panel -------------------------------------------------

@router.get("/calculation")
def calculation(month: dt.date | None = None) -> dict:
    """The formula with this period's actual numbers substituted in."""
    scope = "month" if month else "all loaded months"
    totals = query_one(
        """
        SELECT COALESCE(SUM(sm.generation_kwh),0) AS generation_kwh,
               COALESCE(SUM(sm.eligible_kwh),0)   AS eligible_kwh,
               COALESCE(SUM(sm.excluded_kwh),0)   AS excluded_kwh
        FROM silver.site_month sm
        WHERE (%s::date IS NULL OR sm.month = %s)
        """,
        (month, month),
    ) or {}
    vcu = query_one(
        """
        WITH scoped AS (
            SELECT plant_name, month, vcu_issued, carry_forward_tco2e,
                   ROW_NUMBER() OVER (PARTITION BY plant_name ORDER BY month DESC) AS recency
            FROM gold.vcu
            WHERE (%s::date IS NULL OR month = %s)
        )
        SELECT COALESCE(SUM(vcu_issued), 0) AS vcu_issued,
               -- the balance standing at the end of the period, per site,
               -- not the sum of every month's running balance
               COALESCE(SUM(carry_forward_tco2e) FILTER (WHERE recency = 1), 0)
                   AS carry_forward_tco2e
        FROM scoped
        """,
        (month, month),
    ) or {}
    carbon = query_one(
        """
        SELECT COALESCE(SUM(net_reduction_tco2e),0) AS net_reduction_tco2e,
               MAX(emission_factor) AS emission_factor,
               MAX(factor_source)   AS factor_source,
               MAX(factor_source_url) AS factor_source_url,
               MAX(factor_vintage)  AS factor_vintage,
               MAX(factor_valid_from) AS factor_valid_from,
               MAX(factor_type)     AS factor_type,
               MAX(factor_project_types) AS factor_project_types,
               MAX(factor_valid_to)  AS factor_valid_to,
               bool_and(COALESCE(factor_verified, false)) AS factor_verified,
               bool_or(COALESCE(factor_beyond_validity, false))   AS factor_beyond_validity,
               count(*) FILTER (WHERE emission_factor IS NULL) AS months_without_factor
        FROM gold.carbon WHERE (%s::date IS NULL OR month = %s)
        """,
        (month, month),
    ) or {}
    revenue = query_one(
        """
        SELECT COALESCE(SUM(total_revenue),0) AS total_revenue,
               MAX(vcu_price_per_tco2e) AS vcu_price_per_tco2e,
               MAX(vcu_price_source)    AS vcu_price_source,
               MAX(currency) AS currency
        FROM gold.revenue WHERE (%s::date IS NULL OR month = %s)
        """,
        (month, month),
    ) or {}
    eligible_mwh = float(totals.get("eligible_kwh") or 0) / 1000.0
    return {
        "scope": scope,
        "month": month.isoformat() if month else None,
        **totals,
        "eligible_mwh": eligible_mwh,
        **vcu,
        **carbon,
        **revenue,
        "currency": revenue.get("currency") or settings.price_currency,
        "steps": [
            {
                "label": "Eligible energy",
                "formula": "generation_kwh − excluded_kwh",
                "substituted": f"{float(totals.get('generation_kwh') or 0):,.1f} − {float(totals.get('excluded_kwh') or 0):,.1f}",
                "result": f"{float(totals.get('eligible_kwh') or 0):,.1f} kWh",
                "source": "silver.eligibility",
            },
            {
                "label": "Emission reduction",
                "formula": "eligible_MWh × emission_factor − project_emissions − leakage",
                "substituted": (
                    f"{eligible_mwh:,.3f} × {float(carbon.get('emission_factor') or 0):g} − 0 − 0"
                    if carbon.get("emission_factor") is not None
                    else f"{eligible_mwh:,.3f} × (no applicable emission factor)"
                ),
                "result": (
                    f"{float(carbon.get('net_reduction_tco2e') or 0):,.3f} tCO₂e"
                    if carbon.get("emission_factor") is not None
                    else "not calculable"
                ),
                "source": "gold.carbon",
                "warning": _factor_warning(carbon),
            },
            {
                "label": "VCUs issuable",
                "formula": "floor( cumulative tCO₂e ) − already issued",
                "substituted": (
                    f"{float(carbon.get('net_reduction_tco2e') or 0):,.3f} tCO₂e, whole tonnes only, "
                    f"{float(vcu.get('carry_forward_tco2e') or 0):,.3f} carried forward"
                ),
                "result": f"{float(vcu.get('vcu_issued') or 0):,.0f} VCU",
                "source": "gold.vcu",
            },
            {
                "label": "Indicative revenue",
                "formula": "VCU × price_per_tCO₂e",
                "substituted": (
                    f"{float(vcu.get('vcu_issued') or 0):,.0f} × {float(revenue.get('vcu_price_per_tco2e') or 0):g}"
                    if revenue.get("vcu_price_per_tco2e") is not None
                    else f"{float(vcu.get('vcu_issued') or 0):,.0f} × (no VCU price set)"
                ),
                "result": (
                    f"{revenue.get('currency') or settings.price_currency} {float(revenue.get('total_revenue') or 0):,.2f}"
                    if revenue.get("vcu_price_per_tco2e") is not None
                    else "not calculable"
                ),
                "source": "gold.revenue",
                "warning": (
                    None if revenue.get("vcu_price_per_tco2e") is not None
                    else "No VCU price is set, so no revenue is shown. Set DEFAULT_VCU_PRICE_PER_TCO2E "
                         "with the quote or index it came from."
                ),
            },
        ],
    }


# --- 5.6 Data quality panel ------------------------------------------------

@router.get("/quality")
def quality() -> dict:
    coverage = query_one(
        """
        SELECT COUNT(*) AS site_days,
               COUNT(*) FILTER (WHERE flag = 'ok')      AS days_ok,
               COUNT(*) FILTER (WHERE flag = 'suspect') AS days_suspect,
               COUNT(*) FILTER (WHERE flag = 'missing') AS days_missing
        FROM silver.quality_flag
        """
    ) or {}
    gaps = query(
        """
        SELECT i.label AS site, i.site_code, i.region,
               MIN(runs.reading_date) AS gap_start, MAX(runs.reading_date) AS gap_end,
               COUNT(*) AS days
        FROM (
            SELECT plant_name, reading_date,
                   reading_date - (ROW_NUMBER() OVER (PARTITION BY plant_name ORDER BY reading_date))::int AS grp
            FROM silver.quality_flag WHERE flag = 'missing'
        ) runs
        JOIN silver.site_identity i ON i.plant_name = runs.plant_name
        GROUP BY i.label, i.site_code, i.region, runs.grp
        ORDER BY days DESC, i.label
        """
    )
    suspects = query(
        """
        SELECT i.label AS site, i.site_code, q.reading_date, q.generation_kwh,
               q.specific_yield, q.flag_reason,
               q.is_zero, q.is_implausible, q.is_duplicate, q.is_negative
        FROM silver.quality_flag q
        JOIN silver.site_identity i ON i.plant_name = q.plant_name
        WHERE q.flag = 'suspect'
        ORDER BY q.reading_date, i.label
        LIMIT 500
        """
    )
    rules = query_one("SELECT * FROM silver.rules") or {}
    site_days = coverage.get("site_days") or 0
    return {
        **coverage,
        "coverage_pct": round(100.0 * (site_days - (coverage.get("days_missing") or 0)) / site_days, 1)
        if site_days else 0.0,
        "gaps": gaps,
        "suspects": suspects,
        "rules": rules,
        "overlaps": query(
            """
            SELECT o.overlap_id, o.overlap_start, o.overlap_end, o.grain,
                   nf.filename AS new_filename, nf.file_id AS new_file_id,
                   ef.filename AS existing_filename, ef.file_id AS existing_file_id
            FROM bronze.file_overlap o
            JOIN bronze.source_file nf ON nf.file_id = o.new_file_id
            JOIN bronze.source_file ef ON ef.file_id = o.existing_file_id
            ORDER BY o.detected_at DESC
            """
        ),
        "superseded_count": (query_one("SELECT COUNT(*) AS n FROM silver.superseded_site_day") or {}).get("n", 0),
    }


# --- Energy browser --------------------------------------------------------

@router.get("/energy")
def energy(
    group: str = Query("month", pattern="^(month|day|site|region)$"),
    site: str | None = None,
    month: dt.date | None = None,
) -> dict:
    """Energy generated, grouped the way the reader wants to see it.

    One endpoint rather than four: the grouping is a column choice, and the
    filters narrow the same underlying site-day rows.
    """
    plant = _plant_for(site) if site else None
    where = "WHERE (%(plant)s::text IS NULL OR e.plant_name = %(plant)s) " \
            "AND (%(month)s::date IS NULL OR date_trunc('month', e.reading_date)::date = %(month)s)"
    params = {"plant": plant, "month": month}

    key = {
        "month": "date_trunc('month', e.reading_date)::date",
        "day": "e.reading_date",
        "site": "i.label",
        "region": "COALESCE(i.region, 'Unknown')",
    }[group]

    rows = query(
        f"""
        WITH base AS (
            SELECT {key}                       AS bucket,
                   e.plant_name,
                   e.generation_kwh, e.eligible_kwh, e.excluded_kwh, e.flag,
                   i.installed_kwp
            FROM silver.eligibility e
            JOIN silver.site_identity i ON i.plant_name = e.plant_name
            {where}
        ),
        -- Capacity is a property of a site, so it is summed over the distinct
        -- sites in a bucket, never over its site-days.
        caps AS (
            SELECT bucket, SUM(installed_kwp) AS installed_kwp
            FROM (SELECT DISTINCT bucket, plant_name, installed_kwp FROM base) d
            GROUP BY bucket
        )
        SELECT b.bucket::text                                  AS bucket,
               SUM(b.generation_kwh)                           AS generation_kwh,
               SUM(b.eligible_kwh)                             AS eligible_kwh,
               SUM(b.excluded_kwh)                             AS excluded_kwh,
               COUNT(*)                                        AS site_days,
               COUNT(*) FILTER (WHERE b.flag = 'missing')      AS days_missing,
               COUNT(*) FILTER (WHERE b.flag = 'suspect')      AS days_suspect,
               COUNT(DISTINCT b.plant_name)                    AS site_count,
               c.installed_kwp,
               -- kWh per kWp per day, weighted by capacity: the denominator
               -- sums each site's kWp once per day it actually reported, which
               -- is exactly capacity x days-with-data.
               CASE WHEN SUM(b.installed_kwp) FILTER (WHERE b.flag <> 'missing') > 0
                    THEN SUM(b.generation_kwh)
                         / SUM(b.installed_kwp) FILTER (WHERE b.flag <> 'missing')
               END                                             AS specific_yield_per_day,
               CASE WHEN COUNT(*) > 0
                    THEN 100.0 * COUNT(*) FILTER (WHERE b.flag <> 'missing') / COUNT(*)
                    ELSE 0 END                                 AS coverage_pct
        FROM base b
        LEFT JOIN caps c ON c.bucket = b.bucket
        GROUP BY b.bucket, c.installed_kwp
        ORDER BY 1
        """,
        params,
    )
    totals = query_one(
        f"""
        SELECT COALESCE(SUM(e.generation_kwh), 0) AS generation_kwh,
               COALESCE(SUM(e.eligible_kwh), 0)   AS eligible_kwh,
               COALESCE(SUM(e.excluded_kwh), 0)   AS excluded_kwh,
               COUNT(DISTINCT e.plant_name)       AS site_count
        FROM silver.eligibility e
        JOIN silver.site_identity i ON i.plant_name = e.plant_name
        {where}
        """,
        params,
    ) or {}
    capacity = query_one(
        """
        SELECT COALESCE(SUM(installed_kwp), 0) AS installed_kwp,
               COUNT(*) FILTER (WHERE installed_kwp IS NULL) AS sites_without_capacity
        FROM silver.site_identity
        WHERE (%(plant)s::text IS NULL OR plant_name = %(plant)s)
        """,
        params,
    ) or {}
    return {"group": group, "rows": rows, "totals": {**totals, **capacity},
            "site": site, "month": month.isoformat() if month else None}


# --- Reconciliation --------------------------------------------------------

@router.get("/reconcile")
def reconcile(verbose: bool = False) -> dict:
    """Recompute every figure independently and report the comparison.

    The checks live in cli/reconcile.py and never read the Silver or Gold
    views — they rebuild the same numbers in Python from the Bronze cells and
    from a fresh parse of the stored original files. Agreement between two
    implementations is the point; this endpoint just surfaces it.
    """
    from dataclasses import asdict
    import cli.reconcile as rec

    results = rec.run(verbose)
    return {
        "ok": not any(r.status == "fail" for r in results),
        "passed": sum(1 for r in results if r.status == "pass"),
        "failed": sum(1 for r in results if r.status == "fail"),
        "skipped": sum(1 for r in results if r.status == "skip"),
        "checks": [asdict(r) for r in results],
    }


# --- 5.1 Upload ------------------------------------------------------------

@router.get("/files")
def files() -> list[dict]:
    return query(
        """
        SELECT f.file_id, f.filename, f.sha256, f.bytes, f.grain, f.sheet_name,
               f.header_row, f.period_start, f.period_end, f.uploaded_at,
               f.row_count, f.values_kept, f.values_blank, f.storage_path, f.parse_notes,
               (SELECT COUNT(*) FROM bronze.reading r WHERE r.file_id = f.file_id) AS reading_count
        FROM bronze.source_file f
        ORDER BY f.uploaded_at DESC
        """
    )


@router.post("/upload", dependencies=[Depends(require_token)])
async def upload(files: list[UploadFile] = File(...)) -> dict:
    results = []
    for item in files:
        data = await item.read()
        if len(data) > settings.upload_max_bytes:
            results.append({
                "filename": item.filename,
                "status": "rejected",
                "message": f"File is larger than the {settings.upload_max_mb} MB limit.",
            })
            continue
        try:
            results.append(load_bytes(data, item.filename or "upload.xlsx"))
        except Exception as exc:
            results.append({
                "filename": item.filename,
                "status": "error",
                "message": f"{type(exc).__name__}: {exc}",
            })
    return {"results": results}
