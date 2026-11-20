"""Load DoD contract data from USAspending.gov into PostgreSQL.

    python -m app.loader                 # one update (full or incremental, decided automatically)
    python -m app.loader --step details  # only read award pages that are still due

An update runs these steps; a step that fails is logged and the others still run:
    awards     Hampton Roads contracts above the size floor (all of them weekly, changes daily)
    details    each upcoming contract's own page: options, competition, set-aside, parent company
    totals     monthly and yearly totals, and breakdowns by branch, industry, city and company
    kits       the DLA kit program, nationwide, and the suppliers behind it
    watchlist  every DoD contract for the companies on the watchlist
"""
import argparse
import json
import logging
import sys
import time
from datetime import date, timedelta

from . import config, db
from . import usaspending as api

log = logging.getLogger("loader")

STEPS = ["awards", "details", "totals", "kits", "watchlist"]

SEARCH_COLS = ["generated_id", "award_id", "recipient_name", "recipient_uei", "recipient_hash", "description",
               "amount", "outlays", "start_date", "end_date", "last_modified", "awarding_subagency",
               "funding_subagency", "award_type", "naics_code", "naics_desc", "psc_code", "psc_desc",
               "county_code", "city"]
DETAIL_COLS = ["potential_end_date", "end_date", "base_and_all_options", "awarding_office", "competed_code",
               "competed", "offers", "set_aside_code", "set_aside", "pricing", "solicitation", "parent_name",
               "parent_hash", "small_business", "subaward_count"]


def award_upsert(flag):
    """flag: in_region | in_kits | None. A flag is only ever switched on here."""
    cols = SEARCH_COLS + ([flag] if flag else []) + (["region_seen"] if flag == "in_region" else [])
    updates = [f"{c} = EXCLUDED.{c}" for c in SEARCH_COLS[1:]]
    if flag:
        updates.append(f"{flag} = true")
    if flag == "in_region":
        updates.append("region_seen = now()")
    template = "(" + ", ".join(
        [f"%({c})s" for c in SEARCH_COLS] + (["true"] if flag else []) + (["now()"] if flag == "in_region" else [])
    ) + ")"
    sql = (f"INSERT INTO awards ({', '.join(cols)}) VALUES %s ON CONFLICT (generated_id) DO UPDATE SET "
           + ", ".join(updates) + ", last_seen = now()")
    return sql, template


def save_awards(rows, flag=None):
    if not rows:
        return 0
    rows = list({r["generated_id"]: r for r in rows}.values())     # a page can repeat an award
    sql, template = award_upsert(flag)
    return db.bulk(sql, rows, template=template)


def settings():
    return {r["key"]: r["value"] for r in db.query("SELECT key, value FROM settings")}


def counties():
    return [r["county_code"] for r in db.query("SELECT county_code FROM localities WHERE active ORDER BY name")]


def fy_start(fy):
    return date(fy - 1, 10, 1)


def fy_end(fy):
    return date(fy, 9, 30)


def choose_mode(s):
    """full if there's never been a complete download or the last one is older than full_refresh_days."""
    last = db.one("""SELECT max(started_at) AS t FROM fetch_runs
                     WHERE mode = 'full' AND status IN ('ok', 'partial')""")["t"]
    if not last:
        return "full"
    if isinstance(last, str):
        last = date.fromisoformat(last[:10])
    else:
        last = last.date()
    return "full" if (date.today() - last).days >= int(s["full_refresh_days"]) else "incremental"


def progress(label, done, total, started):
    """'awards: 2,300 of 9,120 (about 4 min left)'"""
    if total:
        rate = (time.time() - started) / max(done, 1)
        left = max(total - done, 0) * rate / 60
        log.info("%s: %s of %s (about %d min left)", label, f"{done:,}", f"{total:,}", round(left))
    else:
        log.info("%s: %s so far", label, f"{done:,}")


