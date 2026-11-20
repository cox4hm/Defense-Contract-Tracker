"""Client for the public USAspending.gov API (no key needed).

Docs: https://api.usaspending.gov/docs/endpoints
Every parse_* function is pure, and tests/test_usaspending.py runs them on real responses
saved in tests/samples/, so a change in the API's shape shows up as a failing test.
"""
import logging
import time
from datetime import date

import requests

from . import config

log = logging.getLogger(__name__)

CONTRACT_TYPES = ["A", "B", "C", "D"]  # BPA call, purchase order, delivery order, definitive contract
DOD = {"type": "awarding", "tier": "toptier", "name": "Department of Defense"}

AWARD_FIELDS = [
    "Award ID", "Recipient Name", "Recipient UEI", "recipient_id", "Description", "Award Amount",
    "Total Outlays", "Start Date", "End Date", "Last Modified Date", "Awarding Sub Agency",
    "Funding Sub Agency", "Contract Award Type", "NAICS", "PSC", "Primary Place of Performance",
    "generated_internal_id",
]
IDV_TYPES = ["IDV_A", "IDV_B", "IDV_B_A", "IDV_B_B", "IDV_B_C", "IDV_C", "IDV_D", "IDV_E"]
IDV_FIELDS = ["Award ID", "Recipient Name", "Award Amount", "Description", "Start Date", "End Date",
              "Last Date to Order", "generated_internal_id"]
SUBAWARD_FIELDS = [
    "Sub-Award ID", "Sub-Awardee Name", "Sub-Award Date", "Sub-Award Amount",
    "Prime Award ID", "Prime Recipient Name", "Sub-Award Description",
]


class ApiError(RuntimeError):
    pass


# --------------------------------------------------------------------- HTTP
_session = requests.Session()
_session.headers.update({"Content-Type": "application/json",
                         "User-Agent": "defense-contract-tracker (student portfolio project)"})


class Unreachable(ApiError):
    """USAspending dropped the connection or is overloaded: worth trying again later, not a bad request."""


# Seconds to wait before each retry. USAspending drops connections when it's busy or when a client
# sends a lot of requests in a row, and it usually takes a minute or more to recover.
BACKOFF = [5, 20, 60, 120]


def _request(method, path, body=None, retries=None):
    """Polite pacing, and patient retries on rate limits, server errors and dropped connections."""
    url = config.USASPENDING_BASE.rstrip("/") + path
    waits = BACKOFF[:retries - 1] if retries else BACKOFF
    for attempt in range(len(waits) + 1):
        time.sleep(config.REQUEST_PAUSE_SECONDS)
        try:
            resp = _session.request(method, url, json=body, timeout=120)
        except requests.RequestException as exc:
            if attempt == len(waits):
                raise Unreachable(f"{path}: {exc}") from exc
            log.warning("%s: no answer (%s); trying again in %ds", path, type(exc).__name__, waits[attempt])
            time.sleep(waits[attempt])
            continue
        if resp.status_code == 200:
            return resp.json()
        if resp.status_code in (429, 500, 502, 503, 504):
            if attempt == len(waits):
                raise Unreachable(f"{path}: HTTP {resp.status_code}")
            log.warning("%s: HTTP %d; trying again in %ds", path, resp.status_code, waits[attempt])
            time.sleep(waits[attempt])
            continue
        raise ApiError(f"{path}: HTTP {resp.status_code}: {resp.text[:300]}")
    raise Unreachable(f"{path}: gave up")


def post(path, body, retries=None):
    return _request("POST", path, body, retries)


def get(path, retries=None):
    return _request("GET", path, None, retries)


