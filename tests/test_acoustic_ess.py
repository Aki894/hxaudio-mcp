import numpy as np
import pytest
from scipy.signal import resample_poly

from hxaudio_mcp.acoustic.ess import analyze_sweep, sweep_probe


def test_ess_aligns_capture_clock_and_recovers_gain():
    rate = 48000
    probe = sweep_probe(rate)
    source = probe[0]
    drifted = np.interp(np.arange(round(len(source) * 1.0004)) / 1.0004,
                        np.arange(len(source)), source)
    recording = resample_poly(np.pad(drifted * 0.5, (24000, 4800)), 147, 160)
    result = analyze_sweep(recording, 44100, rate, probe)
    assert result["delay_seconds"] == pytest.approx(0.5, abs=0.001)
    assert result["drift_ppm"] == pytest.approx(400, abs=15)
    index = 1000
    assert result["response_db"][index] == pytest.approx(-6.02, abs=0.2)
    assert result["harmonic_distortion"][index] < 0.01
    assert not result["valid"][0]
    assert not result["valid"][-1]
    assert not result["harmonic_distortion_valid"][5000]


def test_ess_separates_second_harmonic():
    probe = sweep_probe()
    playback, sweep, _, start, _ = probe
    capture = playback.copy()
    capture[start:start + len(sweep)] += 0.4 * sweep**2
    result = analyze_sweep(capture, 48000, 48000, probe)
    assert result["harmonic_distortion"][1000] == pytest.approx(0.05, abs=0.008)


def test_ess_rejects_missing_markers_and_overlapping_windows():
    probe = sweep_probe()
    with pytest.raises(ValueError, match="timing marker"):
        analyze_sweep(np.zeros_like(probe[0]), 48000, 48000, probe)
    with pytest.raises(ValueError, match="window overlaps"):
        analyze_sweep(probe[0], 48000, 48000, probe, window=0.4)
