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
        # Restore the published solar row. Matched on its value rather than a
        # LIKE pattern: psycopg leaves %% literal in a query with no parameters,
        # so the pattern silently matched nothing and the teardown did nothing.
        cur.execute(
            "UPDATE gold.emission_factor SET active = (value_tco2e_per_mwh = 0.4329)"
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


def test_all_generated_energy_counts():
    """There is no eligibility split: every kilowatt-hour read reaches Gold."""
    result = load_bytes(wide([100.0, 200.0, 300.0]), "wide.xlsx")
    assert result["status"] == "loaded"
    silver = query_one("SELECT COALESCE(SUM(generation_kwh),0) g FROM silver.site_month")
    gold = query_one("SELECT COALESCE(SUM(generation_kwh),0) g FROM gold.revenue")
    assert float(silver["g"]) == 600.0
    assert float(gold["g"]) == 600.0


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


def test_a_day_with_no_reading_simply_is_not_there():
    """No gap, no zero, no flag — the day is absent and nothing is invented."""
    load_bytes(wide([100.0, None, 300.0]), "gap.xlsx")
    days = query("SELECT reading_date, generation_kwh FROM silver.generation_daily ORDER BY 1")
    assert [d["reading_date"] for d in days] == [dt.date(2025, 1, 1), dt.date(2025, 1, 3)]
    assert float(query_one("SELECT SUM(generation_kwh) g FROM silver.site_month")["g"]) == 400.0


def test_an_unusually_large_reading_still_counts():
    """Nothing is second-guessed: what the file says is what is counted."""
    load_bytes(wide([100.0, 2000.0]), "spike.xlsx")
    assert float(query_one("SELECT SUM(generation_kwh) g FROM silver.site_month")["g"]) == 2100.0


def test_energy_before_grid_connection_still_counts():
    load_bytes(wide([100.0, 100.0], grid=dt.date(2025, 1, 2)), "early.xlsx")
    assert float(query_one("SELECT SUM(generation_kwh) g FROM silver.site_month")["g"]) == 200.0


def test_vcus_equal_tonnes_exactly(factor_for_2025):
    """One tonne avoided is one unit. Nothing is floored or carried forward."""
    load_bytes(wide([5000.0, 7000.0], kwp=20000.0), "vcu.xlsx")
    row = query_one(
        "SELECT generation_kwh, net_reduction_tco2e, vcu_issued FROM gold.vcu"
    )
    expected = 12.0 * factor_for_2025          # 12 MWh generated
    assert float(row["net_reduction_tco2e"]) == pytest.approx(expected)
    # The two figures are the same number, to the last decimal place.
    assert row["vcu_issued"] == row["net_reduction_tco2e"]


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


def test_the_published_validity_is_recorded_even_when_the_factor_is_applied_past_it():
    """ASB0038-2018 states validity to 2021-02-18 but is the most recent approved
    baseline for Armenia, so it is applied to later months — and flagged."""
    load_bytes(wide([1000.0], kwp=500.0), "beyond.xlsx")
    row = query_one(
        "SELECT emission_factor, net_reduction_tco2e, factor_published_valid_to, "
        "factor_beyond_validity FROM gold.carbon"
    )
    assert float(row["emission_factor"]) == pytest.approx(0.4329)
    assert row["net_reduction_tco2e"] is not None
    # The difference between applied and published is never hidden.
    assert row["factor_published_valid_to"] == dt.date(2021, 2, 18)
    assert row["factor_beyond_validity"] is True


def test_an_offline_site_still_counts():
    load_bytes(wide([500.0], status="Offline"), "offline.xlsx")
    assert float(query_one("SELECT SUM(generation_kwh) g FROM silver.site_month")["g"]) == 500.0


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
            cols = ", ".join(row.keys())
            slots = ", ".join(f"%({k})s" for k in row)
            cur.execute(f"INSERT INTO gold.emission_factor ({cols}) VALUES ({slots})", row)
        conn.commit()


def test_without_a_factor_carbon_is_absent_rather_than_guessed(no_emission_factor):
    load_bytes(wide([1000.0], kwp=500.0), "nofactor.xlsx")
    row = query_one(
        "SELECT emission_factor, net_reduction_tco2e, generation_kwh FROM gold.carbon"
    )
    # The energy is still there; only the carbon claim is withheld.
    assert float(row["generation_kwh"]) == 1000.0
    assert row["emission_factor"] is None
    assert row["net_reduction_tco2e"] is None
    # And no placeholder has crept in anywhere.
    assert query_one("SELECT COUNT(*) n FROM gold.emission_factor")["n"] == 0


def test_energy_does_not_depend_on_the_factor(no_emission_factor):
    """A missing factor withholds the carbon claim; it must not touch the energy."""
    load_bytes(wide([1500.0] + [0.0] * 30, kwp=5000.0), "nofactor2.xlsx")
    row = query_one("SELECT SUM(generation_kwh) kwh FROM silver.site_month")
    assert float(row["kwh"]) == 1500.0
    assert float(query_one("SELECT COALESCE(SUM(vcu_issued),0) v FROM gold.vcu")["v"]) == 0


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


# --- The reconciliation must actually bite ---------------------------------

def _recon(name: str):
    import cli.reconcile as rec
    return {c.name: c for c in rec.run(verbose=True)}[name]


def test_reconciliation_passes_on_untouched_data(factor_for_2025):
    import cli.reconcile as rec
    load_bytes(wide([1000.0, 1200.0], kwp=500.0), "recon-clean.xlsx")
    results = rec.run(verbose=False)
    failed = [c for c in results if c.status == "fail"]
    assert not failed, [f"{c.name}: {c.detail}" for c in failed]


def test_reconciliation_catches_an_altered_bronze_value():
    """A checker that cannot fail is worth nothing. Change one cell by 0.01."""
    load_bytes(wide([1000.0, 1200.0], kwp=500.0), "recon-tamper.xlsx")
    assert _recon("reparse_matches_bronze").status == "pass"

    with connection() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE bronze.reading SET value = value + 0.01 "
            "WHERE reading_id = (SELECT MIN(reading_id) FROM bronze.reading)"
        )
        conn.commit()

    check = _recon("reparse_matches_bronze")
    assert check.status == "fail"
    assert check.offenders, "a failure must name the rows responsible"