# ------------------------------------------------------------------ filters
def contract_filters(start, end, counties=None, state="VA", agency=None, keywords=None,
                     recipient=None, min_amount=None, date_type=None):
    """Build a USAspending filter object for DoD contracts.

    counties:   3-digit county/city codes for the place of performance (Virginia by default)
    agency:     an agency object; defaults to all of DoD
    keywords:   list of search words (left out when empty: an empty list is an API error)
    recipient:  company name or UEI
    min_amount: only awards whose total obligation is at least this many dollars
    date_type:  "last_modified_date" finds awards changed in the period (used for daily updates);
                by default the period matches awards with any contract action in it
    """
    period = {"start_date": str(start), "end_date": str(end)}
    if date_type:
        period["date_type"] = date_type
    f = {"time_period": [period], "award_type_codes": CONTRACT_TYPES, "agencies": [agency or DOD]}
    if counties:
        f["place_of_performance_locations"] = [{"country": "USA", "state": state, "county": c} for c in counties]
    if keywords:
        f["keywords"] = list(keywords)
    if recipient:
        f["recipient_search_text"] = [recipient]
    if min_amount:
        f["award_amounts"] = [{"lower_bound": float(min_amount)}]
    return f


def subtier(name):
    return {"type": "awarding", "tier": "subtier", "name": name, "toptier_name": "Department of Defense"}


# ------------------------------------------------------------------ dates
def fiscal_year(d):
    """The federal fiscal year starts October 1: Oct 2025 is in fiscal 2026."""
    return d.year + 1 if d.month >= 10 else d.year


def fiscal_month_to_date(fy, fiscal_month):
    """USAspending numbers months by FISCAL month: 1 = October of the prior year."""
    fy, fm = int(fy), int(fiscal_month)
    cal_month = (fm + 8) % 12 + 1          # 1->10, 3->12, 4->1, 12->9
    return date(fy - 1 if fm <= 3 else fy, cal_month, 1)


def to_date(v):
    """'2036-06-02', '2036-06-02 00:00:00' or '11/19/2026' -> date; anything else -> None."""
    if not v or not isinstance(v, str):
        return None
    v = v.strip()
    try:
        if "/" in v:
            m, d, y = (int(x) for x in v.split("/"))
            return date(y, m, d)
        return date.fromisoformat(v[:10])
    except ValueError:
        return None


def _num(v):
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _code(value):
    """NAICS/PSC arrive as {code, description} (or occasionally a bare string)."""
    if isinstance(value, dict):
        return value.get("code"), value.get("description")
    return (value, None) if value else (None, None)


# ------------------------------------------------------------------ parsers
def parse_over_time(payload, group):
    """[(key, amount)]: key is a month (date) or a fiscal year (int)."""
    out = []
    for row in payload.get("results", []):
        tp = row.get("time_period", {})
        amount = row.get("aggregated_amount")
        if amount is None:
            amount = row.get("Contract_Obligations", 0)
        key = fiscal_month_to_date(tp["fiscal_year"], tp["month"]) if group == "month" else int(tp["fiscal_year"])
        out.append((key, float(amount or 0)))
    return out


def parse_awards(payload):
    out = []
    for r in payload.get("results", []):
        gid = r.get("generated_internal_id")
        if not gid:
            continue
        naics, naics_desc = _code(r.get("NAICS"))
        psc, psc_desc = _code(r.get("PSC"))
        place = r.get("Primary Place of Performance") or {}
        out.append({
            "generated_id": gid,
            "award_id": r.get("Award ID"),
            "recipient_name": r.get("Recipient Name"),
            "recipient_uei": r.get("Recipient UEI"),
            "recipient_hash": r.get("recipient_id"),
            "description": r.get("Description"),
            "amount": _num(r.get("Award Amount")),
            "outlays": _num(r.get("Total Outlays")),
            "start_date": to_date(r.get("Start Date")),
            "end_date": to_date(r.get("End Date")),
            "last_modified": to_date(r.get("Last Modified Date")),
            "awarding_subagency": r.get("Awarding Sub Agency"),
            "funding_subagency": r.get("Funding Sub Agency"),
            "award_type": r.get("Contract Award Type"),
            "naics_code": naics, "naics_desc": naics_desc,
            "psc_code": psc, "psc_desc": psc_desc,
            "county_code": place.get("county_code"),
            "city": place.get("city_name"),
        })
    return out


SMALL = "Small Business"
NOT_SMALL = "Not Designated a Small Business"


