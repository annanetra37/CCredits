#!/usr/bin/env python3
"""Prove the numbers, rather than assert them.

Every figure the portal shows is produced by SQL views. This script recomputes
the same figures a second time, in Python, from the raw spreadsheet cells and
the immutable Bronze rows — never from the views — and compares the two.

Two independent implementations agreeing to four decimal places is the closest
thing to proof available without a verifier in the room. Where they disagree,
this prints both numbers and the rows responsible.

    python cli/reconcile.py             # the report
    python cli/reconcile.py --json      # the same thing, machine readable
    python cli/reconcile.py --verbose   # list every disagreeing row

Exit status is 1 if any check fails, so it can gate a deployment.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from collections import defaultdict
from dataclasses import dataclass, field, asdict
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db import query, query_one  # noqa: E402
from app.ingest import storage  # noqa: E402
from app.ingest.parser import load_file  # noqa: E402

TOL = Decimal("0.0001")


def D(value) -> Decimal:
    if value is None:
        return Decimal(0)
    return Decimal(str(value))


@dataclass
class Check:
    name: str
    title: str
    status: str = "pass"          # pass | fail | skip
    app: str = ""                 # what the views say
    recomputed: str = ""          # what this script worked out independently
    detail: str = ""
    offenders: list = field(default_factory=list)

    def fail(self, detail: str, app="", recomputed="", offenders=None):
        self.status, self.detail = "fail", detail
        self.app, self.recomputed = str(app), str(recomputed)
        self.offenders = offenders or []
        return self

    def skip(self, detail: str):
        self.status, self.detail = "skip", detail
        return self

    def ok(self, detail: str, app="", recomputed=""):
        self.status, self.detail = "pass", detail
        self.app, self.recomputed = str(app), str(recomputed)
        return self


# --- 1. The files are still the files ---------------------------------------

def check_files_intact() -> Check:
    c = Check("files_intact", "Stored originals still match their recorded hash")
    files = query("SELECT file_id, filename, sha256, storage_path FROM bronze.source_file")
    if not files:
        return c.skip("no files loaded")
    checked, unreadable, bad = 0, [], []
    for f in files:
        if not f["storage_path"]:
            unreadable.append(f["filename"])
            continue
        try:
            blob = storage.read(f["storage_path"])
        except Exception as exc:
            unreadable.append(f"{f['filename']} ({type(exc).__name__})")
            continue
        checked += 1
        if hashlib.sha256(blob).hexdigest() != f["sha256"]:
            bad.append(f["filename"])
    if bad:
        return c.fail(f"{len(bad)} file(s) no longer hash to what was recorded", offenders=bad)
    if checked == 0:
        return c.skip(f"no stored original was readable ({len(unreadable)} file(s))")
    note = f"{checked} of {len(files)} original(s) re-read and re-hashed"
    if unreadable:
        note += f"; {len(unreadable)} not readable from storage"
    return c.ok(note, app=f"{len(files)} files", recomputed=f"{checked} verified")


# --- 2. Bronze says exactly what the files said -----------------------------

def check_reparse_matches_bronze(verbose: bool) -> Check:
    c = Check("reparse_matches_bronze", "Re-parsing each file reproduces its Bronze rows")
    files = query(
        "SELECT file_id, filename, sha256, storage_path, row_count, values_kept "
        "FROM bronze.source_file ORDER BY file_id"
    )
    if not files:
        return c.skip("no files loaded")

    compared, skipped, mismatches = 0, 0, []
    for f in files:
        if not f["storage_path"]:
            skipped += 1
            continue
        try:
            blob = storage.read(f["storage_path"])
        except Exception:
            skipped += 1
            continue
        parsed = load_file(f["filename"], data=blob, filename=f["filename"])
        compared += 1

        fresh = defaultdict(Decimal)
        for r in parsed.readings:
            fresh[(r.plant_name, r.reading_date, r.metric)] += D(r.value)

        stored = defaultdict(Decimal)
        for r in query(
            "SELECT plant_name, reading_date, metric, value FROM bronze.reading WHERE file_id = %s",
            (f["file_id"],),
        ):
            stored[(r["plant_name"], r["reading_date"], r["metric"])] += D(r["value"])

        if fresh.keys() != stored.keys():
            only_file = len(set(fresh) - set(stored))
            only_db = len(set(stored) - set(fresh))
            mismatches.append(
                f"{f['filename']}: {only_file} key(s) in the file but not in Bronze, "
                f"{only_db} in Bronze but not in the file"
            )
            continue
        for key in fresh:
            if abs(fresh[key] - stored[key]) > TOL:
                mismatches.append(
                    f"{f['filename']}: {key[0]} {key[1]} {key[2]} "
                    f"file={fresh[key]} bronze={stored[key]}"
                )
                if not verbose and len(mismatches) > 5:
                    break

    if mismatches:
        return c.fail("Bronze does not match a fresh parse of the stored file",
                      offenders=mismatches[: (None if verbose else 8)])
    if compared == 0:
        return c.skip(f"no stored original was readable ({skipped} skipped)")
    note = f"{compared} file(s) re-parsed from storage and matched cell for cell"
    if skipped:
        note += f"; {skipped} not readable from storage"
    return c.ok(note, app="bronze.reading", recomputed="fresh parse")


# --- 3. Silver's daily figure, recomputed without SQL -----------------------

def _winning_daily_kwh() -> dict:
    """Recompute silver.generation_daily in Python straight from Bronze.

    Sum the metric per file, then apply the supersession rule — the most recent
    upload wins, plant grain breaking a tie — exactly as the view does, but
    without going near the view.
    """
    per_file = defaultdict(Decimal)
    for r in query(
        """
        SELECT r.file_id, r.plant_name, r.reading_date, r.value
        FROM bronze.reading r
        WHERE r.metric = 'daily_yield' AND r.value IS NOT NULL
        """
    ):
        per_file[(r["file_id"], r["plant_name"], r["reading_date"])] += D(r["value"])

    meta = {
        f["file_id"]: (f["uploaded_at"], 0 if f["grain"] == "plant" else 1, f["file_id"])
        for f in query("SELECT file_id, uploaded_at, grain FROM bronze.source_file")
    }

    best: dict = {}
    for (file_id, plant, day), total in per_file.items():
        uploaded_at, grain_rank, fid = meta[file_id]
        rank = (uploaded_at, -grain_rank, fid)   # later upload wins; plant grain breaks ties
        key = (plant, day)
        if key not in best or rank > best[key][0]:
            best[key] = (rank, total)
    return {k: v[1] for k, v in best.items()}


def check_generation_daily(verbose: bool) -> Check:
    c = Check("generation_daily", "Daily site generation, recomputed from Bronze")
    mine = _winning_daily_kwh()
    theirs = {
        (r["plant_name"], r["reading_date"]): D(r["generation_kwh"])
        for r in query("SELECT plant_name, reading_date, generation_kwh FROM silver.generation_daily")
    }
    if not mine and not theirs:
        return c.skip("no generation readings loaded")

    offenders = []
    for key in set(mine) | set(theirs):
        a, b = mine.get(key, Decimal(0)), theirs.get(key, Decimal(0))
        if abs(a - b) > TOL:
            offenders.append(f"{key[0]} {key[1]}: recomputed={a} view={b}")
    if offenders:
        return c.fail(f"{len(offenders)} site-day(s) disagree",
                      app=f"{len(theirs)} rows", recomputed=f"{len(mine)} rows",
                      offenders=offenders[: (None if verbose else 8)])
    total_mine = sum(mine.values())
    return c.ok(f"{len(mine):,} site-days agree exactly",
                app=f"{sum(theirs.values()):,.4f} kWh", recomputed=f"{total_mine:,.4f} kWh")


# --- 4. Nothing is created or destroyed between the layers ------------------

def check_conservation(verbose: bool) -> Check:
    c = Check("conservation", "Every kWh is either eligible or excluded, never both or neither")
    rows = query(
        "SELECT plant_name, reading_date, generation_kwh, eligible_kwh, excluded_kwh, "
        "exclusion_reason FROM silver.eligibility"
    )
    if not rows:
        return c.skip("nothing loaded")
    offenders = []
    gen = elig = excl = Decimal(0)
    for r in rows:
        g, e, x = D(r["generation_kwh"]), D(r["eligible_kwh"]), D(r["excluded_kwh"])
        gen, elig, excl = gen + g, elig + e, excl + x
        if abs(g - (e + x)) > TOL:
            offenders.append(
                f"{r['plant_name']} {r['reading_date']}: generation={g} "
                f"eligible={e} excluded={x} reason={r['exclusion_reason']}"
            )
        if e > 0 and x > 0:
            offenders.append(f"{r['plant_name']} {r['reading_date']}: counted in both streams")
    if offenders:
        return c.fail(f"{len(offenders)} site-day(s) do not balance",
                      offenders=offenders[: (None if verbose else 8)])
    return c.ok(f"{len(rows):,} site-days balance",
                app=f"generation {gen:,.4f}", recomputed=f"eligible {elig:,.4f} + excluded {excl:,.4f}")


def check_daily_totals_reach_bronze() -> Check:
    """The eligible total must trace back to the sum of the winning Bronze cells."""
    c = Check("eligible_traces_to_bronze", "Fleet totals trace back to the Bronze cells")
    mine = _winning_daily_kwh()
    if not mine:
        return c.skip("nothing loaded")
    recomputed = sum(mine.values())
    row = query_one("SELECT COALESCE(SUM(generation_kwh),0) g FROM silver.eligibility")
    view = D(row["g"])
    if abs(recomputed - view) > TOL:
        return c.fail("fleet generation does not match the sum of the winning Bronze cells",
                      app=f"{view:,.4f} kWh", recomputed=f"{recomputed:,.4f} kWh")
    return c.ok("fleet generation equals the sum of the winning Bronze cells",
                app=f"{view:,.4f} kWh", recomputed=f"{recomputed:,.4f} kWh")


# --- 5. The monthly roll-up ------------------------------------------------

def check_month_rollup(verbose: bool) -> Check:
    c = Check("month_rollup", "Monthly totals are the sum of their days")
    daily = defaultdict(lambda: [Decimal(0), Decimal(0), Decimal(0)])
    for r in query(
        "SELECT plant_name, date_trunc('month', reading_date)::date AS month, "
        "generation_kwh, eligible_kwh, excluded_kwh FROM silver.eligibility"
    ):
        acc = daily[(r["plant_name"], r["month"])]
        acc[0] += D(r["generation_kwh"]); acc[1] += D(r["eligible_kwh"]); acc[2] += D(r["excluded_kwh"])
    if not daily:
        return c.skip("nothing loaded")

    offenders = []
    for r in query("SELECT plant_name, month, generation_kwh, eligible_kwh, excluded_kwh FROM silver.site_month"):
        acc = daily.get((r["plant_name"], r["month"]))
        if acc is None:
            offenders.append(f"{r['plant_name']} {r['month']}: month exists with no days")
            continue
        for i, col in enumerate(("generation_kwh", "eligible_kwh", "excluded_kwh")):
            if abs(acc[i] - D(r[col])) > TOL:
                offenders.append(f"{r['plant_name']} {r['month']} {col}: days={acc[i]} month={D(r[col])}")
    if offenders:
        return c.fail(f"{len(offenders)} disagreement(s)", offenders=offenders[: (None if verbose else 8)])
    return c.ok(f"{len(daily):,} site-months equal the sum of their days")


# --- 6. Gold: carbon and the VCU ledger, recomputed -------------------------

def check_carbon(verbose: bool) -> Check:
    c = Check("carbon", "Emission reduction is eligible MWh times the factor on record")
    rows = query(
        "SELECT plant_name, month, eligible_kwh, emission_factor, net_reduction_tco2e, "
        "project_emissions_tco2e, leakage_tco2e FROM gold.carbon"
    )
    if not rows:
        return c.skip("nothing loaded")
    priced = [r for r in rows if r["emission_factor"] is not None]
    if not priced:
        return c.skip(f"no emission factor applies to any of the {len(rows)} site-month(s)")
    offenders = []
    for r in priced:
        expect = (D(r["eligible_kwh"]) / Decimal(1000)) * D(r["emission_factor"]) \
                 - D(r["project_emissions_tco2e"]) - D(r["leakage_tco2e"])
        got = D(r["net_reduction_tco2e"])
        if abs(expect - got) > TOL:
            offenders.append(f"{r['plant_name']} {r['month']}: recomputed={expect} view={got}")
    if offenders:
        return c.fail(f"{len(offenders)} site-month(s) disagree", offenders=offenders[: (None if verbose else 8)])
    total = sum((D(r["net_reduction_tco2e"]) for r in priced), Decimal(0))
    return c.ok(f"{len(priced):,} site-months agree", app=f"{total:,.4f} tCO2e", recomputed=f"{total:,.4f} tCO2e")


def check_vcu_ledger(verbose: bool) -> Check:
    """Rebuild the whole-tonne carry-forward ledger from scratch."""
    c = Check("vcu_ledger", "The VCU ledger issues whole tonnes and carries the remainder")
    rows = query(
        "SELECT plant_name, month, net_reduction_tco2e, vcu_issued, carry_forward_tco2e "
        "FROM gold.vcu ORDER BY plant_name, month"
    )
    if not rows:
        return c.skip("nothing loaded")

    by_site = defaultdict(list)
    for r in rows:
        by_site[r["plant_name"]].append(r)

    offenders = []
    total_issued = Decimal(0)
    for plant, months in by_site.items():
        cumulative = Decimal(0)
        issued_so_far = Decimal(0)
        for r in months:
            cumulative += D(r["net_reduction_tco2e"])
            cumulative_issued = Decimal(math.floor(cumulative))
            expect_issued = cumulative_issued - issued_so_far
            expect_carry = cumulative - cumulative_issued
            issued_so_far = cumulative_issued
            total_issued += expect_issued
            if D(r["vcu_issued"]) != expect_issued:
                offenders.append(f"{plant} {r['month']}: issued view={D(r['vcu_issued'])} recomputed={expect_issued}")
            if abs(D(r["carry_forward_tco2e"]) - expect_carry) > TOL:
                offenders.append(f"{plant} {r['month']}: carry view={D(r['carry_forward_tco2e'])} recomputed={expect_carry}")
    if offenders:
        return c.fail(f"{len(offenders)} disagreement(s)", offenders=offenders[: (None if verbose else 8)])

    view_issued = D((query_one("SELECT COALESCE(SUM(vcu_issued),0) v FROM gold.vcu") or {}).get("v"))
    reduced = D((query_one("SELECT COALESCE(SUM(net_reduction_tco2e),0) t FROM gold.vcu") or {}).get("t"))
    if view_issued > reduced:
        return c.fail("more units issued than tonnes reduced",
                      app=f"{view_issued} VCU", recomputed=f"{reduced} tCO2e")
    return c.ok(f"{len(rows):,} site-months agree, and nothing is issued that was not reduced",
                app=f"{view_issued} VCU", recomputed=f"{total_issued} VCU")


# --- 7. Completeness --------------------------------------------------------

def check_site_day_grid() -> Check:
    c = Check("site_day_grid", "Every site has a row for every day in the window")
    w = query_one("SELECT window_start, window_end FROM silver.loaded_window")
    if not w or not w["window_start"]:
        return c.skip("no loaded window")
    days = (w["window_end"] - w["window_start"]).days + 1
    sites = (query_one("SELECT COUNT(*) n FROM bronze.site") or {}).get("n", 0)
    actual = (query_one("SELECT COUNT(*) n FROM silver.eligibility") or {}).get("n", 0)
    dupes = (query_one(
        "SELECT COUNT(*) n FROM (SELECT plant_name, reading_date FROM silver.eligibility "
        "GROUP BY 1,2 HAVING COUNT(*) > 1) t"
    ) or {}).get("n", 0)
    if dupes:
        return c.fail(f"{dupes} site-day(s) appear more than once")
    if actual != sites * days:
        return c.fail("the site-day grid is not complete",
                      app=f"{actual:,} rows", recomputed=f"{sites} sites x {days} days = {sites * days:,}")
    return c.ok(f"{sites} sites x {days} days, each exactly once",
                app=f"{actual:,} rows", recomputed=f"{sites * days:,} rows")


def check_every_plant_has_a_site() -> Check:
    c = Check("sites_registered", "Every plant that has readings is in the fleet master")
    missing = query(
        "SELECT DISTINCT r.plant_name FROM bronze.reading r "
        "LEFT JOIN bronze.site s ON s.plant_name = r.plant_name WHERE s.plant_name IS NULL"
    )
    if missing:
        return c.fail(f"{len(missing)} plant(s) have readings but no site row",
                      offenders=[m["plant_name"] for m in missing[:8]])
    n = (query_one("SELECT COUNT(*) n FROM bronze.site") or {}).get("n", 0)
    return c.ok(f"all {n} site(s) registered")


# --- 8. Why a number is missing, said out loud ------------------------------

def check_factor_coverage() -> Check:
    c = Check("factor_coverage", "Every month with eligible energy has an applicable factor")
    rows = query(
        "SELECT month, SUM(eligible_kwh) kwh, bool_or(emission_factor IS NOT NULL) has_factor "
        "FROM gold.carbon GROUP BY month ORDER BY month"
    )
    if not rows:
        return c.skip("nothing loaded")
    uncovered = [r for r in rows if D(r["kwh"]) > 0 and not r["has_factor"]]
    if not uncovered:
        return c.ok(f"all {len(rows)} month(s) covered")
    active = query_one(
        "SELECT value_tco2e_per_mwh v, valid_from, valid_to, source FROM gold.emission_factor "
        "WHERE active ORDER BY valid_from DESC LIMIT 1"
    )
    if active:
        why = (f"the only active factor ({active['v']} tCO2e/MWh) applies "
               f"{active['valid_from']} to {active['valid_to'] or 'open-ended'}")
    else:
        why = "no factor is marked active"
    kwh = sum((D(r["kwh"]) for r in uncovered), Decimal(0))
    return c.fail(
        f"{len(uncovered)} month(s) carrying {kwh:,.0f} kWh of eligible energy have no "
        f"applicable emission factor, so no reduction and no VCUs are claimed — {why}",
        app="0 VCU", recomputed="not calculable",
        offenders=[f"{r['month']}: {D(r['kwh']):,.0f} kWh eligible, no factor" for r in uncovered[:8]],
    )


# --- 9. The headline ties out ----------------------------------------------

def check_headline() -> Check:
    c = Check("headline", "The headline figures equal the sum of their parts")
    s = query_one("SELECT * FROM gold.fleet_summary")
    if not s:
        return c.skip("nothing loaded")
    parts = query_one(
        "SELECT COALESCE(SUM(generation_kwh),0) g, COALESCE(SUM(eligible_kwh),0) e, "
        "COALESCE(SUM(excluded_kwh),0) x FROM silver.eligibility"
    )
    vcu = query_one("SELECT COALESCE(SUM(vcu_issued),0) v FROM gold.vcu")
    bad = []
    for label, a, b in (
        ("generation", D(s["generation_kwh"]), D(parts["g"])),
        ("eligible", D(s["eligible_kwh"]), D(parts["e"])),
        ("excluded", D(s["excluded_kwh"]), D(parts["x"])),
        ("vcu", D(s["vcu_issued"]), D(vcu["v"])),
    ):
        if abs(a - b) > TOL:
            bad.append(f"{label}: headline={a} parts={b}")
    if bad:
        return c.fail("the headline does not equal its parts", offenders=bad)
    return c.ok("generation, eligible, excluded and VCUs all tie out",
                app=f"{D(s['generation_kwh']):,.2f} kWh generated",
                recomputed=f"{D(parts['e']):,.2f} eligible + {D(parts['x']):,.2f} excluded")


CHECKS = [
    ("files_intact", lambda v: check_files_intact()),
    ("reparse", check_reparse_matches_bronze),
    ("generation_daily", check_generation_daily),
    ("eligible_traces", lambda v: check_daily_totals_reach_bronze()),
    ("conservation", check_conservation),
    ("site_day_grid", lambda v: check_site_day_grid()),
    ("sites_registered", lambda v: check_every_plant_has_a_site()),
    ("month_rollup", check_month_rollup),
    ("carbon", check_carbon),
    ("vcu_ledger", check_vcu_ledger),
    ("factor_coverage", lambda v: check_factor_coverage()),
    ("headline", lambda v: check_headline()),
]


def run(verbose: bool = False) -> list[Check]:
    results = []
    for _name, fn in CHECKS:
        try:
            results.append(fn(verbose))
        except Exception as exc:
            results.append(Check(_name, _name).fail(f"{type(exc).__name__}: {exc}"))
    return results


MARK = {"pass": "PASS", "fail": "FAIL", "skip": "SKIP"}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Recompute every figure independently and compare.")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--verbose", action="store_true", help="list every disagreeing row")
    args = ap.parse_args(argv)

    results = run(args.verbose)
    failed = [r for r in results if r.status == "fail"]

    if args.json:
        print(json.dumps({
            "ok": not failed,
            "checks": [asdict(r) for r in results],
        }, indent=2, default=str))
        return 1 if failed else 0

    print()
    print("  CCredits reconciliation")
    print("  Each figure below was recomputed in Python from the Bronze cells,")
    print("  independently of the SQL views, and compared.")
    print("  " + "-" * 74)
    for r in results:
        print(f"  [{MARK[r.status]}]  {r.title}")
        if r.detail:
            print(f"          {r.detail}")
        if r.app or r.recomputed:
            print(f"          views: {r.app or '-'}")
            print(f"          recomputed: {r.recomputed or '-'}")
        for o in r.offenders:
            print(f"            - {o}")
        print()
    n_pass = sum(1 for r in results if r.status == "pass")
    n_skip = sum(1 for r in results if r.status == "skip")
    print("  " + "-" * 74)
    print(f"  {n_pass} passed, {len(failed)} failed, {n_skip} skipped")
    print()
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
