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



@pytest.fixture
def factor_for_2025():
    """A factor whose published validity covers the test data."""
    with connection() as conn, conn.cursor() as cur:
        cur.execute("UPDATE gold.emission_factor SET active = false")
        cur.execute(
            """
            INSERT INTO gold.emission_factor
                (value_tco2e_per_mwh, factor_type, project_types, source, vintage,
                 valid_from, valid_to, active, verified)
            VALUES (0.4329, 'combined_margin', 'test', 'test fixture', '2016',
                    DATE '2024-01-01', DATE '2026-12-31', true, false)
            RETURNING factor_id
            """
        )
        fid = cur.fetchone()["factor_id"]
        conn.commit()
    yield 0.4329
    with connection() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM gold.emission_factor WHERE factor_id = %s", (fid,))
        cur.execute(
            "UPDATE gold.emission_factor SET active = true "
            "WHERE project_types LIKE 'Wind and solar%%'"
        )
        conn.commit()


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


def test_vcu_carries_the_remainder_forward_instead_of_rounding_it_away(factor_for_2025):
    """A VCU is one whole tonne; the fraction rolls on rather than vanishing."""
    # 5,000 kWh in January and 7,000 in February at 0.4329 tCO2e/MWh gives
    # 2.1645 then 3.0303 tonnes. Month 1 issues 2 and carries 0.1645; month 2
    # has 0.1645 + 3.0303 = 3.1948, so it issues 3 and carries 0.1948.
    jan = [5000.0] + [0.0] * 30
    feb = [7000.0] + [0.0] * 27
    days = ([dt.date(2025, 1, 1) + dt.timedelta(days=i) for i in range(31)]
            + [dt.date(2025, 2, 1) + dt.timedelta(days=i) for i in range(28)])
    data = book([
        ["Daily Yield Report (kWh)"],
        ["Plant Name", "Installed Capacity(kWp)", "Grid Connection Date", "Plant Status"] + days,
        ["Site A", 20000.0, dt.date(2024, 1, 1), "Normal"] + jan + feb,
    ])
    load_bytes(data, "carry.xlsx")

    rows = query("SELECT month, vcu_issued, carry_forward_tco2e FROM gold.vcu ORDER BY month")
    assert [int(r["vcu_issued"]) for r in rows] == [2, 3]
    assert float(rows[0]["carry_forward_tco2e"]) == pytest.approx(0.1645, abs=1e-4)
    assert float(rows[1]["carry_forward_tco2e"]) == pytest.approx(0.1948, abs=1e-4)
    # Nothing is issued that was not reduced.
    total = query_one("SELECT SUM(net_reduction_tco2e) t, SUM(vcu_issued) v FROM gold.vcu")
    assert float(total["v"]) <= float(total["t"])


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


def test_carbon_uses_the_dated_factor_not_a_constant(factor_for_2025):
    # 1000 kWh on 500 kWp is 2 kWh/kWp/day — comfortably plausible, so the day
    # is eligible and the factor is what decides the answer.
    load_bytes(wide([1000.0], kwp=500.0), "carbon.xlsx")
    row = query_one("SELECT emission_factor, factor_vintage, net_reduction_tco2e FROM gold.carbon")
    assert float(row["emission_factor"]) == pytest.approx(factor_for_2025)
    assert float(row["net_reduction_tco2e"]) == pytest.approx(1.0 * factor_for_2025)


