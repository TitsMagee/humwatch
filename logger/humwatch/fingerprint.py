"""Measures each tone group against the stopped baseline and its neighbours."""

import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .spectrum import to_db

NEIGHBOUR_HZ = 20.0
GUARD_HZ = 3.0
# Tones wander a little between the 0.5 Hz bins, so groups read slightly wider
SEARCH_MARGIN_HZ = 1.5


@dataclass
class Baseline:
    mic_period: str
    captured_at: int
    freqs: np.ndarray
    spectrum_db: np.ndarray
    band_l90_db: float
    sub_db: float
    # Estimated from the source running, not measured during a stop
    interim: bool = False


def load_baseline(path, mic_period):
    """The baseline for this mic period, or None.

    A baseline from another period is refused: its levels mean nothing now.
    """
    path = Path(path)
    if not path.exists():
        return None
    with open(path, encoding="utf-8") as handle:
        raw = json.load(handle)
    if raw.get("mic_period") != mic_period:
        return None
    return Baseline(
        mic_period=raw["mic_period"],
        captured_at=int(raw["captured_at"]),
        freqs=np.array(raw["freqs"], dtype=float),
        spectrum_db=np.array(raw["spectrum_db"], dtype=float),
        band_l90_db=float(raw["band_l90_db"]),
        sub_db=float(raw["sub_db"]),
        interim=bool(raw.get("interim", False)),
    )


def save_baseline(path, baseline):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    with open(temp, "w", encoding="utf-8") as handle:
        json.dump(
            {
                "mic_period": baseline.mic_period,
                "captured_at": baseline.captured_at,
                "freqs": [float(f) for f in baseline.freqs],
                "spectrum_db": [round(float(v), 2) for v in baseline.spectrum_db],
                "band_l90_db": round(baseline.band_l90_db, 2),
                "sub_db": round(baseline.sub_db, 2),
                "interim": baseline.interim,
            },
            handle,
        )
    temp.replace(path)


class ToneMeter:
    def __init__(self, cfg, freqs, baseline=None):
        section = cfg["fingerprint"]
        self.freqs = np.asarray(freqs)
        self.groups = [tuple(group) for group in section["tone_groups"]]
        self.excess_db = float(section["excess_db"])
        self.prominence_db = float(section["prominence_db"])
        self.baseline = baseline
        if baseline is not None and len(baseline.freqs) != len(self.freqs):
            raise ValueError("baseline was captured with a different band or sample rate")

    def measure(self, spectrum_db):
        """One entry per tone group for a minute's median spectrum in dB."""
        tones = []
        for low, high in self.groups:
            inside = np.flatnonzero(
                (self.freqs >= low - SEARCH_MARGIN_HZ) & (self.freqs <= high + SEARCH_MARGIN_HZ)
            )
            peak = int(inside[np.argmax(spectrum_db[inside])])
            freq = float(self.freqs[peak])
            level = float(spectrum_db[peak])

            distance = np.abs(self.freqs - freq)
            around = spectrum_db[(distance <= NEIGHBOUR_HZ) & (distance > GUARD_HZ)]
            prominence = level - float(np.median(around)) if around.size else None

            excess = None
            if self.baseline is not None:
                excess = level - float(self.baseline.spectrum_db[inside].max())

            # Excess alone also fires when everything is louder (wind, traffic), and
            # prominence alone fired on a dead mic, so a tone needs both. With no
            # baseline nothing counts as present.
            present = (
                excess is not None
                and prominence is not None
                and excess >= self.excess_db
                and prominence >= self.prominence_db
            )
            tones.append(
                {
                    "low": low,
                    "high": high,
                    "hz": freq,
                    "db": round(level, 1),
                    "excess": None if excess is None else round(excess, 1),
                    "prominence": None if prominence is None else round(prominence, 1),
                    "present": bool(present),
                }
            )
        return tones


def median_spectrum_db(power_frames):
    """Median across a minute: keeps a steady hum, drops passing sounds."""
    return to_db(np.median(np.stack(power_frames), axis=0))
