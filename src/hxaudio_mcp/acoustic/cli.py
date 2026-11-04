"""Run a local acoustic measurement with explicit endpoint selection."""
import argparse
import json
from dataclasses import asdict
from pathlib import Path

import numpy as np

from ..core import HXAudioController
from ..registry import WindowsRegistry
from .audio_io import SoundDeviceBackend
from .compare import compare
from .ess import analyze_sweep, sweep_probe
from .measurement import MeasurementConfig, MeasurementResult, measure
from .optimizer import OptimizationConfig, optimize


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("devices")
    ess = commands.add_parser("ess")
    ess.add_argument("--output", type=int, required=True)
    ess.add_argument("--capture", type=int, required=True)
    ess.add_argument("--npz", type=Path, required=True)
    ess.add_argument("--duration", type=float, default=5)
    ess.add_argument("--peak", type=float, default=0.25)
    ess.add_argument("--window", type=float, default=0.15)
    ess.add_argument("--output-channel", type=int, default=0)
    ess.add_argument("--input-channel", type=int, default=0)
    ess.add_argument("--marker-peak", type=float)
    ess.add_argument("--marker-band", type=float, nargs=2, metavar=("LOW", "HIGH"))
    ess.add_argument("--marker-repeats", type=int, default=1)
    diff = commands.add_parser("compare")
    diff.add_argument("before", type=Path)
    diff.add_argument("after", type=Path)
    diff.add_argument("--csv", type=Path, required=True)
    fit = commands.add_parser("optimize")
    fit.add_argument("measurement", type=Path)
    fit.add_argument("--target", choices=("neutral", "warm", "reference", "custom"),
                     default="neutral")
    fit.add_argument("--target-points", type=Path, help="Two-column Hz,dB CSV without header")
    fit.add_argument("--max-filters", type=int, default=8)
    fit.add_argument("--json", type=Path, required=True)
    run = commands.add_parser("measure")
    run.add_argument("--output", type=int, required=True)
    run.add_argument("--capture", type=int, required=True)
    run.add_argument("--profile-key", required=True)
    run.add_argument("--output-channel", type=int, default=0)
    run.add_argument("--input-channel", type=int, default=0)
    run.add_argument("--sample-rate", type=int, default=48000)
    run.add_argument("--duration", type=float, default=12, help="Seconds per take")
    run.add_argument("--peak", type=float, default=0.1)
    run.add_argument("--root", type=Path)
    run.add_argument("--tones-hz", nargs="+", type=float)
    run.add_argument("--transport-delay", type=float, default=0.15)
    run.add_argument("--repetitions", type=int, default=3)
    args = parser.parse_args()
    if args.command == "compare":
        print(json.dumps(compare(args.before, args.after, args.csv), indent=2))
        return
    if args.command == "optimize":
        metadata = json.loads((args.measurement / "measurement.json").read_text(encoding="utf-8"))
        if metadata["config"]["stimulus"] == "stepped_sine":
            parser.error("Use periodic pink noise for fitting and stepped sine for verification")
        controller = HXAudioController(WindowsRegistry(), read_only=True)
        profile = controller.get_dsp_profile(metadata["profile_key"])
        if profile.revision != metadata["revision"]:
            parser.error("DSP profile changed since measurement")
        with np.load(args.measurement / "response.npz") as data:
            measurement = MeasurementResult(**dict(data),
                                            quality_metrics=metadata["quality_metrics"],
                                            metadata=metadata)
        points = None
        if args.target_points:
            frequency, gain = np.loadtxt(args.target_points, delimiter=",", unpack=True)
            points = (frequency, gain)
        result = optimize(measurement, metadata["config"]["sample_rate"], args.target,
                          OptimizationConfig(max_filters=args.max_filters), points,
                          tuple(profile.bands))
        payload = asdict(result)
        for field in ("frequencies_hz", "predicted_response", "target_response"):
            payload[field] = payload[field].tolist()
        payload["candidate_profile"] = {
            "profile_key": profile.profile_key,
            "expected_revision": profile.revision,
            "bands": [band.to_dict() for band in (*profile.bands, *result.filters)],
            "preamp_db": result.preamp_db,
        }
        args.json.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print(json.dumps({"path": str(args.json), "filters": len(result.filters),
                          "predicted_rmse_db": result.predicted_rmse_db, **result.diagnostics}))
        return
    backend = SoundDeviceBackend()
    if args.command == "devices":
        print(json.dumps(backend.list_devices(), indent=2))
        return
    if args.command == "ess":
        rate = 48000
        probe = sweep_probe(rate, args.duration, args.peak, marker_peak=args.marker_peak,
                            marker_band=args.marker_band, marker_repeats=args.marker_repeats)
        recording, capture_rate = backend.play_and_record(
            probe[0], rate, args.output, args.capture, args.output_channel, args.input_channel
        )
        try:
            result = analyze_sweep(recording, capture_rate, rate, probe, window=args.window)
        except ValueError as exc:
            with args.npz.open("xb") as artifact:
                np.savez_compressed(artifact, recording=recording, playback=probe[0],
                                    capture_rate=capture_rate, playback_rate=rate, error=str(exc))
            parser.exit(1, f"{exc}. Raw sweep saved to {args.npz}\n")
        with args.npz.open("xb") as artifact:
            np.savez_compressed(artifact, recording=recording, playback=probe[0],
                                capture_rate=capture_rate, playback_rate=rate, **result)
        print(json.dumps({"path": str(args.npz), "drift_ppm": result["drift_ppm"],
                          "delay_seconds": result["delay_seconds"],
                          "marker_correlation": result["marker_correlation"].tolist(),
                          "marker_timing_residual_seconds":
                              result["marker_timing_residual_seconds"]}))
        return

    controller = HXAudioController(WindowsRegistry(), read_only=True)
    if args.profile_key not in {device["profile_key"] for device in controller.list_devices()}:
        parser.error("Unknown HXAudio profile key")
    profile = controller.get_dsp_profile(args.profile_key)
    path, result = measure(
        backend, MeasurementConfig(sample_rate=args.sample_rate, peak=args.peak,
                                   duration_seconds=args.duration, repetitions=args.repetitions,
                                   stimulus="stepped_sine" if args.tones_hz is not None
                                   else "periodic_pink_noise",
                                   transport_delay_seconds=args.transport_delay),
        args.output, args.capture, profile.profile_key, profile.revision, args.root,
        args.output_channel, args.input_channel, tones_hz=tuple(args.tones_hz or (1000,)),
    )
    if controller.get_dsp_profile(args.profile_key).revision != profile.revision:
        metadata = result.metadata
        result.quality_metrics["state"] = "invalid"
        result.quality_metrics["reasons"].append("dsp_revision_changed")
        (path / "measurement.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    print(json.dumps({"path": str(path), "quality": result.quality_metrics}, indent=2))
    if result.quality_metrics["state"] == "invalid":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
