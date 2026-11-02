"""Microphone input: 2 s windows every second, with recovery if the device goes away."""

import logging
import queue
import time

import numpy as np

from .spectrum import WINDOW_SECONDS

logger = logging.getLogger("humwatch")


def _sounddevice():
    # Imported late so that analysis and tests run without PortAudio
    import sounddevice

    return sounddevice


def find_device(name):
    """Device index whose name contains name, or None for the system default.

    Card numbers change between boots on a Pi, so config uses names.
    """
    if not name:
        return None
    sd = _sounddevice()
    for index, device in enumerate(sd.query_devices()):
        if device["max_input_channels"] > 0 and name.lower() in device["name"].lower():
            return index
    raise LookupError(f"no input device matching {name!r}")


def list_devices():
    return _sounddevice().query_devices()


class Windower:
    """Turns arbitrary audio blocks into overlapping windows."""

    def __init__(self, sample_rate):
        self.length = WINDOW_SECONDS * sample_rate
        self.hop = sample_rate
        self.buffer = np.zeros(self.length, dtype=np.float32)
        self.filled = 0

    def push(self, samples):
        """Yield (window, new_samples) for each window the block completes."""
        offset = 0
        while offset < len(samples):
            count = min(len(samples) - offset, self.length - self.filled)
            self.buffer[self.filled : self.filled + count] = samples[offset : offset + count]
            self.filled += count
            offset += count
            if self.filled == self.length:
                yield self.buffer.copy(), self.buffer[-self.hop :].copy()
                self.buffer[: -self.hop] = self.buffer[self.hop :]
                self.filled = self.length - self.hop


class Capture:
    def __init__(self, cfg):
        self.sample_rate = cfg["audio"]["sample_rate"]
        self.channels = cfg["audio"]["channels"]
        self.device_name = cfg["audio"]["device_name"]
        self.windows = queue.Queue(maxsize=30)
        self.windower = Windower(self.sample_rate)
        self.stream = None

    def _callback(self, indata, frames, time_info, status):
        if status:
            logger.warning("Audio status: %s", status)
        mono = indata.mean(axis=1) if indata.shape[1] > 1 else indata[:, 0]
        for window, new in self.windower.push(mono):
            try:
                self.windows.put_nowait((time.time(), window, new))
            except queue.Full:
                logger.warning("Analysis has fallen behind, dropping a window")

    def start(self):
        sd = _sounddevice()
        # A device plugged in after start-up stays invisible until PortAudio rescans
        sd._terminate()
        sd._initialize()
        self.windower = Windower(self.sample_rate)
        self.stream = sd.InputStream(
            device=find_device(self.device_name),
            channels=self.channels,
            samplerate=self.sample_rate,
            blocksize=4800,
            latency=1.0,
            dtype=np.float32,
            callback=self._callback,
        )
        self.stream.start()
        logger.info("Capture started at %d Hz", self.sample_rate)

    def stop(self):
        if self.stream is not None:
            try:
                self.stream.stop()
                self.stream.close()
            except Exception as exc:
                logger.warning("Error closing audio stream: %s", exc)
            self.stream = None

    def get(self, timeout=1.0):
        try:
            return self.windows.get(timeout=timeout)
        except queue.Empty:
            return None
