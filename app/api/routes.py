"""HTTP API.

Every endpoint is a thin wrapper over a Silver or Gold view. There is no
calculation in Python: if a number appears on screen, a SQL statement produced
it, and that statement is the one shown to a verifier.
"""
from __future__ import annotations

import csv
import datetime as dt
import io

from fastapi import APIRouter, Depends, File, Header, HTTPException, Query, UploadFile
from fastapi.responses import StreamingResponse

from app.config import settings
from app.db import query, query_one
from app.ingest.loader import load_bytes

router = APIRouter(prefix="/api")


def require_token(x_admin_token: str | None = Header(default=None)) -> None:
    """Open by default. Set ADMIN_TOKEN to require a token on writes."""
    if not settings.admin_token:
        return
    if x_admin_token != settings.admin_token:
        raise HTTPException(status_code=401, detail="Missing or invalid X-Admin-Token header")


def _factor_note(row: dict) -> str | None:
    """Warn only where something is actually wrong.

    That the factor is applied past its publication date is a deliberate,
    documented choice and is stated on the factor card itself, so repeating it
    here as an alert would be noise. A missing factor is a different matter.
    """
    if row.get("emission_factor") is None:
        return ("No emission factor is on file, so no emission reduction and no "
                "projected VCU potential is claimed. Nothing stands in for it.")
    if not row.get("factor_verified"):
        return ("This factor has not been checked against its source document. "
                "Set EMISSION_FACTOR_VERIFIED_BY to record who did.")
    return None


def _plant_for(site_code: str) -> str:
    """Resolve a pseudonym back to the real key."""
    row = query_one(
        "SELECT plant_name FROM silver.site_identity WHERE site_code = %s OR plant_name = %s",
        (site_code, site_code),
    )
    if not row:
        raise HTTPException(status_code=404, detail="No such site")
    return row["plant_name"]


# --- Context ---------------------------------------------------------------

@router.get("/health")
def health() -> dict:
    from app.db import LAST_MIGRATION

    try:
        query_one("SELECT 1 AS ok")
    except Exception as exc:
        return {"status": "degraded", "database": False, "detail": str(exc)}
    if LAST_MIGRATION.get("ok") is False:
        # Reachable but running on a schema that did not fully apply, which is
        # exactly the state that produces a 500 on one screen and nowhere else.
        return {
            "status": "degraded",
            "database": True,
            "env": settings.app_env,
            "detail": f"schema did not fully apply at {LAST_MIGRATION.get('file')}: "
                      f"{LAST_MIGRATION.get('error')}",
        }
    return {"status": "ok", "database": True, "env": settings.app_env}


@router.get("/context")
def context() -> dict:
    return {
        "banner": settings.banner_text,
        "fleet_name": settings.fleet_name,
        "env": settings.app_env,
        "real_names": settings.show_real_site_names,
        "emission_factors": query(
            "SELECT * FROM gold.emission_factor ORDER BY active DESC, valid_from DESC"
        ),
        "prices": query("SELECT * FROM gold.price ORDER BY as_of DESC"),
    }


@router.get("/summary")
def summary() -> dict:
    row = query_one("SELECT * FROM gold.fleet_summary") or {}
    return {**row, "factor_note": _factor_note(row)}


@router.get("/flow")
def flow() -> dict:
    """Generated energy becomes a reduction becomes units. Three steps, no splits."""
    s = query_one("SELECT * FROM gold.fleet_summary") or {}
    return {
        "generation_kwh": s.get("generation_kwh", 0),
        "generation_mwh": float(s.get("generation_kwh") or 0) / 1000.0,
        "emission_factor": (query_one(
            "SELECT value_tco2e_per_mwh v FROM gold.emission_factor WHERE active LIMIT 1"
        ) or {}).get("v"),
        "net_reduction_tco2e": s.get("net_reduction_tco2e"),
        "vcu_issued": s.get("vcu_issued", 0),
        "total_revenue": s.get("total_revenue", 0),
        "currency": s.get("currency") or settings.price_currency,
    }


# --- Energy ----------------------------------------------------------------

_GROUP_KEY = {
    "month": "date_trunc('month', g.reading_date)::date",
    "day": "g.reading_date",
    "site": "i.label",
    "region": "COALESCE(i.region, 'Unknown')",
}


