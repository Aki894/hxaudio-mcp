"""Synthetic acceptance checks for the P1 candidate engine."""
import pytest

np = pytest.importorskip("numpy")
pytest.importorskip("scipy")
pytest.importorskip("soundfile")


@pytest.fixture
def engine():
    from hxaudio_mcp.acoustic import biquad, optimizer

    return biquad, optimizer


def test_biquad_reference_points_and_cascade(engine):
    from hxaudio_mcp.models import Band

    biquad, _ = engine
    rate = 48000
    f = np.array([0, 1000, rate / 2])
    for kind, expected in [("LS", [6, 3, 0]), ("HS", [0, 3, 6]), ("PK", [0, 6, 0])]:
        band = Band(type=kind, gain_db=6)
        assert biquad.response_db([band], f, rate) == pytest.approx(expected, abs=1e-8)
        assert biquad.response_db([band, band], f, rate) == pytest.approx(
            np.array(expected) * 2, abs=1e-8
        )
    assert biquad.response_db([Band(type="NO")], f, rate)[1] < -200
    assert biquad.response_db([Band(type="LP")], f, rate)[-1] < -200
    assert biquad.response_db([Band(type="HP")], f, rate)[0] < -200


def test_fit_deterministic_and_headroom(engine):
    from hxaudio_mcp.models import Band

    biquad, optimizer = engine
    f = np.geomspace(80, 16000, 512)
    y = biquad.response_db([Band(frequency_hz=1800, gain_db=7, q=2)], f, 48000)
    measurement = optimizer.MeasurementResult(f, y, y, np.ones_like(f), {"state": "valid"}, {})
    existing = (Band(frequency_hz=600, gain_db=3),) * 2
    result = optimizer.optimize(measurement, 48000, existing=existing)
    repeat = optimizer.optimize(measurement, 48000, existing=existing)
    assert result.filters == repeat.filters
    assert result.predicted_rmse_db < result.diagnostics["initial_rmse_db"] * 0.7
    assert 1 <= len(result.filters) <= 5
    assert all(-8 <= band.gain_db <= 3 and 0.35 <= band.q <= 5 for band in result.filters)
    peak = max(biquad.response_db((*existing, *result.filters), f, 48000))
    assert result.preamp_db + peak <= -1 + 1e-5


def test_guarded_targets_and_bad_measurements(engine):
    from hxaudio_mcp.acoustic.targets import target_curve

    _, optimizer = engine
    f = np.geomspace(40, 16000, 384)
    y = np.where(f < 100, -16., 0.)
    weights = np.ones_like(f)
    reference = (f, 3 * np.sin(np.log(f)))
    for mode in ("neutral", "warm", "reference", "custom"):
        points = reference if mode in {"reference", "custom"} else None
        curve = target_curve(f, y, weights, mode, points)
        assert np.all(curve[f < 100] <= y[f < 100])
        assert np.isfinite(curve).all()
    bad = optimizer.MeasurementResult(f, y, y, weights, {"state": "invalid"}, {})
    with pytest.raises(ValueError, match="usable"):
        optimizer.optimize(bad, 48000)
    bad.quality_metrics["state"] = "valid"
    bad.confidence[:350] = 0
    with pytest.raises(ValueError, match="coverage"):
        optimizer.optimize(bad, 48000)

def test_filter_centers_stay_outside_confidence_gap(engine):
    from hxaudio_mcp.models import Band

    biquad, optimizer = engine
    f = np.geomspace(80, 16000, 1024)
    y = biquad.response_db([Band(frequency_hz=1000, gain_db=8, q=0.7)], f, 48000)
    confidence = np.where((f > 800) & (f < 1250), 0., 1.)
    measurement = optimizer.MeasurementResult(f, y, y, confidence, {"state": "valid"}, {})
    result = optimizer.optimize(measurement, 48000)
    assert result.filters
    assert all(not 800 < band.frequency_hz < 1250 for band in result.filters)
