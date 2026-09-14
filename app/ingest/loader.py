"""Bronze loading: take a ParsedFile and put it in the database.

Rules from the task list that live here:
  2.3  the same file twice is refused, not loaded twice
  2.4  a file overlapping a period already loaded is recorded as an overlap,
       and the older readings are superseded rather than deleted
  2.5  bronze.site is upserted from the plant information columns every load
  2.6  the caller gets a parse summary it can put straight on screen
"""
from __future__ import annotations

import logging
from typing import Any

from psycopg.types.json import Json

from app.db import connection
from app.ingest import storage
from app.ingest.parser import ParsedFile, load_file

log = logging.getLogger("ccredits.loader")


def _existing_file(cur, sha256: str) -> dict | None:
    cur.execute(
        "SELECT file_id, filename, uploaded_at, row_count FROM bronze.source_file WHERE sha256 = %s",
        (sha256,),
    )
    return cur.fetchone()


def _find_overlaps(cur, parsed: ParsedFile) -> list[dict]:
    """Files already loaded that cover some of the same ground."""
    if not parsed.period_start or not parsed.period_end:
        return []
    plants = sorted({r.plant_name for r in parsed.readings})
    if not plants:
        return []
    cur.execute(
        """
        SELECT f.file_id, f.filename, f.uploaded_at,
               GREATEST(f.period_start, %s) AS overlap_start,
               LEAST(f.period_end,   %s)    AS overlap_end
        FROM bronze.source_file f
        WHERE f.grain = %s
          AND f.period_start IS NOT NULL
          AND f.period_start <= %s
          AND f.period_end   >= %s
          AND EXISTS (
              SELECT 1 FROM bronze.reading r
              WHERE r.file_id = f.file_id AND r.plant_name = ANY(%s)
          )
        ORDER BY f.uploaded_at
        """,
        (
            parsed.period_start, parsed.period_end, parsed.grain,
            parsed.period_end, parsed.period_start, plants,
        ),
    )
    return cur.fetchall()


def _upsert_sites(cur, parsed: ParsedFile, file_id: int) -> int:
    """Never overwrite a known value with a blank one — a device-grain export
    that omits capacity must not wipe the capacity a plant list supplied."""
    count = 0
    for site in parsed.sites:
        cur.execute(
            """
            INSERT INTO bronze.site (plant_name, installed_kwp, plant_type,
                                     grid_connection_date, address, plant_status,
                                     first_seen_file_id, last_seen_file_id)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (plant_name) DO UPDATE SET
                installed_kwp        = COALESCE(EXCLUDED.installed_kwp,        bronze.site.installed_kwp),
                plant_type           = COALESCE(EXCLUDED.plant_type,           bronze.site.plant_type),
                grid_connection_date = COALESCE(EXCLUDED.grid_connection_date, bronze.site.grid_connection_date),
                address              = COALESCE(EXCLUDED.address,              bronze.site.address),
                plant_status         = COALESCE(EXCLUDED.plant_status,         bronze.site.plant_status),
                last_seen_file_id    = EXCLUDED.last_seen_file_id,
                updated_at           = now()
            """,
            (
                site.plant_name, site.installed_kwp, site.plant_type,
                site.grid_connection_date, site.address, site.plant_status,
                file_id, file_id,
            ),
        )
        count += 1
    return count


def load_parsed(parsed: ParsedFile, data: bytes) -> dict[str, Any]:
    """Insert one parsed file. Returns the summary shown on the upload screen."""
    summary = parsed.summary()

    if not parsed.ok:
        summary["status"] = "unreadable"
        return summary

    with connection() as conn, conn.cursor() as cur:
        duplicate = _existing_file(cur, parsed.sha256)
        if duplicate:
            summary["status"] = "duplicate"
            summary["duplicate_of"] = {
                "file_id": duplicate["file_id"],
                "filename": duplicate["filename"],
                "uploaded_at": duplicate["uploaded_at"].isoformat(),
            }
            summary["message"] = (
                f"Identical file already loaded as #{duplicate['file_id']} "
                f"({duplicate['filename']}). Nothing was loaded again."
            )
            return summary

        overlaps = _find_overlaps(cur, parsed)

        storage_path = storage.store(data, parsed.sha256, parsed.filename)

        cur.execute(
            """
            INSERT INTO bronze.source_file
                (filename, sha256, bytes, grain, sheet_name, header_row,
                 period_start, period_end, row_count, values_kept, values_blank,
                 storage_path, parse_notes)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING file_id, uploaded_at
            """,
            (
                parsed.filename, parsed.sha256, parsed.bytes, parsed.grain,
                parsed.sheet_name, parsed.header_row, parsed.period_start,
                parsed.period_end, parsed.row_count, parsed.values_kept,
                parsed.values_blank, storage_path, Json(parsed.notes),
            ),
        )
        row = cur.fetchone()
        file_id = row["file_id"]

        if parsed.readings:
            with cur.copy(
                """
                COPY bronze.reading
                    (file_id, source_row, source_col, plant_name, device_sn,
                     reading_date, metric, metric_label, value, unit)
                FROM STDIN
                """
            ) as copy:
                for r in parsed.readings:
                    copy.write_row(
                        (
                            file_id, r.source_row, r.source_col, r.plant_name,
                            r.device_sn, r.reading_date, r.metric, r.metric_label,
                            r.value, r.unit,
                        )
                    )

        site_count = _upsert_sites(cur, parsed, file_id)

        for overlap in overlaps:
            cur.execute(
                """
                INSERT INTO bronze.file_overlap
                    (new_file_id, existing_file_id, grain, overlap_start, overlap_end)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (file_id, overlap["file_id"], parsed.grain,
                 overlap["overlap_start"], overlap["overlap_end"]),
            )
        conn.commit()

    summary["status"] = "loaded"
    summary["file_id"] = file_id
    summary["uploaded_at"] = row["uploaded_at"].isoformat()
    summary["storage_path"] = storage_path
    summary["sites_upserted"] = site_count
    summary["overlaps"] = [
        {
            "file_id": o["file_id"],
            "filename": o["filename"],
            "overlap_start": o["overlap_start"].isoformat(),
            "overlap_end": o["overlap_end"].isoformat(),
        }
        for o in overlaps
    ]
    if overlaps:
        summary["message"] = (
            f"Loaded. This file covers dates already loaded by "
            f"{len(overlaps)} earlier file(s); those readings are kept but "
            f"superseded — this upload now wins for the overlapping site-days."
        )
    else:
        summary["message"] = (
            f"Loaded {len(parsed.readings):,} readings across {site_count} site(s)."
        )
    return summary


def load_bytes(data: bytes, filename: str) -> dict[str, Any]:
    """Parse and load one uploaded file."""
    parsed = load_file(filename, data=data, filename=filename)
    return load_parsed(parsed, data)
