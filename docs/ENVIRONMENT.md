# Environment variables — how to configure CCredits

Every tunable is an environment variable, read once at startup by
`app/config.py`. **The app starts with none of them set** — the defaults below
are what you get out of the box.

Locally: copy `.env.example` to `.env` and edit it.
On Railway: set them as service variables (Service → **Variables**). Changing a
variable there redeploys the service.

---

## Quick reference

| Variable | Default | What it does |
|---|---|---|
| `DATABASE_URL` | `postgresql://postgres:postgres@127.0.0.1:5432/ccredits` | Postgres connection |
| `PORT` | `8000` | Port to bind. **Railway injects this — don't set it** |
| `APP_ENV` | `local` | Free-text label, shown by `/api/health` |
| `LOG_LEVEL` | `info` | `debug` · `info` · `warning` · `error` |
| `AUTO_MIGRATE` | `true` | Apply the schema on startup |
| `STORAGE_BACKEND` | `local` | `local` or `s3` |
| `STORAGE_DIR` | `./var/uploads` | Where originals go when backend is `local` |
| `UPLOAD_MAX_MB` | `50` | Per-file upload limit |
| `S3_BUCKET` | *(empty)* | Bucket for uploaded originals |
| `S3_PREFIX` | `uploads` | Key prefix inside the bucket |
| `S3_ENDPOINT_URL` | *(empty)* | Set for non-AWS S3-compatible storage |
| `S3_REGION` | `eu-west-1` | Bucket region |
| `S3_ACCESS_KEY_ID` | *(empty)* | Access key |
| `S3_SECRET_ACCESS_KEY` | *(empty)* | Secret key |
| `ADMIN_TOKEN` | *(empty)* | If set, uploads require this token |
| `IMPLAUSIBLE_KWH_PER_KWP` | `8.0` | Implausibility threshold |
| `ZERO_DAY_POLICY` | `suspect` | `suspect` or `ok` |
| `MISSING_DAY_POLICY` | `exclude` | `exclude` or `zero` |
| `TRUST_GRID_CONNECTION_DATE` | `true` | Shown as an assumption |
| `DEFAULT_EMISSION_FACTOR` | `0.3550` | Seed only — see the warning below |
| `DEFAULT_EMISSION_FACTOR_SOURCE` | IFI 2021 v3.2 … | Seed only |
| `DEFAULT_IREC_PRICE_PER_MWH` | `1.50` | Seed only |
| `DEFAULT_VCU_PRICE_PER_TCO2E` | `8.00` | Seed only |
| `PRICE_CURRENCY` | `USD` | Seed only |
| `BANNER_TEXT` | `Pilot data. Not verified.` | The banner on every screen |
| `FLEET_NAME` | `SANNOVA` | Shown next to the product name |

---

## Core

### `DATABASE_URL`
The one variable you actually have to get right.

On Railway, add a **Postgres** database to the project, then reference it rather
than pasting a literal:

```
DATABASE_URL=${{Postgres.DATABASE_URL}}
```

That way it keeps working when credentials rotate. `postgres://` URLs are
rewritten to `postgresql://` automatically, so either form is fine.

Locally:
```bash
createdb ccredits
export DATABASE_URL=postgresql://postgres:postgres@127.0.0.1:5432/ccredits
```

### `PORT`
**Do not set this on Railway.** Railway injects it, and the container binds
whatever it is given. Set it locally only if 8000 is taken.

### `AUTO_MIGRATE`
The schema is idempotent — it creates tables if absent and rebuilds the view
graph every start — so leaving this `true` is right for the demo. Set it `false`
if you want schema changes to be a deliberate, separate step:

```bash
python cli/load_bronze.py --migrate
```

If the database is unreachable at startup the app still starts and
`/api/health` reports `degraded` with the reason, rather than crash-looping.

---

## Storage

The original uploaded bytes are kept unmodified alongside their SHA-256. That
stored file is the evidence the lineage view ultimately points at, so it needs
to outlive the container.

### `STORAGE_BACKEND=local`
Writes under `STORAGE_DIR`. Fine locally. **On Railway the filesystem is
ephemeral** — files are lost on every redeploy. The database rows survive (the
readings are already parsed), but the original files do not.

### `STORAGE_BACKEND=s3`
Use this for anything you will show twice. Works with AWS S3 and any
S3-compatible service (Cloudflare R2, Backblaze B2, MinIO, Railway's own
buckets) via `S3_ENDPOINT_URL`.

```bash
STORAGE_BACKEND=s3
S3_BUCKET=ccredits-pilot
S3_REGION=eu-west-1
S3_ACCESS_KEY_ID=...
S3_SECRET_ACCESS_KEY=...
# Cloudflare R2 or MinIO also need:
# S3_ENDPOINT_URL=https://<account>.r2.cloudflarestorage.com
```

If `STORAGE_BACKEND=s3` and `S3_BUCKET` is empty, uploads fail with a clear
error rather than silently writing to a disk that is about to disappear.

### `UPLOAD_MAX_MB`
Files larger than this are rejected with a message on the upload screen. Raise
it if a fleet-wide export is bigger.

---

## Access

### `ADMIN_TOKEN`
Empty by default: **anyone with the URL can upload.** That is deliberate for a
laptop demo and wrong for a link you send to an investor.

