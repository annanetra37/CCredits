"""Database access: one pool, plus migration and reference-data bootstrap."""
from __future__ import annotations

import logging
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from app.config import settings

log = logging.getLogger("ccredits.db")
SQL_DIR = Path(__file__).parent / "sql"

_pool: ConnectionPool | None = None


def get_pool() -> ConnectionPool:
    global _pool
    if _pool is None:
        _pool = ConnectionPool(
            settings.dsn, min_size=1, max_size=10, kwargs={"row_factory": dict_row}, open=True
        )
    return _pool


@contextmanager
def connection() -> Iterator[psycopg.Connection]:
    with get_pool().connection() as conn:
        yield conn


def query(sql: str, params: Any = None) -> list[dict]:
    with connection() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall() if cur.description else []


def query_one(sql: str, params: Any = None) -> dict | None:
    rows = query(sql, params)
    return rows[0] if rows else None


def execute(sql: str, params: Any = None) -> None:
    with connection() as conn, conn.cursor() as cur:
        cur.execute(sql, params)


def migrate() -> None:
    """Apply the schema files in order. They are all idempotent."""
    files = sorted(SQL_DIR.glob("*.sql"))
    with connection() as conn:
        for path in files:
            log.info("applying %s", path.name)
            with conn.cursor() as cur:
                cur.execute(path.read_text())
        conn.commit()
    sync_parameters()
    seed_reference_data()


def sync_parameters() -> None:
    """Push the configured assumptions into silver.parameter.

    The Silver views read their thresholds from that table, so the number shown
    on screen and the number the SQL used are the same number by construction.
    """
    rows = [
        ("implausible_kwh_per_kwp", settings.implausible_kwh_per_kwp, None),
        ("zero_day_policy", None, settings.zero_day_policy),
        ("missing_day_policy", None, settings.missing_day_policy),
        ("trust_grid_connection_date", None, str(settings.trust_grid_connection_date).lower()),
    ]
    with connection() as conn, conn.cursor() as cur:
        for key, num, txt in rows:
            cur.execute(
                """
                INSERT INTO silver.parameter (key, num, txt) VALUES (%s, %s, %s)
                ON CONFLICT (key) DO UPDATE
                    SET num = EXCLUDED.num, txt = EXCLUDED.txt, updated_at = now()
                """,
                (key, num, txt),
            )
        conn.commit()


def seed_reference_data() -> None:
    """Two rows each, as the task list asks. Only inserted if absent — an
    operator who edits a factor in the database keeps their edit."""
    with connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) AS n FROM gold.emission_factor")
        if cur.fetchone()["n"] == 0:
            _seed_armenia_baseline(cur)

        cur.execute("SELECT COUNT(*) AS n FROM gold.price")
        if cur.fetchone()["n"] == 0 and settings.default_vcu_price > 0:
            cur.execute(
                """
                INSERT INTO gold.price (instrument, value, currency, source, as_of)
                VALUES ('vcu', %s, %s, %s, DATE '2024-01-01')
                """,
                (settings.default_vcu_price, settings.price_currency,
                 settings.vcu_price_source or "Supplied by configuration"),
            )
        cur.execute(
            """
            INSERT INTO silver.parameter (key, num, txt) VALUES (%s, NULL, %s)
            ON CONFLICT (key) DO UPDATE SET txt = EXCLUDED.txt, updated_at = now()
            """,
            ("allow_expired_emission_factor",
             str(settings.allow_expired_emission_factor).lower()),
        )
        conn.commit()


# --- The published Armenian grid emission factors ---------------------------
# CDM Standardized Baseline ASB0038-2018 v01.0, Table 1: "Grid emission factor
# for the electricity system of the Republic of Armenia for 2016", adopted by
# the CDM Executive Board on 19 February 2018, valid to 18 February 2021.
#
# Table 1 publishes five factors. Which one applies depends on the project
# type, so all five are recorded and exactly one is marked active — a verifier
# can then see that the others were considered rather than overlooked.
#
# These are transcribed from a published regulatory document, not chosen. They
# are still seeded unverified: verified means a person has opened that document
# and checked the row, and no code can do that on their behalf.

ASB0038_SOURCE = (
    "CDM Standardized Baseline ASB0038-2018 v01.0, Table 1 — "
    "Grid emission factor for the electricity system of the Republic of Armenia for 2016"
)
ASB0038_URL = "https://cdm.unfccc.int/methodologies/standard_base/index.html"
ASB0038_VALID_FROM = "2018-02-19"
ASB0038_VALID_TO = "2021-02-18"

# (value, factor_type, project_types, active)
ASB0038_ROWS = [
    (0.4329, "combined_margin",
     "Wind and solar power generation project activities "
     "(first, second and third crediting periods)", True),
    (0.4620, "operating_margin",
     "All project activities (first, second and third crediting periods)", False),
    (0.3456, "build_margin",
     "All project activities (first, second and third crediting periods)", False),
    (0.4038, "combined_margin",
     "All project activities except wind and solar power generation "
     "(first crediting period)", False),
    (0.3748, "combined_margin",
     "All project activities except wind and solar power generation "
     "(second and third crediting periods)", False),
]


def _seed_armenia_baseline(cur) -> None:
    """Load Table 1 as published. An operator-supplied factor overrides it."""
    if settings.default_emission_factor > 0:
        cur.execute(
            """
            INSERT INTO gold.emission_factor
                (value_tco2e_per_mwh, factor_type, project_types, source, source_url,
                 vintage, valid_from, valid_to, active, verified)
            VALUES (%s, 'combined_margin', 'Supplied by configuration', %s, %s, %s,
                    %s, NULL, true, false)
            """,
            (
                settings.default_emission_factor,
                settings.default_emission_factor_source,
                settings.default_emission_factor_url or None,
                settings.default_emission_factor_vintage,
                settings.emission_factor_valid_from or ASB0038_VALID_FROM,
            ),
        )
        return

    valid_to = settings.emission_factor_valid_to or ASB0038_VALID_TO
    for value, factor_type, project_types, active in ASB0038_ROWS:
        cur.execute(
            """
            INSERT INTO gold.emission_factor
                (value_tco2e_per_mwh, factor_type, project_types, source, source_url,
                 vintage, valid_from, valid_to, active, verified)
            VALUES (%s, %s, %s, %s, %s, '2016', %s, %s, %s, false)
            ON CONFLICT DO NOTHING
            """,
            (value, factor_type, project_types, ASB0038_SOURCE, ASB0038_URL,
             ASB0038_VALID_FROM, valid_to, active),
        )
