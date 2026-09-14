# CCredits

Sungrow exports in, auditable renewable energy credits out.

Someone drags in the exports; the app parses them, computes credits, and lets
them click any credit number all the way down to the spreadsheet cell it came
from.

```
xlsx  →  bronze  →  silver  →  gold  →  screen
         (rows)    (rules)   (credits)   (and back down to the cell)
```

- **Bronze** — what the files said. Immutable, long format, one row per cell.
- **Silver** — the rules, as readable SQL views. Quality flags, eligibility.
- **Gold** — emission reductions, VCUs with carry-forward, revenue, and lineage.

Silver and Gold store nothing. A new upload or a changed emission factor
recomputes everything on the next page load.

---

## Run it locally

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

createdb ccredits
export DATABASE_URL=postgresql://postgres:postgres@127.0.0.1:5432/ccredits

# No real exports to hand? Generate stand-ins with the same awkward shapes.
python scripts/make_sample_data.py sample_data
python cli/load_bronze.py --migrate sample_data/*.xlsx

uvicorn app.main:app --reload
```

Open <http://localhost:8000>.

## Deploy it

One container, one Postgres, one storage account, on **Azure** —
see [docs/DEPLOY_AZURE.md](docs/DEPLOY_AZURE.md).
A Railway path is kept in [docs/DEPLOY_RAILWAY.md](docs/DEPLOY_RAILWAY.md).

## Documentation

| | |
|---|---|
| [Portal guide](docs/PORTAL_GUIDE.md) | How to use the four screens, and a demo path that works |
| [Environment variables](docs/ENVIRONMENT.md) | Every setting, what it does, and which ones change the headline number |
| [Azure deployment](docs/DEPLOY_AZURE.md) | Container Apps, Postgres Flexible Server, Blob Storage |
| [Railway deployment](docs/DEPLOY_RAILWAY.md) | The alternative host |

## Loading from the terminal

The CLI imports the same `load_file()` the upload endpoint uses — one parser,
never two.

```bash
python cli/load_bronze.py sample_data/*.xlsx     # load
python cli/load_bronze.py --json export.xlsx     # raw parse summary
python cli/load_bronze.py --reset                # empty Bronze, keep reference data
```

## Tests

```bash
pytest                       # parser tests need no database
pytest tests/test_pipeline.py  # integration tests, skipped if no DATABASE_URL
```

The pipeline tests check the numbers rather than the plumbing: that the river
balances, that a gap is never interpolated, that a suspect day is excluded with
a reason, and that the I-REC remainder is carried forward rather than rounded
away.

## Layout

```
app/
  config.py          every tunable, read from the environment
  db.py              pool, migrations, reference-data bootstrap
  main.py            FastAPI app
  api/routes.py      HTTP endpoints — thin wrappers over the views
  ingest/parser.py   the one parser: sniffs layout, melts to long format
  ingest/loader.py   duplicate detection, overlap flagging, site upsert
  ingest/storage.py  originals to local disk or object storage
  sql/               the schemas and every rule, as SQL
  static/            the portal
cli/load_bronze.py   terminal loader
scripts/             sample data generator
tests/               parser and pipeline tests
```

## Scope

Single tenant, no login, Sungrow only, generation-based, VCUs only. No meter or storage
adapters, no evidence packs, no verifier sandbox. All of that stays in the
target architecture; none of it is needed to demonstrate the idea.

The banner says **"Pilot data. Not verified."** on every screen. It should stay
there until someone has verified the numbers.

## Open decisions

Five assumptions change the headline number and are displayed on the Overview
screen rather than buried — the implausibility threshold, zero-generation days,
missing days, the grid connection date, and the emission factor. Each is an
environment variable. See [docs/ENVIRONMENT.md](docs/ENVIRONMENT.md#the-stated-assumptions).