Set it to any string and the upload endpoint requires a matching
`X-Admin-Token` header. The portal prompts for it once and remembers it in the
browser. Reading stays open either way — only writes are gated.

```bash
ADMIN_TOKEN=$(openssl rand -hex 24)
```

---

## The stated assumptions

These four are the ones the task list calls out as decisions that cost the same
whether the app takes thirty minutes or three months. They are environment
variables so that a disagreement about a threshold is a config change, not an
argument. All four are displayed on the Overview screen with the variable name
next to them.

The thresholds are pushed into the `silver.parameter` table at startup, and the
SQL views read them from there — so the number on screen and the number the
calculation used are the same number by construction.

### `IMPLAUSIBLE_KWH_PER_KWP` — default `8.0`
Daily specific yield (kWh generated ÷ kWp installed) above which a day is
flagged suspect and excluded. **8.0 is a placeholder for Armenia and is the
single most arguable number in the app.** A summer day at a well-sited Armenian
plant can approach 7; a meter reset reads as hundreds.

Raising it lets more energy through and raises your headline. Lowering it is
conservative. Either way the excluded energy stays visible in the river.

Sites with no installed capacity (e.g. only a device report was uploaded) can't
have a specific yield, so this rule can't fire for them.

### `ZERO_DAY_POLICY` — default `suspect`
- `suspect` — a zero-generation day is flagged and excluded.
- `ok` — zero is treated as a real, eligible zero.

A winter zero and a broken inverter look identical in this dataset. `suspect` is
the conservative reading; it costs you nothing in energy (zero is zero) but it
lowers your quality score, which is the honest signal.

### `MISSING_DAY_POLICY` — default `exclude`
- `exclude` — a missing day is a gap: excluded, flagged missing, and listed on
  the Data quality screen.
- `zero` — a missing day is treated as a covered day that generated nothing.

Neither setting invents energy — a missing day contributes 0 kWh either way. The
difference is **coverage**: `exclude` reports the gap honestly and lowers your
coverage percentage; `zero` makes the fleet look fully covered. `exclude` is the
answer you can defend.

### `TRUST_GRID_CONNECTION_DATE` — default `true`
Whether the grid connection date from the plant list is taken as given. It
decides when each site starts earning, so it is worth a documented answer. Today
this variable is displayed as a stated assumption; the date is used as supplied
either way. Set it `false` when you want the screen to say verification is
outstanding.

---

## Reference data seeds — read this before changing them

`DEFAULT_EMISSION_FACTOR`, `DEFAULT_EMISSION_FACTOR_SOURCE`,
`DEFAULT_IREC_PRICE_PER_MWH`, `DEFAULT_VCU_PRICE_PER_TCO2E` and `PRICE_CURRENCY`
**only seed empty reference tables on first run.** Once `gold.emission_factor`
and `gold.price` have rows, changing these variables does nothing.

That is on purpose: factors and prices are versioned, dated rows with a source
and a vintage, not constants. After the first run, edit the rows:

```sql
-- A new factor from a specific date; the old one keeps its own validity window.
UPDATE gold.emission_factor SET valid_to = DATE '2024-12-31'
 WHERE valid_to IS NULL;

INSERT INTO gold.emission_factor
    (value_tco2e_per_mwh, factor_type, source, vintage, valid_from, valid_to)
VALUES (0.4120, 'combined_margin',
        'IFI Default Grid Factor 2025 — Armenia', '2025', DATE '2025-01-01', NULL);

-- A price as of a date.
INSERT INTO gold.price (instrument, value, currency, source, as_of)
VALUES ('irec', 2.10, 'USD', 'Broker quote', DATE '2025-04-01');
```

Every Gold view joins the factor on date, so historical months keep the factor
that was valid then and the new one applies going forward. Reload the page and
the numbers move — there is nothing to recompute.

**The emission factor is the number most likely to be challenged.** It is still
outstanding, and 0.3550 is a placeholder.

---

## Presentation

### `BANNER_TEXT`
The banner on every screen. Defaults to `Pilot data. Not verified.` Leave it
until the numbers have actually been verified; change it then, not before.

### `FLEET_NAME`
Shown next to the product name in the header.

---

## Minimum sets

**Local development** — none required if Postgres is on localhost with the
default credentials. Otherwise just:
```bash
DATABASE_URL=postgresql://user:pass@host:5432/ccredits
```

**Railway demo, ten minutes before a meeting:**
```
DATABASE_URL=${{Postgres.DATABASE_URL}}
APP_ENV=production
ADMIN_TOKEN=<a random string>
```

**Railway, for anything you will show more than once** — add durable storage:
```
STORAGE_BACKEND=s3
S3_BUCKET=ccredits-pilot
S3_REGION=eu-west-1
S3_ACCESS_KEY_ID=...
S3_SECRET_ACCESS_KEY=...
```

---

## Checking what took effect

```bash
curl https://<your-app>.up.railway.app/api/health
# {"status":"ok","database":true,"env":"production"}
```

`/api/context` returns the banner, the assumptions as displayed, and the current
emission factors and prices — useful for confirming a variable actually landed.
The **Overview** screen shows the same thing without a terminal.

If `/api/health` says `degraded`, the message names the problem; it is almost
always `DATABASE_URL`.