# ------------------------------------------------------------------ steps
def step_awards(s, today, ctx):
    floor = float(s["award_floor"])
    codes = counties()
    mode = ctx["mode"]
    seen, started = set(), time.time()

    def take(page):
        new = [r for r in page if r["generated_id"] not in seen]
        seen.update(r["generated_id"] for r in new)
        return save_awards(new, "in_region")

    if mode == "full":
        first_fy = api.fiscal_year(today) - int(s["history_years"]) + 1
        total = api.award_count(api.contract_filters(fy_start(first_fy), today, counties=codes, min_amount=floor))
        log.info("awards: %s Hampton Roads contracts of $%s or more since fiscal %d", f"{total:,}",
                 f"{floor:,.0f}", first_fy)
        complete = True
        # one fiscal year at a time keeps every query small; an award active in several years
        # comes back more than once and is saved once
        for fy in range(first_fy, api.fiscal_year(today) + 1):
            f = api.contract_filters(fy_start(fy), min(fy_end(fy), today), counties=codes, min_amount=floor)
            pages = 0
            for page in api.iter_awards(f, max_pages=int(s.get("max_award_pages", 400))):
                take(page)
                pages += 1
                if pages % 10 == 0:
                    progress("awards", len(seen), total, started)
            if pages >= int(s.get("max_award_pages", 400)):
                complete = False
                log.warning("awards: fiscal %d hit the page limit; some contracts were skipped", fy)
        progress("awards", len(seen), total, started)
        if complete and seen:
            # gone from the results (cancelled, or now recorded elsewhere): leave them out of the region
            n = db.execute("UPDATE awards SET in_region = false WHERE in_region AND region_seen < %s",
                           (ctx["started_at"],))
            log.info("awards: %d no longer in Hampton Roads results", n)
    else:
        last = db.one("""SELECT max(started_at)::date AS d FROM fetch_runs
                         WHERE mode IN ('full', 'incremental') AND status IN ('ok', 'partial')""")["d"]
        if isinstance(last, str):
            last = date.fromisoformat(last[:10])
        since = (last or today) - timedelta(days=2)        # overlap so nothing falls between runs
        f = api.contract_filters(since, today, counties=codes, min_amount=floor, date_type="last_modified_date")
        for page in api.iter_awards(f, max_pages=100):
            take(page)
        log.info("awards: %d changed since %s", len(seen), since)
    return len(seen)


def detail_candidates(s, limit):
    """Award pages to read: upcoming contracts, the 50 largest, and one per sizable company (for its
    parent company) that were never read, or that changed since they were read. Biggest first, so the
    dashboard fills in the important ones first."""
    return [r["generated_id"] for r in db.query("""
        WITH top AS (SELECT generated_id FROM awards WHERE in_region ORDER BY amount DESC NULLS LAST LIMIT 50),
        -- one award per company that has none read yet: its page names the parent company,
        -- so all of that company's contracts roll up correctly
        unknown_parent AS (
            SELECT DISTINCT ON (recipient_hash) generated_id FROM awards a
            WHERE in_region AND recipient_hash IS NOT NULL
              AND recipient_hash IN (SELECT recipient_hash FROM awards WHERE in_region
                                     GROUP BY recipient_hash HAVING sum(amount) >= 25e6)
              AND NOT EXISTS (SELECT 1 FROM awards b WHERE b.recipient_hash = a.recipient_hash AND b.detail_at IS NOT NULL)
            ORDER BY recipient_hash, amount DESC NULLS LAST)
        SELECT generated_id FROM awards
        WHERE in_region
          AND (detail_at IS NULL OR last_modified > detail_at::date)
          AND ((end_date >= current_date - 30
                AND end_date < current_date + make_interval(months => %s))
               OR generated_id IN (SELECT generated_id FROM top)
               OR generated_id IN (SELECT generated_id FROM unknown_parent))
        ORDER BY amount DESC NULLS LAST
        LIMIT %s""", (int(s["runway_months"]), limit))]


DETAIL_UPDATE = ("UPDATE awards SET " + ", ".join(
    f"{c} = coalesce(%({c})s, {c})" if c == "end_date" else f"{c} = %({c})s" for c in DETAIL_COLS)
    + ", detail_at = now() WHERE generated_id = %(gid)s")


