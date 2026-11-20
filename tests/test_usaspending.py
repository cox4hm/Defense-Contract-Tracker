"""Parsers run on real USAspending responses saved in tests/samples/ (trimmed), so a change in
the API's shape shows up here as a failing test instead of as empty charts."""
import json
from datetime import date
from pathlib import Path

from app import loader
from app import usaspending as api

SAMPLES = Path(__file__).parent / "samples"


def sample(name):
    return json.loads((SAMPLES / name).read_text())


def test_parse_awards_reads_the_real_search_shape():
    rows = api.parse_awards(sample("award_search.json"))
    assert len(rows) == 5
    r = rows[0]
    assert r["generated_id"].startswith("CONT_AWD_")
    assert r["recipient_name"] and r["recipient_hash"].endswith(("-C", "-R", "-P"))
    assert isinstance(r["amount"], float) and r["amount"] > 1e6
    assert isinstance(r["end_date"], date) and isinstance(r["last_modified"], date)
    assert r["naics_code"].isdigit() and r["naics_desc"]
    assert r["psc_code"] and r["psc_desc"]
    assert len(r["county_code"]) == 3 and r["city"]


def test_parse_award_detail_reads_options_competition_and_parent():
    d = api.parse_award_detail(sample("award_detail.json"))
    assert d["generated_id"].startswith("CONT_AWD_")
    assert isinstance(d["potential_end_date"], date)          # "2036-06-02 00:00:00" in the raw data
    assert d["base_and_all_options"] >= d["base_exercised_options"] > 0
    assert d["competed"] and d["competed_code"]
    assert d["offers"] is None or isinstance(d["offers"], int)
    assert d["set_aside_code"] and d["set_aside"]
    assert d["parent_name"] and d["parent_hash"].endswith("-P")
    assert d["small_business"] in (True, False, None)
    assert d["awarding_office"]


def test_parse_award_detail_tolerates_missing_sections():
    d = api.parse_award_detail({"generated_unique_award_id": "X"})
    assert d["generated_id"] == "X" and d["competed"] is None and d["small_business"] is None


def test_parse_over_time_converts_fiscal_months():
    rows = api.parse_over_time(sample("over_time_month.json"), "month")
    assert all(isinstance(k, date) and k.day == 1 for k, _ in rows)
    first = sample("over_time_month.json")["results"][0]["time_period"]
    assert rows[0][0] == api.fiscal_month_to_date(first["fiscal_year"], first["month"])
    fy = api.parse_over_time(sample("over_time_fy.json"), "fiscal_year")
    assert all(isinstance(k, int) and v >= 0 for k, v in fy)


def test_fiscal_dates():
    assert api.fiscal_month_to_date(2026, 1) == date(2025, 10, 1)    # fiscal month 1 is October
    assert api.fiscal_month_to_date(2026, 12) == date(2026, 9, 1)
    assert api.fiscal_year(date(2025, 10, 1)) == 2026
    assert api.fiscal_year(date(2026, 9, 30)) == 2026


def test_to_date_formats():
    assert api.to_date("2036-06-02 00:00:00") == date(2036, 6, 2)
    assert api.to_date("11/19/2026") == date(2026, 11, 19)          # /awards/last_updated/ uses this
    assert api.to_date("") is None and api.to_date(None) is None and api.to_date("soon") is None
    assert api.last_updated.__doc__


def test_categories_and_counts():
    rec = api.parse_category(sample("category_recipient.json"))
    assert rec[0]["name"] and rec[0]["code"] and rec[0]["amount"] > 0
    sub = api.parse_category(sample("category_subagency.json"))
    assert any("Navy" in r["name"] for r in sub)
    assert api.parse_count(sample("award_count.json")) > 1000


def test_contract_filters():
    f = api.contract_filters("2025-10-01", "2026-09-30", counties=["710"], min_amount=1e6,
                             date_type="last_modified_date", keywords=[])
    assert f["time_period"] == [{"start_date": "2025-10-01", "end_date": "2026-09-30", "date_type": "last_modified_date"}]
    assert f["place_of_performance_locations"] == [{"country": "USA", "state": "VA", "county": "710"}]
    assert f["award_amounts"] == [{"lower_bound": 1e6}]
    assert "keywords" not in f                                   # an empty list is an API error
    assert f["agencies"][0]["name"] == "Department of Defense"


