"""Flask web app: dashboard, JSON API and a small admin page."""
import hmac
import json
from collections import defaultdict
from datetime import date, datetime
from decimal import Decimal

from flask import Flask, abort, jsonify, redirect, render_template, request, session, url_for
from flask.json.provider import DefaultJSONProvider

from . import config, db


class JSONProvider(DefaultJSONProvider):
    def default(self, o):
        if isinstance(o, Decimal):
            return float(o)
        if isinstance(o, (date, datetime)):
            return o.isoformat()
        return super().default(o)


# Two-digit NAICS sectors, in plain words
SECTORS = {
    "11": "Farming and fishing", "21": "Mining and energy", "22": "Utilities", "23": "Construction",
    "31": "Manufacturing", "32": "Manufacturing", "33": "Manufacturing", "42": "Wholesale supply",
    "44": "Retail", "45": "Retail", "48": "Transportation", "49": "Transportation",
    "51": "Information and telecom", "52": "Finance and insurance", "53": "Real estate and leasing",
    "54": "Engineering and technical services", "55": "Company management",
    "56": "Facilities and support services", "61": "Education and training", "62": "Health care",
    "71": "Arts and recreation", "72": "Lodging and food", "81": "Repair and maintenance",
    "92": "Public administration",
}
BRANCHES = ["Navy", "DLA", "Army", "Air Force", "DISA", "Other DoD"]


SET_ASIDES = {
    "NONE": "None", "SBA": "Small business", "SBP": "Small business (partial)", "8A": "8(a) program",
    "8AN": "8(a) sole source", "HZC": "HUBZone", "HZS": "HUBZone sole source", "SDVOSBC": "Service-disabled veteran-owned",
    "SDVOSBS": "Service-disabled veteran-owned, sole source", "WOSB": "Women-owned small business",
    "WOSBSS": "Women-owned, sole source", "EDWOSB": "Economically disadvantaged women-owned",
    "VSA": "Veteran-owned", "ISBEE": "Indian small business economic enterprise",
}


def set_aside_name(code, text):
    if code in SET_ASIDES:
        return SET_ASIDES[code]
    return (text or "Not stated").capitalize().rstrip(".")


def sector(naics_code):
    return SECTORS.get((naics_code or "")[:2], "Other industries")


RUNWAY_COLS = """generated_id AS id, award_id, company, recipient_name, branch, amount, base_and_all_options AS ceiling,
                 final_end, end_date, potential_end_date, end_month, days_left, has_detail, sole_source, set_aside_used,
                 offers, small_business, options_left, left(description, 90) AS description, naics_code, city"""


def overview():
    head = db.one("SELECT * FROM headline()") or {}
    runway = db.query(f"SELECT {RUNWAY_COLS} FROM v_runway ORDER BY final_end, amount DESC")
    for r in runway:
        r["sector"] = sector(r.pop("naics_code"))
    return {
        "headline": head,
        "runway": runway,
        "runway_months": int(db.one("SELECT setting('runway_months') AS v")["v"]),
        "events": db.query("""SELECT e.kind, e.generated_id, e.title, e.detail, e.amount, e.created_at,
                                     coalesce(a.parent_name, a.recipient_name) AS who
                              FROM events e LEFT JOIN awards a USING (generated_id)
                              ORDER BY e.created_at DESC, e.id DESC LIMIT 14"""),
        "last_run": db.one("""SELECT id, mode, started_at, finished_at, status, rows_written, message
                              FROM fetch_runs WHERE status IN ('ok', 'partial') ORDER BY id DESC LIMIT 1"""),
        "lag_days": int(db.one("SELECT setting('reporting_lag_days') AS v")["v"]),
        "branches": BRANCHES,
    }


def award(gid):
    a = db.one("""SELECT *, (SELECT name FROM localities l WHERE l.county_code = v.county_code) AS locality
                  FROM v_company_awards v WHERE generated_id = %s""", (gid,))
    if not a:
        return None
    a["sector"] = sector(a.get("naics_code"))
    a["company_others"] = db.query(f"""
        SELECT {RUNWAY_COLS} FROM v_runway WHERE company_key = %s AND generated_id <> %s
        ORDER BY final_end LIMIT 8""", (a["company_key"], gid))
    return a


def companies():
    return db.query("""SELECT company_key AS key, company, awards, amount, ending, ending_amount, small_business,
                              branches, main_industry, main_city
                       FROM v_companies ORDER BY amount DESC NULLS LAST LIMIT 60""")


