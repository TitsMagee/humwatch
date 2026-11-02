import copy

import numpy as np
import pytest

from humwatch.config import DEFAULTS, validate


@pytest.fixture
def cfg(tmp_path):
    config = copy.deepcopy(DEFAULTS)
    config["mic_period"] = "test"
    config["fingerprint"]["tone_groups"] = [[66, 68], [128, 130], [200, 200.5]]
    config["paths"]["data_dir"] = str(tmp_path)
    config["paths"]["baseline"] = str(tmp_path / "baseline.json")
    return validate(config)


def sine(freq, amplitude, seconds=2, rate=48000):
    t = np.arange(int(seconds * rate)) / rate
    return amplitude * np.sin(2 * np.pi * freq * t)