@router.get("/energy")
def energy(
    group: str = Query("month", pattern="^(month|day|site|region)$"),
    site: str | None = None,
    region: str | None = None,
    month: dt.date | None = None,
) -> dict:
    """Energy generated, grouped the way the reader wants to see it."""
    plant = _plant_for(site) if site else None
    where = """
        WHERE (%(plant)s::text IS NULL OR g.plant_name = %(plant)s)
          AND (%(region)s::text IS NULL OR i.region = %(region)s)
          AND (%(month)s::date IS NULL OR date_trunc('month', g.reading_date)::date = %(month)s)
    """
    params = {"plant": plant, "region": region, "month": month}
    key = _GROUP_KEY[group]

    rows = query(
        f"""
        WITH base AS (
            SELECT {key} AS bucket, g.plant_name, g.generation_kwh, i.installed_kwp
            FROM silver.generation_daily g
            JOIN silver.site_identity i ON i.plant_name = g.plant_name
            {where}
        ),
        caps AS (
            SELECT bucket, SUM(installed_kwp) AS installed_kwp
            FROM (SELECT DISTINCT bucket, plant_name, installed_kwp FROM base) d
            GROUP BY bucket
        )
        SELECT b.bucket::text                AS bucket,
               SUM(b.generation_kwh)         AS generation_kwh,
               COUNT(*)                      AS days_with_data,
               COUNT(DISTINCT b.plant_name)  AS site_count,
               c.installed_kwp,
               CASE WHEN SUM(b.installed_kwp) > 0
                    THEN SUM(b.generation_kwh) / SUM(b.installed_kwp) END AS specific_yield_per_day
        FROM base b
        LEFT JOIN caps c ON c.bucket = b.bucket
        GROUP BY b.bucket, c.installed_kwp
        ORDER BY 1
        """,
        params,
    )
    totals = query_one(
        f"""
        SELECT COALESCE(SUM(g.generation_kwh), 0) AS generation_kwh,
               COUNT(*)                           AS days_with_data,
               COUNT(DISTINCT g.plant_name)       AS site_count
        FROM silver.generation_daily g
        JOIN silver.site_identity i ON i.plant_name = g.plant_name
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
          AND (%(region)s::text IS NULL OR region = %(region)s)
        """,
        params,
    ) or {}
    return {"group": group, "rows": rows, "totals": {**totals, **capacity},
            "site": site, "region": region,
            "month": month.isoformat() if month else None}


@router.get("/regions")
def regions() -> list[dict]:
    """Energy by Armenian province, for the map."""
    return query(
        """
        SELECT COALESCE(i.region, 'Unknown')     AS region,
               COUNT(DISTINCT i.plant_name)      AS site_count,
               SUM(i.installed_kwp)              AS installed_kwp,
               COALESCE(SUM(f.generation_kwh), 0) AS generation_kwh,
               COALESCE(SUM(f.vcu_issued), 0)     AS vcu_issued,
               COALESCE(SUM(f.total_revenue), 0)  AS total_revenue
        FROM silver.site_identity i
        LEFT JOIN (
            SELECT plant_name, SUM(generation_kwh) AS generation_kwh,
                   SUM(vcu_issued) AS vcu_issued, SUM(total_revenue) AS total_revenue
            FROM gold.revenue GROUP BY plant_name
        ) f ON f.plant_name = i.plant_name
        GROUP BY COALESCE(i.region, 'Unknown')
        ORDER BY generation_kwh DESC
        """
    )


# --- Drill-down ------------------------------------------------------------

@router.get("/drill/months")
def drill_months() -> list[dict]:
    return query(
        """
        SELECT f.month, f.generation_kwh, f.site_count, f.days_with_data,
               COALESCE(g.net_reduction_tco2e, 0) AS net_reduction_tco2e,
               COALESCE(g.vcu_issued, 0)          AS vcu_issued,
               COALESCE(g.total_revenue, 0)       AS total_revenue
        FROM silver.fleet_month f
        LEFT JOIN (
            SELECT month, SUM(net_reduction_tco2e) AS net_reduction_tco2e,
                   SUM(vcu_issued) AS vcu_issued, SUM(total_revenue) AS total_revenue
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
               sm.generation_kwh, sm.days_with_data,
               COALESCE(g.vcu_issued, 0)          AS vcu_issued,
               COALESCE(g.net_reduction_tco2e, 0) AS net_reduction_tco2e
        FROM silver.site_month sm
        JOIN silver.site_identity i ON i.plant_name = sm.plant_name
        LEFT JOIN gold.revenue g ON g.plant_name = sm.plant_name AND g.month = sm.month
        WHERE sm.month = %s
        ORDER BY sm.generation_kwh DESC
        """,
        (month,),
    )


