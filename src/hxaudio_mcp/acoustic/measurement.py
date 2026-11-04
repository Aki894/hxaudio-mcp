"""Stationary transfer estimates and reproducible measurement artifacts."""
import json
import os
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import soundfile as sf
from scipy.ndimage import gaussian_filter1d
from scipy.signal import welch

from .audio_io import AudioBackend
from .stepped import stepped_response, stepped_signal
from .stimuli import stimulus


@dataclass(frozen=True)
class MeasurementConfig:
    sample_rate: int = 48000
    duration_seconds: float = 12
    repetitions: int = 3
    stimulus: str = "periodic_pink_noise"
    smoothing_octave: float = 1 / 6
    discard_initial_seconds: float = 1
    nperseg: int = 16384
    overlap: float = 0.5
    peak: float = 0.1
    seed: int = 0
    transport_delay_seconds: float = 0.15

    def __post_init__(self):
        if not (8000 <= self.sample_rate <= 192000 and 1 <= self.duration_seconds <= 60
                and 1 <= self.repetitions <= 10 and 0 <= self.discard_initial_seconds
                < self.duration_seconds and 256 <= self.nperseg <= 65536
                and 0 <= self.overlap < 1 and 0 <= self.smoothing_octave <= 2
                and 0 < self.peak <= 0.5):
            raise ValueError("Measurement configuration out of range")
        if not 0 <= self.transport_delay_seconds <= 0.45:
            raise ValueError("Transport delay must be between 0 and 0.45 seconds")
        if (self.stimulus != "stepped_sine"
                and (self.duration_seconds - self.discard_initial_seconds)
                * self.sample_rate < self.nperseg):
            raise ValueError("Measurement window is shorter than nperseg")


@dataclass
class MeasurementResult:
    frequencies_hz: np.ndarray
    response_db: np.ndarray
    raw_response_db: np.ndarray
    confidence: np.ndarray
    quality_metrics: dict
    metadata: dict


