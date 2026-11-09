"""Command line entry point."""

import argparse
import json
import logging
import signal
import sys
import time
from datetime import datetime

import numpy as np

from . import calibration, learn
from .config import load
from .fingerprint import ToneMeter, load_baseline, save_baseline
from .spectrum import Analyzer
from .store import Store


def _run(cfg, args):
    from .service import Service

    service = Service(cfg)
    signal.signal(signal.SIGTERM, service.stop)
    signal.signal(signal.SIGINT, service.stop)
    service.run()


def _devices(cfg, args):
    from .capture import list_devices

    print(list_devices())


def _check(cfg, args):
    print(f"Mic period: {cfg['mic_period']}")
    print(f"Tone groups: {cfg['fingerprint']['tone_groups'] or 'none yet'}")
    print(f"Calibrated levels: {'yes' if cfg['calibration']['spl_offset_db'] is not None else 'no, relative only'}")
    path = cfg["calibration"]["file"]
    if path:
        with open(path, encoding="utf-8", errors="replace") as handle:
            freqs, db, sensitivity = calibration.parse(handle.read())
        print(f"Calibration file: {freqs[0]:g}-{freqs[-1]:g} Hz, {len(freqs)} points")
        print(f"  At 30 Hz: {np.interp(30, freqs, db):+.2f} dB, at 100 Hz: {np.interp(100, freqs, db):+.2f} dB")
        print(f"  Sensitivity line: {sensitivity if sensitivity is not None else 'none found'}")
        if freqs[0] > cfg["analysis"]["band_hz"][0]:
            print("  WARNING: file does not reach the bottom of the band; the end value is held")


def _learn(cfg, args):
    stops = learn.read_windows(args.stops)
    store = Store(cfg["paths"]["data_dir"])
    times, spectra = store.spectra(cfg["mic_period"])
    if not len(times):
        sys.exit("No logged spectra for this mic period yet")
    freqs = Analyzer(cfg).band_freqs
    groups, stats = learn.learn_groups(freqs, times, spectra, stops, cfg["fingerprint"]["prominence_db"])
    for low, run_fraction, stop_fraction in stats:
        print(f"{low:6.1f} Hz  running {run_fraction:6.1%}  stopped {stop_fraction:6.1%}")
    print(json.dumps({"tone_groups": groups}))


def _baseline(cfg, args):
    stops = learn.read_windows(args.stops)
    store = Store(cfg["paths"]["data_dir"])
    times, spectra = store.spectra(cfg["mic_period"])
    if not len(times):
        sys.exit("No logged spectra for this mic period yet")
    minutes = store.minutes_between(cfg["mic_period"], times[0], times[-1] + 60)
    baseline = learn.build_baseline(cfg["mic_period"], Analyzer(cfg).band_freqs, times, spectra, minutes, stops)
    save_baseline(cfg["paths"]["baseline"], baseline)
    print(f"Saved baseline: band L90 {baseline.band_l90_db:.1f} dB")


def _interim_baseline(cfg, args):
    existing = load_baseline(cfg["paths"]["baseline"], cfg["mic_period"])
    if existing is not None and not existing.interim:
        sys.exit("This mic period already has a baseline from real stops: not replacing it")
    store = Store(cfg["paths"]["data_dir"])
    times, spectra = store.spectra(cfg["mic_period"], time.time() - args.days * 86400)
    if not len(times):
        sys.exit("No logged spectra for this mic period yet")
    minutes = store.minutes_between(cfg["mic_period"], times[0], times[-1] + 60)
    baseline = learn.build_interim_baseline(cfg["mic_period"], Analyzer(cfg).band_freqs, times, spectra, minutes)
    save_baseline(cfg["paths"]["baseline"], baseline)
    print(f"Saved interim baseline from {len(times)} minutes. Restart the logger to use it.")


def _clock(t):
    return datetime.fromtimestamp(t).astimezone().strftime("%Y-%m-%dT%H:%M")


