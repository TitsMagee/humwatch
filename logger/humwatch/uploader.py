"""Sends queued data to the server over HTTPS. The Pi only ever calls out.

Requests are signed so that only this logger can write to the site. A lost connection
loses nothing: rows stay in the local queue until the server acknowledges them.
"""

import hashlib
import hmac
import json
import logging
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

from .store import Store

logger = logging.getLogger("humwatch")

MAX_BACKOFF_SECONDS = 300


def sign(secret, timestamp, body):
    """Signature over the timestamp and the body's hash."""
    message = f"{timestamp}\n{hashlib.sha256(body).hexdigest()}".encode()
    return hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()


class Uploader(threading.Thread):
    def __init__(self, cfg):
        super().__init__(name="uploader", daemon=True)
        section = cfg["upload"]
        self.base = section["url"].rstrip("/")
        self.secret = section["secret"]
        self.interval = float(section["live_interval_seconds"])
        self.batch = int(section["batch_minutes"])
        self.timeout = float(section["timeout_seconds"])
        self.data_dir = cfg["paths"]["data_dir"]
        self.live = None
        self.lock = threading.Lock()
        self.stopping = threading.Event()

    def set_live(self, snapshot):
        with self.lock:
            self.live = snapshot

    def stop(self):
        self.stopping.set()

    def _post(self, path, body, content_type, headers=None):
        timestamp = str(int(time.time()))
        request = urllib.request.Request(
            f"{self.base}{path}",
            data=body,
            method="POST",
            headers={
                "Content-Type": content_type,
                "X-HumWatch-Timestamp": timestamp,
                "X-HumWatch-Signature": sign(self.secret, timestamp, body),
                **(headers or {}),
            },
        )
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            return response.status

    def send_once(self, store):
        """Send one batch and any waiting clips. Raises if the server can't be reached."""
        rows = store.next_batch(self.batch)
        with self.lock:
            live, self.live = self.live, None
        payload = {"minutes": [], "events": [], "live": live}
        for _, kind, body in rows:
            payload["minutes" if kind == "minute" else "events"].append(json.loads(body))
        if rows or live:
            try:
                self._post("/api/ingest.php", json.dumps(payload).encode(), "application/json")
            except Exception:
                with self.lock:
                    # Keep the newer snapshot if one arrived while this was in flight
                    self.live = self.live or live
                raise
            if rows:
                store.ack(rows[-1][0])
        for event_id, path in store.pending_clips():
            clip = Path(path)
            if not clip.exists():
                store.mark_clip_uploaded(event_id)
                continue
            self._post(
                "/api/clip.php",
                clip.read_bytes(),
                "audio/flac",
                headers={"X-HumWatch-Event": event_id},
            )
            store.mark_clip_uploaded(event_id)
        return len(rows)

    def send_archive(self, path, skip=0):
        """Send a file of minute rows, one JSON object per line. Returns rows sent.

        For readings from before this logger. Send them before live logging starts:
        the server rolls minutes up in order, and won't go back for older ones.
        A failure raises with the line to resume from. Resending rows is harmless.
        """
        sent = skip
        batch = []

        def flush():
            nonlocal sent
            body = json.dumps({"minutes": batch, "events": []}).encode()
            try:
                self._post("/api/ingest.php", body, "application/json")
            except (urllib.error.URLError, OSError) as exc:
                raise RuntimeError(f"stopped at {exc}. Resume with --skip {sent}") from exc
            sent += len(batch)
            batch.clear()

        with open(path, encoding="utf-8") as handle:
            for number, line in enumerate(handle):
                if number < skip or not line.strip():
                    continue
                batch.append(json.loads(line))
                if len(batch) >= self.batch:
                    flush()
        if batch:
            flush()
        return sent

    def run(self):
        store = Store(self.data_dir)
        backoff = self.interval
        while not self.stopping.is_set():
            try:
                sent = self.send_once(store)
                backoff = self.interval
                # Work through a backlog quickly once the link is back
                wait = 0.5 if sent >= self.batch else self.interval
            except (urllib.error.URLError, OSError, ValueError) as exc:
                logger.warning("Upload failed, will retry: %s", exc)
                backoff = min(backoff * 2, MAX_BACKOFF_SECONDS)
                wait = backoff
            self.stopping.wait(wait)
        store.close()