def analyze(reference: np.ndarray, recordings: list[tuple[np.ndarray, int]],
            config: MeasurementConfig) -> MeasurementResult:
    rate = config.sample_rate
    nfft = config.nperseg
    options = {"nperseg": nfft, "noverlap": int(nfft * config.overlap), "nfft": nfft}
    start = int(config.discard_initial_seconds * rate)
    frequencies, px = welch(reference[start:], rate, **options)
    responses = []
    metrics = []
    invalid = []
    warnings = []
    band_snr = []
    upper = min(20000, rate * 0.45)
    for recording, native_rate in recordings:
        if recording.ndim != 1 or not np.isfinite(recording).all() or native_rate < 10:
            invalid.append("malformed_recording")
            continue
        expected_frames = round(len(reference) * native_rate / rate)
        if len(recording) < expected_frames:
            invalid.append("short_recording")
            continue
        upper = min(upper, native_rate * 0.45)
        # Analyze stationary content after capture preroll and transport settling.
        end = expected_frames
        first = round((config.discard_initial_seconds + 0.5) * native_rate)
        clip = recording[first:end]
        native_fft = round(nfft * native_rate / rate)
        if len(clip) < native_fft:
            invalid.append("short_recording")
            continue
        peak = float(np.max(np.abs(recording)))
        rms = float(np.sqrt(np.mean(clip.astype(float) ** 2)))
        blocks = np.array_split(clip, max(1, len(clip) // (native_rate // 10)))
        block_rms = np.array([np.sqrt(np.mean(block.astype(float) ** 2)) for block in blocks])
        dropout = bool(np.any(block_rms < max(1e-7, rms * 0.03)))
        span = float(20 * np.log10(max(block_rms.max(), 1e-12) /
                                   max(block_rms.min(), 1e-12)))
        metrics.append({"peak": peak, "rms": rms, "dropout": dropout,
                        "block_level_span_db": span})
        if peak >= 0.999 or rms < 1e-5 or dropout:
            invalid.append("clipping" if peak >= 0.999 else "silence_or_dropout")
        elif len(block_rms) >= 4 and np.median(block_rms[-(len(block_rms) // 4):]) < (
            np.median(block_rms[:len(block_rms) // 4]) * 0.4
        ):
            invalid.append("sustained_level_drop")
        elif span > 12:
            warnings.append("level_variation")
        f, py = welch(clip, native_rate, nperseg=native_fft,
                      noverlap=int(native_fft * config.overlap))
        preroll = recording[:native_rate // 2]
        noise_fft = min(native_fft, len(preroll))
        nf, pn = welch(preroll, native_rate, nperseg=noise_fft)
        measured_power = np.interp(frequencies, f, py)
        noise_power = np.interp(frequencies, nf, pn)
        band_snr.append(10 * np.log10(
            np.maximum(measured_power, 1e-30) / np.maximum(noise_power, 1e-30)
        ))
        upper = min(upper, native_rate * 0.45)
        ratio = np.interp(frequencies, f, py) / np.maximum(px, 1e-30)
        responses.append(10 * np.log10(np.maximum(ratio, 1e-30)))

    raw = np.full_like(frequencies, np.nan)
    response = raw.copy()
    confidence = np.zeros_like(frequencies)
    valid = (frequencies >= 20) & (frequencies <= upper) & (px >= px.max() * 1e-5)
    if band_snr:
        snr = np.median(band_snr, axis=0)
        valid &= snr >= 10
    if not responses or not valid.any():
        invalid.append("no_usable_spectrum")
    else:
        estimates = np.array(responses)[:, valid]
        raw[valid] = np.median(estimates, axis=0)
        spread = np.std(estimates, axis=0)
        if np.median(spread) > 2:
            warnings.append("poor_repeatability")
        if not invalid:
            confidence[valid] = np.exp(-spread / 3) * np.clip(snr[valid] / 20, 0, 1)
        response[valid] = raw[valid]
        if config.smoothing_octave and config.stimulus == "periodic_pink_noise":
            log_frequency = np.log2(frequencies[valid])
            grid = np.linspace(log_frequency[0], log_frequency[-1], len(log_frequency))
            if len(grid) > 1:
                sigma = config.smoothing_octave / (2.355 * (grid[1] - grid[0]))
                smoothed = gaussian_filter1d(np.interp(grid, log_frequency, raw[valid]), sigma)
                response[valid] = np.interp(log_frequency, grid, smoothed)
    quality = {"state": "invalid" if invalid else "warning" if warnings else "valid",
               "reasons": sorted(set(invalid + warnings)), "takes": metrics,
               "repeatability_median_sd_db": float(np.median(spread))
               if responses and valid.any() else None,
               "effective_band_hz": [float(frequencies[valid][0]),
                                     float(frequencies[valid][-1])] if valid.any() else None}
    return MeasurementResult(frequencies, response, raw, confidence, quality, {})


def measure(backend: AudioBackend, config: MeasurementConfig, output: int, capture: int,
            profile_key: str, revision: str, root: Path | None = None,
            output_channel: int = 0, input_channel: int = 0,
            tones_hz: tuple[float, ...] = (1000,),
            check: Callable[[], None] | None = None,
            measurement_id: str | None = None) -> tuple[Path, MeasurementResult]:
    rate = config.sample_rate
    stepped = config.stimulus == "stepped_sine"
    if stepped:
        frequencies = np.asarray(tones_hz, dtype=float)
        if (frequencies.ndim != 1 or not 2 <= len(frequencies) <= 100
                or not np.isfinite(frequencies).all() or np.any(np.diff(frequencies) <= 0)
                or frequencies[0] < 20 or frequencies[-1] >= rate * 0.45
                or config.duration_seconds / len(frequencies) < 0.45
                or config.repetitions < 2):
            raise ValueError("Stepped sine requires 2–100 ascending tones, at least 0.45 seconds "
                             "per tone and two or more repetitions")
        signal = stepped_signal(rate, config.duration_seconds, config.peak, frequencies)
    else:
        signal = stimulus(config.stimulus, rate, round(config.duration_seconds * rate),
                          config.peak, config.seed, tones_hz)
    devices = backend.list_devices()
    output_info = next(device for device in devices if device["index"] == output)
    input_info = next(device for device in devices if device["index"] == capture)
    measurement_id = measurement_id or uuid.uuid4().hex
    if root is None:
        root = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "HXAudioMCP" / "calibrations"
    path = root / measurement_id
    path.mkdir(parents=True)
    sf.write(path / "stimulus.wav", signal, rate, subtype="FLOAT")
    metadata = {
        "measurement_id": measurement_id, "started_at": datetime.now(UTC).isoformat(),
        "config": asdict(config), "profile_key": profile_key, "revision": revision,
        "output": output_info, "input": input_info, "output_channel": output_channel,
        "input_channel": input_channel, "tones_hz": tones_hz,
        "stimulus_rms": float(np.sqrt(np.mean(signal ** 2))),
    }
    recordings = []
    try:
        for index in range(config.repetitions):
            if check:
                check()
            recording, capture_rate = backend.play_and_record(
                signal, rate, output, capture, output_channel, input_channel
            )
            sf.write(path / f"capture_{index + 1}.wav", recording, capture_rate, subtype="FLOAT")
            recordings.append((recording, capture_rate))
        if check:
            check()
        if stepped:
            values, confidence, spread, metrics = stepped_response(
                recordings, frequencies, len(signal) / rate / len(frequencies),
                config.transport_delay_seconds,
            )
            valid = confidence >= 0.5
            usable = np.count_nonzero(valid) >= 2
            quality = {
                "state": ("valid" if valid.all() else "warning") if usable else "invalid",
                "reasons": ([] if valid.all() else ["untrusted_tones"]) if usable
                else ["insufficient_usable_tones"],
                "takes": metrics,
                "effective_band_hz": frequencies[valid][[0, -1]].tolist() if usable else None,
                "repeatability_median_sd_db": float(np.median(spread[valid]) / 2)
                if usable and len(recordings) == 2 else None,
            }
            values -= 20 * np.log10(config.peak)
            result = MeasurementResult(frequencies, values, values.copy(), confidence, quality, {})
        else:
            result = analyze(signal, recordings, config)
        result.metadata = metadata
        np.savez(path / "response.npz", frequencies_hz=result.frequencies_hz,
                 response_db=result.response_db, raw_response_db=result.raw_response_db,
                 confidence=result.confidence)
        metadata["quality_metrics"] = result.quality_metrics
    except Exception as exc:
        metadata["quality_metrics"] = {"state": "invalid", "reasons": [str(exc)]}
        raise
    finally:
        (path / "measurement.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    return path, result
