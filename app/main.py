"""CCredits — Sungrow exports in, auditable credits out.

Bronze / Silver / Gold in one Postgres database, every number traceable to the
spreadsheet cell it came from.
"""
from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api.routes import router
from app.config import settings
from app.db import migrate

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
log = logging.getLogger("ccredits")

STATIC_DIR = Path(__file__).parent / "static"

app = FastAPI(
    title="CCredits",
    description="Sungrow exports to auditable renewable energy credits.",
    version="0.1.0",
)
app.include_router(router)


@app.on_event("startup")
def startup() -> None:
    if settings.auto_migrate:
        try:
            migrate()
            log.info("schema applied")
        except Exception:
            # A database that is not up yet should not stop the process: the
            # health endpoint reports it and Railway restarts on the next deploy.
            log.exception("migration failed at startup")
    else:
        log.info("AUTO_MIGRATE is off; skipping schema application")


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


def run() -> None:
    import uvicorn

    uvicorn.run("app.main:app", host="0.0.0.0", port=settings.port, log_level=settings.log_level)


if __name__ == "__main__":
    run()