def test_iter_awards_pages_until_the_api_says_stop(monkeypatch):
    calls = []

    def fake_post(path, body, retries=4):
        calls.append(body["page"])
        data = dict(sample("award_search.json"))
        data["page_metadata"] = {"hasNext": body["page"] < 3}
        return data

    monkeypatch.setattr(api, "post", fake_post)
    pages = list(api.iter_awards({}, max_pages=10))
    assert calls == [1, 2, 3] and len(pages) == 3
    assert list(api.iter_awards({}, max_pages=2)) and calls[-1] == 2


def test_award_upsert_only_ever_switches_a_flag_on():
    sql, template = loader.award_upsert("in_region")
    assert "in_region = true" in sql and "region_seen = now()" in sql
    assert template.count("%(") == len(loader.SEARCH_COLS)
    sql, _ = loader.award_upsert(None)
    assert "in_region" not in sql and "in_kits" not in sql     # a watchlist save leaves both alone


def test_detail_check_flags_a_silently_missing_field():
    assert loader.detail_check(500, 0).startswith("data check")
    assert loader.detail_check(500, 480) is None
    assert loader.detail_check(10, 0) is None                    # too small a sample to judge


def test_detail_update_keeps_a_known_end_date():
    assert "end_date = coalesce(%(end_date)s, end_date)" in loader.DETAIL_UPDATE
    assert all(f"%({c})s" in loader.DETAIL_UPDATE for c in loader.DETAIL_COLS)


def test_plain_words_for_industries_and_set_asides():
    from app import web
    assert web.sector("336611") == "Manufacturing"
    assert web.sector("541330") == "Engineering and technical services"
    assert web.sector(None) == "Other industries"
    assert web.set_aside_name("8AN", "8(A) SOLE SOURCE") == "8(a) sole source"
    assert web.set_aside_name("XYZ", "SOMETHING NEW.") == "Something new"


def test_dropped_connections_are_retried_patiently_then_reported_as_unreachable(monkeypatch):
    import pytest
    import requests
    attempts, waits = [], []
    monkeypatch.setattr(api.time, "sleep", lambda s: waits.append(s))

    def drop(*a, **k):
        attempts.append(1)
        raise requests.ConnectionError("Remote end closed connection without response")

    monkeypatch.setattr(api._session, "request", drop)
    with pytest.raises(api.Unreachable):
        api.get("/api/v2/awards/X/")
    assert len(attempts) == len(api.BACKOFF) + 1
    assert [w for w in waits if w > 1] == api.BACKOFF            # 5s, 20s, 1 min, 2 min
    attempts.clear()
    with pytest.raises(api.Unreachable):
        api.award_detail("X", retries=2)                          # contract pages: one quick retry only
    assert len(attempts) == 2


def test_kit_program_parsers_read_master_contracts_and_orders():
    idvs = api.parse_idvs(sample("kit_idvs.json"))
    assert idvs and all(i["piid"].startswith("SPE8EJ") for i in idvs)
    assert all(i["gid"].startswith("CONT_IDV_") and i["holder"] for i in idvs)
    assert isinstance(idvs[0]["order_end"], date)
    amounts = api.parse_idv_amounts(sample("kit_idv_amounts.json"))
    assert amounts["orders"] > 1000 and amounts["obligated"] > 1e9
    orders = api.parse_idv_children(sample("kit_idv_children.json"))
    assert len(orders) == 5 and orders[0]["gid"].startswith("CONT_AWD_") and orders[0]["amount"] > 0
    assert orders[0]["amount"] >= orders[-1]["amount"]                 # largest first
    assert api.parse_award_subawards({"results": [{"recipient_name": "X LLC", "amount": 5e4, "action_date": "2025-01-02",
                                                   "subaward_number": 7}]})[0]["sub_name"] == "X LLC"
    assert api.parse_award_subawards({"results": []}) == []


def test_master_contract_found_from_an_order_id():
    assert api.parent_idv("CONT_AWD_SPE8EL22F198X_9700_SPE8EJ21D0020_9700", "SPE8EJ") == "CONT_IDV_SPE8EJ21D0020_9700"
    assert api.parent_idv("CONT_AWD_N0002416C2116_9700_-NONE-_-NONE-", "SPE8EJ") is None     # a standalone contract
    assert api.parent_idv("CONT_AWD_X_9700_SPM4A123D0001_9700", "SPE8EJ") is None            # another program
    page = api.parse_idv_page(sample("kit_idv_page.json"))
    assert page["gid"] == "CONT_IDV_SPE8EJ21D0020_9700" and page["piid"] == "SPE8EJ21D0020"
    assert page["holder"] and page["order_end"] == date(2031, 1, 4)
