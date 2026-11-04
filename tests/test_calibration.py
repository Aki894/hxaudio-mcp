"""Exercise the P2 job lifecycle with a simulated audio chain."""
import time

import pytest

np = pytest.importorskip("numpy")
scipy_signal = pytest.importorskip("scipy.signal")
pytest.importorskip("soundfile")


@pytest.mark.parametrize("mode, positions, dropout, keep", [
    ("fixed", 1, False, False), ("multi", 2, False, False), ("moving", 1, False, False),
    ("fixed", 1, True, False), ("fixed", 1, False, True),
])
def test_calibration_loop_and_restore(
    tmp_path, harness, monkeypatch, mode, positions, dropout, keep,
):
    from hxaudio_mcp.acoustic.biquad import coefficients
    from hxaudio_mcp.acoustic.calibration import CalibrationJobs
    from hxaudio_mcp.acoustic.measurement import MeasurementConfig
    from hxaudio_mcp.acoustic.optimizer import OptimizationConfig
    from hxaudio_mcp.models import Band

    ctl, _ = harness
    profile = ctl.get_dsp_profile()
    monkeypatch.setattr(ctl, "wait_for_generation", lambda *args: {"state": "observed"})

    class Backend:
        def list_devices(self):
            return [{"index": 1}, {"index": 2}]

        def play_and_record(self, signal, rate, *args):
            current = ctl.get_dsp_profile(profile.profile_key)
            if dropout and current.revision != profile.revision:
                return np.zeros(len(signal) + rate), rate
            recorded = signal * 0.3
            for band in [Band(frequency_hz=1800, gain_db=8, q=2), *current.bands]:
                b, a = coefficients(band, rate)
                recorded = scipy_signal.lfilter(b, a, recorded)
            return np.pad(recorded, rate // 2), rate

    jobs = CalibrationJobs(ctl, Backend(), tmp_path / "sessions")
    sid = jobs.start(profile.profile_key, 1, 2,
                     MeasurementConfig(duration_seconds=4, repetitions=2),
                     OptimizationConfig(), rounds=1, keep=keep,
                     mode=mode, positions=positions)["session_id"]
    deadline = time.monotonic() + 10
    last_prompt = None
    while jobs.active[1].is_alive() and time.monotonic() < deadline:
        status = jobs.status(sid)
        if status["state"] == "PAUSED":
            prompt = (status["phase"], status["instruction"])
            if prompt != last_prompt:
                jobs.signal(sid, cancel=False)
                last_prompt = prompt
        time.sleep(.01)
    jobs.active[1].join(.1)
    result = jobs.status(sid)
    assert result["state"] == "COMPLETED", result["stop_reason"]
    assert result["iterations"][0]["accepted"] is not dropout
    if dropout:
        assert result["stop_reason"] == "verification_quality_degraded"
    assert (ctl.get_dsp_profile().revision == profile.revision) is not keep
    assert set(result["reports"]) == {"summary", "eq", "iterations", "measurements", "html"}
    if mode != "fixed" or dropout or keep:
        return
    sid = jobs.start(profile.profile_key, 1, 2, MeasurementConfig(),
                     OptimizationConfig(), mode="multi", positions=2)["session_id"]
    for _ in range(100):
        if jobs.status(sid)["state"] == "PAUSED":
            break
        time.sleep(.02)
    assert jobs.status(sid)["state"] == "PAUSED"
    jobs.signal(sid, cancel=True)
    jobs.active[1].join(5)
    assert jobs.status(sid)["state"] == "CANCELLED"

def test_external_change_pauses_without_overwriting(tmp_path, harness):
    from hxaudio_mcp.acoustic.calibration import CalibrationJobs
    from hxaudio_mcp.acoustic.measurement import MeasurementConfig
    from hxaudio_mcp.acoustic.optimizer import OptimizationConfig

    ctl, _ = harness
    profile = ctl.get_dsp_profile()

    class Backend:
        def list_devices(self):
            return [{"index": 1}, {"index": 2}]

    jobs = CalibrationJobs(ctl, Backend(), tmp_path / "sessions")
    sid = jobs.start(profile.profile_key, 1, 2, MeasurementConfig(), OptimizationConfig(),
                     mode="moving")["session_id"]
    deadline = time.monotonic() + 5
    while jobs.status(sid)["state"] != "PAUSED" and time.monotonic() < deadline:
        time.sleep(.01)
    ctl.apply_dsp_profile([], -2, profile.revision, profile.profile_key, timeout_seconds=0)
    edited = ctl.get_dsp_profile().revision
    while "externally" not in (jobs.status(sid).get("instruction") or ""):
        assert time.monotonic() < deadline
        time.sleep(.01)
    jobs.signal(sid, cancel=True)
    jobs.active[1].join(5)
    assert jobs.status(sid)["stop_reason"] == "external_revision_conflict"
    assert ctl.get_dsp_profile().revision == edited

def test_calibration_mcp_tools_registered():
    import asyncio

    from hxaudio_mcp.server import server

    tools = asyncio.run(server.list_tools())
    names = {tool.name for tool in tools}
    assert {"calibration_check", "calibration_measure", "calibration_optimize",
            "calibration_run", "calibration_status", "calibration_cancel",
            "calibration_resume", "calibration_recover", "calibration_report"} <= names


def test_standalone_capture_cancellation_and_restart(tmp_path, harness):
    from threading import Event

    from hxaudio_mcp.acoustic.calibration import CalibrationJobs
    from hxaudio_mcp.acoustic.measurement import MeasurementConfig

    ctl, _ = harness
    profile = ctl.get_dsp_profile()
    entered, release = Event(), Event()
    takes = []

    class Backend:
        def list_devices(self):
            return [{"index": 1}, {"index": 2}]

        def play_and_record(self, signal, rate, *args):
            takes.append(len(signal))
            entered.set()
            assert release.wait(5)
            return np.pad(signal * 0.3, rate // 2), rate

    jobs = CalibrationJobs(ctl, Backend(), tmp_path / "sessions")
    config = MeasurementConfig(duration_seconds=3, repetitions=2)
    session, worker = jobs.start_measurement(profile.profile_key, 1, 2, config, 20)
    try:
        assert entered.wait(5)
        assert jobs.status(session.id)["state"] == "MEASURING"
        with pytest.raises(RuntimeError, match="already running"):
            jobs.start_measurement(profile.profile_key, 1, 2, config, 20)
        jobs.signal(session.id, cancel=True)
    finally:
        release.set()
        worker.join(5)
    assert jobs.status(session.id)["state"] == "CANCELLED"
    assert len(takes) == 1
    assert (tmp_path / session.id / "capture_1.wav").exists()
    assert not (tmp_path / session.id / "response.npz").exists()

    session, worker = jobs.start_measurement(profile.profile_key, 1, 2, config, 20)
    worker.join(5)
    status = jobs.status(session.id)
    assert status["state"] == "COMPLETED"
    assert status["measurement"]["measurement_id"] == session.id
    assert status["measurement"]["quality"]["state"] == "valid"
    assert ctl.get_dsp_profile().revision == profile.revision
