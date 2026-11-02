"""One-second analysis frames from overlapping 2 s Hann windows.

Levels are dB re full scale, where a full-scale sine reads 0 dB. With a measured
spl_offset_db they become unweighted dB SPL. None of these figures are dB(A).
"""

from dataclasses import dataclass

import numpy as np

from . import calibration

WINDOW_SECONDS = 2
EPS = 1e-20


def to_db(power):
    return 10 * np.log10(np.maximum(power, EPS))


@dataclass
class Frame:
    t: float
    band_power: np.ndarray  # power per FFT bin inside the band
    band_db: float
    wide_db: float
    sub_db: float
    clip_fraction: float
    digital_silence: bool


class Analyzer:
    def __init__(self, cfg):
        self.sample_rate = cfg["audio"]["sample_rate"]
        self.window_length = WINDOW_SECONDS * self.sample_rate
        self.hann = np.hanning(self.window_length)
        freqs = np.fft.rfftfreq(self.window_length, 1 / self.sample_rate)
        analysis = cfg["analysis"]
        self._band = self._slice(freqs, analysis["band_hz"])
        self._wide = self._slice(freqs, analysis["wide_hz"])
        self._sub = self._slice(freqs, analysis["sub_hz"])
        self.band_freqs = freqs[self._band]

        # Sine of amplitude A: |X|^2 peaks at (A * sum(w) / 2)^2, so this scale makes
        # A = 1 read 1.0 in its peak bin. Summing bins also counts the window's spread
        # of that tone, which the ENBW divides back out.
        self._scale = 4.0 / np.sum(self.hann) ** 2
        self._enbw = self.window_length * np.sum(self.hann**2) / np.sum(self.hann) ** 2

        offset = cfg["calibration"]["spl_offset_db"]
        correction = calibration.correction_db(cfg["calibration"]["file"], freqs)
        self._gain = 10 ** ((float(offset or 0) - correction) / 10)

    @staticmethod
    def _slice(freqs, pair):
        indices = np.flatnonzero((freqs >= pair[0]) & (freqs <= pair[1]))
        return slice(int(indices[0]), int(indices[-1]) + 1)

    def process(self, t, samples):
        samples = np.asarray(samples, dtype=np.float64)
        spectrum = np.fft.rfft(samples * self.hann)
        power = (spectrum.real**2 + spectrum.imag**2) * self._scale * self._gain
        band = power[self._band]
        return Frame(
            t=t,
            band_power=band.astype(np.float32),
            band_db=float(to_db(band.sum() / self._enbw)),
            wide_db=float(to_db(power[self._wide].sum() / self._enbw)),
            sub_db=float(to_db(power[self._sub].sum() / self._enbw)),
            clip_fraction=float(np.mean(np.abs(samples) >= 0.999)),
            digital_silence=bool(np.all(samples == samples[0])),
        )
