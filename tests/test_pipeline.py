"""End-to-end tests against a real Postgres.

These check the numbers, not the plumbing: that the river balances, that a
suspect day is excluded with a reason, that a gap is never interpolated, and
that the I-REC remainder is carried forward rather than rounded away.

Skipped automatically when no database is reachable.
"""
from __future__ import annotations

import datetime as dt
import io

import pytest
from openpyxl import Workbook

from app.db import connection, migrate, query, query_one
from app.ingest.loader import load_bytes


def _db_available() -> bool:
    try:
        query_one("SELECT 1")
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _db_available(), reason="no database reachable")


@pytest.fixture(scope="module", autouse=True)
def schema():
    migrate()
    yield


@pytest.fixture(autouse=True)
def clean():
    with connection() as conn, conn.cursor() as cur:
        cur.execute(
            "TRUNCATE bronze.reading, bronze.file_overlap, bronze.site, "
            "bronze.source_file RESTART IDENTITY CASCADE"
        )
        conn.commit()
    yield


def book(rows, title="Daily Yield(kWh)") -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = title
    for row in rows:
        ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def wide(values, kwp=100.0, grid=dt.date(2024, 1, 1), status="Normal", name="Site A"):
    """One site, a run of days starting 2025-01-01."""
    days = [dt.date(2025, 1, 1) + dt.timedelta(days=i) for i in range(len(values))]
    return book([
        ["Daily Yield Report (kWh)"],
        ["Plant Name", "Installed Capacity(kWp)", "Grid Connection Date", "Plant Status"] + days,
        [name, kwp, grid, status] + list(values),
    ])


def test_loads_and_the_river_balances():
    result = load_bytes(wide([100.0, 200.0, 300.0]), "wide.xlsx")
    assert result["status"] == "loaded"
    row = query_one(
        "SELECT SUM(generation_kwh) g, SUM(eligible_kwh) e, SUM(excluded_kwh) x "
        "FROM silver.eligibility"
    )
    # Nothing may vanish between generated and credits.
    assert float(row["g"]) == 600.0
    assert float(row["e"]) + float(row["x"]) == float(row["g"])


def test_identical_file_is_refused_not_loaded_twice():
    data = wide([100.0, 200.0])
    assert load_bytes(data, "a.xlsx")["status"] == "loaded"
    second = load_bytes(data, "a-again.xlsx")
    assert second["status"] == "duplicate"
    assert query_one("SELECT COUNT(*) n FROM bronze.source_file")["n"] == 1
    assert query_one("SELECT COUNT(*) n FROM bronze.reading")["n"] == 2


def test_overlapping_upload_supersedes_rather_than_deletes():
    load_bytes(wide([100.0, 100.0]), "first.xlsx")
    load_bytes(wide([100.0, 555.0]), "second.xlsx")  # same days, corrected value

    assert query_one("SELECT COUNT(*) n FROM bronze.file_overlap")["n"] == 1
    # Both files' readings are still in Bronze — nothing was deleted.
    assert query_one("SELECT COUNT(*) n FROM bronze.reading")["n"] == 4
    # The later upload wins for the overlapping day.
    winner = query_one(
        "SELECT generation_kwh FROM silver.generation_daily "
        "WHERE reading_date = DATE '2025-01-02'"
    )
    assert float(winner["generation_kwh"]) == 555.0
    assert query_one("SELECT COUNT(*) n FROM silver.superseded_site_day")["n"] == 2


def test_missing_day_is_a_gap_and_is_never_interpolated():
    load_bytes(wide([100.0, None, 300.0]), "gap.xlsx")
    day = query_one(
        """
        SELECT e.flag, e.generation_kwh, e.eligible_kwh
        FROM silver.eligibility e
        WHERE e.reading_date = DATE '2025-01-02'
        """
    )
    assert day["flag"] == "missing"
    assert float(day["generation_kwh"]) == 0.0      # not the average of its neighbours
    assert float(day["eligible_kwh"]) == 0.0
    reason = query_one(
        "SELECT exclusion_reason FROM silver.eligibility WHERE reading_date = DATE '2025-01-02'"
    )
    assert reason["exclusion_reason"] == "data_gap"


def test_implausible_day_is_excluded_with_the_rule_named():
    # 100 kWp at 8 kWh/kWp/day is 800 kWh; 2000 is far above the threshold.
    load_bytes(wide([100.0, 2000.0]), "spike.xlsx")
    day = query_one(
        "SELECT flag, flag_reason, is_implausible FROM silver.quality_flag "
        "WHERE reading_date = DATE '2025-01-02'"
    )
    assert day["is_implausible"] is True
    assert day["flag"] == "suspect"
    assert "8" in day["flag_reason"]
    excluded = query_one(
        "SELECT eligible_kwh, excluded_kwh, exclusion_reason FROM silver.eligibility "
        "WHERE reading_date = DATE '2025-01-02'"
    )
    assert float(excluded["eligible_kwh"]) == 0.0
    assert float(excluded["excluded_kwh"]) == 2000.0
    assert excluded["exclusion_reason"] == "quality_suspect"


