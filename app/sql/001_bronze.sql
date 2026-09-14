-- ---------------------------------------------------------------------------
-- Bronze: what the files said. Immutable. Nothing here is ever updated.
-- ---------------------------------------------------------------------------
CREATE SCHEMA IF NOT EXISTS bronze;
CREATE SCHEMA IF NOT EXISTS silver;
CREATE SCHEMA IF NOT EXISTS gold;

-- 1.1 One row per uploaded file.
CREATE TABLE IF NOT EXISTS bronze.source_file (
    file_id         bigserial PRIMARY KEY,
    filename        text        NOT NULL,
    sha256          text        NOT NULL UNIQUE,
    bytes           bigint      NOT NULL,
    grain           text        NOT NULL CHECK (grain IN ('plant', 'inverter', 'unknown')),
    sheet_name      text,
    header_row      integer,
    period_start    date,
    period_end      date,
    uploaded_at     timestamptz NOT NULL DEFAULT now(),
    row_count       integer     NOT NULL DEFAULT 0,
    values_kept     integer     NOT NULL DEFAULT 0,
    values_blank    integer     NOT NULL DEFAULT 0,
    storage_path    text,
    parse_notes     jsonb       NOT NULL DEFAULT '[]'::jsonb
);

-- 1.2 Long format, one row per value read out of a cell.
CREATE TABLE IF NOT EXISTS bronze.reading (
    reading_id      bigserial PRIMARY KEY,
    file_id         bigint      NOT NULL REFERENCES bronze.source_file (file_id) ON DELETE CASCADE,
    source_row      integer     NOT NULL,   -- 1-based row in the sheet, as a human sees it
    source_col      text,                   -- spreadsheet column letter, for cell-level lineage
    plant_name      text        NOT NULL,
    device_sn       text,
    reading_date    date        NOT NULL,
    metric          text        NOT NULL,   -- canonical key, e.g. daily_yield
    metric_label    text,                   -- the raw header text as written in the file
    value           numeric(18, 4),
    unit            text
);

CREATE INDEX IF NOT EXISTS reading_file_idx   ON bronze.reading (file_id);
CREATE INDEX IF NOT EXISTS reading_lookup_idx ON bronze.reading (plant_name, reading_date, metric);

-- 1.3 The fleet master, derived from the plant information columns.
CREATE TABLE IF NOT EXISTS bronze.site (
    plant_name           text PRIMARY KEY,
    installed_kwp        numeric(12, 3),
    plant_type           text,
    grid_connection_date date,
    address              text,
    plant_status         text,
    first_seen_file_id   bigint REFERENCES bronze.source_file (file_id),
    last_seen_file_id    bigint REFERENCES bronze.source_file (file_id),
    updated_at           timestamptz NOT NULL DEFAULT now()
);

-- 2.4 Overlaps are recorded, never resolved by deletion. Silver decides which
-- reading wins; this table exists so the overlap itself stays visible.
CREATE TABLE IF NOT EXISTS bronze.file_overlap (
    overlap_id      bigserial PRIMARY KEY,
    new_file_id     bigint NOT NULL REFERENCES bronze.source_file (file_id) ON DELETE CASCADE,
    existing_file_id bigint NOT NULL REFERENCES bronze.source_file (file_id) ON DELETE CASCADE,
    grain           text   NOT NULL,
    overlap_start   date   NOT NULL,
    overlap_end     date   NOT NULL,
    detected_at     timestamptz NOT NULL DEFAULT now()
);

-- Rules and thresholds. Read by the Silver views so that every threshold on
-- screen is the same number the SQL used.
CREATE TABLE IF NOT EXISTS silver.parameter (
    key        text PRIMARY KEY,
    num        numeric,
    txt        text,
    updated_at timestamptz NOT NULL DEFAULT now()
);
