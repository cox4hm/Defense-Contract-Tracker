-- Defense Contract Tracker (version 2): tables
-- Dollar figures are federal contract OBLIGATIONS (legal promises to pay) as reported to
-- USAspending.gov, unless a column says otherwise. Obligations are not the same as money paid out.

-- Version 1 used a different awards table. Its data is all re-downloadable, so start clean.
DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'awards')
       AND NOT EXISTS (SELECT 1 FROM information_schema.columns
                       WHERE table_name = 'awards' AND column_name = 'potential_end_date') THEN
        DROP TABLE IF EXISTS award_tags, subaward_tags, alerts, awards, subawards, monthly_obligations,
                             locality_fy, category_totals CASCADE;
    END IF;
END $$;

CREATE TABLE IF NOT EXISTS localities (
    fips5        char(5) PRIMARY KEY,          -- state FIPS (51) + county/city FIPS
    county_code  char(3) NOT NULL,             -- the 3-digit code USAspending filters on
    name         text    NOT NULL,
    kind         text    NOT NULL CHECK (kind IN ('city', 'county')),
    active       boolean NOT NULL DEFAULT true
);

CREATE TABLE IF NOT EXISTS settings (
    key          text PRIMARY KEY,
    value        text NOT NULL,
    description  text
);

CREATE TABLE IF NOT EXISTS fetch_runs (
    id            serial PRIMARY KEY,
    source        text NOT NULL DEFAULT 'manual',   -- manual | systemd | cron | docker
    mode          text,                             -- full | incremental | details
    started_at    timestamptz NOT NULL DEFAULT now(),
    finished_at   timestamptz,
    status        text NOT NULL DEFAULT 'running' CHECK (status IN ('running', 'ok', 'partial', 'failed')),
    rows_written  integer NOT NULL DEFAULT 0,
    message       text
);

-- One row per contract award. Search results fill the first block; the award's own page
-- (fetched for the contracts that matter most) fills the second.
CREATE TABLE IF NOT EXISTS awards (
    generated_id          text PRIMARY KEY,        -- USAspending's unique award key
    award_id              text,                    -- the contract number (PIID)
    recipient_name        text,
    recipient_uei         text,
    recipient_hash        text,
    description           text,
    amount                numeric,                 -- obligated so far
    outlays               numeric,                 -- actually paid out so far
    start_date            date,
    end_date              date,                    -- current end of the period of performance
    last_modified         date,
    awarding_subagency    text,                    -- Navy, Army, DLA ...
    funding_subagency     text,
    award_type            text,
    naics_code            text,
    naics_desc            text,
    psc_code              text,
    psc_desc              text,
    county_code           text,
    city                  text,
    in_region             boolean NOT NULL DEFAULT false,   -- performed in Hampton Roads, above the size floor
    region_seen           timestamptz,             -- last time the Hampton Roads download returned it
    in_kits               boolean NOT NULL DEFAULT false,   -- part of the DLA kit program
    -- from the award page
    potential_end_date    date,                    -- end date if every option is exercised
    base_and_all_options  numeric,                 -- ceiling: value if every option is exercised
    awarding_office       text,
    competed_code         text,
    competed              text,                    -- extent competed, e.g. FULL AND OPEN COMPETITION
    offers                integer,                 -- number of bids received
    set_aside_code        text,
    set_aside             text,                    -- small-business set-aside, if any
    pricing               text,                    -- firm fixed price, cost plus ...
    solicitation          text,
    parent_name           text,
    parent_hash           text,
    small_business        boolean,
    subaward_count        integer,
    detail_at             timestamptz,             -- when the award page was last read
    first_seen            timestamptz NOT NULL DEFAULT now(),
    last_seen             timestamptz NOT NULL DEFAULT now()
);

