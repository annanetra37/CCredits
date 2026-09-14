"""Generate Sungrow-shaped sample exports.

The real SANNOVA exports are not in this repository. These files stand in for
them so the pipeline can be exercised end to end, and so a demo can be shown
before the real files arrive. They deliberately contain the awkward things the
real ones have: a title row above the header, blank cells, a data gap, zero
days, one implausible spike, and a site that connects part-way through.

    python scripts/make_sample_data.py [output_dir]
"""
from __future__ import annotations

import datetime as dt
import math
import random
import sys
from pathlib import Path

from openpyxl import Workbook

random.seed(20250914)

START = dt.date(2025, 1, 1)
END = dt.date(2025, 3, 31)

SITES = [
    # name, kWp, grid connection, type, address, status, inverters
    ("SANNOVA Ararat 1",   248.4, dt.date(2023, 6, 14),  "Ground mounted", "Ararat, Armenia",   "Normal", 3),
    ("SANNOVA Armavir 2",  186.0, dt.date(2023, 9, 2),   "Rooftop",        "Armavir, Armenia",  "Normal", 2),
    ("SANNOVA Kotayk 3",   412.5, dt.date(2024, 2, 18),  "Ground mounted", "Kotayk, Armenia",   "Normal", 4),
    ("SANNOVA Shirak 4",    95.2, dt.date(2024, 11, 5),  "Rooftop",        "Shirak, Armenia",   "Normal", 1),
    ("SANNOVA Syunik 5",   320.0, dt.date(2025, 2, 10),  "Ground mounted", "Syunik, Armenia",   "Normal", 3),
    ("SANNOVA Lori 6",     150.8, dt.date(2023, 4, 21),  "Rooftop",        "Lori, Armenia",     "Offline", 2),
]


def daily_kwh(kwp: float, day: dt.date, seed_offset: int) -> float | None:
    """Armenian seasonality with weather noise, plus deliberate blemishes."""
    doy = day.timetuple().tm_yday
    seasonal = 2.4 + 1.9 * math.sin((doy - 80) / 365 * 2 * math.pi)  # kWh/kWp/day
    weather = random.uniform(0.45, 1.15)
    yield_per_kwp = max(0.0, seasonal * weather)
    return round(kwp * yield_per_kwp, 2)


def build_series() -> dict[tuple[str, dt.date], float | None]:
    series: dict[tuple[str, dt.date], float | None] = {}
    days = [START + dt.timedelta(days=i) for i in range((END - START).days + 1)]
    for index, (name, kwp, grid_date, *_rest) in enumerate(SITES):
        for day in days:
            if day < grid_date:
                # The site exists in the fleet but is not yet connected.
                series[(name, day)] = None if day < grid_date - dt.timedelta(days=5) else 0.0
                continue
            series[(name, day)] = daily_kwh(kwp, day, index)

    # A data gap: the logger dropped out for nine days.
    for i in range(9):
        series[("SANNOVA Armavir 2", dt.date(2025, 2, 3) + dt.timedelta(days=i))] = None
    # A broken inverter: six zero days in a row in the middle of March.
    for i in range(6):
        series[("SANNOVA Kotayk 3", dt.date(2025, 3, 12) + dt.timedelta(days=i))] = 0.0
    # One implausible spike — a meter reset reported as a day's generation.
    series[("SANNOVA Ararat 1", dt.date(2025, 2, 19))] = 248.4 * 11.4
    return series


def write_plant_list(path: Path) -> None:
    wb = Workbook()
    ws = wb.active
    ws.title = "Plant List"
    ws.append(["Plant List Export", None, None, None, None, None])
    ws.append([f"Exported {dt.date.today().isoformat()}", None, None, None, None, None])
    ws.append([])
    ws.append(["Plant Name", "Installed Capacity(kWp)", "Plant Type",
               "Grid Connection Date", "Address", "Plant Status"])
    for name, kwp, grid_date, ptype, address, status, _inv in SITES:
        ws.append([name, kwp, ptype, grid_date, address, status])
    wb.save(path)


def write_plant_wide(path: Path, series) -> None:
    """Plant-grain report: identifiers on the left, one column per day."""
    wb = Workbook()
    ws = wb.active
    ws.title = "Daily Yield(kWh)"
    days = [START + dt.timedelta(days=i) for i in range((END - START).days + 1)]
    ws.append(["Power Station Daily Yield Report (kWh)"])
    ws.append([f"Period: {START.isoformat()} to {END.isoformat()}"])
    ws.append(["Plant Name", "Installed Capacity(kWp)", "Grid Connection Date"] + days)
    for name, kwp, grid_date, *_rest in SITES:
        row = [name, kwp, grid_date]
        for day in days:
            row.append(series.get((name, day)))
        ws.append(row)
    wb.save(path)


def write_device_long(path: Path, series) -> None:
    """Inverter-grain report: one row per device per day."""
    wb = Workbook()
    ws = wb.active
    ws.title = "Device Report"
    ws.append(["Device Daily Report"])
    ws.append([])
    ws.append(["Date", "Plant Name", "Device SN", "Daily Yield(kWh)",
               "Total Yield(kWh)", "Plant Status"])
    days = [START + dt.timedelta(days=i) for i in range((END - START).days + 1)]
    totals: dict[str, float] = {}
    for name, kwp, grid_date, ptype, address, status, inverters in SITES:
        if name != "SANNOVA Shirak 4":
            continue  # one site reported at device grain, as in the real export set
        for day in days:
            site_value = series.get((name, day))
            for n in range(inverters):
                sn = f"A2340{n}{abs(hash(name)) % 900 + 100}"
                share = None if site_value is None else round(site_value / inverters, 2)
                if share is not None:
                    totals[sn] = totals.get(sn, 0.0) + share
                ws.append([day, name, sn, share, round(totals.get(sn, 0.0), 2), status])
    wb.save(path)


def main() -> int:
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "sample_data")
    out.mkdir(parents=True, exist_ok=True)
    series = build_series()
    write_plant_list(out / "sungrow_plant_list.xlsx")
    write_plant_wide(out / "sungrow_plant_daily_yield_2025Q1.xlsx", series)
    write_device_long(out / "sungrow_device_daily_2025Q1.xlsx", series)
    print(f"Wrote 3 sample exports to {out.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
