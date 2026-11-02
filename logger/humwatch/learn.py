"""Works out a site's fingerprint and baseline from logged minutes and known stops.

A person supplies the stop times, because only someone there can know them. These tools
then find the tones that come and go with the source, and report how a detector using
them would have done.
"""

import csv
from datetime import datetime

import numpy as np
from scipy.ndimage import median_filter

from .fingerprint import Baseline

BIN_HZ = 2.0
MIN_RUNNING_FRACTION = 0.08
MAX_STOPPED_FRACTION = 0.10
NEIGHBOUR_BINS = 81  # about 40 Hz at 0.5 Hz per bin
MIN_STOP_MINUTES = 30


def read_windows(path):
    """Read 'start,end' rows of ISO times. Times with no zone are taken as local."""
    windows = []
    with open(path, newline="", encoding="utf-8") as handle:
        for row in csv.reader(handle):
            if not row or row[0].lstrip().startswith("#"):
                continue
            start = datetime.fromisoformat(row[0].strip()).astimezone().timestamp()
            end = datetime.fromisoformat(row[1].strip()).astimezone().timestamp()
            if end <= start:
                raise ValueError(f"stop ends before it starts: {row}")
            windows.append((start, end))
    return windows


def _inside(times, windows):
    mask = np.zeros(len(times), dtype=bool)
    for start, end in windows:
        mask |= (times >= start) & (times < end)
    return mask


def learn_groups(freqs, times, spectra_db, stops, prominence_db=6.0, margin_seconds=7200):
    """Find tone groups: frequent while running, rare while stopped.

    Minutes close to a stop are left out of 'running', because stop times are rarely
    exact. Returns (groups, stats).
    """
    stopped = _inside(times, stops)
    if stopped.sum() < MIN_STOP_MINUTES:
        raise ValueError(f"need at least {MIN_STOP_MINUTES} logged minutes inside known stops")
    near = _inside(times, [(a - margin_seconds, b + margin_seconds) for a, b in stops])
    running = ~near
    if not running.any():
        raise ValueError("no running minutes away from the stops")

    floor = median_filter(spectra_db, size=(1, NEIGHBOUR_BINS), mode="nearest")
    peaks = spectra_db - floor >= prominence_db

    edges = np.arange(freqs[0], freqs[-1] + BIN_HZ, BIN_HZ)
    which = np.minimum(np.digitize(freqs, edges) - 1, len(edges) - 2)
    chosen, stats = [], []
    for index in range(len(edges) - 1):
        members = which == index
        if not members.any():
            continue
        hits = peaks[:, members].any(axis=1)
        run_fraction = float(hits[running].mean())
        stop_fraction = float(hits[stopped].mean())
        if run_fraction >= MIN_RUNNING_FRACTION and stop_fraction <= MAX_STOPPED_FRACTION:
            chosen.append(index)
            stats.append((float(edges[index]), run_fraction, stop_fraction))

    groups = []
    for index in chosen:
        low, high = float(edges[index]), float(edges[index + 1])
        if groups and low - groups[-1][1] <= 1e-9:
            groups[-1][1] = high
        else:
            groups.append([low, high])
    return groups, stats


def build_baseline(mic_period, freqs, times, spectra_db, minutes, stops):
    """Baseline from minutes inside known stops.

    minutes are (t, l90, sub_l50, groups_present, state) rows.
    """
    mask = _inside(times, stops)
    if mask.sum() < MIN_STOP_MINUTES:
        raise ValueError(f"need at least {MIN_STOP_MINUTES} logged minutes inside known stops")
    levels = [
        row
        for row in minutes
        if row[1] is not None and any(start <= row[0] < end for start, end in stops)
    ]
    if not levels:
        raise ValueError("no level readings inside the stops")
    return Baseline(
        mic_period=mic_period,
        captured_at=int(times[mask][0]),
        freqs=np.asarray(freqs),
        spectrum_db=np.median(spectra_db[mask], axis=0),
        band_l90_db=float(np.median([row[1] for row in levels])),
        sub_db=float(np.median([row[2] for row in levels if row[2] is not None])),
    )


def validate(minutes, stops, margin_seconds=7200):
    """Score logged states against stops a person has confirmed."""
    inside = {"on": 0, "off": 0, "other": 0}
    outside = {"on": 0, "off": 0, "other": 0}
    for t, _, _, _, state in minutes:
        if any(a <= t < b for a, b in stops):
            bucket = inside
        elif any(a - margin_seconds <= t < b + margin_seconds for a, b in stops):
            continue
        else:
            bucket = outside
        bucket[state if state in ("on", "off") else "other"] += 1
    return {"during_stops": inside, "away_from_stops": outside}
