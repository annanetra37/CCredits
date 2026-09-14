"""Parser tests. These run without a database."""
from __future__ import annotations

import datetime as dt
import io

import pytest
from openpyxl import Workbook

from app.ingest.parser import (
    load_file, normalise, parse_date, parse_number, split_unit, find_header_row,
)


def book(rows, title="Sheet1") -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = title
    for row in rows:
        ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_split_unit_and_normalise():
    assert split_unit("Daily Yield(kWh)") == ("Daily Yield", "kWh")
    assert split_unit("Installed Capacity [kWp]") == ("Installed Capacity", "kWp")
    assert split_unit("Plant Name") == ("Plant Name", None)
    assert normalise("Daily_Yield (kWh)") == "daily yield"
    assert normalise("GRID-CONNECTION DATE") == "grid connection date"


@pytest.mark.parametrize("raw,expected", [
    ("1,234.5", 1234.5),     # US: comma groups, dot decides
    ("1.234,5", 1234.5),     # European: the mirror image
    ("1 234,5 kWh", 1234.5), # space-grouped with a comma decimal
    ("1,5", 1.5),            # a lone comma before one digit is a decimal point
    ("1,234", 1234.0),       # a lone comma before three digits groups thousands
    ("1,234,567", 1234567.0),
    ("12.5", 12.5), ("250", 250.0),
    ("1.234", 1.234),        # a lone dot is a decimal point, always
    ("0.123", 0.123),        # the case that makes that rule necessary
    ("1.234.567", 1234567.0),# only a repeated dot groups thousands
    ("--", None), ("N/A", None), ("", None), (None, None), ("abc", None), (7, 7.0),
    (True, None),            # a boolean is not a measurement
])
def test_parse_number(raw, expected):
    assert parse_number(raw) == expected


@pytest.mark.parametrize("raw,expected", [
    ("2025-03-05", dt.date(2025, 3, 5)),
    ("2025/03/05", dt.date(2025, 3, 5)),
    ("05/03/2025", dt.date(2025, 3, 5)),
    ("20250305", dt.date(2025, 3, 5)),
    (dt.datetime(2025, 3, 5, 12, 0), dt.date(2025, 3, 5)),
    ("not a date", None),
])
def test_parse_date(raw, expected):
    assert parse_date(raw) == expected


def test_header_row_found_below_title_rows():
    rows = [["Power Station Report"], [], ["Date", "Plant Name", "Daily Yield(kWh)"]]
    assert find_header_row(rows) == 2


def test_long_layout_with_device_grain():
    data = book([
        ["Device Daily Report"],
        [],
        ["Date", "Plant Name", "Device SN", "Daily Yield(kWh)"],
        [dt.date(2025, 1, 1), "Site A", "SN1", 10.5],
        [dt.date(2025, 1, 1), "Site A", "SN2", 9.5],
        [dt.date(2025, 1, 2), "Site A", "SN1", "--"],
    ])
    parsed = load_file("device.xlsx", data=data)
    assert parsed.layout == "long_by_row"
    assert parsed.grain == "inverter"
    assert len(parsed.readings) == 2
    assert parsed.values_kept == 2 and parsed.values_blank == 1
    assert parsed.period_start == dt.date(2025, 1, 1)
    assert {r.device_sn for r in parsed.readings} == {"SN1", "SN2"}


def test_wide_layout_melts_date_columns_and_keeps_cell_reference():
    data = book([
        ["Daily Yield Report (kWh)"],
        ["Plant Name", "Installed Capacity(kWp)", dt.date(2025, 1, 1), dt.date(2025, 1, 2)],
        ["Site A", 100.0, 250.0, None],
        ["Site B", 50.0, 120.0, 130.0],
    ], title="Daily Yield(kWh)")
    parsed = load_file("wide.xlsx", data=data)
    assert parsed.layout == "wide_by_date"
    assert parsed.grain == "plant"
    assert len(parsed.readings) == 3
    assert parsed.values_blank == 1
    first = [r for r in parsed.readings if r.plant_name == "Site A"][0]
    # Row 3 of the sheet, column C — the cell a person can go and look at.
    assert (first.source_row, first.source_col) == (3, "C")
    assert first.metric == "daily_yield"
    assert {s.plant_name: s.installed_kwp for s in parsed.sites} == {"Site A": 100.0, "Site B": 50.0}


def test_site_master_has_no_readings():
    data = book([
        ["Plant List"],
        ["Plant Name", "Installed Capacity(kWp)", "Grid Connection Date", "Plant Status"],
        ["Site A", 100.0, dt.date(2024, 5, 1), "Normal"],
    ])
    parsed = load_file("plants.xlsx", data=data)
    assert parsed.layout == "site_master"
    assert parsed.readings == []
    assert parsed.sites[0].grid_connection_date == dt.date(2024, 5, 1)
    assert parsed.ok


def test_unreadable_file_reports_instead_of_raising():
    parsed = load_file("junk.xlsx", data=b"this is not a spreadsheet")
    assert not parsed.ok
    assert parsed.notes


def test_totals_row_is_not_read_as_a_site():
    data = book([
        ["Plant Name", dt.date(2025, 1, 1)],
        ["Site A", 10.0],
        ["Total", 10.0],
    ])
    parsed = load_file("wide.xlsx", data=data)
    assert {s.plant_name for s in parsed.sites} == {"Site A"}


def test_csv_is_accepted():
    csv_bytes = b"Date,Plant Name,Daily Yield(kWh)\n2025-01-01,Site A,12.5\n"
    parsed = load_file("export.csv", data=csv_bytes)
    assert len(parsed.readings) == 1
    assert parsed.readings[0].value == 12.5


def test_sha256_is_stable_for_identical_bytes():
    data = book([["Plant Name", dt.date(2025, 1, 1)], ["Site A", 1.0]])
    assert load_file("a.xlsx", data=data).sha256 == load_file("b.xlsx", data=data).sha256