def _find_stops(cfg, args):
    store = Store(cfg["paths"]["data_dir"])
    times, spectra = store.spectra(cfg["mic_period"], time.time() - args.days * 86400)
    if not len(times):
        sys.exit("No logged spectra for this mic period yet")
    usable = {
        row[0]: row[1]
        for row in store.minutes_between(cfg["mic_period"], times[0], times[-1] + 60)
        if row[1] is not None and row[4] != "fault"
    }
    keep = np.array([int(t) in usable for t in times], dtype=bool)
    times, spectra = times[keep], spectra[keep]
    freqs = Analyzer(cfg).band_freqs
    groups = learn.tones_by_prominence(ToneMeter(cfg, freqs), spectra)
    levels = [usable[int(t)] for t in times]
    found = learn.find_stops(times, groups, levels)
    if not found:
        print(f"No stretches found in {len(times)} minutes")
    print("start,end,minutes,level_drop_db,groups_around,groups_during,other_lines,verdict")
    for stop in found:
        inside = (times >= stop["start"]) & (times < stop["end"])
        lines = learn.other_lines(freqs, np.median(spectra[inside], axis=0), cfg["fingerprint"]["tone_groups"])
        print(
            f"{_clock(stop['start'])},{_clock(stop['end'])},{stop['minutes']},{stop['level_drop']},"
            f"{stop['groups_around']},{stop['groups_during']},{lines},{stop['verdict']}"
        )
    if args.out:
        # The minutes either side of a stop are a mix of running and stopped
        with open(args.out, "w", encoding="utf-8") as handle:
            for stop in found:
                if stop["verdict"] == "likely":
                    handle.write(f"{_clock(stop['start'] + 180)},{_clock(stop['end'] - 180)}\n")


def _validate(cfg, args):
    stops = learn.read_windows(args.stops)
    store = Store(cfg["paths"]["data_dir"])
    minutes = store.minutes_between(cfg["mic_period"], 0, 2**62)
    print(json.dumps(learn.validate(minutes, stops), indent=2))


def _send_archive(cfg, args):
    from .uploader import Uploader

    if not cfg["upload"]["url"]:
        sys.exit("upload.url is not set")
    print(f"Sent {Uploader(cfg).send_archive(args.file, args.skip)} rows")


def main(argv=None):
    parser = argparse.ArgumentParser(prog="humwatch")
    parser.add_argument("--config", default="config.json")
    parser.add_argument("-v", "--verbose", action="store_true")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("run", help="log continuously").set_defaults(func=_run)
    commands.add_parser("list-devices", help="show audio inputs").set_defaults(func=_devices)
    commands.add_parser("check", help="show config and calibration file").set_defaults(func=_check)
    for name, func, text in (
        ("learn", _learn, "find tone groups from known stops"),
        ("baseline", _baseline, "build the stopped baseline from known stops"),
        ("validate", _validate, "score logged states against known stops"),
    ):
        sub = commands.add_parser(name, help=text)
        sub.add_argument("--stops", required=True, help="CSV of start,end ISO times")
        sub.set_defaults(func=func)

    interim = commands.add_parser("interim-baseline", help="estimate a stand-in baseline with the source running")
    interim.add_argument("--days", type=float, default=7, help="how far back to look")
    interim.set_defaults(func=_interim_baseline)

    stops = commands.add_parser("find-stops", help="list likely stops from the saved spectra")
    stops.add_argument("--days", type=float, default=30, help="how far back to look")
    stops.add_argument("--out", help="write likely stops here, as a CSV for the baseline command")
    stops.set_defaults(func=_find_stops)

    archive = commands.add_parser("send-archive", help="upload older readings from a JSON lines file")
    archive.add_argument("file")
    archive.add_argument("--skip", type=int, default=0, help="lines already sent")
    archive.set_defaults(func=_send_archive)

    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    args.func(load(args.config), args)


if __name__ == "__main__":
    main()
