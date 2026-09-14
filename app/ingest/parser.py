"""The one parser.

`load_file()` turns a Sungrow export into long-format readings plus whatever
site master data the file happens to carry. The app upload endpoint and the
command line both import this function — there is never a second parser.

Sungrow exports are not one shape, so the parser sniffs rather than assumes:

  * title and blurb rows above the real header are skipped by scoring every
    row in the first 30 and taking the best;
  * a sheet whose header carries many date-like columns is read wide (one
    column per day) and melted;
  * a sheet with a single date column is read long, one column per metric;
  * a sheet with plant information but no dates is read as a site master.

Anything it cannot read becomes a note on the parse summary rather than a
silent drop.
"""
from __future__ import annotations

import csv
import datetime as dt
import hashlib
import io
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from openpyxl.utils import get_column_letter

# --- Column vocabulary -----------------------------------------------------
# Matched against a normalised header (lowercased, unit suffix removed).

PLANT_NAME_KEYS = {
    "plant name", "plant", "power station name", "station name", "station",
    "power plant", "power plant name", "site", "site name", "plant_name",
    "power station", "name",
}
DEVICE_KEYS = {
    "device sn", "device serial number", "serial number", "sn", "inverter sn",
    "device name", "device", "inverter", "inverter name", "equipment sn",
    "device_sn", "serial no", "device no",
}
DATE_KEYS = {
    "date", "time", "day", "statistical period", "date time", "datetime",
    "statistics time", "statistical time", "record time", "period", "data time",
}
INSTALLED_KWP_KEYS = {
    "installed capacity", "capacity", "installed power", "kwp", "nominal power",
    "total installed capacity", "installed capacity kwp", "rated power",
    "plant capacity", "system size", "dc capacity",
}
PLANT_TYPE_KEYS = {"plant type", "station type", "type", "power station type", "system type"}
GRID_DATE_KEYS = {
    "grid connection date", "grid connection time", "grid connected time",
    "grid-connected time", "installation date", "commissioning date",
    "connection date", "grid connect date", "operation date", "online date",
}
ADDRESS_KEYS = {"address", "location", "plant address", "region", "country", "city"}
STATUS_KEYS = {"plant status", "status", "station status", "device status", "state", "plant state"}

# Metric header -> canonical key. Only daily_yield drives credits; the rest are
# kept because throwing away a column you parsed is a decision, not a default.
METRIC_KEYS: dict[str, set[str]] = {
    "daily_yield": {
        "daily yield", "daily energy", "daily generation", "daily power generation",
        "energy yield", "yield", "generation", "power generation", "e-day", "eday",
        "daily yield energy", "day energy", "daily production", "production",
        "energy", "electricity generation", "generated energy", "daily generation energy",
    },
    "total_yield": {
        "total yield", "cumulative yield", "total energy", "e-total", "etotal",
        "total generation", "lifetime yield", "accumulated yield", "total production",
    },
    "specific_energy": {"specific energy", "equivalent hours", "specific yield", "full load hours"},
    "performance_ratio": {"pr", "performance ratio"},
    "peak_power": {"peak power", "max power", "peak ac power"},
}

# Headers we recognise and deliberately do not turn into readings.
IGNORED_METRIC_KEYS = {
    "revenue", "income", "earnings", "profit", "co2 reduction", "co2 reduced",
    "co2", "standard coal saved", "coal saved", "trees planted", "so2 reduction",
    "no", "no.", "index", "serial", "remark", "remarks",
}

NULL_TOKENS = {"", "-", "--", "---", "n/a", "na", "null", "none", "nan", "/", "\\", "--:--"}

UNIT_RE = re.compile(r"[\(\[\{]\s*([^)\]\}]{1,20}?)\s*[\)\]\}]\s*$")
WS_RE = re.compile(r"\s+")


# --- Small helpers ---------------------------------------------------------

def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (dt.datetime, dt.date)):
        return value.isoformat()
    return str(value).strip()


def split_unit(header: str) -> tuple[str, str | None]:
    """'Daily Yield(kWh)' -> ('Daily Yield', 'kWh')."""
    header = header.strip()
    match = UNIT_RE.search(header)
    if not match:
        return header, None
    unit = match.group(1).strip()
    # A parenthesised phrase with spaces is usually a comment, not a unit.
    if " " in unit and not re.fullmatch(r"[\w/²³°%·\.\-]+", unit):
        return header, None
    return header[: match.start()].strip(), unit or None


def normalise(header: str) -> str:
    base, _ = split_unit(_text(header))
    base = base.replace("_", " ").replace("-", " ").strip().lower()
    base = WS_RE.sub(" ", base)
    return base.rstrip(":：").strip()


