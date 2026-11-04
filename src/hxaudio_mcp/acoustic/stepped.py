"""Stepped-sine stimuli and narrowband response estimates."""
import numpy as np
from scipy.signal.windows import tukey


def stepped_signal(rate, duration, peak, frequencies):
    dwell = round(rate * duration / len(frequencies))
    time = np.arange(dwell) / rate
    tones = np.sin(2 * np.pi * np.asarray(frequencies)[:, None] * time)
    return (peak * tones * tukey(dwell, 0.2)).ravel().astype(np.float32)


def stepped_response(recordings, frequencies, dwell, delay):
    """Measure the middle of each tone and reject noisy or unstable points."""
    levels, snrs, stability, metrics = [], [], [], []
    for recording, rate in recordings:
        peak = float(np.max(np.abs(recording)))
        metrics.append({"peak": peak, "capture_rate": rate})
        if peak >= 0.999 or not np.isfinite(recording).all():
            raise ValueError("Invalid or clipped stepped-sine capture")
        width = round(min(dwell / 6, 0.25) * rate)
        time = np.arange(width) / rate
        window = np.hanning(width)
        take, noise, variation = [], [], []
        for index, frequency in enumerate(frequencies):
            start = round((0.5 + delay + (index + 0.25) * dwell) * rate)
            end = start + 3 * width
            if end > len(recording) or frequency >= rate * 0.45:
                take.append(np.nan)
                noise.append(-np.inf)
                variation.append(np.inf)
                continue
            kernel = window * np.exp(-2j * np.pi * frequency * time)
            blocks = recording[start:end].reshape(3, width)
            amplitudes = 2 * np.abs(blocks @ kernel) / window.sum()
            floor = 2 * max(
                abs(recording[offset:offset + width] @ kernel)
                for offset in range(0, rate // 2 - width + 1, width)
            ) / window.sum()
            block_db = 20 * np.log10(np.maximum(amplitudes, 1e-10))
            take.append(float(np.median(block_db)))
            noise.append(take[-1] - 20 * np.log10(max(floor, 1e-10)))
            variation.append(float(np.ptp(block_db)))
        levels.append(take)
        snrs.append(noise)
        stability.append(variation)
    response = np.median(levels, axis=0)
    spread = np.ptp(np.where(np.isfinite(levels), levels, 0), axis=0)
    valid = (np.isfinite(response) & (np.min(snrs, axis=0) >= 15)
             & (np.max(stability, axis=0) <= 2) & (spread <= 1.5))
    confidence = np.where(valid, np.exp(-spread / 3), 0)
    return response, confidence, spread, metrics