def company(key):
    c = db.one("SELECT * FROM v_companies WHERE company_key = %s", (key,))
    if not c:
        return None
    c["by_branch"] = db.query("""SELECT branch, count(*) AS awards, sum(amount) AS amount FROM v_company_awards
                                 WHERE in_region AND company_key = %s GROUP BY branch ORDER BY 3 DESC""", (key,))
    c["subsidiaries"] = db.query("""SELECT recipient_name AS name, count(*) AS awards, sum(amount) AS amount
                                    FROM v_company_awards WHERE in_region AND company_key = %s
                                    GROUP BY recipient_name ORDER BY 3 DESC LIMIT 8""", (key,))
    c["ending_list"] = db.query(f"SELECT {RUNWAY_COLS} FROM v_runway WHERE company_key = %s ORDER BY final_end", (key,))
    c["largest"] = db.query("""SELECT generated_id AS id, award_id, branch, amount, start_date, final_end,
                                      left(description, 90) AS description
                               FROM v_company_awards WHERE in_region AND company_key = %s
                               ORDER BY amount DESC NULLS LAST LIMIT 10""", (key,))
    for r in c["ending_list"]:
        r.pop("naics_code", None)
    return c


def flow(top_companies=10, top_sectors=7):
    """Branch -> industry -> company, for contracts active now (final end date in the future)."""
    rows = db.query("""SELECT branch, naics_code, company_key, company, sum(amount) AS amount
                       FROM v_company_awards
                       WHERE in_region AND final_end >= current_date AND amount > 0
                       GROUP BY 1, 2, 3, 4""")
    by_company = defaultdict(float)
    names = {}
    for r in rows:
        by_company[r["company_key"]] += float(r["amount"])
        names[r["company_key"]] = r["company"]
    top = sorted(by_company, key=by_company.get, reverse=True)[:top_companies]
    by_sector = defaultdict(float)
    for r in rows:
        by_sector[sector(r["naics_code"])] += float(r["amount"])
    top_sectors = set(sorted(by_sector, key=by_sector.get, reverse=True)[:top_sectors])
    links = defaultdict(float)
    for r in rows:
        sec = sector(r["naics_code"])
        sec = sec if sec in top_sectors else "Other industries"
        co = names[r["company_key"]] if r["company_key"] in top else "All other companies"
        links[("b:" + r["branch"], "s:" + sec)] += float(r["amount"])
        links[("s:" + sec, "c:" + co)] += float(r["amount"])
    return {"links": [{"source": s, "target": t, "value": v} for (s, t), v in links.items()],
            "total": sum(by_company.values()), "companies": len(by_company)}


def money():
    fy = db.one("SELECT last_full_fy() AS y")["y"]
    cats = {}
    for cat in ("branch", "naics", "county", "recipient", "sb_recipient", "psc"):
        cats[cat] = db.query("""SELECT code, name, amount,
                                   (SELECT amount FROM category_totals p WHERE p.category = c.category
                                    AND p.fiscal_year = c.fiscal_year - 1 AND p.name = c.name) AS prev
                                FROM category_totals c WHERE category = %s AND fiscal_year = %s
                                ORDER BY amount DESC""", (cat, fy))
    return {
        "fiscal_year": fy,
        "fy_totals": db.query("""SELECT r.fiscal_year, r.amount AS region, s.amount AS small_business
                                 FROM fy_totals r LEFT JOIN fy_totals s
                                   ON s.series = 'small_business' AND s.fiscal_year = r.fiscal_year
                                 WHERE r.series = 'region' ORDER BY 1"""),
        "monthly": db.query("SELECT month, fiscal_year, amount, complete FROM v_region_monthly ORDER BY month"),
        "categories": cats,
        "set_asides": [dict(r, name=set_aside_name(r["code"], r["name"])) for r in db.query(
            """SELECT set_aside_code AS code, max(set_aside) AS name, count(*) AS awards, sum(amount) AS amount
               FROM v_runway WHERE has_detail GROUP BY 1 ORDER BY 3 DESC""")],
        "competition": db.query("""SELECT CASE WHEN sole_source THEN 'Not competed'
                                               WHEN competed IS NULL THEN 'Not stated' ELSE 'Competed' END AS name,
                                          count(*) AS awards, sum(amount) AS amount
                                   FROM v_runway WHERE has_detail GROUP BY 1 ORDER BY 2 DESC"""),
    }


def kits():
    return {
        "summary": db.one("""SELECT count(*) AS contracts, count(DISTINCT holder) AS holders, sum(orders) AS orders,
                                    sum(obligated) AS obligated, max(order_end) AS order_end, max(checked_at) AS checked_at
                             FROM kit_idvs"""),
        "holders": db.query("""SELECT holder, count(*) AS contracts, sum(orders) AS orders, sum(obligated) AS obligated
                               FROM kit_idvs GROUP BY holder ORDER BY 4 DESC NULLS LAST"""),
        "orders": db.query("""SELECT piid, holder, description, amount, start_date, end_date
                              FROM kit_orders ORDER BY amount DESC NULLS LAST LIMIT 10"""),
        "suppliers": db.query("""SELECT sub_name, prime_recipient, sum(amount) AS amount, count(*) AS subawards,
                                        max(sub_date) AS latest
                                 FROM subawards GROUP BY 1, 2 ORDER BY 3 DESC NULLS LAST LIMIT 15"""),
    }


