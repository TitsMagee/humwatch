"""The logging loop: audio in, one summary per minute out."""

import logging
import os
import subprocess
import time

import numpy as np

from . import health
from .detector import FAULT, OFF, ON, Detector, ReferenceLevel
from .events import ClipRecorder, EventDetector
from .fingerprint import ToneMeter, load_baseline
from .minute import MinuteBuilder
from . import pulse
from .spectrum import Analyzer
from .store import Store

logger = logging.getLogger("humwatch")

STALL_SECONDS = 10
RESTART_BACKOFF_SECONDS = 30
HOUSEKEEPING_SECONDS = 3600


def clock_synced():
    """True once NTP has set the clock. A Pi boots with its last saved time."""
    if os.path.exists("/run/systemd/timesync/synchronized"):
        return True
    try:
        result = subprocess.run(
            ["timedatectl", "show", "-p", "NTPSynchronized", "--value"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.stdout.strip() == "yes"


def _round(value, digits=1):
    return None if value is None else round(float(value), digits)


class MinuteProcessor:
    """Turns a finished minute into its stored row. No audio or network here."""

    def __init__(self, cfg, freqs, baseline=None, pulse_freqs=None):
        self.cfg = cfg
        self.pulse_freqs = pulse_freqs
        self.baseline = baseline
        self.meter = ToneMeter(cfg, freqs, baseline)
        self.detector = Detector(cfg)
        self.reference = ReferenceLevel(cfg["detector"]["reference_days"])
        self.calibrated = cfg["calibration"]["spl_offset_db"] is not None

    def seed(self, rows):
        """Rebuild detector memory from stored minutes after a restart."""
        for t, l90, groups, state in rows:
            self.reference.add(t, l90)
            self.detector.update(groups, l90, self.reference.value, fault=state == FAULT)

    def process(self, minute):
        fault = health.check(self.cfg, minute, self.baseline)
        tones = []
        groups = None
        hiss = None
        if minute.usable and not fault:
            tones = self.meter.measure(minute.spectrum_db)
            groups = sum(tone["present"] for tone in tones)
            if self.pulse_freqs is not None:
                hiss = pulse.measure(self.cfg, self.pulse_freqs, minute.pulse_power)

        level = minute.l90 if minute.usable and not fault else None
        self.reference.add(minute.t, level)
        state = self.detector.update(groups, level, self.reference.value, fault=bool(fault))

        excess = None
        wind = None
        interim = self.baseline is not None and self.baseline.interim
        if self.baseline is not None and level is not None:
            # An interim baseline has no stopped level, so there is no honest excess
            excess = None if interim else level - self.baseline.band_l90_db
            threshold = float(self.cfg["wind"]["sub_excess_db"])
            wind = minute.sub_l50 - self.baseline.sub_db >= threshold

        return {
            "t": minute.t,
            "mic_period": self.cfg["mic_period"],
            "n_frames": minute.n_frames,
            "state": state,
            "provisional": self.detector.provisional or (interim and state in (ON, OFF)),
            "fault": fault,
            "calibrated": self.calibrated,
            "l90": _round(minute.l90),
            "l50": _round(minute.l50),
            "leq": _round(minute.leq),
            "lmax": _round(minute.lmax),
            "wide_l90": _round(minute.wide_l90),
            "wide_leq": _round(minute.wide_leq),
            "sub_l50": _round(minute.sub_l50),
            "hiss_l50": _round(minute.hiss_l50) if minute.usable and not fault else None,
            "pulse_hz": hiss and hiss["hz"],
            "pulse_db": hiss and hiss["db"],
            "pulsing": hiss and hiss["present"],
            "excess": _round(excess),
            "wind": wind,
            "groups_present": groups,
            "tones": [
                {
                    **{key: tone[key] for key in ("low", "high", "hz", "db", "present")},
                    "excess": None if interim else tone["excess"],
                }
                for tone in tones
            ],
        }


class Service:
    def __init__(self, cfg):
        from .capture import Capture
        from .uploader import Uploader

        self.cfg = cfg
        self.analyzer = Analyzer(cfg)
        baseline = load_baseline(cfg["paths"]["baseline"], cfg["mic_period"], self.analyzer.band_freqs)
        if baseline is None:
            logger.warning(
                "No baseline for mic period %s and band %s: state stays unknown",
                cfg["mic_period"], cfg["analysis"]["band_hz"],
            )
        self.store = Store(cfg["paths"]["data_dir"])
        self.minutes = MinuteBuilder()
        self.processor = MinuteProcessor(cfg, self.analyzer.band_freqs, baseline, self.analyzer.pulse_freqs)
        self.events = EventDetector(cfg)
        self.clips = ClipRecorder(cfg, self.store)
        self.capture = Capture(cfg)
        self.uploader = Uploader(cfg) if cfg["upload"]["url"] else None
        self.running = True
        self.last_frame = None
        self.last_row = None
        self.last_level = None

        days = cfg["detector"]["reference_days"]
        self.processor.seed(self.store.recent_minutes(cfg["mic_period"], time.time() - days * 86400))

    def stop(self, *_):
        self.running = False

    def _wait_for_clock(self):
        if not self.cfg["clock"]["require_ntp_sync"]:
            return
        while self.running and not clock_synced():
            logger.info("Waiting for NTP before logging anything")
            time.sleep(5)

    def _start_capture(self, now):
        try:
            self.capture.stop()
            self.capture.start()
            self.last_frame = now
        except Exception as exc:
            logger.error("Could not open the microphone: %s", exc)
        self.next_capture_try = now + RESTART_BACKOFF_SECONDS

    def _finish_minutes(self, minutes):
        for minute in minutes:
            row = self.processor.process(minute)
            self.store.save_minute(row, minute.spectrum_db)
            self.last_row = row
            if row["fault"]:
                logger.warning("Mic fault at %d: %s", row["t"], row["fault"])

    def _publish_live(self, now):
        if self.uploader is None or self.last_row is None:
            return
        row = self.last_row
        self.uploader.set_live(
            {
                "t": int(now),
                "level": _round(self.last_level),
                **{key: row[key] for key in (
                    "state", "provisional", "fault", "calibrated", "mic_period", "excess", "l90", "tones",
                    "hiss_l50", "pulse_hz", "pulse_db", "pulsing",
                )},
                "band_hz": self.cfg["analysis"]["band_hz"],
                "minute_t": row["t"],
            }
        )

    def run(self):
        self._wait_for_clock()
        if not self.running:
            return
        if self.uploader:
            self.uploader.start()
        self._start_capture(time.time())
        next_live = next_housekeeping = 0.0

        while self.running:
            item = self.capture.get(timeout=1.0)
            now = time.time()
            if item is not None:
                self._handle_window(*item)
                self.last_frame = now
            else:
                self._finish_minutes(self.minutes.tick(now))
                self.last_level = None
                stalled = self.last_frame is None or now - self.last_frame > STALL_SECONDS
                if stalled and now >= self.next_capture_try:
                    logger.warning("No audio for %d s, reopening the microphone", STALL_SECONDS)
                    self._start_capture(now)

            if now >= next_live:
                self._publish_live(now)
                next_live = now + self.cfg["upload"]["live_interval_seconds"]
            if now >= next_housekeeping:
                self._housekeeping(now)
                next_housekeeping = now + HOUSEKEEPING_SECONDS

        self.capture.stop()
        if self.uploader:
            self.uploader.stop()
            self.uploader.join(timeout=5)
        self.store.close()

    def _handle_window(self, t, window, new_samples):
        frame = self.analyzer.process(t, window)
        self.last_level = frame.band_db
        self._finish_minutes(self.minutes.add(frame))
        kind = self.events.add(frame)
        self.clips.add_audio(new_samples)
        if kind:
            event_id = f"{int(t)}-{kind}"
            has_clip = self.clips.start(event_id, kind, t)
            self.store.queue_event(
                {
                    "id": event_id,
                    "t": int(t),
                    "kind": kind,
                    "mic_period": self.cfg["mic_period"],
                    "band_db": _round(frame.band_db),
                    "wide_db": _round(frame.wide_db),
                    "has_clip": has_clip,
                }
            )

    def _housekeeping(self, now):
        days = self.cfg["paths"]["spectra_retention_days"]
        self.store.prune_spectra(now - days * 86400)
        self.clips.cleanup(now)
