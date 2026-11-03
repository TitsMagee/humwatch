import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import numpy as np
import pytest

from conftest import sine
from humwatch import learn
from humwatch.config import ConfigError, load, validate
from humwatch.events import BAND, WIDE, ClipRecorder, EventDetector
from humwatch.fingerprint import Baseline
from humwatch.minute import Minute, summarise
from humwatch.service import MinuteProcessor
from humwatch.spectrum import Analyzer, Frame
from humwatch.store import Store
from humwatch.uploader import Uploader, sign


def frame(wide=-60.0, band=-40.0):
    return Frame(0.0, np.zeros(1), band, wide, -60.0, 0.0, False)


def test_event_fires_once_on_a_sustained_wide_rise(cfg):
    detector = EventDetector(cfg)
    assert [detector.add(frame()) for _ in range(120)] == [None] * 120
    results = [detector.add(frame(wide=-40.0)) for _ in range(30)]
    assert results.count(WIDE) == 1


def test_short_blip_is_not_an_event(cfg):
    detector = EventDetector(cfg)
    for _ in range(120):
        detector.add(frame())
    assert [detector.add(frame(wide=-40.0)) for _ in range(2)] == [None, None]


def test_band_rise_is_a_separate_kind(cfg):
    detector = EventDetector(cfg)
    for _ in range(120):
        detector.add(frame())
    results = [detector.add(frame(band=-20.0)) for _ in range(10)]
    assert results.count(BAND) == 1


def test_a_lasting_step_becomes_the_new_normal(cfg):
    detector = EventDetector(cfg)
    for _ in range(120):
        detector.add(frame())
    assert [detector.add(frame(wide=-40.0)) for _ in range(700)].count(WIDE) == 1
    for _ in range(120):
        detector.add(frame(wide=-40.0))
    assert [detector.add(frame(wide=-20.0)) for _ in range(5)].count(WIDE) == 1


def test_clips_off_by_default(cfg, tmp_path):
    recorder = ClipRecorder(cfg, Store(tmp_path))
    assert recorder.start("a", WIDE, 1000.0) is False


def test_clip_is_written_as_flac_and_expires(cfg, tmp_path):
    cfg["clips"].update(enabled=True, pre_seconds=2, post_seconds=2, min_free_mb=0)
    store = Store(tmp_path)
    recorder = ClipRecorder(cfg, store)
    second = sine(440, 0.1, seconds=1).astype(np.float32)
    recorder.add_audio(second)
    recorder.add_audio(second)
    assert recorder.start("100-wide", WIDE, 1000.0)
    assert recorder.add_audio(second) is None
    assert recorder.add_audio(second) == "100-wide"
    (event_id, path), = store.pending_clips()
    import soundfile

    audio, rate = soundfile.read(path)
    assert rate == 12000 and len(audio) == 48000
    recorder.cleanup(now=1000.0 + 31 * 86400)
    assert store.pending_clips() == []
    assert not Path(path).exists()


def test_clip_cap_and_gap(cfg, tmp_path):
    cfg["clips"].update(enabled=True, max_per_day=1, min_gap_seconds=300, post_seconds=1, min_free_mb=0)
    recorder = ClipRecorder(cfg, Store(tmp_path))
    second = sine(440, 0.1, seconds=1).astype(np.float32)
    assert recorder.start("a", WIDE, 1000.0)
    recorder.add_audio(second)
    assert recorder.start("b", WIDE, 1100.0) is False  # inside the gap
    assert recorder.start("c", BAND, 1100.0) is False  # daily cap


def row(t, state="on"):
    return {
        "t": t, "mic_period": "test", "l90": 40.0, "sub_l50": -60.0,
        "groups_present": 3, "state": state,
    }


def test_store_queue_survives_reopening(tmp_path):
    store = Store(tmp_path)
    store.save_minute(row(60), np.array([-50.0, -60.0]))
    store.close()
    store = Store(tmp_path)
    batch = store.next_batch(10)
    assert len(batch) == 1 and json.loads(batch[0][2])["t"] == 60
    store.ack(batch[0][0])
    assert store.next_batch(10) == []
    times, matrix = store.spectra("test")
    assert list(times) == [60] and matrix[0, 0] == pytest.approx(-50.0)


