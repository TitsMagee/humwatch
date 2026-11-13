"""Rolls one-second frames up into one summary per wall-clock minute."""

from dataclasses import dataclass, field

import numpy as np

from .fingerprint import median_spectrum_db
from .pulse import median_envelope
from .spectrum import to_db

MIN_FRAMES = 30


@dataclass
class Minute:
    t: int
    n_frames: int
    l90: float | None = None
    l50: float | None = None
    leq: float | None = None
    lmax: float | None = None
    wide_l90: float | None = None
    wide_leq: float | None = None
    sub_l50: float | None = None
    hiss_l50: float | None = None
    silent_fraction: float = 0.0
    clip_fraction: float = 0.0
    spectrum_db: np.ndarray | None = field(default=None, repr=False)
    pulse_power: np.ndarray | None = field(default=None, repr=False)

    @property
    def usable(self):
        return self.n_frames >= MIN_FRAMES


def _leq(values):
    return float(to_db(np.mean(10 ** (np.asarray(values) / 10))))


def summarise(t, frames):
    if not frames:
        return Minute(t=t, n_frames=0)
    band = [f.band_db for f in frames]
    wide = [f.wide_db for f in frames]
    hiss = [f.hiss_db for f in frames if f.hiss_db is not None]
    pulses = [f.pulse_power for f in frames if f.pulse_power is not None]
    return Minute(
        t=t,
        n_frames=len(frames),
        l90=float(np.percentile(band, 10)),
        l50=float(np.percentile(band, 50)),
        leq=_leq(band),
        lmax=float(np.max(band)),
        wide_l90=float(np.percentile(wide, 10)),
        wide_leq=_leq(wide),
        sub_l50=float(np.percentile([f.sub_db for f in frames], 50)),
        silent_fraction=float(np.mean([f.digital_silence for f in frames])),
        clip_fraction=float(np.mean([f.clip_fraction for f in frames])),
        spectrum_db=median_spectrum_db([f.band_power for f in frames]),
        hiss_l50=float(np.percentile(hiss, 50)) if hiss else None,
        pulse_power=median_envelope(pulses) if pulses else None,
    )


class MinuteBuilder:
    def __init__(self):
        self._minute = None
        self._frames = []

    def add(self, frame):
        """Add a frame. Returns any minutes this closes."""
        finished = self.tick(frame.t)
        self._frames.append(frame)
        return finished

    def tick(self, now):
        """Close minutes that have passed, including empty ones during an outage."""
        finished = []
        minute = int(now // 60) * 60
        if self._minute is None:
            self._minute = minute
        while self._minute < minute:
            finished.append(summarise(self._minute, self._frames))
            self._frames = []
            self._minute += 60
        return finished
