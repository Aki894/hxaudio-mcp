"""Exercise the measurement pipeline without PortAudio hardware."""
import json

import pytest

np = pytest.importorskip("numpy")
pytest.importorskip("soundfile")
scipy_signal = pytest.importorskip("scipy.signal")


@pytest.fixture
def measurement():
    from hxaudio_mcp.acoustic import measurement

    return measurement


class SimulatedBackend:
    def __init__(self, silence=False):
        self.silence = silence

    def list_devices(self):
        return [{"index": 1, "name": "output"}, {"index": 2, "name": "capture"}]

    def play_and_record(self, signal, rate, output, capture, output_channel, input_channel):
        capture_rate = 44100
        filtered = scipy_signal.lfilter([0.25, 0.25], [1], signal)
        capture_signal = scipy_signal.resample_poly(filtered, 147, 160)
        if self.silence:
            capture_signal[:] = 0
        pad = capture_rate // 2
        return np.pad(capture_signal, (pad, pad)), capture_rate


@pytest.mark.parametrize("silence", [False, True])
def test_measure_writes_evidence(tmp_path, measurement, silence):
    config = measurement.MeasurementConfig(duration_seconds=4, repetitions=3)
    path, result = measurement.measure(
        SimulatedBackend(silence), config, 1, 2, "profile", "revision", tmp_path
    )
    assert (path / "capture_3.wav").is_file()
    assert (path / "stimulus.wav").is_file()
    metadata = json.loads((path / "measurement.json").read_text(encoding="utf-8"))
    assert metadata["revision"] == "revision"
    with np.load(path / "response.npz") as response:
        assert np.array_equal(response["confidence"], result.confidence)
    if silence:
        assert result.quality_metrics["state"] == "invalid"
        assert not result.confidence.any()
    else:
        assert result.quality_metrics["state"] == "valid"
        low_band = (result.frequencies_hz >= 500) & (result.frequencies_hz <= 2000)
        assert np.median(result.raw_response_db[low_band]) == pytest.approx(-6.02, abs=0.4)
        assert result.quality_metrics["repeatability_median_sd_db"] == pytest.approx(0)


def test_stimulus_peak_and_repeatability():
    from hxaudio_mcp.acoustic.stimuli import stimulus

    first = stimulus("periodic_pink_noise", 48000, 192000, 0.1, 2)
    assert np.array_equal(first[:96000], first[96000:])
    assert np.max(np.abs(first)) == pytest.approx(0.1)
    assert np.array_equal(first, stimulus("periodic_pink_noise", 48000, 192000, 0.1, 2))
    for kind, tones in [("sine", (1000,)), ("multitone", (1000, 4000))]:
        generated = stimulus(kind, 48000, 48000, 0.1, 2, tones)
        spectrum = np.abs(np.fft.rfft(generated))
        for tone in tones:
            assert spectrum[tone] > 100


def test_compare_separates_global_gain_from_local_change(tmp_path, measurement):
    from hxaudio_mcp.acoustic.compare import compare

    config = measurement.MeasurementConfig(duration_seconds=4, repetitions=2)
    before, _ = measurement.measure(
        SimulatedBackend(), config, 1, 2, "profile", "before", tmp_path
    )
    after, _ = measurement.measure(
        SimulatedBackend(), config, 1, 2, "profile", "after", tmp_path
    )
    response_path = after / "response.npz"
    with np.load(response_path) as archive:
        data = dict(archive)
    data["raw_response_db"] += 2
    local = np.abs(data["frequencies_hz"] - 1000) <= 50
    data["raw_response_db"][local] -= 6
    np.savez(response_path, **data)
    result = compare(before, after, tmp_path / "delta.csv")
    assert result["off_band_delta_db"] == pytest.approx(2)
    assert result["near_1khz_relative_delta_db"] == pytest.approx(-6)

def test_ambient_noise_reduces_band_confidence(measurement):
    from hxaudio_mcp.acoustic.stimuli import stimulus

    config = measurement.MeasurementConfig(duration_seconds=4, repetitions=1)
    rate = config.sample_rate
    reference = stimulus("periodic_pink_noise", rate, 4 * rate, 0.1, 2)
    clean = np.pad(reference, (rate // 2, rate // 2))
    time = np.arange(len(clean)) / rate
    noisy = clean + 0.03 * np.sin(2 * np.pi * 1000 * time)
    results = [measurement.analyze(reference, [(take, rate)], config)
               for take in (clean, noisy)]
    tone = np.argmin(abs(results[0].frequencies_hz - 1000))
    assert results[0].confidence[tone] > 0.5
    assert results[1].confidence[tone] == 0
    assert results[1].confidence[np.argmin(abs(results[1].frequencies_hz - 2000))] > 0.5
    truncated = measurement.analyze(reference, [(clean[:2 * rate], rate)], config)
    assert "short_recording" in truncated.quality_metrics["reasons"]

def test_paired_score_excludes_nonfinite_response_and_aligns_level(measurement):
    from hxaudio_mcp.acoustic.evaluation import compare_to_target

    frequency = np.geomspace(100, 10000, 64)
    response = np.linspace(-4, 4, 64)
    improved = response / 2 - 3
    improved[20] = np.nan
    confidence = np.ones_like(frequency)
    quality = {"state": "valid"}
    before = measurement.MeasurementResult(frequency, response, response, confidence, quality, {})
    after = measurement.MeasurementResult(frequency, improved, improved, confidence, quality, {})
    result = compare_to_target(before, after, frequency, np.zeros_like(frequency))
    assert result["common_bins"] == 63
    assert result["after_rmse_db"] == pytest.approx(result["before_rmse_db"] / 2)