def processor_for(cfg):
    analyzer = Analyzer(cfg)
    baseline = Baseline("test", 0, analyzer.band_freqs,
                        np.full(len(analyzer.band_freqs), -90.0), -80.0, -90.0)
    return analyzer, MinuteProcessor(cfg, analyzer.band_freqs, baseline)


def test_processor_produces_a_row_for_a_running_source(cfg):
    analyzer, processor = processor_for(cfg)
    rng = np.random.default_rng(3)
    audio = sine(67, 0.01) + sine(129, 0.01)
    frames = [analyzer.process(60 + i, audio + rng.normal(0, 1e-5, 96000)) for i in range(40)]
    minute = summarise(60, frames)
    for _ in range(10):
        result = processor.process(minute)
    assert result["state"] == "on" and result["groups_present"] == 2 and result["fault"] is None
    assert result["excess"] > 0 and result["calibrated"] is False
    assert json.dumps(result)  # must be plain JSON for the upload


def test_processor_reports_dead_mic_as_fault(cfg):
    _, processor = processor_for(cfg)
    result = processor.process(Minute(t=60, n_frames=0))
    assert result["state"] == "fault" and result["fault"] == "no_audio" and result["tones"] == []
    assert json.dumps(result)


def test_processor_without_baseline_stays_unknown(cfg):
    analyzer = Analyzer(cfg)
    processor = MinuteProcessor(cfg, analyzer.band_freqs, None)
    rng = np.random.default_rng(4)
    frames = [analyzer.process(60 + i, sine(67, 0.01) + rng.normal(0, 1e-5, 96000)) for i in range(40)]
    minute = summarise(60, frames)
    states = {processor.process(minute)["state"] for _ in range(30)}
    assert "off" not in states


def test_learn_finds_tones_that_stop_with_the_source(cfg):
    analyzer = Analyzer(cfg)
    freqs = analyzer.band_freqs
    rng = np.random.default_rng(5)
    times = np.arange(0, 86400, 60, dtype=float)
    spectra = rng.normal(-90, 1, (len(times), len(freqs)))
    stops = [(40000.0, 46000.0)]
    stopped = (times >= 40000) & (times < 46000)
    for hz in (67.0, 129.0):
        spectra[~stopped, np.argmin(np.abs(freqs - hz))] = -60
    # A tone that never stops, e.g. something else nearby, must not be chosen
    spectra[:, np.argmin(np.abs(freqs - 200.0))] = -60
    groups, _ = learn.learn_groups(freqs, times, spectra, stops)
    flat = [(low, high) for low, high in groups]
    assert any(low <= 67 <= high for low, high in flat)
    assert any(low <= 129 <= high for low, high in flat)
    assert not any(low <= 200 <= high for low, high in flat)


def test_learn_refuses_too_little_stop_data(cfg):
    freqs = Analyzer(cfg).band_freqs
    times = np.arange(0, 86400, 60, dtype=float)
    spectra = np.zeros((len(times), len(freqs)))
    with pytest.raises(ValueError):
        learn.learn_groups(freqs, times, spectra, [(0.0, 600.0)])


def test_baseline_comes_from_stop_minutes_only(cfg):
    freqs = Analyzer(cfg).band_freqs
    times = np.arange(0, 7200, 60, dtype=float)
    spectra = np.full((len(times), len(freqs)), -60.0)
    stops = [(3600.0, 7200.0)]
    spectra[times >= 3600] = -90.0
    minutes = [(t, -80.0 if t >= 3600 else -50.0, -70.0, 0, "on") for t in times]
    baseline = learn.build_baseline("test", freqs, times, spectra, minutes, stops)
    assert baseline.band_l90_db == -80.0 and baseline.spectrum_db[0] == -90.0


def test_validate_counts_states(cfg):
    stops = [(1000.0, 2000.0)]
    minutes = [
        (1500, 0, 0, 0, "off"), (1600, 0, 0, 0, "on"),
        (2500, 0, 0, 0, "on"),  # inside the margin: left out
        (20000, 0, 0, 0, "on"), (21000, 0, 0, 0, "fault"),
    ]
    result = learn.validate(minutes, stops)
    assert result["during_stops"] == {"on": 1, "off": 1, "other": 0}
    assert result["away_from_stops"] == {"on": 1, "off": 0, "other": 1}


