-- Defense Contract Tracker: helper functions and triggers

CREATE OR REPLACE FUNCTION setting(p_key text) RETURNS text
LANGUAGE sql STABLE AS $$
    SELECT value FROM settings WHERE key = p_key
$$;

-- Federal fiscal year: FY2027 runs Oct 1 2026 – Sep 30 2027
CREATE OR REPLACE FUNCTION fiscal_year(d date) RETURNS integer
LANGUAGE sql IMMUTABLE AS $$
    SELECT extract(year FROM d)::int + CASE WHEN extract(month FROM d) >= 10 THEN 1 ELSE 0 END
$$;

-- DoD contract data is published about 90 days after signing, so recent months are
-- incomplete. A month is "complete" once its last day is older than the reporting lag.
CREATE OR REPLACE FUNCTION month_is_complete(m date) RETURNS boolean
LANGUAGE sql STABLE AS $$
    SELECT (m + interval '1 month' - interval '1 day')::date
           < current_date - coalesce(setting('reporting_lag_days'), '95')::int
$$;

-- The latest fiscal year whose data is complete (its last month is past the reporting lag)
CREATE OR REPLACE FUNCTION last_full_fy() RETURNS integer
LANGUAGE sql STABLE AS $$
    SELECT fiscal_year(current_date - coalesce(setting('reporting_lag_days'), '95')::int) - 1
$$;

-- Short, familiar names for the buying organization
CREATE OR REPLACE FUNCTION branch_label(subagency text) RETURNS text
LANGUAGE sql IMMUTABLE AS $$
    SELECT CASE
        WHEN subagency IS NULL THEN 'Other DoD'
        WHEN subagency ILIKE '%navy%' THEN 'Navy'
        WHEN subagency ILIKE '%army%' THEN 'Army'
        WHEN subagency ILIKE '%air force%' THEN 'Air Force'
        WHEN subagency ILIKE '%logistics agency%' THEN 'DLA'
        WHEN subagency ILIKE '%information systems agency%' THEN 'DISA'
        ELSE 'Other DoD'
    END
$$;

-- Change alerts start after the first complete load, so loading history doesn't flood the feed.
CREATE OR REPLACE FUNCTION alerts_armed() RETURNS boolean
LANGUAGE sql STABLE AS $$
    SELECT EXISTS (SELECT 1 FROM fetch_runs WHERE status IN ('ok', 'partial') AND mode IN ('full', 'incremental'))
$$;

CREATE OR REPLACE FUNCTION money_text(v numeric) RETURNS text
LANGUAGE sql IMMUTABLE AS $$
    SELECT CASE
        WHEN v IS NULL THEN '–'
        WHEN abs(v) >= 1e9 THEN '$' || to_char(v / 1e9, 'FM999,990.0') || 'B'
        WHEN abs(v) >= 1e6 THEN '$' || to_char(v / 1e6, 'FM999,990.0') || 'M'
        ELSE '$' || to_char(v / 1e3, 'FM999,990') || 'K'
    END
$$;

-- Turn daily snapshots into a history of what changed (USAspending only shows the current state)
CREATE OR REPLACE FUNCTION award_event() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    big  numeric := coalesce(setting('big_award_threshold'), '100000000')::numeric;
    who  text := coalesce(NEW.parent_name, NEW.recipient_name, 'a contractor');
BEGIN
    IF NOT NEW.in_region OR NOT alerts_armed() THEN
        RETURN NEW;
    END IF;
    IF TG_OP = 'INSERT' OR NOT OLD.in_region THEN
        IF NEW.amount >= big THEN
            INSERT INTO events (kind, generated_id, title, detail, amount)
            VALUES ('new_award', NEW.generated_id,
                    money_text(NEW.amount) || ' ' || branch_label(NEW.awarding_subagency) || ' contract to ' || who,
                    NEW.description, NEW.amount);
        END IF;
        RETURN NEW;
    END IF;
    IF NEW.end_date >= OLD.end_date + 30 THEN
        INSERT INTO events (kind, generated_id, title, detail, amount)
        VALUES ('extended', NEW.generated_id,
                who || ' contract extended to ' || to_char(NEW.end_date, 'FMMon YYYY'),
                'Was ending ' || to_char(OLD.end_date, 'FMMon YYYY') || '. ' || coalesce(NEW.description, ''),
                NEW.amount);
    END IF;
    IF NEW.amount - OLD.amount >= greatest(10e6, 0.1 * OLD.amount) THEN
        INSERT INTO events (kind, generated_id, title, detail, amount)
        VALUES ('value_up', NEW.generated_id,
                who || ' contract grew by ' || money_text(NEW.amount - OLD.amount),
                'Now ' || money_text(NEW.amount) || ' obligated. ' || coalesce(NEW.description, ''),
                NEW.amount - OLD.amount);
    END IF;
    RETURN NEW;
END $$;

DROP TRIGGER IF EXISTS award_event_trg ON awards;
CREATE TRIGGER award_event_trg AFTER INSERT OR UPDATE ON awards
    FOR EACH ROW EXECUTE FUNCTION award_event();

-- version 1 objects
DROP FUNCTION IF EXISTS award_tag_alert() CASCADE;
DROP FUNCTION IF EXISTS subaward_tag_alert() CASCADE;
DROP FUNCTION IF EXISTS fmt_money(numeric) CASCADE;
