"""Background measure, optimize, apply and verify workflow."""
import ctypes
import json
import sys
import threading
import time
from dataclasses import asdict
from pathlib import Path

import numpy as np

from .evaluation import ACCEPTANCE_GRID, compare_to_target, evaluate_bands
from .measurement import MeasurementConfig, measure
from .optimizer import OptimizationConfig, optimize
from .report import report
from .session import Session
from .spatial import aggregate
from .targets import target_curve


class InvalidCapture(ValueError):
    """A measurement completed with unusable audio."""


class CalibrationJobs:
    def __init__(self, controller, backend, root: Path):
        self.controller = controller
        self.backend = backend
        self.root = root
        self.lock = threading.Lock()
        self.active = None

    def start(self, profile_key: str, output: int, capture: int,
              measurement: MeasurementConfig, optimization: OptimizationConfig,
              target: str = "neutral", rounds: int = 5, keep: bool = True,
              mode: str = "fixed", positions: int = 1, goal_db: float = 1,
              target_points: list[list[float]] | None = None, stereo: bool = False) -> dict:
        if (not 1 <= rounds <= 20 or mode not in {"fixed", "multi", "moving"}
                or not 1 <= positions <= 20 or not np.isfinite(goal_db) or goal_db < 0
                or target not in {"neutral", "warm", "reference", "custom"}
                or measurement.stimulus != "periodic_pink_noise"):
            raise ValueError("Invalid calibration settings")
        if mode != "multi" and positions != 1:
            raise ValueError("Multiple positions require multi mode")
        points = tuple(np.asarray(target_points, dtype=float).T) if target_points else None
        if target in {"reference", "custom"} and (points is None or len(points) != 2):
            raise ValueError("Target requires Hz,dB pairs")
        if getattr(self.controller, "read_only", False):
            raise ValueError("Calibration requires DSP write access")
        with self.lock:
            if self.active and self.active[1].is_alive():
                raise RuntimeError("Calibration is already running")
            session = Session(self.root, dict(
                profile_key=profile_key, output=output, capture=capture, target=target,
                measurement=asdict(measurement), optimization=asdict(optimization),
                rounds=rounds, keep=keep, mode=mode, positions=positions, goal_db=goal_db,
                target_points=target_points, stereo=stereo,
            ))
            worker = threading.Thread(target=self._run, args=(session, self._run_job, measurement,
                                      optimization, points), daemon=True)
            self.active = (session, worker)
            worker.start()
            return {"session_id": session.id}

    def start_measurement(self, profile_key, output, capture, config, timeout_seconds,
                          output_channel=0, input_channel=0, tones_hz=(1000,)):
        """Reserve the same audio route used by the calibration loop."""
        with self.lock:
            if self.active and self.active[1].is_alive():
                raise RuntimeError("Calibration is already running")
            session = Session(self.root, dict(
                kind="measurement", profile_key=profile_key, output=output, capture=capture,
                measurement=asdict(config), timeout_seconds=timeout_seconds,
                output_channel=output_channel, input_channel=input_channel, tones_hz=tones_hz,
            ))
            worker = threading.Thread(target=self._run,
                                      args=(session, self._measure_job, config), daemon=True)
            self.active = (session, worker)
            worker.start()
        return session, worker

    def _measure_job(self, session, config):
        cfg = session.data["config"]
        key = cfg["profile_key"]
        deadline = time.monotonic() + cfg["timeout_seconds"]

        def check():
            if session.cancel.is_set():
                raise InterruptedError("cancel_requested")
            if time.monotonic() >= deadline:
                raise TimeoutError("measurement_timeout")
            if self.controller.get_dsp_profile(key).revision != profile.revision:
                raise RuntimeError("external_revision_conflict")

        try:
            profile = self.controller.get_dsp_profile(key)
            session.update("MEASURING")
            path, result = measure(
                self.backend, config, cfg["output"], cfg["capture"], key, profile.revision,
                self.root.parent, cfg["output_channel"], cfg["input_channel"],
                tones_hz=cfg["tones_hz"], check=check, measurement_id=session.id,
            )
            check()
            session.update("COMPLETED", measurement={
                "measurement_id": path.name, "path": str(path), "quality": result.quality_metrics,
            })
        except InterruptedError:
            session.update("CANCELLED", stop_reason="cancel_requested")
        except TimeoutError:
            session.update("FAILED", stop_reason="measurement_timeout")
        except Exception as exc:
            session.update("FAILED", stop_reason=str(exc))

    def status(self, session_id: str) -> dict:
        if len(session_id) != 32 or any(c not in "0123456789abcdef" for c in session_id):
            raise ValueError("Invalid session ID")
        active = self.active
        if active and active[0].id == session_id:
            return active[0].read()
        data = json.loads((self.root / session_id / "session.json").read_text(encoding="utf-8"))
        terminal = {"FAILED", "CANCELLED", "COMPLETED"}
        if data["state"] not in terminal:
            data["state"] = "FAILED"
            data["stop_reason"] = "interrupted_process"
        return data

    def signal(self, session_id: str, cancel: bool) -> dict:
        with self.lock:
            if not self.active or self.active[0].id != session_id or not self.active[1].is_alive():
                raise ValueError("Session is not running")
            session = self.active[0]
            if self.status(session_id)["state"] in {"FAILED", "CANCELLED", "COMPLETED"}:
                raise ValueError("Session is already finished")
            if cancel:
                session.cancel.set()
            elif self.status(session_id)["state"] == "PAUSED":
                session.resume.set()
            else:
                raise ValueError("Session is not paused")
        return {"session_id": session_id, "accepted": True}

    def _run(self, session, operation, *args):
        if sys.platform != "win32":
            operation(session, *args)
            return
        ole = ctypes.windll.ole32
        if ole.CoInitializeEx(None, 2) < 0:
            session.update("FAILED", stop_reason="Windows audio thread COM initialization failed")
            return
        try:
            operation(session, *args)
        finally:
            ole.CoUninitialize()

    def _run_job(self, session, measurement, optimization, points):
        cfg = session.data["config"]
        ctl = self.controller
        key = cfg["profile_key"]
        output, capture = cfg["output"], cfg["capture"]
        original = current = best = None
        backup = None
        curves = {}
        reason = "iteration_limit"
        state = "COMPLETED"

        def conflict():
            session.resume.clear()
            session.update("PAUSED", instruction="DSP changed externally. Review the current "
                           "profile, then resume to close this session or cancel.")
            while not session.cancel.is_set() and not session.resume.wait(0.2):
                pass
            raise RuntimeError("external_revision_conflict")

        def guard():
            if session.cancel.is_set():
                raise InterruptedError("cancelled")
            if ctl.get_dsp_profile(key).revision != current.revision:
                conflict()

        def warm(generation):
            if generation is None:
                raise RuntimeError("APO generation unavailable")
            self.backend.play_and_record(
                np.zeros(measurement.sample_rate, dtype=np.float32),
                measurement.sample_rate, output, capture, 0, 0,
            )
            if ctl.wait_for_generation(generation, 2, key)["state"] != "observed":
                raise RuntimeError("APO generation not confirmed")

        def apply(bands, preamp):
            nonlocal current
            result = ctl.apply_dsp_profile(bands, preamp, current.revision, key, timeout_seconds=0)
            if result["state"] == "revision_conflict":
                conflict()
            written = ctl.get_dsp_profile(key)
            if written.revision != result["profile"]["revision"]:
                conflict()
            current = written
            session.update(session.data["state"], owned_revision=current.revision)
            if result["state"] != "written":
                raise RuntimeError(result["state"])
            warm(result["apo_generation"])

        channels = (0, 1) if cfg["stereo"] else (0,)

        def sample(phase):
            takes = {}
            for i in range(cfg["positions"]):
                if cfg["mode"] != "fixed":
                    session.resume.clear()
                    action = "move microphone slowly" if cfg["mode"] == "moving" else (
                        f"place microphone at position {i + 1}"
                    )
                    session.update("PAUSED", instruction=action, phase=phase)
                    while not session.resume.wait(0.2):
                        guard()
                for channel in channels:
                    guard()
                    session.update(phase, instruction=None, position=i + 1, output_channel=channel)
                    path, take = measure(
                        self.backend, measurement, output, capture, key, current.revision,
                        session.path, output_channel=channel, check=guard,
                    )
                    session.data.setdefault("measurements", []).append(
                        {"phase": phase, "position": i + 1, "output_channel": channel,
                         "path": str(path), "quality": take.quality_metrics}
                    )
                    session.update(phase)
                    guard()
                    if take.quality_metrics["state"] == "invalid":
                        reasons = ", ".join(take.quality_metrics.get("reasons", []))
                        raise InvalidCapture(f"{phase.lower()} quality invalid: {reasons}")
                    takes[i + 1, channel] = take
            return takes

        def channel_aggregate(takes, channel):
            selected = [take for (_, ch), take in takes.items() if ch == channel]
            return aggregate(selected) if len(selected) > 1 else selected[0]

        def evaluate(before, after, target):
            return [dict(evaluate_bands(take, after[position, channel], target),
                         position=position, output_channel=channel)
                    for (position, channel), take in before.items()]

        def band_results(results):
            return [band for pair in results for band in pair["bands"].values()]

        def target_reached(results):
            return all(band["after_rmse_db"] is not None
                       and band["after_rmse_db"] <= cfg["goal_db"]
                       for band in band_results(results))

        try:
            session.update("CHECKING")
            original = current = best = ctl.get_dsp_profile(key)
            devices = {device["index"]: device for device in self.backend.list_devices()}
            if output not in devices or capture not in devices:
                raise ValueError("Audio device unavailable")
            if (devices[output].get("max_output_channels", 1) < len(channels)
                    or devices[capture].get("max_input_channels", 1) < 1):
                raise ValueError("Selected endpoints do not support the requested direction")
            backup = ctl.create_backup()["backup_id"]
            session.update("CHECKING", backup_id=backup, original_revision=original.revision,
                           original_profile=original.to_dict(), owned_revision=current.revision)
            guard()
            baseline_takes = accepted_takes = sample("BASELINE_MEASUREMENT")
            baseline = accepted = channel_aggregate(baseline_takes, 0)
            session.update("BASELINE_MEASUREMENT", baseline_quality=baseline.quality_metrics)
            for index in range(cfg["rounds"]):
                session.update("OPTIMIZING")
                guard()
                coverage = evaluate(accepted_takes, accepted_takes, np.zeros_like(ACCEPTANCE_GRID))
                if any(b["status"] == "insufficient_coverage" for b in band_results(coverage)):
                    session.update("OPTIMIZING", coverage_check=coverage)
                    reason = "insufficient_band_coverage"
                    break
                other = channel_aggregate(accepted_takes, 1) if cfg["stereo"] else None
                candidate = optimize(accepted, measurement.sample_rate, cfg["target"],
                                     optimization, points, tuple(current.bands), other_channel=other)
                target = np.interp(ACCEPTANCE_GRID, candidate.frequencies_hz,
                                   candidate.target_response, left=np.nan, right=np.nan)
                current_scores = evaluate(accepted_takes, accepted_takes, target)
                session.update("OPTIMIZING", current_band_scores=current_scores)
                if any(b["status"] == "insufficient_coverage"
                       for b in band_results(current_scores)):
                    reason = "insufficient_band_coverage"
                    break
                if not curves:
                    curves = {"frequency": candidate.frequencies_hz,
                              "before": np.interp(candidate.frequencies_hz,
                                                  baseline.frequencies_hz, baseline.response_db),
                              "target": candidate.target_response,
                              "after": np.interp(candidate.frequencies_hz,
                                                 accepted.frequencies_hz, accepted.response_db)}
                if target_reached(current_scores):
                    reason = "target_reached"
                    break
                if not candidate.filters:
                    reason = "no_candidate"
                    break
                bands = [b.to_dict() for b in (*current.bands, *candidate.filters)]
                entry = {"iteration": index + 1, "bands": bands,
                         "preamp_db": candidate.preamp_db,
                         "predicted_rmse_db": candidate.predicted_rmse_db}
                session.data["iterations"].append(entry)
                session.update("APPLYING")
                apply(bands, candidate.preamp_db)
                try:
                    verified_takes = sample("VERIFYING")
                except InvalidCapture as exc:
                    reason = "verification_quality_degraded"
                    entry.update(accepted=False, rejection=str(exc))
                    break
                session.update("EVALUATING")
                results = evaluate(accepted_takes, verified_takes, target)
                statuses = {band["status"] for band in band_results(results)}
                rejection = None
                if "insufficient_coverage" in statuses:
                    rejection = "insufficient_band_coverage"
                elif "worsened" in statuses:
                    rejection = "band_worsened"
                elif ("improved" not in statuses
                      or np.mean([pair["improvement_db"] - pair["repeatability_threshold_db"]
                                  for pair in results]) <= 0):
                    rejection = "within_repeatability"
                verified = channel_aggregate(verified_takes, 0)
                entry.update(pair_results=results, accepted=rejection is None,
                             verification_quality=verified.quality_metrics)
                if rejection:
                    entry["rejection"] = reason = rejection
                    break
                best, accepted, accepted_takes = current, verified, verified_takes
                f = candidate.frequencies_hz
                curves = {"frequency": f, "before": np.interp(
                    f, baseline.frequencies_hz, baseline.response_db),
                    "target": candidate.target_response, "after": np.interp(
                    f, accepted.frequencies_hz, accepted.response_db)}
                session.update("EVALUATING", best_revision=best.revision)
                if target_reached(results):
                    reason = "target_reached"
                    break
            guard()
            if curves:
                f = curves["frequency"]
                measured = np.interp(f, baseline.frequencies_hz, baseline.response_db)
                weight = np.interp(f, baseline.frequencies_hz, baseline.confidence)
                weight[~np.isfinite(measured)] = 0
                measured = np.nan_to_num(measured)
                final_target = np.interp(ACCEPTANCE_GRID, f, curves["target"],
                                         left=np.nan, right=np.nan)
                session.update(session.data["state"],
                               final_band_scores=evaluate(baseline_takes, accepted_takes, final_target))
                comparisons = {}
                for mode in ("neutral", "warm"):
                    target = target_curve(f, measured, weight, mode)
                    comparisons[mode] = compare_to_target(baseline, accepted, f, target)
                session.update(session.data["state"], target_comparison=comparisons)
        except InterruptedError:
            state, reason = "CANCELLED", "cancelled"
            session.update("CANCELLING")
        except Exception as exc:
            reason = str(exc)
            state = "CANCELLED" if reason == "external_revision_conflict" and (
                session.cancel.is_set()
            ) else "FAILED"
        finally:
            try:
                if current and original and reason != "external_revision_conflict":
                    if ctl.get_dsp_profile(key).revision != current.revision:
                        raise RuntimeError("external_revision_conflict")
                    desired = best if state == "COMPLETED" and cfg["keep"] else original
                    if current.revision != desired.revision:
                        if desired.revision == original.revision:
                            with ctl.lock:
                                if ctl.get_dsp_profile(key).revision != current.revision:
                                    raise RuntimeError("external_revision_conflict")
                                result = ctl.restore_backup(backup, confirm=True)
                                current = ctl.get_dsp_profile(key)
                            warm(result["apo_generation"])
                        else:
                            apply([b.to_dict() for b in desired.bands], desired.preamp_db)
                        if current.revision != desired.revision:
                            raise RuntimeError("Restored revision mismatch")
            except Exception as exc:
                state, reason = "FAILED", f"{reason}; recovery: {exc}"
            summary = {**session.data, "state": state, "stop_reason": reason}
            try:
                files = report(session.path, summary, best.to_dict() if best else {}, curves)
            except Exception as exc:
                files = {}
                state, reason = "FAILED", f"{reason}; report: {exc}"
            session.update(state, stop_reason=reason, reports=files,
                           final_revision=current.revision if current else None)
