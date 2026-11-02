"""Reads a microphone calibration file.

These are plain text, one "frequency dB [phase]" per line, in the form REW reads.
The header wording differs between makers, so a sensitivity line is reported but
never applied automatically: the sign convention has to be checked per mic.
"""

import re

import numpy as np

_SENS = re.compile(r"sens(?:itivity)?(?:\s*factor)?\s*[=:]\s*(-?\d+(?:\.\d+)?)", re.IGNORECASE)


def parse(text):
    """Return (freqs, db, sensitivity_or_None)."""
    rows, sensitivity = [], None
    for line in text.splitlines():
        match = _SENS.search(line)
        if match and sensitivity is None:
            sensitivity = float(match.group(1))
        parts = line.replace(",", " ").split()
        if len(parts) < 2:
            continue
        try:
            rows.append((float(parts[0]), float(parts[1])))
        except ValueError:
            continue
    if len(rows) < 2:
        raise ValueError("no frequency/dB rows found in calibration file")
    rows.sort()
    freqs, db = zip(*rows)
    return np.array(freqs), np.array(db), sensitivity


def correction_db(path, freqs):
    """dB to subtract from a measured level at each frequency in freqs.

    Outside the file's range the end value is held rather than extrapolated.
    """
    if not path:
        return np.zeros(len(freqs))
    with open(path, encoding="utf-8", errors="replace") as handle:
        cal_freqs, cal_db, _ = parse(handle.read())
    return np.interp(freqs, cal_freqs, cal_db)
