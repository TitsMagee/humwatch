"""Local SQLite store: survives power cuts, unlike appended CSV, and holds the queue."""

import json
import sqlite3
from pathlib import Path

import numpy as np

SCHEMA = """
CREATE TABLE IF NOT EXISTS minutes (
    t INTEGER PRIMARY KEY,
    mic_period TEXT NOT NULL,
    l90 REAL,
    sub_l50 REAL,
    groups_present INTEGER,
    state TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS spectra (
    t INTEGER PRIMARY KEY,
    mic_period TEXT NOT NULL,
    centi_db BLOB NOT NULL
);
CREATE TABLE IF NOT EXISTS outbox (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,
    body TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS clips (
    event_id TEXT PRIMARY KEY,
    path TEXT NOT NULL,
    created INTEGER NOT NULL,
    uploaded INTEGER NOT NULL DEFAULT 0
);
"""


class Store:
    def __init__(self, data_dir):
        Path(data_dir).mkdir(parents=True, exist_ok=True)
        self.path = str(Path(data_dir) / "humwatch.db")
        self.db = sqlite3.connect(self.path, timeout=30)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=NORMAL")
        self.db.executescript(SCHEMA)
        self.db.commit()

    def close(self):
        self.db.close()

    # Minutes and spectra -------------------------------------------------

    def save_minute(self, row, spectrum_db=None):
        """Record a minute and queue it, in one transaction."""
        with self.db:
            self.db.execute(
                "INSERT OR REPLACE INTO minutes VALUES (?,?,?,?,?,?)",
                (row["t"], row["mic_period"], row["l90"], row["sub_l50"], row["groups_present"], row["state"]),
            )
            self.db.execute("INSERT INTO outbox (kind, body) VALUES ('minute', ?)", (json.dumps(row),))
            if spectrum_db is not None:
                blob = np.round(np.clip(spectrum_db, -320, 320) * 100).astype(np.int16).tobytes()
                self.db.execute(
                    "INSERT OR REPLACE INTO spectra VALUES (?,?,?)", (row["t"], row["mic_period"], blob)
                )

    def recent_minutes(self, mic_period, since):
        return self.db.execute(
            "SELECT t, l90, groups_present, state FROM minutes "
            "WHERE mic_period=? AND t>=? ORDER BY t",
            (mic_period, since),
        ).fetchall()

    def minutes_between(self, mic_period, start, end):
        return self.db.execute(
            "SELECT t, l90, sub_l50, groups_present, state FROM minutes "
            "WHERE mic_period=? AND t>=? AND t<? ORDER BY t",
            (mic_period, start, end),
        ).fetchall()

    def spectra(self, mic_period, since=0):
        """Return (times, matrix of dB), one row per minute."""
        rows = self.db.execute(
            "SELECT t, centi_db FROM spectra WHERE mic_period=? AND t>=? ORDER BY t", (mic_period, since)
        ).fetchall()
        if not rows:
            return np.array([]), np.empty((0, 0))
        times = np.array([row[0] for row in rows])
        matrix = np.stack([np.frombuffer(row[1], dtype=np.int16) for row in rows]) / 100.0
        return times, matrix

    # Upload queue --------------------------------------------------------

    def queue_event(self, event):
        with self.db:
            self.db.execute("INSERT INTO outbox (kind, body) VALUES ('event', ?)", (json.dumps(event),))

    def next_batch(self, limit):
        return self.db.execute(
            "SELECT seq, kind, body FROM outbox ORDER BY seq LIMIT ?", (limit,)
        ).fetchall()

    def ack(self, last_seq):
        with self.db:
            self.db.execute("DELETE FROM outbox WHERE seq<=?", (last_seq,))

    # Clips ---------------------------------------------------------------

    def add_clip(self, event_id, path, created):
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO clips VALUES (?,?,?,0)", (event_id, path, created))

    def pending_clips(self, limit=5):
        return self.db.execute(
            "SELECT event_id, path FROM clips WHERE uploaded=0 ORDER BY created LIMIT ?", (limit,)
        ).fetchall()

    def mark_clip_uploaded(self, event_id):
        with self.db:
            self.db.execute("UPDATE clips SET uploaded=1 WHERE event_id=?", (event_id,))

    def expired_clips(self, before):
        return self.db.execute(
            "SELECT event_id, path FROM clips WHERE created<?", (before,)
        ).fetchall()

    def forget_clip(self, event_id):
        with self.db:
            self.db.execute("DELETE FROM clips WHERE event_id=?", (event_id,))

    # Housekeeping --------------------------------------------------------

    def prune_spectra(self, before):
        with self.db:
            self.db.execute("DELETE FROM spectra WHERE t<?", (before,))
