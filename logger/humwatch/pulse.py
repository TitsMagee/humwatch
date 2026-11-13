"""The rhythm in a hiss: how strongly its loudness pulses, and how many times a second.

Some sources add a broadband hiss that swells and fades at a steady rate, such as a
screen or sieve shaking a few times a second. Its pitch is too high and too spread
out to show as a tone, but its loudness rises and falls with the machine. So each
analysis window is filtered to the hiss band, its envelope taken, and the envelope's
own spectrum kept. A steady pulse is a peak in that spectrum.
"""

import numpy as np

ENVELOPE_RATE = 200
# The envelope spectrum kept per frame, and the reference its peak is measured against
KEEP_HZ = (1.0, 20.0)


class PulseMeter:
    """Turns one analysis window into its hiss envelope's spectrum."""

    def __init__(self, cfg, sample_rate, window_length):
        low, high = cfg["hiss"]["band_hz"]
        freqs = np.fft.rfftfreq(window_length, 1 / sample_rate)
        self._mask = (freqs >= low) & (freqs <= high)
        self._step = max(1, round(sample_rate / ENVELOPE_RATE))
        self._length = window_length // self._step
        rate = sample_rate / self._step
        self._hann = np.hanning(self._length)
        envelope_freqs = np.fft.rfftfreq(self._length, 1 / rate)
        self._keep = np.flatnonzero((envelope_freqs >= KEEP_HZ[0]) & (envelope_freqs <= KEEP_HZ[1]))
        self.freqs = envelope_freqs[self._keep]

    def envelope_power(self, samples):
        """The hiss envelope's power spectrum, relative to its mean level squared.

        Relative, so a louder hiss with the same rhythm gives the same figures.
        """
        spectrum = np.fft.rfft(samples)
        spectrum[~self._mask] = 0
        hiss = np.fft.irfft(spectrum, len(samples))
        envelope = np.abs(hiss[: self._length * self._step]).reshape(self._length, self._step).mean(axis=1)
        mean = envelope.mean()
        if mean <= 0:
            return np.zeros(len(self._keep), dtype=np.float32)
        power = np.abs(np.fft.rfft((envelope - mean) * self._hann)) ** 2
        return (power[self._keep] / mean**2).astype(np.float32)


def median_envelope(frames_power):
    """Median across a minute, so a passing sound's rhythm doesn't count."""
    return np.median(np.stack(frames_power), axis=0)


def measure(cfg, freqs, power):
    """The strongest pulse in the configured range: its rate, and its height in dB
    above the envelope's typical level from 1 to 20 Hz. None without readings."""
    if power is None:
        return None
    section = cfg["hiss"]
    low, high = section["pulse_hz"]
    inside = np.flatnonzero((freqs >= low) & (freqs <= high))
    reference = float(np.median(power))
    if not inside.size or reference <= 0:
        return None
    peak = int(inside[np.argmax(power[inside])])
    hz = float(freqs[peak])
    # The bins are 0.5 Hz apart: a parabola through the peak and its neighbours places it between them
    if 0 < peak < len(power) - 1 and min(power[peak - 1 : peak + 2]) > 0:
        left, middle, right = np.log(power[peak - 1 : peak + 2])
        bend = left - 2 * middle + right
        if bend < 0:
            hz += 0.5 * (left - right) / bend * float(freqs[1] - freqs[0])
    height = 10 * np.log10(power[peak] / reference)
    return {
        "hz": round(float(hz), 1),
        "db": round(float(height), 1),
        "present": bool(height >= float(section["pulse_db"])),
    }
