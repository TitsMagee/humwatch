from humwatch.detector import FAULT, OFF, ON, UNKNOWN, Detector, ReferenceLevel


def feed(detector, groups, level=40.0, reference=45.0, count=20):
    state = None
    for _ in range(count):
        state = detector.update(groups, level, reference)
    return state


def test_starts_unknown_then_on(cfg):
    detector = Detector(cfg)
    assert detector.update(5, 45.0, 45.0) == UNKNOWN
    assert feed(detector, 5, 45.0) == ON


def test_goes_off_when_tones_vanish_and_level_drops(cfg):
    detector = Detector(cfg)
    feed(detector, 5, 45.0)
    assert feed(detector, 0, 37.0) == OFF


def test_low_load_with_tones_stays_on(cfg):
    # Quiet, but tones are still there: a low-load night, not a stop
    detector = Detector(cfg)
    feed(detector, 5, 45.0)
    assert feed(detector, 3, 38.0) == ON


def test_tones_gone_without_level_drop_stays_on(cfg):
    # The false OFFs seen in testing had tones hidden but no real fall in level
    detector = Detector(cfg)
    feed(detector, 5, 45.0)
    assert feed(detector, 0, 44.0) == ON


def test_no_off_without_a_reference_level(cfg):
    detector = Detector(cfg)
    feed(detector, 5, 45.0)
    assert feed(detector, 0, 37.0, reference=None) == ON


def test_one_group_alone_does_not_count(cfg):
    detector = Detector(cfg)
    feed(detector, 5, 45.0)
    assert feed(detector, 1, 37.0) == OFF


def test_comes_back_on(cfg):
    detector = Detector(cfg)
    feed(detector, 5, 45.0)
    feed(detector, 0, 37.0)
    assert feed(detector, 5, 42.0) == ON


def test_dead_mic_reports_fault_never_off(cfg):
    detector = Detector(cfg)
    feed(detector, 5, 45.0)
    assert detector.update(None, None, 45.0, fault=True) == FAULT
    assert detector.state != OFF


def test_after_a_fault_it_does_not_resume_off_on_stale_memory(cfg):
    detector = Detector(cfg)
    feed(detector, 0, 37.0)
    for _ in range(20):
        detector.update(None, None, 45.0, fault=True)
    assert detector.update(0, 37.0, 45.0) == UNKNOWN


def test_missing_minutes_end_in_unknown(cfg):
    detector = Detector(cfg)
    feed(detector, 5, 45.0)
    assert feed(detector, None, None) == UNKNOWN


def test_off_is_provisional_until_validated(cfg):
    detector = Detector(cfg)
    feed(detector, 5, 45.0)
    feed(detector, 0, 37.0)
    assert detector.provisional is True
    cfg["detector"]["validated"] = True
    validated = Detector(cfg)
    feed(validated, 5, 45.0)
    feed(validated, 0, 37.0)
    assert validated.provisional is False


def test_reference_needs_a_day_of_history():
    reference = ReferenceLevel(days=7)
    for hour in range(10):
        reference.add(hour * 3600, 40.0)
    assert reference.value is None
    for hour in range(10, 30):
        reference.add(hour * 3600, 40.0)
    assert reference.value == 40.0


def test_reference_ignores_missing_levels():
    reference = ReferenceLevel(days=7)
    for hour in range(30):
        reference.add(hour * 3600, 40.0)
        reference.add(hour * 3600 + 60, None)
    assert reference.value == 40.0