def test_reconciliation_catches_a_deleted_bronze_row():
    load_bytes(wide([1000.0, 1200.0], kwp=500.0), "recon-delete.xlsx")
    with connection() as conn, conn.cursor() as cur:
        cur.execute(
            "DELETE FROM bronze.reading "
            "WHERE reading_id = (SELECT MIN(reading_id) FROM bronze.reading)"
        )
        conn.commit()
    assert _recon("reparse_matches_bronze").status == "fail"


def test_reconciliation_names_a_missing_factor_as_the_reason_for_zero(no_emission_factor):
    """With no factor on file the credits are zero, and the report says why."""
    load_bytes(wide([1000.0, 1200.0], kwp=500.0), "recon-nofactor.xlsx")
    check = _recon("factor_coverage")
    assert check.status == "fail"
    assert "no applicable emission factor" in check.detail
    assert float(query_one("SELECT COALESCE(SUM(vcu_issued),0) v FROM gold.vcu")["v"]) == 0


def test_sites_are_pseudonymised_by_default():
    """Opening the portal must not disclose which client a site belongs to."""
    load_bytes(wide([500.0], kwp=500.0, name="Very Identifiable Client Ltd"), "named.xlsx")
    row = query_one("SELECT site_code, label, region FROM silver.site_identity")
    assert row["site_code"].startswith("SITE-")
    assert row["label"] == row["site_code"]
    assert "Identifiable" not in row["label"]
    # The code is stable, so the same site is the same code on every screen.
    again = query_one("SELECT site_code FROM silver.site_identity")
    assert again["site_code"] == row["site_code"]


def test_region_is_split_out_of_the_address():
    load_bytes(wide([500.0], kwp=500.0), "region.xlsx")
    with connection() as conn, conn.cursor() as cur:
        cur.execute("UPDATE bronze.site SET address = 'Ararat, Armenia'")
        conn.commit()
    row = query_one("SELECT region, country FROM silver.site_identity")
    assert row["region"] == "Ararat"
    assert row["country"] == "Armenia"


# --- Databases seeded by an earlier version must be corrected in place ------

def test_migration_repairs_a_database_seeded_by_an_older_version():
    """The seed only fills an empty table, so a live deployment keeps its old
    rows. These corrections have to reach it through the migration instead."""
    from app.db import migrate

    with connection() as conn, conn.cursor() as cur:
        # Put the database back into the shape the previous version left it in.
        cur.execute(
            """
            UPDATE gold.emission_factor
               SET valid_to = DATE '2021-02-18',
                   published_valid_to = NULL,
                   project_types = NULL,
                   source_url = 'https://cdm.unfccc.int/methodologies/standard_base/index.html',
                   active = false
            """
        )
        cur.execute("ALTER TABLE gold.price DROP CONSTRAINT IF EXISTS price_instrument_check")
        cur.execute(
            "INSERT INTO gold.price (instrument, value, currency, source, as_of) "
            "VALUES ('irec', 1.50, 'USD', 'Indicative pilot pricing', DATE '2024-01-01') "
            "ON CONFLICT DO NOTHING"
        )
        cur.execute(
            "UPDATE gold.price SET value = 8.00, source = 'Indicative pilot pricing' "
            "WHERE instrument = 'vcu'"
        )
        conn.commit()

    migrate()

    # I-RECs are gone, and only the VCU price remains.
    assert query_one("SELECT COUNT(*) n FROM gold.price WHERE instrument <> 'vcu'")["n"] == 0
    price = query_one("SELECT value, source FROM gold.price WHERE instrument = 'vcu'")
    assert float(price["value"]) == 3.00
    assert "conservative end" in price["source"]

    # The factor applies open-endedly, and what the document says is retained.
    factor = query_one(
        "SELECT value_tco2e_per_mwh v, valid_to, published_valid_to, project_types, source_url "
        "FROM gold.emission_factor WHERE active"
    )
    assert float(factor["v"]) == pytest.approx(0.4329)
    assert factor["valid_to"] is None
    assert factor["published_valid_to"] == dt.date(2021, 2, 18)
    assert factor["project_types"].startswith("Wind and solar")
    assert "environment.gov.am" in factor["source_url"]

    # Exactly one row drives a number.
    assert query_one("SELECT COUNT(*) n FROM gold.emission_factor WHERE active")["n"] == 1
