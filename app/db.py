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
            cur.execute(
                """
                INSERT INTO gold.emission_factor
                    (value_tco2e_per_mwh, factor_type, source, vintage, valid_from, valid_to)
                VALUES
                    (%s, 'combined_margin', %s, '2021', DATE '2020-01-01', DATE '2023-12-31'),
                    (%s, 'combined_margin', %s, '2024', DATE '2024-01-01', NULL)
                """,
                (
                    settings.default_emission_factor,
                    settings.default_emission_factor_source,
                    settings.default_emission_factor,
                    settings.default_emission_factor_source,
                ),
            )
        cur.execute("SELECT COUNT(*) AS n FROM gold.price")
        if cur.fetchone()["n"] == 0:
            cur.execute(
                """
                INSERT INTO gold.price (instrument, value, currency, source, as_of) VALUES
                    ('irec', %s, %s, 'Indicative pilot pricing', DATE '2024-01-01'),
                    ('irec', %s, %s, 'Indicative pilot pricing', DATE '2025-01-01'),
                    ('vcu',  %s, %s, 'Indicative pilot pricing', DATE '2024-01-01'),
                    ('vcu',  %s, %s, 'Indicative pilot pricing', DATE '2025-01-01')
                """,
                (
                    settings.default_irec_price, settings.price_currency,
                    settings.default_irec_price, settings.price_currency,
                    settings.default_vcu_price, settings.price_currency,
                    settings.default_vcu_price, settings.price_currency,
                ),
            )
        conn.commit()
