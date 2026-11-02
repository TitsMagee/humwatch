-- HumWatch database. Works on MySQL 5.7+ and MariaDB 10.3+.
-- All times are UTC seconds since 1970.

CREATE TABLE IF NOT EXISTS mic_periods (
    id VARCHAR(64) NOT NULL PRIMARY KEY,
    first_t INT UNSIGNED NOT NULL,
    last_t INT UNSIGNED NOT NULL,
    calibrated TINYINT(1) NOT NULL DEFAULT 0
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS minutes (
    t INT UNSIGNED NOT NULL PRIMARY KEY,
    mic_period VARCHAR(64) NOT NULL,
    n_frames SMALLINT UNSIGNED NOT NULL,
    state VARCHAR(8) NOT NULL,
    provisional TINYINT(1) NOT NULL DEFAULT 0,
    fault VARCHAR(20) NULL,
    calibrated TINYINT(1) NOT NULL DEFAULT 0,
    l90 FLOAT NULL,
    l50 FLOAT NULL,
    leq FLOAT NULL,
    lmax FLOAT NULL,
    wide_l90 FLOAT NULL,
    wide_leq FLOAT NULL,
    sub_l50 FLOAT NULL,
    excess FLOAT NULL,
    wind TINYINT(1) NULL,
    groups_present TINYINT UNSIGNED NULL,
    tones TEXT NULL,
    KEY idx_period_t (mic_period, t)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- Unbroken stretches of one state, kept up to date by cron/rollup.php
CREATE TABLE IF NOT EXISTS runs (
    start_t INT UNSIGNED NOT NULL PRIMARY KEY,
    end_t INT UNSIGNED NOT NULL,
    state VARCHAR(8) NOT NULL,
    mic_period VARCHAR(64) NOT NULL,
    provisional TINYINT(1) NOT NULL DEFAULT 0,
    -- What ended it: a state, data_gap, new_mic, or NULL while still open
    ended_by VARCHAR(10) NULL,
    KEY idx_state (state, mic_period)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- Clock-aligned 15 minute blocks, used for records and charts
CREATE TABLE IF NOT EXISTS quarters (
    t INT UNSIGNED NOT NULL PRIMARY KEY,
    mic_period VARCHAR(64) NOT NULL,
    minutes_seen TINYINT UNSIGNED NOT NULL,
    minutes_on TINYINT UNSIGNED NOT NULL,
    any_wind TINYINT(1) NOT NULL DEFAULT 0,
    l90 FLOAT NULL,
    excess FLOAT NULL,
    KEY idx_period_l90 (mic_period, l90)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- Candidate sounds for a person to judge. Never published automatically.
CREATE TABLE IF NOT EXISTS events (
    id VARCHAR(40) NOT NULL PRIMARY KEY,
    t INT UNSIGNED NOT NULL,
    kind VARCHAR(8) NOT NULL,
    mic_period VARCHAR(64) NOT NULL,
    band_db FLOAT NULL,
    wide_db FLOAT NULL,
    has_clip TINYINT(1) NOT NULL DEFAULT 0,
    clip_file VARCHAR(64) NULL,
    label VARCHAR(32) NULL,
    note VARCHAR(255) NULL,
    reviewed_at INT UNSIGNED NULL,
    KEY idx_review (reviewed_at, t)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS login_attempts (
    who CHAR(64) NOT NULL,
    t INT UNSIGNED NOT NULL,
    KEY idx_who (who, t)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS meta (
    k VARCHAR(32) NOT NULL PRIMARY KEY,
    v VARCHAR(255) NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