-- Totals the API adds up for us (they include the many small orders we don't store one by one).
CREATE TABLE IF NOT EXISTS monthly_totals (
    series      text NOT NULL,                 -- region | kits
    month       date NOT NULL CHECK (extract(day FROM month) = 1),
    amount      numeric NOT NULL,
    PRIMARY KEY (series, month)
);

CREATE TABLE IF NOT EXISTS fy_totals (
    series       text NOT NULL,                -- region | small_business
    fiscal_year  integer NOT NULL,
    amount       numeric NOT NULL,
    PRIMARY KEY (series, fiscal_year)
);

CREATE TABLE IF NOT EXISTS category_totals (
    category     text NOT NULL,                -- branch | naics | psc | county | recipient | sb_recipient
    fiscal_year  integer NOT NULL,
    code         text NOT NULL DEFAULT '',
    name         text NOT NULL,
    amount       numeric NOT NULL,
    PRIMARY KEY (category, fiscal_year, name)
);

-- Watchlist: companies to follow across all of DoD, not just Hampton Roads.
CREATE TABLE IF NOT EXISTS companies (
    id            serial PRIMARY KEY,
    display_name  text NOT NULL,
    search_text   text NOT NULL UNIQUE,        -- company name or 12-character UEI
    note          text,
    checked_at    timestamptz,
    award_count   integer,
    total_amount  numeric
);

CREATE TABLE IF NOT EXISTS company_awards (
    company_id    integer NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    generated_id  text NOT NULL REFERENCES awards(generated_id) ON DELETE CASCADE,
    PRIMARY KEY (company_id, generated_id)
);

-- The kit program's master contracts and their largest orders (nationwide)
CREATE TABLE IF NOT EXISTS kit_idvs (
    gid          text PRIMARY KEY,
    piid         text,
    holder       text,
    description  text,
    orders       integer,                      -- orders placed under it so far
    obligated    numeric,                      -- obligated on those orders
    order_end    date,                         -- last day to place orders
    checked_at   timestamptz
);

CREATE TABLE IF NOT EXISTS kit_orders (
    gid          text PRIMARY KEY,
    idv_gid      text,
    piid         text,
    holder       text,
    description  text,
    amount       numeric,
    start_date   date,
    end_date     date
);

-- Kit-program subcontracts (who the primes buy from)
CREATE TABLE IF NOT EXISTS subawards (
    subaward_id      text,
    prime_award_id   text,
    prime_recipient  text,
    sub_name         text NOT NULL,
    amount           numeric,
    sub_date         date,
    description      text,
    PRIMARY KEY (prime_award_id, sub_name, sub_date, amount)
);

-- Last outcome of each loader step, so a step that failed (USAspending busy) is retried within the hour
CREATE TABLE IF NOT EXISTS step_status (
    step        text PRIMARY KEY,
    last_ok     timestamptz,
    last_try    timestamptz,
    last_error  text
);

-- Notable changes, written by triggers (see 02_functions.sql)
CREATE TABLE IF NOT EXISTS events (
    id            bigserial PRIMARY KEY,
    kind          text NOT NULL,               -- new_award | extended | value_up | ended
    generated_id  text,
    title         text NOT NULL,
    detail        text,
    amount        numeric,
    created_at    timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS awards_region_end   ON awards (end_date) WHERE in_region;
CREATE INDEX IF NOT EXISTS awards_recipient    ON awards (recipient_hash);
CREATE INDEX IF NOT EXISTS awards_parent       ON awards (parent_hash);
CREATE INDEX IF NOT EXISTS awards_detail_due   ON awards (amount DESC) WHERE detail_at IS NULL;
CREATE INDEX IF NOT EXISTS events_created      ON events (created_at DESC);

-- Upgrading a version 1 install: tables kept from version 1 get the new columns
ALTER TABLE fetch_runs ADD COLUMN IF NOT EXISTS mode text;
ALTER TABLE companies  ADD COLUMN IF NOT EXISTS checked_at timestamptz;
ALTER TABLE companies  ADD COLUMN IF NOT EXISTS award_count integer;
ALTER TABLE companies  ADD COLUMN IF NOT EXISTS total_amount numeric;
