"""Register calibration MCP tools without loading optional DSP dependencies."""
from functools import wraps
from pathlib import Path
from typing import Literal


def register(server, controller, root: Path):
    jobs = None

    def manager():
        nonlocal jobs
        if jobs is None:
            from .acoustic.audio_io import SoundDeviceBackend
            from .acoustic.calibration import CalibrationJobs

            jobs = CalibrationJobs(controller, SoundDeviceBackend(), root)
        return jobs

    def tool(function):
        @wraps(function)
        def invoke(*args, **kwargs):
            try:
                return function(*args, **kwargs)
            except FileNotFoundError:
                code, action = "artifact_not_found", "Check the ID and local artifact directory."
            except ImportError:
                code, action = "dependency_missing", "Install the calibration extra."
            except (ValueError, RuntimeError, OSError) as error:
                message = str(error)
                if message == "external_revision_conflict":
                    code, action = message, "Read the current DSP profile and remeasure."
                else:
                    code = "invalid_input" if isinstance(error, ValueError) else "operation_failed"
                    return {"state": "error", "error": {"code": code, "message": message,
                            "action": "Check settings, audio routing and active session status."}}
            return {"state": "error", "error": {"code": code, "action": action}}

        server.tool()(invoke)
        return function

    @tool
    def calibration_check(profile_key: str | None = None, output: int | None = None,
                          capture: int | None = None, peak: float = 0.1) -> dict:
        """List endpoints and APO status. Supply profile and both endpoints for an audible probe."""
        result = {"audio": manager().backend.list_devices(),
                  "profiles": controller.list_devices(),
                  "runtime": controller.runtime_status()}
        if output is not None or capture is not None:
            if profile_key is None or output is None or capture is None:
                raise ValueError("A route probe requires profile_key, output and capture")
            result["probe"] = calibration_measure(profile_key, output, capture, 3, peak,
                                                  repetitions=1)
        return result

    @tool
    def calibration_run(profile_key: str, output: int, capture: int, rounds: int = 5,
                        duration_seconds: float = 5, peak: float = 0.1, keep: bool = True,
                        mode: Literal["fixed", "multi", "moving"] = "fixed", positions: int = 1,
                        target: Literal["neutral", "warm", "reference", "custom"] = "neutral",
                        target_points: list[list[float]] | None = None,
                        sample_rate: int = 48000, repetitions: int = 3, max_filters: int = 8,
                        max_boost_db: float = 3, max_cut_db: float = 8,
                        goal_db: float = 1, smoothing_octave: float = 1 / 6,
                        stereo: bool = False) -> dict:
        """Start calibration in the background. Poll status with the returned session ID."""
        from .acoustic.measurement import MeasurementConfig
        from .acoustic.optimizer import OptimizationConfig

        return manager().start(
            profile_key, output, capture,
            MeasurementConfig(sample_rate=sample_rate, duration_seconds=duration_seconds,
                              peak=peak, repetitions=repetitions,
                              smoothing_octave=smoothing_octave),
            OptimizationConfig(max_filters=max_filters, max_boost_db=max_boost_db,
                               max_cut_db=max_cut_db),
            target=target, rounds=rounds, keep=keep, mode=mode, positions=positions,
            target_points=target_points, goal_db=goal_db, stereo=stereo,
        )

    @tool
    def calibration_status(session_id: str) -> dict:
        """Read persisted progress and microphone placement instructions."""
        return manager().status(session_id)

    @tool
    def calibration_cancel(session_id: str) -> dict:
        """Request cancellation and recovery at the next capture boundary."""
        return manager().signal(session_id, cancel=True)

    @tool
    def calibration_resume(session_id: str) -> dict:
        """Resume after placing the microphone as requested in status."""
        return manager().signal(session_id, cancel=False)

    @tool
    def calibration_report(session_id: str) -> dict:
        """Return report artifact paths when the session finishes."""
        status = manager().status(session_id)
        return {"session_id": session_id, "state": status["state"],
                "reports": status.get("reports", {})}

    @tool
    def calibration_measure(profile_key: str, output: int, capture: int,
                            duration_seconds: float = 5, peak: float = 0.1,
                            sample_rate: int = 48000, repetitions: int = 3,
                            smoothing_octave: float = 1 / 6, background: bool = False,
                            timeout_seconds: float = 45, output_channel: int = 0,
                            input_channel: int = 0, tones_hz: list[float] | None = None,
                            transport_delay_seconds: float = 0.15) -> dict:
        """Archive a response. Supply tones_hz for a stepped-sine capture."""
        from .acoustic.measurement import MeasurementConfig

        if duration_seconds * repetitions > 30:
            raise ValueError("Standalone capture is limited to 30 seconds of playback")
        if not 1 <= timeout_seconds <= 120:
            raise ValueError("Measurement timeout must be between 1 and 120 seconds")
        config = MeasurementConfig(
            duration_seconds=duration_seconds, peak=peak, sample_rate=sample_rate,
            repetitions=repetitions, smoothing_octave=smoothing_octave,
            stimulus="stepped_sine" if tones_hz is not None else "periodic_pink_noise",
            transport_delay_seconds=transport_delay_seconds,
        )
        session, worker = manager().start_measurement(
            profile_key, output, capture, config, timeout_seconds, output_channel, input_channel,
            tuple(tones_hz) if tones_hz is not None else (1000,),
        )
        if not background:
            worker.join(timeout_seconds)
            status = session.read()
            if "measurement" in status:
                return status["measurement"]
            return {"session_id": session.id, "state": status["state"],
                    "stop_reason": status.get("stop_reason")}
        return {"session_id": session.id}

    def load_measurement(measurement_id):
        import json

        import numpy as np

        from .acoustic.measurement import MeasurementResult

        if len(measurement_id) != 32 or any(c not in "0123456789abcdef" for c in measurement_id):
            raise ValueError("Invalid measurement ID")
        session_file = root / measurement_id / "session.json"
        if session_file.exists():
            session_state = json.loads(session_file.read_text(encoding="utf-8"))
            if session_state["state"] != "COMPLETED":
                raise ValueError("Optimization requires a completed measurement")
        path = root.parent / measurement_id
        metadata = json.loads((path / "measurement.json").read_text(encoding="utf-8"))
        with np.load(path / "response.npz") as data:
            return path, MeasurementResult(**dict(data), metadata=metadata,
                                           quality_metrics=metadata["quality_metrics"])

    @tool
    def calibration_compare_tones(before_id: str, after_id: str) -> dict:
        """Compare common trusted stepped-sine frequencies from the same measurement route."""
        from .acoustic.evaluation import compare_tones

        return compare_tones(load_measurement(before_id)[1], load_measurement(after_id)[1])

    @tool
    def calibration_optimize(measurement_id: str, target: str = "neutral",
                             target_points: list[list[float]] | None = None,
                             max_filters: int = 8, max_boost_db: float = 3,
                             max_cut_db: float = 8, other_channel_id: str | None = None) -> dict:
        """Generate a PEQ candidate. Optionally fit both output channels to one shared EQ."""
        import json
        import uuid
        from dataclasses import asdict

        import numpy as np

        from .acoustic.optimizer import OptimizationConfig, optimize

        path, measurement = load_measurement(measurement_id)
        metadata = measurement.metadata
        if metadata["config"]["stimulus"] == "stepped_sine":
            raise ValueError("Use periodic pink noise for fitting and stepped sine for verification")
        profile = controller.get_dsp_profile(metadata["profile_key"])
        if profile.revision != metadata["revision"]:
            raise RuntimeError("external_revision_conflict")
        other = None
        if other_channel_id is not None:
            _, other = load_measurement(other_channel_id)
            for field in ("profile_key", "revision", "output", "input", "input_channel", "config"):
                if other.metadata[field] != metadata[field]:
                    raise ValueError(f"Stereo measurement mismatch: {field}")
            if {other.metadata["output_channel"], metadata["output_channel"]} != {0, 1}:
                raise ValueError("Stereo fitting requires separate left and right measurements")
        points = tuple(np.asarray(target_points, dtype=float).T) if target_points else None
        candidate = optimize(measurement, metadata["config"]["sample_rate"], target,
                             OptimizationConfig(max_filters=max_filters, max_boost_db=max_boost_db,
                                                max_cut_db=max_cut_db),
                             points=points, existing=tuple(profile.bands), other_channel=other)
        optimization_id = uuid.uuid4().hex
        file = path / f"candidate_{optimization_id}.json"
        payload = asdict(candidate)
        payload.update(profile_key=profile.profile_key, expected_revision=profile.revision,
                       bands=[band.to_dict() for band in (*profile.bands, *candidate.filters)],
                       other_channel_id=other_channel_id)
        file.write_text(json.dumps(payload, indent=2, default=lambda value: value.tolist(),
                                   allow_nan=False), encoding="utf-8")
        return {"optimization_id": optimization_id, "path": str(file),
                "predicted_rmse_db": candidate.predicted_rmse_db}

    @tool
    def calibration_recover(session_id: str) -> dict:
        """Restore an interrupted job when its last owned DSP revision is still current."""
        job = manager()
        with job.lock:
            if job.active and job.active[1].is_alive():
                raise RuntimeError("Calibration is already running")
            data = job.status(session_id)
            if data.get("stop_reason") != "interrupted_process":
                raise ValueError("Session is not interrupted")
            key = data["config"]["profile_key"]
            current = controller.get_dsp_profile(key)
            if current.revision == data.get("original_revision"):
                return {"state": "already_restored"}
            if current.revision != data.get("owned_revision") or not data.get("backup_id"):
                raise RuntimeError("external_revision_conflict")
            original = data["original_profile"]
            result = controller.apply_dsp_profile(original["bands"], original["preamp_db"],
                                                  current.revision, key, timeout_seconds=0)
            if result["state"] != "written":
                return result
            if result["profile"]["revision"] != data["original_revision"]:
                raise RuntimeError("Restored revision mismatch")
            import numpy as np

            cfg = data["config"]
            rate = cfg["measurement"]["sample_rate"]
            job.backend.play_and_record(np.zeros(rate, dtype=np.float32),
                                        rate, cfg["output"], cfg["capture"], 0, 0)
            result["runtime_confirmation"] = controller.wait_for_generation(
                result["apo_generation"], 2, key
            )
            return result
