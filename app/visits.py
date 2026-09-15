"""Recording who opens the portal.

Self-hosted and first-party: the visitor id is a random value this app puts in
its own cookie, and the IP address is hashed with a per-installation salt
rather than stored. Nothing is sent anywhere.
"""
from __future__ import annotations

import hashlib
import logging
import secrets

from app.config import settings
from app.db import connection, query_one

log = logging.getLogger("ccredits.visits")

COOKIE = "ccredits_visitor"
COOKIE_MAX_AGE = 60 * 60 * 24 * 365   # a year


def _salt() -> str:
    """A salt generated once per installation and kept in the database.

    It never leaves this deployment, and it means the stored hashes cannot be
    matched against an IP address by anyone who only has the table.
    """
    row = query_one("SELECT txt FROM silver.parameter WHERE key = 'visitor_salt'")
    if row and row["txt"]:
        return row["txt"]
    value = secrets.token_hex(16)
    with connection() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO silver.parameter (key, txt) VALUES ('visitor_salt', %s) "
            "ON CONFLICT (key) DO NOTHING",
            (value,),
        )
        conn.commit()
    row = query_one("SELECT txt FROM silver.parameter WHERE key = 'visitor_salt'")
    return row["txt"] if row else value


# The country database is bundled with the package and consulted in-process:
# no request leaves this deployment to resolve a location. It costs about 50 MB
# resident, so it is loaded on the first visit rather than at import.
_geo = None
_geo_failed = False


def _country(address: str | None) -> tuple[str | None, str | None]:
    """Resolve an address to a country, then forget the address.

    Country is as far as an offline database can go honestly, and it is the
    level that answers "where did they open it from" without turning a visit
    log into a location history.
    """
    global _geo, _geo_failed
    if not address or _geo_failed:
        return None, None
    if _geo is None:
        try:
            from geoip2fast import GeoIP2Fast
            _geo = GeoIP2Fast(geoip2fast_data_file="geoip2fast-ipv6.dat.gz")
        except Exception:
            _geo_failed = True
            log.warning("no country database available; visits will have no location")
            return None, None
    try:
        found = _geo.lookup(address)
    except Exception:
        return None, None
    code = (found.country_code or "").strip()
    name = (found.country_name or "").strip()
    # Private and loopback ranges resolve to placeholder names; they are not
    # a location and should not be shown as one.
    if not code or code in {"--", "??"}:
        return None, None
    return code, name or code


def hash_ip(address: str | None) -> str | None:
    if not address:
        return None
    return hashlib.sha256((_salt() + address).encode()).hexdigest()[:32]


def record(request, response) -> None:
    """Note one page view. Never lets a tracking failure break the page."""
    if not settings.track_visits:
        return
    try:
        visitor = request.cookies.get(COOKIE)
        if not visitor:
            visitor = secrets.token_urlsafe(16)
            response.set_cookie(
                COOKIE, visitor,
                max_age=COOKIE_MAX_AGE, httponly=True, samesite="lax",
                secure=request.url.scheme == "https",
            )
        # A shared link can carry ?from=<who>, which is what turns "somebody
        # opened it" into "this person opened it".
        tag = request.query_params.get("from") or request.query_params.get("v")
        forwarded = request.headers.get("x-forwarded-for", "")
        address = forwarded.split(",")[0].strip() or (
            request.client.host if request.client else None
        )
        # A CDN in front of the app often knows the country already and is more
        # reliable than a database lookup through a proxy chain.
        header_code = (request.headers.get("cf-ipcountry")
                       or request.headers.get("x-vercel-ip-country")
                       or request.headers.get("x-country-code") or "").strip().upper()
        code, country = _country(address)
        if header_code and header_code not in {"XX", "T1"}:
            code = header_code
            country = country or header_code
        with connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO ops.visit
                    (visitor_id, tag, path, referrer, user_agent, ip_hash,
                     country_code, country)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    visitor,
                    (tag or None) and tag[:80],
                    str(request.url.path)[:200],
                    (request.headers.get("referer") or None) and request.headers["referer"][:300],
                    (request.headers.get("user-agent") or None) and request.headers["user-agent"][:300],
                    hash_ip(address),
                    code,
                    country,
                ),
            )
            conn.commit()
    except Exception:
        log.exception("could not record a visit")