def watchlist():
    cos = db.query("SELECT * FROM companies ORDER BY total_amount DESC NULLS LAST, display_name")
    for c in cos:
        c["awards"] = db.query("""SELECT v.generated_id AS id, v.award_id, v.branch, v.amount, v.final_end,
                                         v.city, left(v.description, 80) AS description
                                  FROM company_awards ca JOIN v_company_awards v USING (generated_id)
                                  WHERE ca.company_id = %s ORDER BY v.amount DESC NULLS LAST LIMIT 5""", (c["id"],))
    return cos


def create_app():
    app = Flask(__name__)
    app.json = JSONProvider(app)
    app.secret_key = (config.ADMIN_TOKEN or "dev-only-not-secret") + "-session"

    def _admin_ok():
        return bool(config.ADMIN_TOKEN) and session.get("admin") is True

    @app.get("/")
    def index():
        return render_template("index.html", portfolio_url=config.PORTFOLIO_URL, source_url=config.SOURCE_URL)

    @app.get("/api/overview")
    def api_overview():
        return jsonify(overview())

    @app.get("/api/award/<path:gid>")
    def api_award(gid):
        a = award(gid)
        return jsonify(a) if a else abort(404)

    @app.get("/api/companies")
    def api_companies():
        return jsonify(companies())

    @app.get("/api/company/<path:key>")
    def api_company(key):
        c = company(key)
        return jsonify(c) if c else abort(404)

    @app.get("/api/flow")
    def api_flow():
        return jsonify(flow())

    @app.get("/api/money")
    def api_money():
        return jsonify(money())

    @app.get("/api/kits")
    def api_kits():
        return jsonify(kits())

    @app.get("/api/watchlist")
    def api_watchlist():
        return jsonify(watchlist())

    @app.get("/health")
    def health():
        return jsonify({"ok": True, "last_run": db.one(
            "SELECT status, mode, finished_at FROM fetch_runs ORDER BY id DESC LIMIT 1")})

    # ------------------------------------------------------------------ admin
    @app.route("/admin", methods=["GET", "POST"])
    def admin():
        if request.method == "POST" and "token" in request.form:
            if config.ADMIN_TOKEN and hmac.compare_digest(request.form["token"], config.ADMIN_TOKEN):
                session["admin"] = True
            return redirect(url_for("admin"))
        if not _admin_ok():
            return render_template("admin_login.html", configured=bool(config.ADMIN_TOKEN))
        return render_template(
            "admin.html",
            companies=db.query("SELECT * FROM companies ORDER BY display_name"),
            settings=db.query("SELECT * FROM settings ORDER BY key"),
            localities=db.query("SELECT * FROM localities ORDER BY name"),
            runs=db.query("SELECT * FROM fetch_runs ORDER BY id DESC LIMIT 15"),
            pending=db.one("SELECT details_pending FROM headline()")["details_pending"],
        )

    def _require_admin():
        if not _admin_ok():
            abort(403)

    @app.post("/admin/company")
    def admin_add_company():
        _require_admin()
        name = request.form.get("display_name", "").strip()
        text = request.form.get("search_text", "").strip()
        if name and text:
            db.execute("""INSERT INTO companies (display_name, search_text, note) VALUES (%s, %s, %s)
                          ON CONFLICT (search_text) DO UPDATE SET display_name = EXCLUDED.display_name""",
                       (name, text, request.form.get("note", "").strip() or None))
        return redirect(url_for("admin"))

    @app.post("/admin/company/<int:company_id>/delete")
    def admin_delete_company(company_id):
        _require_admin()
        db.execute("DELETE FROM companies WHERE id = %s", (company_id,))
        return redirect(url_for("admin"))

    @app.post("/admin/locality/<fips5>/toggle")
    def admin_toggle_locality(fips5):
        _require_admin()
        db.execute("UPDATE localities SET active = NOT active WHERE fips5 = %s", (fips5,))
        return redirect(url_for("admin"))

    @app.post("/admin/settings")
    def admin_settings():
        _require_admin()
        for key, value in request.form.items():
            if not key.startswith("setting:"):
                continue
            key, value = key[len("setting:"):], value.strip()
            if key == "kits_keywords":
                try:
                    parsed = json.loads(value)
                    assert isinstance(parsed, list) and all(isinstance(x, str) for x in parsed)
                except Exception:
                    abort(400, "kits_keywords must be a JSON list of strings")
            db.execute("UPDATE settings SET value = %s WHERE key = %s", (value, key))
        return redirect(url_for("admin"))

    @app.post("/admin/logout")
    def admin_logout():
        session.clear()
        return redirect(url_for("index"))

    return app
