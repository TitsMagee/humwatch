-- Adds the rhythmic hiss figures to a database created before November 2026.
-- Run once, in phpMyAdmin or with: mysql DATABASE < 2026-11-hiss.sql
-- A new database made from schema.sql already has these columns.

ALTER TABLE minutes
    ADD COLUMN hiss_l50 FLOAT NULL AFTER sub_l50,
    ADD COLUMN pulse_hz FLOAT NULL AFTER hiss_l50,
    ADD COLUMN pulse_db FLOAT NULL AFTER pulse_hz,
    ADD COLUMN pulsing TINYINT(1) NULL AFTER pulse_db;
