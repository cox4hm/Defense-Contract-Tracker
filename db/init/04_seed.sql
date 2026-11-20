-- Defense Contract Tracker: starting data (safe to re-run)

-- The Virginia side of Hampton Roads. Independent cities have their own FIPS
-- codes, so USAspending treats each one like a county.
INSERT INTO localities (fips5, county_code, name, kind) VALUES
    ('51550', '550', 'Chesapeake',      'city'),
    ('51650', '650', 'Hampton',         'city'),
    ('51700', '700', 'Newport News',    'city'),
    ('51710', '710', 'Norfolk',         'city'),
    ('51735', '735', 'Poquoson',        'city'),
    ('51740', '740', 'Portsmouth',      'city'),
    ('51800', '800', 'Suffolk',         'city'),
    ('51810', '810', 'Virginia Beach',  'city'),
    ('51830', '830', 'Williamsburg',    'city'),
    ('51073', '073', 'Gloucester',      'county'),
    ('51093', '093', 'Isle of Wight',   'county'),
    ('51095', '095', 'James City',      'county'),
    ('51115', '115', 'Mathews',         'county'),
    ('51199', '199', 'York',            'county')
ON CONFLICT (fips5) DO NOTHING;

INSERT INTO settings (key, value, description) VALUES
    ('reporting_lag_days',   '95',
     'DoD contract data is made public about 90 days after signing. Newer months are marked "still filling in".'),
    ('award_floor',          '1000000',
     'Contracts at least this large (dollars obligated) are stored one by one. Smaller orders still count in the totals.'),
    ('history_years',        '5',
     'How many fiscal years of contract activity to load.'),
    ('runway_months',        '24',
     'How far ahead the recompete runway looks.'),
    ('detail_max_per_run',   '1500',
     'Most award pages to read in one update (each takes about a second). The rest are read in the next update.'),
    ('full_refresh_days',    '7',
     'Re-download every contract this often; other days only download what changed.'),
    ('big_award_threshold',  '100000000',
     'New Hampton Roads contracts at least this large appear in "What changed".'),
    ('kits_subagency',       'Defense Logistics Agency',
     'Awarding sub-agency for the kit program.'),
    ('kits_keywords',        '["Operational Equipment"]',
     'JSON list of words in the kit program''s master-contract descriptions.'),
    ('kits_idv_prefix',      'SPE8EJ',
     'Contract-number prefix of the kit program''s master contracts (DLA Troop Support).')
ON CONFLICT (key) DO NOTHING;
-- version 2.0 searched order descriptions, which never name the program
UPDATE settings SET value = '["Operational Equipment"]', description = 'JSON list of words in the kit program''s master-contract descriptions.'
WHERE key = 'kits_keywords' AND value = '["special operational equipment"]';
DELETE FROM settings WHERE key IN ('region_award_limit', 'award_years', 'history_start');

-- Starter watchlist. search_text can be a company name or its UEI (the
-- government's 12-character company ID), which avoids look-alike names.
INSERT INTO companies (display_name, search_text, note) VALUES
    ('ADS Inc. (Atlantic Diving Supply)', 'ATLANTIC DIVING SUPPLY',
     'Virginia Beach prime contractor on the DLA special operational equipment program'),
    ('Huntington Ingalls Industries', 'HUNTINGTON INGALLS',
     'Newport News Shipbuilding parent; largest employer in the region'),
    ('CACI',                         'CACI',
     'IT and analytics contractor with Norfolk Navy work'),
    ('Serco',                        'SERCO',
     'Navy support contractor in Norfolk')
ON CONFLICT (search_text) DO NOTHING;