def step_details(s, today, ctx):
    todo = detail_candidates(s, int(s["detail_max_per_run"]))
    if not todo:
        return 0
    log.info("details: reading %d award pages (biggest first)", len(todo))
    started, done, missing, competed_seen = time.time(), 0, 0, 0
    for gid in todo:
        try:
            d = api.award_detail(gid, retries=2)        # a quick retry; the next update picks up the rest
        except api.Unreachable as exc:
            # every page read so far is saved; stop here instead of hammering a busy server
            note = f"details: USAspending stopped answering after {done} pages ({exc}); the rest are read in the next update"
            log.warning(note)
            ctx.setdefault("warnings", []).append(note)
            if done == 0:
                raise
            break
        except api.ApiError as exc:
            if "HTTP 404" not in str(exc):
                raise
            missing += 1
            db.execute("UPDATE awards SET detail_at = now() WHERE generated_id = %s", (gid,))
            continue
        d["gid"] = gid
        db.execute(DETAIL_UPDATE, d)
        done += 1
        if done % 250 == 0:
            time.sleep(20)          # a short break every 250 pages keeps USAspending from cutting us off
        competed_seen += d["competed"] is not None
        if done % 100 == 0:
            progress("details", done, len(todo), started)
    log.info("details: %d read, %d no longer on USAspending", done, missing)
    check = detail_check(done, competed_seen)
    if check:
        log.warning(check)
        ctx.setdefault("warnings", []).append(check)
    return done


def detail_check(read, with_competition):
    """APIs can drop a field without any error, so check results instead of trusting the request:
    nearly every contract page states how it was competed."""
    if read >= 50 and with_competition < 0.5 * read:
        return (f"data check: only {with_competition} of {read} award pages said how the contract was "
                "competed; USAspending may have renamed latest_transaction_contract_data fields")
    return None


MONTH_UPSERT = """INSERT INTO monthly_totals VALUES (%s, %s, %s)
                  ON CONFLICT (series, month) DO UPDATE SET amount = EXCLUDED.amount"""


def step_totals(s, today, ctx):
    codes = counties()
    first_fy = api.fiscal_year(today) - int(s["history_years"]) + 1
    stmts = [("DELETE FROM monthly_totals WHERE series = 'region'", None),
             ("DELETE FROM fy_totals WHERE series IN ('region', 'small_business')", None)]
    # one fiscal year per request: small queries are far less likely to time out than a five-year one
    for fy in range(first_fy, api.fiscal_year(today) + 1):
        region = api.contract_filters(fy_start(fy), min(fy_end(fy), today), counties=codes)
        months = [(m, a) for m, a in api.spending_over_time(region, "month") if fy_start(fy) <= m <= today and m <= fy_end(fy)]
        stmts += [(MONTH_UPSERT, ("region", m, a)) for m, a in months]
        stmts.append(("INSERT INTO fy_totals VALUES ('region', %s, %s)", (fy, sum(a for _, a in months))))
        small = dict(region, recipient_type_names=["small_business"])
        stmts.append(("INSERT INTO fy_totals VALUES ('small_business', %s, %s)",
                      (fy, sum(a for k, a in api.spending_over_time(small, "fiscal_year") if k == fy))))

    # breakdowns for the last complete fiscal year and the one before (for comparison)
    full_fy = db.one("SELECT last_full_fy() AS y")["y"]
    for fy in (full_fy - 1, full_fy):
        f = api.contract_filters(fy_start(fy), fy_end(fy), counties=codes)
        stmts.append(("DELETE FROM category_totals WHERE fiscal_year = %s", (fy,)))
        for cat, api_cat, flt, limit in (
                ("branch", "awarding_subagency", f, 15), ("naics", "naics", f, 25), ("psc", "psc", f, 25),
                ("county", "county", f, 20), ("recipient", "recipient", f, 25),
                ("sb_recipient", "recipient", dict(f, recipient_type_names=["small_business"]), 25)):
            for r in api.spending_by_category(api_cat, flt, limit):
                stmts.append(("""INSERT INTO category_totals VALUES (%s, %s, %s, %s, %s)
                                 ON CONFLICT (category, fiscal_year, name) DO UPDATE SET amount = EXCLUDED.amount""",
                              (cat, fy, r["code"] or "", r["name"], r["amount"])))
    return db.run_many(stmts)


