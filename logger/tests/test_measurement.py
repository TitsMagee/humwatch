import numpy as np
import pytest

from conftest import sine
from humwatch import calibration, health
from humwatch.capture import Windower
from humwatch.fingerprint import Baseline, ToneMeter, load_baseline, save_baseline
from humwatch.minute import MinuteBuilder, summarise
from humwatch.spectrum import Analyzer, to_db


def baseline_for(analyzer, level_db=-90.0, l90=-80.0):
    return Baseline(
        mic_period="test",
        captured_at=0,
        freqs=analyzer.band_freqs,
        spectrum_db=np.full(len(analyzer.band_freqs), level_db),
        band_l90_db=l90,
        sub_db=-90.0,
    )


def minute_spectrum(analyzer, tones, noise=1e-5, seed=1):
    rng = np.random.default_rng(seed)
    audio = sum(sine(freq, amp) for freq, amp in tones) if tones else np.zeros(96000)
    return summarise(60, [analyzer.process(60 + i, audio + rng.normal(0, noise, 96000)) for i in range(40)])


def test_full_scale_sine_reads_zero_db(cfg):
    frame = Analyzer(cfg).process(0.0, sine(100, 1.0))
    assert frame.band_db == pytest.approx(0.0, abs=0.05)


def test_level_follows_amplitude(cfg):
    frame = Analyzer(cfg).process(0.0, sine(100, 0.01))
    assert frame.band_db == pytest.approx(-40.0, abs=0.05)


def test_tone_outside_band_is_not_counted(cfg):
    frame = Analyzer(cfg).process(0.0, sine(1000, 0.5))
    assert frame.band_db < -60
    assert frame.wide_db == pytest.approx(-6.0, abs=0.1)


def test_spl_offset_is_applied(cfg):
    cfg["calibration"]["spl_offset_db"] = 94.0
    frame = Analyzer(cfg).process(0.0, sine(100, 1.0))
    assert frame.band_db == pytest.approx(94.0, abs=0.05)


def test_calibration_file_corrects_response(cfg, tmp_path):
    path = tmp_path / "cal.txt"
    path.write_text('"Sens Factor =-1.25dB"\n20 2.0\n1000 2.0\n')
    cfg["calibration"]["file"] = str(path)
    frame = Analyzer(cfg).process(0.0, sine(100, 1.0))
    assert frame.band_db == pytest.approx(-2.0, abs=0.05)


def test_calibration_parser_reads_sensitivity_and_skips_text():
    freqs, db, sensitivity = calibration.parse('"Sens Factor =-1.25dB, SERNO: 1"\nfreq spl\n10\t0.5\n20\t1.5\n')
    assert list(freqs) == [10, 20] and list(db) == [0.5, 1.5] and sensitivity == -1.25


def test_windower_overlaps_by_half():
    windower = Windower(100)
    results = list(windower.push(np.arange(400, dtype=np.float32)))
    assert len(results) == 3
    assert results[0][0][0] == 0 and results[1][0][0] == 100
    assert list(results[1][1]) == list(range(200, 300))


def test_windower_handles_tiny_blocks():
    windower = Windower(100)
    count = sum(1 for i in range(400) for _ in windower.push(np.array([float(i)], dtype=np.float32)))
    assert count == 3


def test_tone_needs_both_excess_and_prominence(cfg):
    analyzer = Analyzer(cfg)
    meter = ToneMeter(cfg, analyzer.band_freqs, baseline_for(analyzer))
    minute = minute_spectrum(analyzer, [(67, 0.01), (129, 0.01)])
    tones = meter.measure(minute.spectrum_db)
    assert [tone["present"] for tone in tones] == [True, True, False]
    assert tones[0]["hz"] == pytest.approx(67, abs=0.5)


def test_louder_broadband_noise_is_not_a_tone(cfg):
    analyzer = Analyzer(cfg)
    meter = ToneMeter(cfg, analyzer.band_freqs, baseline_for(analyzer))
    minute = minute_spectrum(analyzer, [], noise=0.05)
    assert not any(tone["present"] for tone in meter.measure(minute.spectrum_db))


def test_without_a_baseline_nothing_counts_as_present(cfg):
    analyzer = Analyzer(cfg)
    meter = ToneMeter(cfg, analyzer.band_freqs, None)
    minute = minute_spectrum(analyzer, [(67, 0.01)])
    tones = meter.measure(minute.spectrum_db)
    assert not any(tone["present"] for tone in tones) and tones[0]["excess"] is None


def test_passing_sound_does_not_create_a_tone(cfg):
    analyzer = Analyzer(cfg)
    meter = ToneMeter(cfg, analyzer.band_freqs, baseline_for(analyzer))
    rng = np.random.default_rng(2)
    frames = []
    for i in range(40):
        audio = rng.normal(0, 1e-5, 96000)
        if i < 5:
            audio = audio + sine(67, 0.1)
        frames.append(analyzer.process(60 + i, audio))
    assert not any(tone["present"] for tone in meter.measure(summarise(60, frames).spectrum_db))


def test_baseline_from_another_mic_period_is_refused(cfg, tmp_path):
    analyzer = Analyzer(cfg)
    path = tmp_path / "baseline.json"
    save_baseline(path, baseline_for(analyzer))
    assert load_baseline(path, "test") is not None
    assert load_baseline(path, "new-mic") is None


def test_baseline_from_another_band_is_refused(cfg, tmp_path):
    path = tmp_path / "baseline.json"
    save_baseline(path, baseline_for(Analyzer(cfg)))
    assert load_baseline(path, "test", Analyzer(cfg).band_freqs) is not None
    cfg["analysis"]["band_hz"] = [30, 260]
    assert load_baseline(path, "test", Analyzer(cfg).band_freqs) is None


def test_minutes_close_and_outage_gives_empty_minutes(cfg):
    analyzer = Analyzer(cfg)
    builder = MinuteBuilder()
    audio = sine(100, 0.1)
    assert builder.add(analyzer.process(120.0, audio)) == []
    assert builder.add(analyzer.process(121.0, audio)) == []
    closed = builder.tick(300.0)
    assert [m.t for m in closed] == [120, 180, 240]
    assert [m.n_frames for m in closed] == [2, 0, 0]


def test_no_audio_from_the_start_still_reports_minutes():
    builder = MinuteBuilder()
    assert builder.tick(120.0) == []
    assert [m.n_frames for m in builder.tick(180.0)] == [0]


@pytest.mark.parametrize(
    "tweak,reason",
    [
        ({"n_frames": 0}, health.NO_AUDIO),
        ({"silent_fraction": 1.0}, health.SILENCE),
        ({"clip_fraction": 0.5}, health.CLIPPING),
        ({"l90": -120.0}, health.BELOW_FLOOR),
        ({"l90": -95.0}, health.BELOW_BASELINE),
    ],
)
def test_mic_faults(cfg, tweak, reason):
    analyzer = Analyzer(cfg)
    minute = minute_spectrum(analyzer, [(67, 0.01)])
    for key, value in tweak.items():
        setattr(minute, key, value)
    assert health.check(cfg, minute, baseline_for(analyzer)) == reason


def test_healthy_minute_is_not_a_fault(cfg):
    analyzer = Analyzer(cfg)
    minute = minute_spectrum(analyzer, [(67, 0.01)])
    minute.l90 = -85.0
    assert health.check(cfg, minute, baseline_for(analyzer)) is None


def test_to_db_handles_zero():
    assert np.isfinite(to_db(np.array([0.0]))).all()