@router.get("/drill/sites/{site_code}/days")
def drill_days(site_code: str, month: dt.date | None = None) -> list[dict]:
    plant_name = _plant_for(site_code)
    return query(
        """
        SELECT g.reading_date, g.generation_kwh, g.device_count, g.grain,
               g.file_id, g.filename, g.source_row,
               CASE WHEN i.installed_kwp > 0 THEN g.generation_kwh / i.installed_kwp END
                   AS specific_yield
        FROM silver.generation_daily g
        JOIN silver.site_identity i ON i.plant_name = g.plant_name
        WHERE g.plant_name = %s
          AND (%s::date IS NULL OR date_trunc('month', g.reading_date)::date = %s)
        ORDER BY g.reading_date
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


@router.get("/lineage")
def lineage(site: str = Query(...), month: dt.date = Query(...),
            limit: int = Query(500, le=5000)) -> dict:
    plant_name = _plant_for(site)
    gold_row = query_one(
        """
        SELECT r.month, r.generation_kwh, v.generation_mwh, v.emission_factor,
               v.net_reduction_tco2e, v.vcu_issued, r.vcu_price_per_tco2e,
               r.total_revenue, r.currency, c.factor_source, c.factor_source_url
        FROM gold.revenue r
        JOIN gold.vcu v    ON v.plant_name = r.plant_name AND v.month = r.month
        JOIN gold.carbon c ON c.plant_name = r.plant_name AND c.month = r.month
        WHERE r.plant_name = %s AND r.month = %s
        """,
        (plant_name, month),
    )
    if not gold_row:
        raise HTTPException(status_code=404, detail="No gold row for that site and month")
    cells = query(
        """
        SELECT reading_date, day_generation_kwh, reading_id, device_sn, metric,
               cell_value, unit, source_row, source_col, file_id, filename, sha256, grain
        FROM gold.lineage
        WHERE plant_name = %s AND month = %s
        ORDER BY reading_date, source_row
        LIMIT %s
        """,
        (plant_name, month, limit),
    )
    return {"gold": gold_row, "cells": cells, "cell_count": len(cells)}


# --- Sites -----------------------------------------------------------------

@router.get("/fleet")
def fleet() -> list[dict]:
    return query(
        """
        SELECT i.label AS site, i.site_code, i.region, i.country, i.address,
               i.plant_type, f.installed_kwp, f.grid_connection_date, f.plant_status,
               f.days_with_data, f.generation_kwh, f.first_day, f.last_day,
               f.specific_yield_per_day, f.vcu_issued, f.net_reduction_tco2e,
               f.total_revenue
        FROM gold.fleet_table f
        JOIN silver.site_identity i ON i.plant_name = f.plant_name
        ORDER BY f.generation_kwh DESC NULLS LAST
        """
    )


# --- The calculation, in words ---------------------------------------------

@router.get("/calculation")
def calculation(month: dt.date | None = None) -> dict:
    totals = query_one(
        """
        SELECT COALESCE(SUM(generation_kwh), 0)     AS generation_kwh,
               COALESCE(SUM(net_reduction_tco2e), 0) AS net_reduction_tco2e,
               COALESCE(SUM(vcu_issued), 0)          AS vcu_issued,
               COALESCE(SUM(total_revenue), 0)       AS total_revenue,
               MAX(vcu_price_per_tco2e)              AS vcu_price_per_tco2e,
               MAX(currency)                         AS currency,
               bool_and(factor_verified)             AS factor_verified,
               bool_or(factor_beyond_validity)       AS factor_beyond_validity
        FROM gold.revenue WHERE (%s::date IS NULL OR month = %s)
        """,
        (month, month),
    ) or {}
    factor = query_one(
        """
        SELECT MAX(emission_factor) AS emission_factor,
               MAX(factor_source) AS factor_source,
               MAX(factor_source_url) AS factor_source_url,
               MAX(factor_vintage) AS factor_vintage,
               MAX(factor_type) AS factor_type,
               MAX(factor_project_types) AS factor_project_types,
               MAX(factor_published_valid_to) AS factor_published_valid_to
        FROM gold.carbon WHERE (%s::date IS NULL OR month = %s)
        """,
        (month, month),
    ) or {}
    mwh = float(totals.get("generation_kwh") or 0) / 1000.0
    ef = factor.get("emission_factor")
    cur = totals.get("currency") or settings.price_currency
    return {
        **totals, **factor,
        "generation_mwh": mwh,
        "month": month.isoformat() if month else None,
        "currency": cur,
        "steps": [
            {
                "label": "Energy generated",
                "formula": "sum of every daily reading in the files",
                "substituted": f"{float(totals.get('generation_kwh') or 0):,.1f} kWh",
                "result": f"{mwh:,.3f} MWh",
                "source": "silver.site_month",
            },
            {
                "label": "Emission reduction",
                "formula": "MWh generated × emission factor − project emissions − leakage",
                "substituted": (f"{mwh:,.3f} × {float(ef):g} − 0 − 0" if ef is not None
                                else f"{mwh:,.3f} × (no emission factor)"),
                "result": (f"{float(totals.get('net_reduction_tco2e') or 0):,.3f} tCO₂e"
                           if ef is not None else "not calculable"),
                "source": "gold.carbon",
                "warning": _factor_note({**factor, **totals}),
            },
            {
                "label": "Projected VCU potential, pre-validation",
                "formula": "one tonne avoided is one unit",
                "substituted": f"{float(totals.get('net_reduction_tco2e') or 0):,.3f} tCO₂e",
                "result": f"{float(totals.get('vcu_issued') or 0):,.3f} units",
                "source": "gold.vcu",
            },
            {
                "label": "Indicative revenue",
                "formula": "Projected VCU potential × price per tonne",
                "substituted": (
                    f"{float(totals.get('vcu_issued') or 0):,.3f} × {float(totals.get('vcu_price_per_tco2e')):g}"
                    if totals.get("vcu_price_per_tco2e") is not None
                    else "no price set"),
                "result": (f"{cur} {float(totals.get('total_revenue') or 0):,.2f}"
                           if totals.get("vcu_price_per_tco2e") is not None else "not calculable"),
                "source": "gold.revenue",
            },
        ],
    }


# --- The Gold layer as a table ---------------------------------------------

LEDGER_SQL = """
    SELECT site, site_code, region, installed_kwp, grid_connection_date, month,
           days_with_data, generation_kwh, generation_mwh, emission_factor,
           net_reduction_tco2e, vcu_issued, vcu_price_per_tco2e, currency,
           total_revenue, factor_source, factor_vintage
    FROM gold.ledger
    WHERE (%(site)s::text IS NULL OR site_code = %(site)s)
      AND (%(month)s::date IS NULL OR month = %(month)s)
    ORDER BY month, site
"""


@router.get("/ledger")
def ledger(site: str | None = None, month: dt.date | None = None) -> dict:
    rows = query(LEDGER_SQL, {"site": site, "month": month})
    return {"rows": rows, "row_count": len(rows)}


@router.get("/ledger.csv")
def ledger_csv(site: str | None = None, month: dt.date | None = None) -> StreamingResponse:
    """The same table as a spreadsheet, so the numbers can be checked anywhere."""
    rows = query(LEDGER_SQL, {"site": site, "month": month})
    buf = io.StringIO()
    if rows:
        writer = csv.DictWriter(buf, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    stamp = dt.date.today().isoformat()
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="ccredits-gold-{stamp}.csv"'},
    )


# --- Who has opened the portal ---------------------------------------------

@router.get("/visitors")
def visitors(limit: int = Query(50, le=500)) -> dict:
    """Self-hosted visit data. No third-party script; nothing leaves this box."""
    totals = query_one(
        """
        SELECT COUNT(*)                     AS visits,
               COUNT(DISTINCT visitor_id)   AS people,
               MIN(seen_at)                 AS first_seen,
               MAX(seen_at)                 AS last_seen,
               COUNT(*) FILTER (WHERE seen_at > now() - interval '7 days')  AS visits_7d,
               COUNT(DISTINCT visitor_id) FILTER (WHERE seen_at > now() - interval '7 days')
                                            AS people_7d
        FROM ops.visit
        """
    ) or {}
    return {
        "enabled": settings.track_visits,
        **totals,
        "links": query(
            "SELECT tag, people, visits, first_opened, last_opened FROM ops.link "
            "ORDER BY last_opened DESC"
        ),
        # Named "visitors", not "people": the totals above already use that key
        # for a count, and one of them would have silently overwritten the other.
        "visitors": query(
            "SELECT visitor_id, tag, visits, first_seen, last_seen, user_agent, "
            "referrer, country, country_code "
            "FROM ops.visitor ORDER BY last_seen DESC LIMIT %s",
            (limit,),
        ),
        "locations": query(
            "SELECT country, country_code, people, visits, last_seen FROM ops.location"
        ),
        "by_day": query(
            """
            SELECT seen_at::date AS day, COUNT(*) AS visits,
                   COUNT(DISTINCT visitor_id) AS people
            FROM ops.visit
            WHERE seen_at > now() - interval '30 days'
            GROUP BY 1 ORDER BY 1
            """
        ),
    }


# --- Upload ----------------------------------------------------------------

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
            results.append({"filename": item.filename, "status": "rejected",
                            "message": f"File is larger than the {settings.upload_max_mb} MB limit."})
            continue
        try:
            results.append(load_bytes(data, item.filename or "upload.xlsx"))
        except Exception as exc:
            results.append({"filename": item.filename, "status": "error",
                            "message": f"{type(exc).__name__}: {exc}"})
    return {"results": results}
