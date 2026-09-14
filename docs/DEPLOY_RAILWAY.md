# Deploying to Railway

One `web` service, one Postgres, no worker and no scheduler — nothing in this
app runs on a timer.

## 1. Create the project

1. Railway → **New Project** → **Deploy from GitHub repo** → pick this repo.
2. Set the region to **EU West** (Service → Settings → Region) if the data
   should stay in Europe.
3. Railway detects `railway.json` and builds with the `Dockerfile`. No build
   command to configure.

## 2. Add Postgres

**New** → **Database** → **Add PostgreSQL**, in the same project.

## 3. Set the variables

On the **web** service, Variables:

```
DATABASE_URL=${{Postgres.DATABASE_URL}}
APP_ENV=production
ADMIN_TOKEN=<a random string>
```

Use the `${{Postgres.DATABASE_URL}}` reference rather than pasting the literal
URL, so it survives a credential rotation.

**Do not set `PORT`** — Railway injects it and the container binds what it is
given.

For anything you will show more than once, add object storage as well, because
Railway's filesystem is ephemeral and uploaded originals would not survive a
redeploy:

```
STORAGE_BACKEND=s3
S3_BUCKET=ccredits-pilot
S3_REGION=eu-west-1
S3_ACCESS_KEY_ID=...
S3_SECRET_ACCESS_KEY=...
```

Full list: [ENVIRONMENT.md](ENVIRONMENT.md).

## 4. Deploy and check

Railway builds and starts the service. The schema is applied on startup
(`AUTO_MIGRATE=true`), so there is no migration step to run by hand.

```bash
curl https://<your-app>.up.railway.app/api/health
# {"status":"ok","database":true,"env":"production"}
```

`railway.json` points the healthcheck at `/api/health` with a 120s timeout,
which is enough for the first boot to create the schema.

If it reports `degraded`, the message names the cause — almost always
`DATABASE_URL`.

## 5. Load data

Open the URL and use the **Upload** screen. Or, from a local checkout pointed at
the Railway database:

```bash
export DATABASE_URL="<the Railway Postgres public URL>"
python cli/load_bronze.py --migrate path/to/*.xlsx
```

## Backups

Nothing here schedules one. A nightly `pg_dump` to the same bucket is the
intended next step:

```bash
pg_dump "$DATABASE_URL" | gzip > ccredits-$(date +%F).sql.gz
```

Bronze is the only thing that matters — Silver and Gold are views and rebuild
themselves from it.

## What is deliberately not deployed

No worker, no scheduler, no cron. Everything recomputes on read, so there is
nothing to keep warm.
