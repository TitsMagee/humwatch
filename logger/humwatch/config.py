"""Loads and checks the site configuration."""

import json
from pathlib import Path

DEFAULTS = {
    "mic_period": "",
    "audio": {"device_name": "", "sample_rate": 48000, "channels": 1},
    "analysis": {"band_hz": [30, 260], "wide_hz": [500, 5000], "sub_hz": [5, 25]},
    "calibration": {"file": "", "spl_offset_db": None},
    "fingerprint": {"tone_groups": [], "excess_db": 8.0, "prominence_db": 6.0},
    "detector": {
        "window_minutes": 15,
        "min_groups": 2,
        "on_fraction": 0.5,
        "off_fraction": 0.15,
        "off_level_drop_db": 3.0,
        "reference_days": 7,
        "validated": False,
    },
    "health": {"floor_db": -100.0, "below_baseline_db": 10.0, "clip_fraction": 0.01},
    "wind": {"sub_excess_db": 15.0},
    "clips": {
        "enabled": False,
        "rise_db": 10.0,
        "min_seconds": 3,
        "pre_seconds": 15,
        "post_seconds": 45,
        "decimation": 4,
        "max_per_day": 40,
        "min_gap_seconds": 300,
        "retention_days": 30,
        "min_free_mb": 1000,
    },
    "paths": {
        "data_dir": "data",
        "baseline": "data/baseline.json",
        "spectra_retention_days": 120,
    },
    "upload": {
        "url": "",
        "secret": "",
        "live_interval_seconds": 10,
        "batch_minutes": 500,
        "timeout_seconds": 20,
    },
    "clock": {"require_ntp_sync": True},
}


class ConfigError(ValueError):
    pass


def _merge(defaults, given):
    merged = dict(defaults)
    for key, value in given.items():
        if key.startswith("_"):
            continue
        if isinstance(value, dict) and isinstance(defaults.get(key), dict):
            merged[key] = _merge(defaults[key], value)
        else:
            merged[key] = value
    return merged


def _check_range(name, pair):
    if len(pair) != 2 or not 0 < pair[0] < pair[1]:
        raise ConfigError(f"{name} must be [low, high] in Hz, got {pair}")


def validate(cfg):
    if not cfg["mic_period"]:
        raise ConfigError("mic_period must be set")
    analysis = cfg["analysis"]
    for name in ("band_hz", "wide_hz", "sub_hz"):
        _check_range(f"analysis.{name}", analysis[name])
    if analysis["wide_hz"][1] >= cfg["audio"]["sample_rate"] / 2:
        raise ConfigError("analysis.wide_hz must stay below half the sample rate")
    band = analysis["band_hz"]
    for group in cfg["fingerprint"]["tone_groups"]:
        _check_range("fingerprint.tone_groups entry", group)
        if group[0] < band[0] or group[1] > band[1]:
            raise ConfigError(f"tone group {group} is outside band_hz {band}")
    det = cfg["detector"]
    if not 0 <= det["off_fraction"] < det["on_fraction"] <= 1:
        raise ConfigError("detector needs 0 <= off_fraction < on_fraction <= 1")
    upload = cfg["upload"]
    if upload["url"]:
        if not upload["url"].startswith("https://"):
            raise ConfigError("upload.url must use https://")
        if len(upload["secret"]) < 32:
            raise ConfigError("upload.secret must be at least 32 characters")
    return cfg


def load(path):
    path = Path(path)
    with open(path, encoding="utf-8") as handle:
        cfg = validate(_merge(DEFAULTS, json.load(handle)))
    # Relative paths are relative to the config file, not wherever we were started
    base = path.resolve().parent
    for section, key in (("paths", "data_dir"), ("paths", "baseline"), ("calibration", "file")):
        value = cfg[section][key]
        if value and not Path(value).is_absolute():
            cfg[section][key] = str(base / value)
    return cfg
