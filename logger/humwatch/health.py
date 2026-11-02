"""Tells a faulty microphone apart from a quiet night.

Without this a dead mic looks exactly like a stopped source: no tones, low level.
"""

NO_AUDIO = "no_audio"
SILENCE = "digital_silence"
BELOW_FLOOR = "below_floor"
BELOW_BASELINE = "below_baseline"
CLIPPING = "clipping"


def check(cfg, minute, baseline=None):
    """Return a fault reason for a minute's summary, or None."""
    health = cfg["health"]
    if minute.n_frames == 0:
        return NO_AUDIO
    if minute.silent_fraction > 0.5:
        return SILENCE
    if minute.clip_fraction > float(health["clip_fraction"]):
        return CLIPPING
    if minute.l90 < float(health["floor_db"]):
        return BELOW_FLOOR
    if baseline is not None and minute.l90 < baseline.band_l90_db - float(health["below_baseline_db"]):
        return BELOW_BASELINE
    return None