def parse_number(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = _text(value)
    if text.lower() in NULL_TOKENS:
        return None
    # Drop the unit and any spacing, keeping only what can form a number.
    text = re.sub(r"[^\d.,\-+eE]", "", text)
    if text in {"", "-", "+", ".", ","}:
        return None

    # Decide which separator is the decimal point before stripping anything.
    # A European export writes 1.234,5 or 1 234,5 and must not become 12345.
    last_dot, last_comma = text.rfind("."), text.rfind(",")
    if last_dot >= 0 and last_comma >= 0:
        decimal_sep = "." if last_dot > last_comma else ","
    elif last_comma >= 0:
        # A lone comma is a decimal point when it separates 1-2 trailing digits
        # (1,5) and a thousands separator when it groups 3 (1,234) or repeats.
        tail = text[last_comma + 1 :]
        decimal_sep = "," if (text.count(",") == 1 and len(tail) in (1, 2)) else ""
    elif last_dot >= 0:
        # A lone dot is a decimal point (0.123 must not become 123); only a
        # repeated dot is thousands grouping, as in 1.234.567.
        decimal_sep = "." if text.count(".") == 1 else ""
    else:
        decimal_sep = ""

    if decimal_sep == ",":
        text = text.replace(".", "").replace(",", ".")
    elif decimal_sep == ".":
        text = text.replace(",", "")
    else:
        text = text.replace(",", "").replace(".", "")

    try:
        return float(text)
    except ValueError:
        return None


_DATE_FORMATS = (
    "%Y-%m-%d", "%Y/%m/%d", "%d/%m/%Y", "%m/%d/%Y", "%d-%m-%Y", "%d.%m.%Y",
    "%Y%m%d", "%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M:%S", "%Y-%m-%d %H:%M",
    "%d/%m/%Y %H:%M", "%b %d, %Y", "%d %b %Y", "%Y-%m", "%Y/%m", "%m/%d/%y", "%d/%m/%y",
)


def parse_date(value: Any) -> dt.date | None:
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    text = _text(value)
    if not text or text.lower() in NULL_TOKENS:
        return None
    # Excel serial dates that arrived as numbers.
    if re.fullmatch(r"\d{5}(\.\d+)?", text):
        serial = float(text)
        if 20000 <= serial <= 60000:
            return (dt.datetime(1899, 12, 30) + dt.timedelta(days=serial)).date()
    for fmt in _DATE_FORMATS:
        try:
            return dt.datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def looks_like_date_header(value: Any) -> bool:
    """A header cell that is a date, e.g. the columns of a wide export."""
    if isinstance(value, (dt.date, dt.datetime)):
        return True
    text = _text(value)
    if not text or len(text) > 24:
        return False
    if not re.search(r"\d", text):
        return False
    return parse_date(text) is not None


# --- Reading the workbook into plain rows ----------------------------------

@dataclass
class Sheet:
    name: str
    rows: list[list[Any]]  # rows[0] is spreadsheet row 1


def read_sheets(path: Path | str, data: bytes | None = None) -> list[Sheet]:
    """Read xlsx/xlsm/xls/csv into plain row lists, values only."""
    path = Path(path)
    suffix = path.suffix.lower()
    raw = data if data is not None else path.read_bytes()

    if suffix in {".csv", ".txt", ".tsv"}:
        text = raw.decode("utf-8-sig", errors="replace")
        sample = text[:4096]
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
        except csv.Error:
            dialect = csv.excel
        rows = [list(r) for r in csv.reader(io.StringIO(text), dialect)]
        return [Sheet(name=path.stem, rows=rows)]

    if suffix == ".xls":
        import xlrd

        book = xlrd.open_workbook(file_contents=raw)
        sheets = []
        for sh in book.sheets():
            rows = []
            for r in range(sh.nrows):
                row = []
                for c in range(sh.ncols):
                    cell = sh.cell(r, c)
                    if cell.ctype == xlrd.XL_CELL_DATE:
                        row.append(xlrd.xldate_as_datetime(cell.value, book.datemode))
                    elif cell.ctype == xlrd.XL_CELL_EMPTY:
                        row.append(None)
                    else:
                        row.append(cell.value)
                rows.append(row)
            sheets.append(Sheet(name=sh.name, rows=rows))
        return sheets

    from openpyxl import load_workbook

    book = load_workbook(io.BytesIO(raw), read_only=True, data_only=True)
    sheets = []
    for ws in book.worksheets:
        rows = [list(r) for r in ws.iter_rows(values_only=True)]
        sheets.append(Sheet(name=ws.title, rows=rows))
    book.close()
    return sheets


# --- Header detection ------------------------------------------------------

def _score_header_row(row: list[Any]) -> int:
    """How much does this row look like a header?"""
    score = 0
    date_cols = 0
    for cell in row:
        text = _text(cell)
        if not text:
            continue
        key = normalise(text)
        if key in PLANT_NAME_KEYS or key in DEVICE_KEYS or key in DATE_KEYS:
            score += 6
        elif key in INSTALLED_KWP_KEYS or key in GRID_DATE_KEYS or key in STATUS_KEYS:
            score += 4
        elif any(key in aliases for aliases in METRIC_KEYS.values()):
            score += 5
        elif looks_like_date_header(cell):
            date_cols += 1
        elif isinstance(cell, str) and len(text) <= 40:
            score += 1
    if date_cols >= 2:
        score += 5 + min(date_cols, 20)
    return score


def find_header_row(rows: list[list[Any]], limit: int = 30) -> int | None:
    best_index, best_score = None, 0
    for index, row in enumerate(rows[:limit]):
        if not any(_text(c) for c in row):
            continue
        score = _score_header_row(row)
        if score > best_score:
            best_index, best_score = index, score
    if best_score < 6:
        return None
    return best_index


# --- The parse result ------------------------------------------------------

@dataclass
class Reading:
    source_row: int
    source_col: str | None
    plant_name: str
    device_sn: str | None
    reading_date: dt.date
    metric: str
    metric_label: str | None
    value: float | None
    unit: str | None


@dataclass
class SiteInfo:
    plant_name: str
    installed_kwp: float | None = None
    plant_type: str | None = None
    grid_connection_date: dt.date | None = None
    address: str | None = None
    plant_status: str | None = None


@dataclass
class ParsedFile:
    filename: str
    sha256: str
    bytes: int
    grain: str = "unknown"
    layout: str = "unknown"
    sheet_name: str | None = None
    header_row: int | None = None
    period_start: dt.date | None = None
    period_end: dt.date | None = None
    row_count: int = 0
    values_kept: int = 0
    values_blank: int = 0
    readings: list[Reading] = field(default_factory=list)
    sites: list[SiteInfo] = field(default_factory=list)
    blank_by_metric: dict[str, int] = field(default_factory=dict)
    kept_by_metric: dict[str, int] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return bool(self.readings) or bool(self.sites)

    def summary(self) -> dict:
        return {
            "filename": self.filename,
            "sha256": self.sha256,
            "bytes": self.bytes,
            "grain": self.grain,
            "layout": self.layout,
            "sheet_name": self.sheet_name,
            "header_row": self.header_row,
            "period_start": self.period_start.isoformat() if self.period_start else None,
            "period_end": self.period_end.isoformat() if self.period_end else None,
            "row_count": self.row_count,
            "values_kept": self.values_kept,
            "values_blank": self.values_blank,
            "reading_count": len(self.readings),
            "site_count": len(self.sites),
            "blank_by_metric": self.blank_by_metric,
            "kept_by_metric": self.kept_by_metric,
            "notes": self.notes,
        }


# --- Column map ------------------------------------------------------------

@dataclass
class ColumnMap:
    plant_name: int | None = None
    device_sn: int | None = None
    date: int | None = None
    installed_kwp: int | None = None
    plant_type: int | None = None
    grid_connection_date: int | None = None
    address: int | None = None
    plant_status: int | None = None
    metrics: dict[int, tuple[str, str, str | None]] = field(default_factory=dict)  # idx -> (key, label, unit)
    date_columns: dict[int, dt.date] = field(default_factory=dict)
    unknown: list[str] = field(default_factory=list)


def map_columns(header: list[Any]) -> ColumnMap:
    cmap = ColumnMap()
    for index, cell in enumerate(header):
        raw = _text(cell)
        if not raw:
            continue
        if looks_like_date_header(cell):
            parsed = parse_date(cell)
            if parsed:
                cmap.date_columns[index] = parsed
                continue
        key = normalise(raw)
        label, unit = split_unit(raw)
        if key in DATE_KEYS and cmap.date is None:
            cmap.date = index
        elif key in DEVICE_KEYS and cmap.device_sn is None:
            cmap.device_sn = index
        elif key in PLANT_NAME_KEYS and cmap.plant_name is None:
            cmap.plant_name = index
        elif key in INSTALLED_KWP_KEYS and cmap.installed_kwp is None:
            cmap.installed_kwp = index
        elif key in GRID_DATE_KEYS and cmap.grid_connection_date is None:
            cmap.grid_connection_date = index
        elif key in PLANT_TYPE_KEYS and cmap.plant_type is None:
            cmap.plant_type = index
        elif key in ADDRESS_KEYS and cmap.address is None:
            cmap.address = index
        elif key in STATUS_KEYS and cmap.plant_status is None:
            cmap.plant_status = index
        elif key in IGNORED_METRIC_KEYS:
            continue
        else:
            matched = None
            for canonical, aliases in METRIC_KEYS.items():
                if key in aliases:
                    matched = canonical
                    break
            if matched:
                cmap.metrics[index] = (matched, label, unit)
            else:
                cmap.unknown.append(raw)
    return cmap


def _metric_for_wide_sheet(sheet_name: str, preamble: Iterable[Any], header: list[Any]) -> tuple[str, str, str | None]:
    """A wide export names its metric in the sheet name or a title row."""
    candidates = [sheet_name] + [_text(c) for c in preamble] + [_text(c) for c in header[:3]]
    for text in candidates:
        if not text:
            continue
        key = normalise(text)
        label, unit = split_unit(text)
        for canonical, aliases in METRIC_KEYS.items():
            if key in aliases:
                return canonical, label, unit
        for canonical, aliases in METRIC_KEYS.items():
            for alias in aliases:
                if len(alias) > 5 and alias in key:
                    return canonical, label, unit
        found = UNIT_RE.search(text)
        if found and found.group(1).strip().lower() in {"kwh", "wh", "mwh"}:
            return "daily_yield", label, found.group(1).strip()
    return "daily_yield", "Daily Yield", "kWh"


# --- Sheet parsing ---------------------------------------------------------

def _parse_sheet(sheet: Sheet, result: ParsedFile) -> ParsedFile | None:
    rows = sheet.rows
    header_index = find_header_row(rows)
    if header_index is None:
        return None
    header = rows[header_index]
    cmap = map_columns(header)

    if cmap.plant_name is None and cmap.device_sn is None:
        return None

    body = rows[header_index + 1 :]
    result.sheet_name = sheet.name
    result.header_row = header_index + 1  # 1-based, as a human counts rows
    result.grain = "inverter" if cmap.device_sn is not None else "plant"

    if cmap.date_columns:
        _parse_wide(sheet, body, header_index, header, cmap, result)
    elif cmap.date is not None and cmap.metrics:
        _parse_long(body, header_index, cmap, result)
    elif cmap.installed_kwp is not None or cmap.grid_connection_date is not None:
        _parse_site_master(body, cmap, result)
    else:
        return None

    if cmap.unknown:
        result.notes.append(
            "Columns read but not used: " + ", ".join(sorted(set(cmap.unknown))[:12])
        )
    return result


def _site_from_row(row: list[Any], cmap: ColumnMap, plant_name: str) -> SiteInfo:
    def at(index: int | None) -> Any:
        return row[index] if index is not None and index < len(row) else None

    return SiteInfo(
        plant_name=plant_name,
        installed_kwp=parse_number(at(cmap.installed_kwp)),
        plant_type=_text(at(cmap.plant_type)) or None,
        grid_connection_date=parse_date(at(cmap.grid_connection_date)),
        address=_text(at(cmap.address)) or None,
        plant_status=_text(at(cmap.plant_status)) or None,
    )


def _record(result: ParsedFile, metric: str, value: float | None) -> None:
    if value is None:
        result.values_blank += 1
        result.blank_by_metric[metric] = result.blank_by_metric.get(metric, 0) + 1
    else:
        result.values_kept += 1
        result.kept_by_metric[metric] = result.kept_by_metric.get(metric, 0) + 1


def _parse_wide(sheet: Sheet, body, header_index: int, header, cmap: ColumnMap, result: ParsedFile) -> None:
    """Identifier columns plus one column per day."""
    result.layout = "wide_by_date"
    metric, label, unit = _metric_for_wide_sheet(
        sheet.name, [c for r in sheet.rows[:header_index] for c in r], header
    )
    seen_sites: dict[str, SiteInfo] = {}

    for offset, row in enumerate(body):
        row_number = header_index + 2 + offset
        plant_name = _text(row[cmap.plant_name]) if cmap.plant_name is not None and cmap.plant_name < len(row) else ""
        device_sn = _text(row[cmap.device_sn]) if cmap.device_sn is not None and cmap.device_sn < len(row) else ""
        if not plant_name and not device_sn:
            continue
        if not plant_name:
            plant_name = device_sn
        if normalise(plant_name) in PLANT_NAME_KEYS or normalise(plant_name) in {"total", "sum", "subtotal"}:
            continue
        result.row_count += 1
        if plant_name not in seen_sites:
            seen_sites[plant_name] = _site_from_row(row, cmap, plant_name)

        for index, day in cmap.date_columns.items():
            raw = row[index] if index < len(row) else None
            value = parse_number(raw)
            _record(result, metric, value)
            if value is None:
                continue
            result.readings.append(
                Reading(
                    source_row=row_number,
                    source_col=get_column_letter(index + 1),
                    plant_name=plant_name,
                    device_sn=device_sn or None,
                    reading_date=day,
                    metric=metric,
                    metric_label=label,
                    value=value,
                    unit=unit,
                )
            )
    result.sites = list(seen_sites.values())


def _parse_long(body, header_index: int, cmap: ColumnMap, result: ParsedFile) -> None:
    """One date column, one column per metric."""
    result.layout = "long_by_row"
    seen_sites: dict[str, SiteInfo] = {}

    for offset, row in enumerate(body):
        row_number = header_index + 2 + offset
        plant_name = _text(row[cmap.plant_name]) if cmap.plant_name is not None and cmap.plant_name < len(row) else ""
        device_sn = _text(row[cmap.device_sn]) if cmap.device_sn is not None and cmap.device_sn < len(row) else ""
        if not plant_name and not device_sn:
            continue
        if not plant_name:
            plant_name = device_sn
        if normalise(plant_name) in {"total", "sum", "subtotal"}:
            continue
        day = parse_date(row[cmap.date]) if cmap.date < len(row) else None
        if day is None:
            result.notes.append(f"Row {row_number}: no readable date, row skipped")
            continue
        result.row_count += 1
        if plant_name not in seen_sites:
            seen_sites[plant_name] = _site_from_row(row, cmap, plant_name)

        for index, (metric, label, unit) in cmap.metrics.items():
            raw = row[index] if index < len(row) else None
            value = parse_number(raw)
            _record(result, metric, value)
            if value is None:
                continue
            result.readings.append(
                Reading(
                    source_row=row_number,
                    source_col=get_column_letter(index + 1),
                    plant_name=plant_name,
                    device_sn=device_sn or None,
                    reading_date=day,
                    metric=metric,
                    metric_label=label,
                    value=value,
                    unit=unit,
                )
            )
    result.sites = list(seen_sites.values())


def _parse_site_master(body, cmap: ColumnMap, result: ParsedFile) -> None:
    """A plant list export: fleet master data, no readings."""
    result.layout = "site_master"
    result.grain = "plant"
    seen: dict[str, SiteInfo] = {}
    for row in body:
        plant_name = _text(row[cmap.plant_name]) if cmap.plant_name is not None and cmap.plant_name < len(row) else ""
        if not plant_name or normalise(plant_name) in {"total", "sum", "subtotal"}:
            continue
        result.row_count += 1
        seen[plant_name] = _site_from_row(row, cmap, plant_name)
    result.sites = list(seen.values())


# --- Entry point -----------------------------------------------------------

def load_file(path: Path | str, data: bytes | None = None, filename: str | None = None) -> ParsedFile:
    """Parse one export. Never raises on bad content — it reports instead."""
    path = Path(path)
    raw = data if data is not None else path.read_bytes()
    result = ParsedFile(
        filename=filename or path.name,
        sha256=hashlib.sha256(raw).hexdigest(),
        bytes=len(raw),
    )

    try:
        sheets = read_sheets(path, raw)
    except Exception as exc:  # a corrupt or unsupported file is a note, not a crash
        result.notes.append(f"Could not open the file: {exc}")
        return result

    best: ParsedFile | None = None
    for sheet in sheets:
        if not sheet.rows:
            continue
        candidate = ParsedFile(filename=result.filename, sha256=result.sha256, bytes=result.bytes)
        try:
            parsed = _parse_sheet(sheet, candidate)
        except Exception as exc:
            result.notes.append(f"Sheet '{sheet.name}' could not be read: {exc}")
            continue
        if parsed is None:
            result.notes.append(f"Sheet '{sheet.name}': no recognisable header, skipped")
            continue
        if best is None or len(parsed.readings) > len(best.readings):
            best = parsed

    if best is None:
        result.notes.append(
            "No sheet had a header this parser recognised. Expected a plant or "
            "device column plus either a date column or date columns across the top."
        )
        return result

    notes = result.notes + best.notes
    best.notes = notes
    if best.readings:
        days = [r.reading_date for r in best.readings]
        best.period_start, best.period_end = min(days), max(days)
    return best
