#!/usr/bin/env python3
"""Load Sungrow exports into Bronze from the terminal.

The terminal is faster than the browser for a handful of files, and it imports
exactly the same `load_file()` the upload endpoint uses. One parser, never two.

    python cli/load_bronze.py sample_data/*.xlsx
    python cli/load_bronze.py --migrate sample_data/*.xlsx
    python cli/load_bronze.py --reset            # drop every loaded file
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db import connection, migrate  # noqa: E402
from app.ingest.loader import load_parsed  # noqa: E402
from app.ingest.parser import load_file  # noqa: E402


def reset() -> None:
    with connection() as conn, conn.cursor() as cur:
        cur.execute("TRUNCATE bronze.reading, bronze.file_overlap, bronze.site, bronze.source_file RESTART IDENTITY CASCADE")
        conn.commit()
    print("Bronze emptied. Reference data in Gold is untouched.")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Load Sungrow exports into Bronze.")
    ap.add_argument("files", nargs="*", type=Path, help="xlsx / xls / csv exports")
    ap.add_argument("--migrate", action="store_true", help="apply schema first")
    ap.add_argument("--reset", action="store_true", help="empty Bronze before loading")
    ap.add_argument("--json", action="store_true", help="print the raw summaries")
    args = ap.parse_args(argv)

    if args.migrate:
        migrate()
        print("Schema applied.")
    if args.reset:
        reset()
    if not args.files:
        return 0

    exit_code = 0
    for path in args.files:
        if not path.exists():
            print(f"  {path}: not found")
            exit_code = 1
            continue
        data = path.read_bytes()
        parsed = load_file(path, data=data)
        summary = load_parsed(parsed, data)

        if args.json:
            print(json.dumps(summary, indent=2, default=str))
            continue

        status = summary.get("status")
        print(f"\n{path.name}")
        print(f"  status        {status}")
        if status == "unreadable":
            for note in summary.get("notes", []):
                print(f"  ! {note}")
            exit_code = 1
            continue
        if status == "duplicate":
            print(f"  {summary['message']}")
            continue
        print(f"  layout        {summary['layout']} ({summary['grain']} grain), header row {summary['header_row']}")
        print(f"  period        {summary['period_start']} to {summary['period_end']}")
        print(f"  rows read     {summary['row_count']:,}")
        print(f"  values kept   {summary['values_kept']:,}   blank {summary['values_blank']:,}")
        print(f"  readings      {summary['reading_count']:,}")
        print(f"  sites         {summary['sites_upserted']}")
        for overlap in summary.get("overlaps", []):
            print(f"  ! overlaps #{overlap['file_id']} {overlap['filename']} "
                  f"({overlap['overlap_start']} to {overlap['overlap_end']})")
        for note in summary.get("notes", []):
            print(f"  note: {note}")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
