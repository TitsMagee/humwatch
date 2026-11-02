"""Decides whether the source is on, off or unknown, once a minute.

The rule was tested at one site only (docs/DESIGN.md). Level alone could not separate
a low-load night from a stop there, and any single tone drifted away over weeks, so
it counts tone groups and only uses level to confirm an OFF.
"""

from collections import deque
from statistics import median

ON = "on"
OFF = "off"
UNKNOWN = "unknown"
FAULT = "fault"


class Detector:
    def __init__(self, cfg):
        det = cfg["detector"]
        self.window = int(det["window_minutes"])
        self.min_groups = int(det["min_groups"])
        self.on_fraction = float(det["on_fraction"])
        self.off_fraction = float(det["off_fraction"])
        self.level_drop = float(det["off_level_drop_db"])
        self.validated = bool(det["validated"])
        self.recent = deque(maxlen=self.window)
        self.state = UNKNOWN

    def update(self, groups_present, level, reference, fault=False):
        """Feed one minute. groups_present is None when there is no usable reading.

        reference is the usual level (see ReferenceLevel), or None before there is
        enough history. OFF needs it: tones vanishing with no drop in level is more
        often a measurement problem than a stop.
        """
        if fault:
            # A broken mic must never read as a quiet source
            self.recent.append(None)
            self.state = UNKNOWN
            return FAULT

        self.recent.append(None if groups_present is None else groups_present >= self.min_groups)
        known = [hit for hit in self.recent if hit is not None]
        if len(known) < self.window / 3:
            self.state = UNKNOWN
            return self.state

        fraction = sum(known) / len(known)
        if fraction >= self.on_fraction:
            self.state = ON
        elif fraction <= self.off_fraction:
            if reference is not None and level is not None and level <= reference - self.level_drop:
                self.state = OFF
        return self.state

    @property
    def provisional(self):
        """An OFF that nobody has validated must not be published as fact."""
        return self.state == OFF and not self.validated


class ReferenceLevel:
    """The usual level: median of hourly median levels over the last few days."""

    MIN_HOURS = 24

    def __init__(self, days=7):
        self.hours = deque(maxlen=int(days * 24))
        self._hour = None
        self._levels = []

    def add(self, t, level):
        hour = int(t // 3600)
        if hour != self._hour:
            if self._levels:
                self.hours.append(median(self._levels))
            self._hour, self._levels = hour, []
        if level is not None:
            self._levels.append(level)

    @property
    def value(self):
        return median(self.hours) if len(self.hours) >= self.MIN_HOURS else None