def test_only_the_active_factor_drives_the_number(factor_for_2025):
    """Table 1 publishes several margins; only the applicable one may be used."""
    with connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO gold.emission_factor
                (value_tco2e_per_mwh, factor_type, project_types, source, vintage,
                 valid_from, valid_to, active, verified)
            VALUES (0.9999, 'operating_margin', 'not this one', 'test fixture', '2016',
                    DATE '2024-01-01', DATE '2026-12-31', false, false)
            """
        )
        conn.commit()
    load_bytes(wide([1000.0], kwp=500.0), "active.xlsx")
    row = query_one("SELECT emission_factor FROM gold.carbon")
    assert float(row["emission_factor"]) == pytest.approx(factor_for_2025)
    with connection() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM gold.emission_factor WHERE value_tco2e_per_mwh = 0.9999")
        conn.commit()


def test_a_lapsed_baseline_yields_no_claim_by_default():
    """The seeded Armenian baseline lapsed in 2021; the data here is 2025."""
    load_bytes(wide([1000.0], kwp=500.0), "lapsed.xlsx")
    row = query_one("SELECT emission_factor, net_reduction_tco2e FROM gold.carbon")
    assert row["emission_factor"] is None
    assert row["net_reduction_tco2e"] is None
    assert int(query_one("SELECT COALESCE(SUM(vcu_issued),0) v FROM gold.vcu")["v"]) == 0


def test_inactive_site_energy_is_excluded_with_its_own_reason():
    load_bytes(wide([500.0], status="Offline"), "offline.xlsx")
    row = query_one("SELECT eligible_kwh, excluded_kwh, exclusion_reason FROM silver.eligibility")
    assert float(row["eligible_kwh"]) == 0.0
    assert float(row["excluded_kwh"]) == 500.0
    assert row["exclusion_reason"] == "site_inactive"


# --- The emission factor must never arrive without a provenance -------------

@pytest.fixture
def no_emission_factor():
    """Remove the factors for one test, then put them back."""
    saved = query("SELECT * FROM gold.emission_factor")
    with connection() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM gold.emission_factor")
        conn.commit()
    yield
    with connection() as conn, conn.cursor() as cur:
        for row in saved:
            cur.execute(
                """
                INSERT INTO gold.emission_factor
                    (factor_id, value_tco2e_per_mwh, factor_type, source, source_url,
                     vintage, valid_from, valid_to, verified, verified_by, verified_at)
                VALUES (%(factor_id)s, %(value_tco2e_per_mwh)s, %(factor_type)s, %(source)s,
                        %(source_url)s, %(vintage)s, %(valid_from)s, %(valid_to)s,
                        %(verified)s, %(verified_by)s, %(verified_at)s)
                """,
                row,
            )
        conn.commit()


def test_without_a_factor_carbon_is_absent_rather_than_guessed(no_emission_factor):
    load_bytes(wide([1000.0], kwp=500.0), "nofactor.xlsx")
    row = query_one(
        "SELECT emission_factor, net_reduction_tco2e, eligible_kwh FROM gold.carbon"
    )
    # The energy is still there; only the carbon claim is withheld.
    assert float(row["eligible_kwh"]) == 1000.0
    assert row["emission_factor"] is None
    assert row["net_reduction_tco2e"] is None
    # And no placeholder has crept in anywhere.
    assert query_one("SELECT COUNT(*) n FROM gold.emission_factor")["n"] == 0


def test_energy_does_not_depend_on_the_factor(no_emission_factor):
    """A missing factor withholds the carbon claim; it must not touch the energy."""
    load_bytes(wide([1500.0] + [0.0] * 30, kwp=5000.0), "nofactor.xlsx")
    row = query_one("SELECT SUM(eligible_kwh) kwh FROM silver.site_month")
    assert float(row["kwh"]) == 1500.0
    assert int(query_one("SELECT COALESCE(SUM(vcu_issued),0) v FROM gold.vcu")["v"]) == 0


def test_a_factor_is_unverified_until_a_person_says_otherwise():
    rows = query("SELECT factor_id, verified FROM gold.emission_factor")
    assert rows, "expected the published baseline to be seeded"
    # Nothing in the code path may set verified = true; only a person does.
    assert all(r["verified"] is False for r in rows)


def test_the_verified_flag_travels_with_the_number_it_produced(factor_for_2025):
    load_bytes(wide([1000.0], kwp=500.0), "verified.xlsx")
    row = query_one("SELECT factor_verified, net_reduction_tco2e FROM gold.carbon")
    assert row["factor_verified"] is False
    assert row["net_reduction_tco2e"] is not None
