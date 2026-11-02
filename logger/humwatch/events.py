"""Spots sounds that rise above the usual level, and saves clips for a person to judge.

Nothing here decides what a sound was. A lorry, a yard machine and a plane all look
alike at this level, so events are only ever candidates for review.
"""

import logging
import shutil
import time
from collections import deque
from pathlib import Path

import numpy as np
from scipy.signal import resample_poly

logger = logging.getLogger("humwatch")

WIDE = "wide"  # rise above the hum band, e.g. yard work, lorries, machinery hiss
BAND = "band"  # rise inside the hum band, e.g. a motor running for a while

HISTORY_SECONDS = 600
MIN_HISTORY_SECONDS = 60


class _Rise:
    def __init__(self, rise_db, min_seconds):
        self.rise_db = rise_db
        self.min_seconds = min_seconds
        self.history = deque(maxlen=HISTORY_SECONDS)
        self.run = 0

    def add(self, level):
        """True on the frame where a sustained rise is first confirmed."""
        fired = False
        if len(self.history) >= MIN_HISTORY_SECONDS:
            if level >= float(np.median(self.history)) + self.rise_db:
                self.run += 1
                fired = self.run == self.min_seconds
                # A level that never comes back down is the new normal, not an event
                if self.run >= HISTORY_SECONDS:
                    self.history.clear()
                    self.run = 0
            else:
                self.run = 0
        # Hold the rise out of its own reference, or a long event hides itself
        if self.run == 0:
            self.history.append(level)
        return fired


class EventDetector:
    def __init__(self, cfg):
        section = cfg["clips"]
        rise, seconds = float(section["rise_db"]), int(section["min_seconds"])
        self.wide = _Rise(rise, seconds)
        self.band = _Rise(rise, seconds)

    def add(self, frame):
        """Return the kind of event this frame confirms, or None."""
        wide = self.wide.add(frame.wide_db)
        band = self.band.add(frame.band_db)
        if wide:
            return WIDE
        if band:
            return BAND
        return None


class ClipRecorder:
    """Keeps a short rolling buffer, then writes FLAC once the post-roll is in."""

    def __init__(self, cfg, store):
        section = cfg["clips"]
        self.enabled = bool(section["enabled"])
        self.store = store
        self.sample_rate = cfg["audio"]["sample_rate"]
        self.decimation = int(section["decimation"])
        self.pre_seconds = int(section["pre_seconds"])
        self.post_seconds = int(section["post_seconds"])
        self.max_per_day = int(section["max_per_day"])
        self.min_gap = int(section["min_gap_seconds"])
        self.retention_days = int(section["retention_days"])
        self.min_free_mb = int(section["min_free_mb"])
        self.dir = Path(cfg["paths"]["data_dir"]) / "clips"
        self.ring = deque(maxlen=max(1, self.pre_seconds))
        self.active = None
        self.last_start = {}
        self.day = None
        self.today = 0

    def add_audio(self, new_samples):
        """Feed each second of new audio. Returns a finished event id, if any."""
        if not self.enabled:
            return None
        if self.active is None:
            self.ring.append(new_samples.copy())
            return None
        self.active["chunks"].append(new_samples.copy())
        self.active["left"] -= 1
        if self.active["left"] <= 0:
            return self._finish()
        return None

    def start(self, event_id, kind, t):
        """Begin a clip. Returns True if this event will have one."""
        if not self.enabled or self.active is not None:
            return False
        day = int(t // 86400)
        if day != self.day:
            self.day, self.today = day, 0
        if self.today >= self.max_per_day:
            logger.info("Clip for %s skipped: daily cap reached", event_id)
            return False
        # One drawn-out event should not use up the day's cap on itself
        if t - self.last_start.get(kind, -self.min_gap) < self.min_gap:
            return False
        self.dir.mkdir(parents=True, exist_ok=True)
        if shutil.disk_usage(self.dir).free < self.min_free_mb * 1024 * 1024:
            logger.warning("Clip for %s skipped: disk nearly full", event_id)
            return False
        self.today += 1
        self.last_start[kind] = t
        self.active = {
            "id": event_id,
            "chunks": list(self.ring),
            "left": self.post_seconds,
            "created": int(t),
        }
        self.ring.clear()
        return True

    def _finish(self):
        import soundfile

        clip, self.active = self.active, None
        audio = np.concatenate(clip["chunks"])
        if self.decimation > 1:
            audio = resample_poly(audio, 1, self.decimation)
        path = self.dir / f"{clip['id']}.flac"
        soundfile.write(
            str(path),
            np.clip(audio, -1.0, 1.0),
            self.sample_rate // self.decimation,
            format="FLAC",
            subtype="PCM_16",
        )
        self.store.add_clip(clip["id"], str(path), clip["created"])
        return clip["id"]

    def cleanup(self, now=None):
        """Delete clips past retention: they are personal data as much as evidence."""
        now = time.time() if now is None else now
        for event_id, path in self.store.expired_clips(now - self.retention_days * 86400):
            Path(path).unlink(missing_ok=True)
            self.store.forget_clip(event_id)