def step_kits(s, today, ctx):
    """The DLA special operational equipment ("kit") program, nationwide.

    The program is a set of master contracts (SPE8EJ-21-D-...) that thousands of small orders are
    placed under. Order descriptions name the item ("GPS receiver"), not the program, so the program
    is found by its master contracts, and each one's totals come from its own page."""
    keywords = json.loads(s["kits_keywords"])
    prefix = s.get("kits_idv_prefix", "SPE8EJ")
    first_fy = api.fiscal_year(today) - int(s["history_years"]) + 1
    f = api.contract_filters(fy_start(first_fy), today, agency=api.subtier(s["kits_subagency"]), keywords=keywords)
    idvs = [i for i in api.idvs(f) if (i["piid"] or "").startswith(prefix)]
    # the description search misses some master contracts; orders already loaded name theirs
    known = {i["gid"] for i in idvs}
    seen = {api.parent_idv(r["generated_id"], prefix) for r in db.query(
        "SELECT generated_id FROM awards WHERE generated_id LIKE %s", (f"%\\_{prefix}%",))}
    for gid in sorted(g for g in seen if g and g not in known):
        try:
            idvs.append(api.idv_page(gid))
        except api.ApiError as exc:
            log.warning("kits: couldn't read master contract %s (%s)", gid, exc)
    if not idvs:
        raise RuntimeError(f"no master contracts found for keywords {keywords} and prefix {prefix}")
    stmts = [("DELETE FROM kit_idvs", None), ("DELETE FROM kit_orders", None)]
    orders = []
    for i in idvs:
        i.update(api.idv_amounts(i["gid"]))
        stmts.append(("""INSERT INTO kit_idvs VALUES (%(gid)s, %(piid)s, %(holder)s, %(description)s, %(orders)s,
                                                      %(obligated)s, %(order_end)s, now())""", i))
        for o in api.idv_children(i["gid"], limit=int(s.get("kits_orders_per_contract", 25))):
            o.update(idv_gid=i["gid"], holder=i["holder"])
            orders.append(o)
            stmts.append(("""INSERT INTO kit_orders VALUES (%(gid)s, %(idv_gid)s, %(piid)s, %(holder)s, %(description)s,
                                                           %(amount)s, %(start_date)s, %(end_date)s)
                             ON CONFLICT (gid) DO NOTHING""", o))
        log.info("kits: %s (%s): %s orders", i["piid"], i["holder"], f"{i['orders']:,}")
    n = db.run_many(stmts)
    # suppliers are reported against each order, so read them for the largest orders
    subs = []
    for o in sorted(orders, key=lambda o: -(o["amount"] or 0))[:int(s.get("kits_supplier_orders", 30))]:
        for r in api.award_subawards(o["gid"]):
            r.update(prime_award_id=o["piid"], prime_recipient=o["holder"])
            subs.append(r)
    db.run_many([("DELETE FROM subawards", None)] + [("""
        INSERT INTO subawards VALUES (%(subaward_id)s, %(prime_award_id)s, %(prime_recipient)s, %(sub_name)s,
                                      %(amount)s, %(sub_date)s, %(description)s)
        ON CONFLICT DO NOTHING""", r) for r in subs])
    log.info("kits: %d master contracts, %d largest orders, %d supplier records", len(idvs), len(orders), len(subs))
    return n + len(subs)


def step_watchlist(s, today, ctx):
    first_fy = api.fiscal_year(today) - int(s["history_years"]) + 1
    n = 0
    for c in db.query("SELECT id, display_name, search_text FROM companies ORDER BY id"):
        f = api.contract_filters(fy_start(first_fy), today, recipient=c["search_text"])
        count = api.award_count(f)
        rows = api.awards(f, max_rows=50) if count else []
        save_awards(rows)
        db.run_many([("DELETE FROM company_awards WHERE company_id = %s", (c["id"],))]
                    + [("INSERT INTO company_awards VALUES (%s, %s) ON CONFLICT DO NOTHING", (c["id"], r["generated_id"]))
                       for r in rows]
                    + [("UPDATE companies SET checked_at = now(), award_count = %s, total_amount = %s WHERE id = %s",
                        (count, sum(r["amount"] or 0 for r in rows), c["id"]))])
        log.info("watchlist: %s: %d DoD contracts", c["display_name"], count)
        n += len(rows)
    return n


