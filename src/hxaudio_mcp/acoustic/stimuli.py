"""Generate repeatable, peak-limited measurement signals."""
import numpy as np
from scipy.fft import irfft, rfft, rfftfreq


def stimulus(kind: str, rate: int, samples: int, peak: float, seed: int,
             tones_hz: tuple[float, ...] = (1000,), low_hz: float = 20,
             high_hz: float = 20000) -> np.ndarray:
    if kind == "ess":
        if not 0 < low_hz < high_hz < rate / 2 or samples < 2:
            raise ValueError("Invalid ESS band or duration")
        duration = samples / rate
        scale = duration / np.log(high_hz / low_hz)
        time = np.arange(samples) / rate
        signal = np.sin(2 * np.pi * low_hz * scale * np.expm1(time / scale))
    elif kind == "periodic_pink_noise":
        period = min(samples, rate * 2)
        frequencies = rfftfreq(period, 1 / rate)
        spectrum = rfft(np.random.default_rng(seed).standard_normal(period))
        valid = (frequencies >= low_hz) & (frequencies <= high_hz)
        spectrum[~valid] = 0
        spectrum[valid] /= np.sqrt(frequencies[valid])
        signal = np.resize(irfft(spectrum, n=period), samples)
    elif kind in {"sine", "multitone"}:
        if not tones_hz or (kind == "sine" and len(tones_hz) != 1):
            raise ValueError("sine requires one tone and multitone requires at least one")
        if any(not 0 < frequency < rate / 2 for frequency in tones_hz):
            raise ValueError("Tones must lie below Nyquist and above DC")
        time = np.arange(samples) / rate
        signal = sum(np.sin(2 * np.pi * frequency * time) for frequency in tones_hz)
    else:
        raise ValueError(f"Unknown stimulus: {kind}")
    maximum = np.max(np.abs(signal))
    if not maximum:
        raise ValueError("Stimulus has no energy in the selected frequency range")
    return (signal * (peak / maximum)).astype(np.float32)
