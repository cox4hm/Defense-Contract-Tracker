-- Defense Contract Tracker: example queries to run in psql
--   docker compose exec -T db psql -U defense defense_tracker < db/demo_queries.sql

-- 1. The headline numbers
SELECT money_text(runway_amount) AS ending_in_two_years, runway_count, money_text(next6_amount) AS next_six_months,
       sole_source, detailed, details_pending
FROM headline();

-- 2. The 15 largest contracts reaching their final end date in the next two years
SELECT to_char(final_end, 'Mon YYYY') AS final_end, company, branch, money_text(amount) AS obligated,
       CASE WHEN sole_source THEN 'not competed' WHEN has_detail THEN coalesce(offers::text, '?') || ' bids' END AS last_time,
       left(description, 60) AS work
FROM v_runway ORDER BY amount DESC LIMIT 15;

-- 3. Which companies have the most work ending, as a share of what they hold
SELECT company, money_text(amount) AS holds, money_text(ending_amount) AS ending,
       round(100 * ending_amount / nullif(amount, 0)) AS pct_ending
FROM v_companies WHERE amount > 100e6 ORDER BY ending_amount DESC LIMIT 15;

-- 4. Sole-source contracts coming up: candidates for a first competition
SELECT company, branch, money_text(amount) AS obligated, to_char(final_end, 'Mon YYYY') AS final_end,
       left(description, 60) AS work
FROM v_runway WHERE sole_source ORDER BY amount DESC LIMIT 15;

-- 5. The September "use it or lose it" rush: September vs the average month, by fiscal year
SELECT fiscal_year,
       money_text(max(amount) FILTER (WHERE extract(month FROM month) = 9)) AS september,
       money_text(avg(amount)) AS avg_month,
       round(max(amount) FILTER (WHERE extract(month FROM month) = 9) / avg(amount), 2) AS ratio
FROM v_region_monthly WHERE complete
GROUP BY fiscal_year HAVING count(*) = 12
ORDER BY fiscal_year;

-- 6. Small businesses' share of the region's contract dollars
SELECT r.fiscal_year, money_text(r.amount) AS region, money_text(s.amount) AS small_business,
       round(100 * s.amount / nullif(r.amount, 0), 1) AS pct
FROM fy_totals r JOIN fy_totals s ON s.series = 'small_business' AND s.fiscal_year = r.fiscal_year
WHERE r.series = 'region' ORDER BY 1;

-- 7. Spending by branch, last complete fiscal year
SELECT branch_label(name) AS branch, money_text(sum(amount)) AS obligated
FROM category_totals WHERE category = 'branch' AND fiscal_year = last_full_fy()
GROUP BY 1 ORDER BY sum(amount) DESC;

-- 8. What changed recently
SELECT created_at::date, kind, title FROM events ORDER BY created_at DESC LIMIT 20;

-- 9. How the updates went
SELECT id, mode, source, status, rows_written, finished_at - started_at AS took, left(message, 80) AS message
FROM fetch_runs ORDER BY id DESC LIMIT 10;