def parse_award_detail(d):
    """The fields only the award page has: options, potential end date, competition, set-aside,
    parent company and business size."""
    pop = d.get("period_of_performance") or {}
    tx = d.get("latest_transaction_contract_data") or {}
    rec = d.get("recipient") or {}
    cats = rec.get("business_categories") or []
    small = True if SMALL in cats else (False if NOT_SMALL in cats else None)
    offers = tx.get("number_of_offers_received")
    try:
        offers = int(offers) if offers not in (None, "") else None
    except ValueError:
        offers = None
    awarding = d.get("awarding_agency") or {}
    return {
        "generated_id": d.get("generated_unique_award_id"),
        "potential_end_date": to_date(pop.get("potential_end_date")),
        "end_date": to_date(pop.get("end_date")),
        "base_and_all_options": _num(d.get("base_and_all_options")),
        "base_exercised_options": _num(d.get("base_exercised_options")),
        "awarding_office": awarding.get("office_agency_name"),
        "competed_code": tx.get("extent_competed"),
        "competed": tx.get("extent_competed_description"),
        "offers": offers,
        "set_aside_code": tx.get("type_set_aside"),
        "set_aside": tx.get("type_set_aside_description"),
        "pricing": tx.get("type_of_contract_pricing_description"),
        "solicitation": tx.get("solicitation_procedures_description"),
        "parent_name": rec.get("parent_recipient_name"),
        "parent_hash": rec.get("parent_recipient_hash"),
        "small_business": small,
        "subaward_count": d.get("subaward_count"),
    }


def parse_subawards(payload):
    out = []
    for r in payload.get("results", []):
        name = r.get("Sub-Awardee Name")
        if not name:
            continue
        out.append({
            "subaward_id": r.get("Sub-Award ID"),
            "prime_award_id": r.get("Prime Award ID"),
            "prime_recipient": r.get("Prime Recipient Name"),
            "sub_name": name,
            "amount": _num(r.get("Sub-Award Amount")),
            "sub_date": to_date(r.get("Sub-Award Date")),
            "description": r.get("Sub-Award Description"),
        })
    return out


def parse_category(payload):
    out = []
    for r in payload.get("results", []):
        name = r.get("name") or r.get("code")
        if not name:
            continue
        out.append({"code": r.get("code") or r.get("uei"), "name": name,
                    "amount": float(r.get("amount") or 0)})
    return out


def parse_idvs(payload):
    """Master contracts (indefinite-delivery vehicles) from a search."""
    out = []
    for r in payload.get("results", []):
        if not r.get("generated_internal_id"):
            continue
        out.append({"gid": r["generated_internal_id"], "piid": r.get("Award ID"), "holder": r.get("Recipient Name"),
                    "description": r.get("Description"), "order_end": to_date(r.get("Last Date to Order"))})
    return out


def parse_idv_page(d):
    """A master contract's own page (/api/v2/awards/CONT_IDV_.../)."""
    pop = d.get("period_of_performance") or {}
    return {"gid": d.get("generated_unique_award_id"), "piid": d.get("piid"),
            "holder": (d.get("recipient") or {}).get("recipient_name"), "description": d.get("description"),
            "order_end": to_date(pop.get("end_date"))}


def parent_idv(order_gid, prefix=""):
    """An order's ID names its master contract: CONT_AWD_SPE8EL22F198X_9700_SPE8EJ21D0020_9700 ->
    CONT_IDV_SPE8EJ21D0020_9700. None when the order has no master contract (or another prefix)."""
    parts = (order_gid or "").split("_")
    if len(parts) == 6 and parts[0] == "CONT" and parts[4] != "-NONE-" and parts[4].startswith(prefix):
        return f"CONT_IDV_{parts[4]}_{parts[5]}"
    return None


def parse_idv_amounts(d):
    return {"orders": int(d.get("child_award_count") or 0),
            "obligated": float(d.get("child_award_total_obligation") or 0)}


