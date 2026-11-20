-- Defense Contract Tracker: views the dashboard reads

DROP FUNCTION IF EXISTS headline() CASCADE;
DROP VIEW IF EXISTS v_runway, v_company_awards, v_companies, v_region_monthly CASCADE;
-- version 1 views
DROP VIEW IF EXISTS v_last_full_fy, v_locality_last_fy, v_region_fy, v_awards, v_company_summary,
    v_subawards, v_kits_suppliers CASCADE;
DROP FUNCTION IF EXISTS category_fy() CASCADE;

-- Companies are grouped by name with punctuation and suffixes removed: one company often holds
-- contracts under several registrations ("BAE SYSTEMS NORFOLK SHIP REPAIR INC." / "..., INC").
CREATE OR REPLACE FUNCTION company_key(name text) RETURNS text
LANGUAGE sql IMMUTABLE AS $$
    SELECT regexp_replace(
             regexp_replace(upper(coalesce(name, '?')), '[^A-Z0-9 ]', ' ', 'g'),
             '\m(INC|INCORPORATED|LLC|L L C|CORP|CORPORATION|CO|COMPANY|LTD|LP|THE)\M|\s+', '', 'g')
$$;

-- Each company's parent, learned from any award page that names one. Many awards are only
-- known from search results (no parent listed), so this lets them roll up correctly too.
CREATE VIEW v_company_awards AS
WITH parents AS (
    SELECT DISTINCT ON (recipient_hash) recipient_hash, parent_hash, parent_name
    FROM awards
    WHERE parent_hash IS NOT NULL AND recipient_hash IS NOT NULL
    ORDER BY recipient_hash, detail_at DESC NULLS LAST
)
SELECT a.*,
       branch_label(a.awarding_subagency)                                  AS branch,
       company_key(coalesce(a.parent_name, p.parent_name, a.recipient_name)) AS company_key,
       coalesce(a.parent_name, p.parent_name, a.recipient_name)            AS company,
       -- the last day the contract can run if every option is exercised
       CASE WHEN a.potential_end_date > a.end_date THEN a.potential_end_date ELSE a.end_date END AS final_end,
       a.detail_at IS NOT NULL                                             AS has_detail,
       a.competed_code IN ('B', 'C', 'G', 'NDO')                           AS sole_source,
       a.set_aside_code IS NOT NULL AND a.set_aside_code NOT IN ('NONE', '') AS set_aside_used,
       'https://www.usaspending.gov/award/' || a.generated_id              AS link
FROM awards a
LEFT JOIN parents p ON p.recipient_hash = a.recipient_hash;

-- The recompete runway: Hampton Roads contracts whose final end date (options included)
-- falls in the next runway_months. These are the ones that have to be re-bid or replaced.
CREATE VIEW v_runway AS
SELECT c.*,
       (c.final_end - current_date)                                      AS days_left,
       date_trunc('month', c.final_end)::date                            AS end_month,
       (c.potential_end_date IS NOT NULL AND c.potential_end_date > c.end_date) AS options_left
FROM v_company_awards c
WHERE c.in_region
  AND c.final_end >= current_date
  AND c.final_end < current_date + make_interval(months => coalesce(setting('runway_months'), '24')::int);

CREATE VIEW v_companies AS
SELECT company_key,
       max(company)                                                       AS company,
       count(*)                                                           AS awards,
       sum(amount)                                                        AS amount,
       count(*) FILTER (WHERE final_end >= current_date
                        AND final_end < current_date + make_interval(months => coalesce(setting('runway_months'), '24')::int))
                                                                          AS ending,
       coalesce(sum(amount) FILTER (WHERE final_end >= current_date
                        AND final_end < current_date + make_interval(months => coalesce(setting('runway_months'), '24')::int)), 0)
                                                                          AS ending_amount,
       bool_and(small_business)                                           AS small_business,  -- small only if every known award says so
       array_agg(DISTINCT branch)                                         AS branches,
       (array_agg(naics_desc ORDER BY amount DESC NULLS LAST))[1]         AS main_industry,
       (array_agg(city ORDER BY amount DESC NULLS LAST))[1]               AS main_city
FROM v_company_awards
WHERE in_region
GROUP BY company_key;

CREATE VIEW v_region_monthly AS
SELECT month, fiscal_year(month) AS fiscal_year, amount, month_is_complete(month) AS complete
FROM monthly_totals WHERE series = 'region';

CREATE OR REPLACE FUNCTION headline()
RETURNS TABLE (
    runway_amount numeric, runway_count bigint, next6_amount numeric, next6_count bigint,
    detailed bigint, sole_source bigint, set_aside bigint, region_fy integer, region_fy_amount numeric,
    small_fy_amount numeric, region_awards bigint, details_pending bigint
)
LANGUAGE sql STABLE AS $$
    WITH r AS (SELECT * FROM v_runway),
    fy AS (SELECT last_full_fy() AS y)
    SELECT
        (SELECT coalesce(sum(amount), 0) FROM r),
        (SELECT count(*) FROM r),
        (SELECT coalesce(sum(amount), 0) FROM r WHERE days_left < 183),
        (SELECT count(*) FROM r WHERE days_left < 183),
        (SELECT count(*) FROM r WHERE has_detail),
        (SELECT count(*) FROM r WHERE has_detail AND sole_source),
        (SELECT count(*) FROM r WHERE has_detail AND set_aside_used),
        (SELECT y FROM fy),
        (SELECT amount FROM fy_totals, fy WHERE series = 'region' AND fiscal_year = fy.y),
        (SELECT amount FROM fy_totals, fy WHERE series = 'small_business' AND fiscal_year = fy.y),
        (SELECT count(*) FROM awards WHERE in_region),
        (SELECT count(*) FROM awards WHERE in_region AND detail_at IS NULL
                AND end_date >= current_date
                AND end_date < current_date + make_interval(months => coalesce(setting('runway_months'), '24')::int))
$$;
