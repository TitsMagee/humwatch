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
MIN_INTERIM_MINUTES = 60


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


def build_interim_baseline(mic_period, freqs, times, spectra_db, minutes):
    """A stand-in for sites that have no recorded stop yet.

    Takes what each frequency would read without narrow tones: the floor either side of
    it, at its quietest across the minutes given. That ignores any broad noise the source
    adds, so it says nothing about how much louder the source makes things.
    """
    usable = {row[0]: row for row in minutes if row[1] is not None and row[4] != "fault"}
    mask = np.array([int(t) in usable for t in times], dtype=bool)
    if mask.sum() < MIN_INTERIM_MINUTES:
        raise ValueError(f"need at least {MIN_INTERIM_MINUTES} usable logged minutes")
    floor = median_filter(spectra_db[mask], size=(1, NEIGHBOUR_BINS), mode="nearest")
    rows = [usable[int(t)] for t in times[mask]]
    return Baseline(
        mic_period=mic_period,
        captured_at=int(times[mask][-1]),
        freqs=np.asarray(freqs),
        spectrum_db=np.percentile(floor, 10, axis=0),
        band_l90_db=float(np.median([row[1] for row in rows])),
        sub_db=float(np.median([row[2] for row in rows if row[2] is not None])),
        interim=True,
    )


def tones_by_prominence(meter, spectra_db):
    """How many tone groups stand out in each minute, needing no baseline."""
    needed = meter.prominence_db
    return np.array(
        [
            sum(tone["prominence"] is not None and tone["prominence"] >= needed for tone in meter.measure(row))
            for row in spectra_db
        ]
    )


def other_lines(freqs, spectrum_db, groups, prominence_db=6.0):
    """Narrow peaks outside the tone groups: a mic that still hears these isn't dead."""
    freqs = np.asarray(freqs)
    above = spectrum_db - median_filter(spectrum_db, size=NEIGHBOUR_BINS, mode="nearest")
    tops = (above[1:-1] >= prominence_db) & (above[1:-1] > above[:-2]) & (above[1:-1] >= above[2:])
    return sum(
        not any(low - 1.5 <= f <= high + 1.5 for low, high in groups) for f in freqs[1:-1][tops]
    )


def find_stops(
    times,
    groups,
    levels,
    max_groups=1,
    min_minutes=20,
    max_gap_minutes=3,
    context_minutes=120,
    level_drop_db=3.0,
    dead_mic_drop_db=20.0,
):
    """Stretches where the tone set vanished together, for a person to judge.

    times are minute starts in order, with groups and levels alongside. Level alone
    can't tell a low-load night from a stop, and tones alone vanish under wind, so a
    stretch is only 'likely' if the tones go and the level falls with them.
    """
    times, groups, levels = np.asarray(times), np.asarray(groups), np.asarray(levels, dtype=float)
    quiet = groups <= max_groups
    found = []
    i = 0
    while i < len(times):
        if not quiet[i]:
            i += 1
            continue
        first = last = i
        j = i + 1
        while j < len(times) and times[j] - times[last] <= (max_gap_minutes + 1) * 60:
            if quiet[j]:
                last = j
            j += 1
        i = last + 1
        start, end = int(times[first]), int(times[last]) + 60
        if (end - start) / 60 < min_minutes:
            continue
        inside = (times >= start) & (times < end)
        around = ~quiet & ~inside & (times >= start - context_minutes * 60) & (times < end + context_minutes * 60)
        drop, groups_around = None, None
        if around.any():
            drop = float(np.median(levels[around]) - np.median(levels[inside]))
            groups_around = float(np.median(groups[around]))
        if drop is None or groups_around < 2:
            verdict = "weak"
        elif drop >= dead_mic_drop_db:
            verdict = "check_mic"
        elif drop >= level_drop_db:
            verdict = "likely"
        else:
            verdict = "weak"
        found.append(
            {
                "start": start,
                "end": end,
                "minutes": (end - start) // 60,
                "level_drop": None if drop is None else round(drop, 1),
                "groups_around": groups_around,
                "groups_during": float(np.median(groups[inside])),
                "verdict": verdict,
            }
        )
    return found