def parse_idv_children(payload):
    out = []
    for r in payload.get("results", []):
        gid = r.get("generated_unique_award_id")
        if not gid:
            continue
        out.append({"gid": gid, "piid": r.get("piid"), "description": r.get("description"),
                    "amount": _num(r.get("obligated_amount")),
                    "start_date": to_date(r.get("period_of_performance_start_date")),
                    "end_date": to_date(r.get("period_of_performance_current_end_date"))})
    return out


def parse_award_subawards(payload):
    """/api/v2/subawards/ rows (the subcontracts a prime reported under one award)."""
    out = []
    for r in payload.get("results", []):
        name = r.get("recipient_name") or r.get("sub_awardee_name")
        if not name:
            continue
        out.append({"subaward_id": str(r.get("subaward_number") or r.get("id") or ""), "sub_name": name,
                    "amount": _num(r.get("amount")), "sub_date": to_date(r.get("action_date")),
                    "description": r.get("description")})
    return out


def parse_count(payload):
    return int((payload.get("results") or {}).get("contracts") or 0)


# ------------------------------------------------------------------ calls
def spending_over_time(filters, group="month"):
    return parse_over_time(post("/api/v2/search/spending_over_time/", {"group": group, "filters": filters}), group)


def spending_by_category(category, filters, limit=15):
    return parse_category(post(f"/api/v2/search/spending_by_category/{category}/",
                               {"filters": filters, "limit": limit, "page": 1}))


def award_count(filters):
    return parse_count(post("/api/v2/search/spending_by_award_count/", {"filters": filters}))


def iter_awards(filters, max_pages=500, sort="Award Amount"):
    """Yield pages of parsed awards, 100 at a time, until the results run out (or max_pages)."""
    for page in range(1, max_pages + 1):
        payload = post("/api/v2/search/spending_by_award/", {
            "filters": filters, "fields": AWARD_FIELDS, "limit": 100, "page": page,
            "sort": sort, "order": "desc", "subawards": False,
        })
        yield parse_awards(payload)
        if not (payload.get("page_metadata") or {}).get("hasNext"):
            return


def awards(filters, max_rows=100):
    rows = []
    for page in iter_awards(filters, max_pages=(max_rows + 99) // 100):
        rows.extend(page)
    return rows[:max_rows]


def award_detail(generated_id, retries=None):
    return parse_award_detail(get(f"/api/v2/awards/{generated_id}/", retries))


def idvs(filters, limit=50):
    f = dict(filters, award_type_codes=IDV_TYPES)
    return parse_idvs(post("/api/v2/search/spending_by_award/", {
        "filters": f, "fields": IDV_FIELDS, "limit": limit, "page": 1, "sort": "Award Amount", "order": "desc",
        "subawards": False}))


def idv_page(gid):
    return parse_idv_page(get(f"/api/v2/awards/{gid}/"))


def idv_amounts(gid):
    return parse_idv_amounts(get(f"/api/v2/idvs/amounts/{gid}/"))


def idv_children(gid, limit=25):
    """The largest orders placed under a master contract."""
    return parse_idv_children(post("/api/v2/idvs/awards/", {
        "award_id": gid, "type": "child_awards", "limit": limit, "page": 1,
        "sort": "obligated_amount", "order": "desc"}))


def award_subawards(gid, limit=50):
    return parse_award_subawards(post("/api/v2/subawards/", {
        "award_id": gid, "limit": limit, "page": 1, "sort": "amount", "order": "desc"}))


def subawards(filters, max_rows=100):
    rows, page = [], 1
    while len(rows) < max_rows:
        payload = post("/api/v2/search/spending_by_award/", {
            "filters": filters, "fields": SUBAWARD_FIELDS, "limit": 100, "page": page,
            "sort": "Sub-Award Amount", "order": "desc", "subawards": True,
        })
        rows.extend(parse_subawards(payload))
        if not (payload.get("page_metadata") or {}).get("hasNext"):
            break
        page += 1
    return rows[:max_rows]


def last_updated():
    """The date USAspending last loaded new data."""
    return to_date(get("/api/v2/awards/last_updated/").get("last_updated"))