STEP_FUNCS = {"awards": step_awards, "details": step_details, "totals": step_totals,
              "kits": step_kits, "watchlist": step_watchlist}


def details_pending(s=None):
    s = s or settings()
    return len(detail_candidates(s, 100000))


def run(steps=None, source="manual", today=None):
    today = today or date.today()
    steps = steps or STEPS
    s = settings()
    mode = choose_mode(s) if "awards" in steps else ("details" if steps == ["details"] else "partial")
    run = db.one("INSERT INTO fetch_runs (source, mode) VALUES (%s, %s) RETURNING id, started_at", (source, mode))
    ctx = {"run_id": run["id"], "started_at": run["started_at"], "mode": mode}
    log.info("update %d (%s): %s", run["id"], mode, ", ".join(steps))
    written, failures = 0, []
    for name in steps:
        log.info("step %s ...", name)
        try:
            n = STEP_FUNCS[name](s, today, ctx)
            written += n
            log.info("step %s: %d rows", name, n)
            db.execute("""INSERT INTO step_status (step, last_ok, last_try) VALUES (%s, now(), now())
                          ON CONFLICT (step) DO UPDATE SET last_ok = now(), last_try = now(), last_error = NULL""", (name,))
        except Exception as exc:
            if isinstance(exc, api.Unreachable):
                log.warning("step %s: USAspending isn't answering (%s); it will be retried within the hour", name, exc)
            else:
                log.exception("step %s failed", name)
            failures.append(f"{name}: {exc}")
            db.execute("""INSERT INTO step_status (step, last_try, last_error) VALUES (%s, now(), %s)
                          ON CONFLICT (step) DO UPDATE SET last_try = now(), last_error = EXCLUDED.last_error""",
                       (name, str(exc)[:500]))
    db.execute("DELETE FROM events WHERE created_at < now() - interval '180 days'")
    status = "ok" if not failures else ("failed" if len(failures) == len(steps) else "partial")
    db.execute("UPDATE fetch_runs SET finished_at = now(), status = %s, rows_written = %s, message = %s WHERE id = %s",
               (status, written, "; ".join(failures + ctx.get("warnings", []))[:2000] or None, run["id"]))
    result = {"run_id": run["id"], "mode": mode, "status": status, "rows": written, "failed": len(failures)}
    log.info("done: %s", result)
    return result


def steps_to_retry(wait_minutes=60):
    """Steps whose last attempt failed at least wait_minutes ago (USAspending often recovers in an hour)."""
    return [r["step"] for r in db.query(
        """SELECT step FROM step_status
           WHERE last_error IS NOT NULL AND last_try < now() - make_interval(mins => %s)
           ORDER BY array_position(%s::text[], step)""", (wait_minutes, STEPS))]


def catch_up_details(source="manual"):
    """Keep reading award pages until none are due (the first load has a few thousand; later
    days have a handful). Stops if a round makes no progress."""
    result, before = None, None
    while True:
        left = details_pending()
        if not left or left == before:
            return result
        log.info("%s award pages still to read: continuing", f"{left:,}")
        before = left
        result = run(["details"], source=source)
        if result["status"] == "failed":
            return result


def run_with_catch_up(source="manual"):
    result = run(source=source)
    if result["status"] != "failed":
        result = catch_up_details(source) or result
    return result


def main(argv=None):
    p = argparse.ArgumentParser(description="Load DoD contract data from USAspending.gov")
    p.add_argument("--step", action="append", choices=STEPS, help="run only this step (repeatable)")
    p.add_argument("--source", default="manual", help="label for the run log")
    args = p.parse_args(argv)
    logging.basicConfig(level=config.LOG_LEVEL, format="%(asctime)s %(levelname)s %(message)s")
    result = run(args.step, args.source) if args.step else run_with_catch_up(args.source)
    print(result)
    return 0 if result["status"] != "failed" else 1


if __name__ == "__main__":
    sys.exit(main())