def test_read_windows_skips_comments(tmp_path):
    path = tmp_path / "stops.csv"
    path.write_text("# my stops\n2026-11-20T21:25:00+00:00,2026-11-20T22:13:00+00:00\n")
    windows = learn.read_windows(path)
    assert len(windows) == 1 and windows[0][1] - windows[0][0] == 48 * 60


def test_config_rejects_http_and_short_secret(cfg):
    cfg["upload"].update(url="http://example.org", secret="x" * 40)
    with pytest.raises(ConfigError):
        validate(cfg)
    cfg["upload"].update(url="https://example.org", secret="short")
    with pytest.raises(ConfigError):
        validate(cfg)


def test_config_rejects_tone_outside_band(cfg):
    cfg["fingerprint"]["tone_groups"] = [[300, 310]]
    with pytest.raises(ConfigError):
        validate(cfg)


def test_config_ignores_comment_keys_and_resolves_paths(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"_comment": "x", "mic_period": "m", "audio": {"_a": 1}}))
    loaded = load(path)
    assert "_comment" not in loaded and "_a" not in loaded["audio"]
    assert loaded["paths"]["data_dir"].startswith(str(tmp_path.resolve()))


class _Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        body = self.rfile.read(int(self.headers["Content-Length"]))
        self.server.seen.append((self.path, self.headers, body))
        self.send_response(self.server.status)
        self.end_headers()

    def log_message(self, *args):
        pass


@pytest.fixture
def server():
    httpd = HTTPServer(("127.0.0.1", 0), _Handler)
    httpd.seen, httpd.status = [], 200
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield httpd
    httpd.shutdown()


def uploader_for(cfg, httpd):
    cfg["upload"].update(url=f"http://127.0.0.1:{httpd.server_port}", secret="s" * 40)
    return Uploader(cfg)


def test_upload_signs_and_clears_the_queue(cfg, tmp_path, server):
    store = Store(tmp_path)
    store.save_minute(row(60))
    uploader = uploader_for(cfg, server)
    uploader.set_live({"t": 61})
    assert uploader.send_once(store) == 1
    assert store.next_batch(10) == []
    path, headers, body = server.seen[0]
    assert path == "/api/ingest.php"
    assert headers["X-HumWatch-Signature"] == sign("s" * 40, headers["X-HumWatch-Timestamp"], body)
    payload = json.loads(body)
    assert payload["minutes"][0]["t"] == 60 and payload["live"] == {"t": 61}


def test_failed_upload_keeps_everything(cfg, tmp_path, server):
    server.status = 500
    store = Store(tmp_path)
    store.save_minute(row(60))
    uploader = uploader_for(cfg, server)
    uploader.set_live({"t": 61})
    with pytest.raises(Exception):
        uploader.send_once(store)
    assert len(store.next_batch(10)) == 1
    assert uploader.live == {"t": 61}


def test_send_archive_batches_and_resumes(cfg, tmp_path, server):
    path = tmp_path / "archive.jsonl"
    path.write_text("".join(json.dumps({"t": t}) + "\n" for t in range(5)))
    uploader = uploader_for(cfg, server)
    uploader.batch = 2
    assert uploader.send_archive(path) == 5
    sent = [json.loads(body)["minutes"] for _, _, body in server.seen]
    assert [len(batch) for batch in sent] == [2, 2, 1]
    server.seen.clear()
    assert uploader.send_archive(path, skip=3) == 5
    assert [row["t"] for row in json.loads(server.seen[0][2])["minutes"]] == [3, 4]


def test_send_archive_reports_where_to_resume(cfg, tmp_path, server):
    path = tmp_path / "archive.jsonl"
    path.write_text('{"t": 1}\n')
    server.status = 500
    with pytest.raises(RuntimeError, match="--skip 0"):
        uploader_for(cfg, server).send_archive(path)


def test_clip_upload(cfg, tmp_path, server):
    store = Store(tmp_path)
    clip = tmp_path / "a.flac"
    clip.write_bytes(b"fLaC123")
    store.add_clip("100-wide", str(clip), 100)
    uploader = uploader_for(cfg, server)
    uploader.send_once(store)
    path, headers, body = server.seen[0]
    assert path == "/api/clip.php" and headers["X-HumWatch-Event"] == "100-wide" and body == b"fLaC123"
    assert store.pending_clips() == []