def test_energy_before_grid_connection_does_not_earn():
    load_bytes(wide([100.0, 100.0], grid=dt.date(2025, 1, 2)), "early.xlsx")
    first = query_one(
        "SELECT eligible_kwh, excluded_kwh, exclusion_reason FROM silver.eligibility "
        "WHERE reading_date = DATE '2025-01-01'"
    )
    assert float(first["eligible_kwh"]) == 0.0
    assert first["exclusion_reason"] == "before_grid_connection"
    second = query_one(
        "SELECT eligible_kwh FROM silver.eligibility WHERE reading_date = DATE '2025-01-02'"
    )
    assert float(second["eligible_kwh"]) == 100.0


def test_irec_carries_the_remainder_forward_instead_of_rounding_it_away():
    # 1500 kWh in January and 1700 in February: 1.5 MWh then 1.7 MWh.
    # Month 1 issues 1 and carries 0.5; month 2 has 0.5 + 1.7 = 2.2, issues 2.
    jan = [1500.0] + [0.0] * 30
    feb = [1700.0] + [0.0] * 27
    days = ([dt.date(2025, 1, 1) + dt.timedelta(days=i) for i in range(31)]
            + [dt.date(2025, 2, 1) + dt.timedelta(days=i) for i in range(28)])
    data = book([
        ["Daily Yield Report (kWh)"],
        ["Plant Name", "Installed Capacity(kWp)", "Grid Connection Date", "Plant Status"] + days,
        ["Site A", 5000.0, dt.date(2024, 1, 1), "Normal"] + jan + feb,
    ])
    load_bytes(data, "carry.xlsx")

    rows = query("SELECT month, irec_issued, carry_forward_mwh FROM gold.irec ORDER BY month")
    assert [int(r["irec_issued"]) for r in rows] == [1, 2]
    assert float(rows[0]["carry_forward_mwh"]) == pytest.approx(0.5)
    assert float(rows[1]["carry_forward_mwh"]) == pytest.approx(0.2)
    # Nothing is lost: total issued never exceeds total eligible.
    total = query_one("SELECT SUM(eligible_kwh)/1000 mwh, SUM(irec_issued) issued FROM gold.irec")
    assert float(total["issued"]) <= float(total["mwh"])


def test_lineage_reaches_the_cell():
    load_bytes(wide([100.0, 200.0]), "lineage.xlsx")
    row = query_one(
        """
        SELECT filename, sha256, source_row, source_col, cell_value
        FROM gold.lineage
        WHERE reading_date = DATE '2025-01-02'
        """
    )
    assert row["filename"] == "lineage.xlsx"
    assert len(row["sha256"]) == 64
    assert row["source_row"] == 3          # the data row, as a human counts
    assert row["source_col"] == "F"        # 4 identifier columns, then two days
    assert float(row["cell_value"]) == 200.0


def test_carbon_uses_the_dated_factor_not_a_constant():
    # 1000 kWh on 500 kWp is 2 kWh/kWp/day — comfortably plausible, so the day
    # is eligible and the factor is what decides the answer.
    load_bytes(wide([1000.0], kwp=500.0), "carbon.xlsx")
    row = query_one("SELECT emission_factor, factor_vintage, net_reduction_tco2e FROM gold.carbon")
    factor = query_one(
        "SELECT value_tco2e_per_mwh v FROM gold.emission_factor "
        "WHERE valid_from <= DATE '2025-01-31' AND (valid_to IS NULL OR valid_to >= DATE '2025-01-01') "
        "ORDER BY valid_from DESC LIMIT 1"
    )
    assert float(row["emission_factor"]) == float(factor["v"])
    assert float(row["net_reduction_tco2e"]) == pytest.approx(1.0 * float(factor["v"]))


def test_inactive_site_energy_is_excluded_with_its_own_reason():
    load_bytes(wide([500.0], status="Offline"), "offline.xlsx")
    row = query_one("SELECT eligible_kwh, excluded_kwh, exclusion_reason FROM silver.eligibility")
    assert float(row["eligible_kwh"]) == 0.0
    assert float(row["excluded_kwh"]) == 500.0
    assert row["exclusion_reason"] == "site_inactive"
