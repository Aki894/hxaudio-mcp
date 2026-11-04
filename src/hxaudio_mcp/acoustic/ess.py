"""Clock-aligned exponential sweep measurement and harmonic separation."""
from math import gcd

import numpy as np
from scipy.signal import correlate, fftconvolve, resample_poly
from scipy.signal.windows import tukey

from .stimuli import stimulus


def sweep_probe(rate=48000, duration=5, peak=0.25, low=20, high=20000, *,
                marker_peak=None, marker_band=None, marker_repeats=1):
    if not 2 <= duration <= 30 or not 0 < peak <= 0.5:
        raise ValueError("ESS requires 2–30 seconds and a peak within (0, 0.5]")
    sweep = stimulus("ess", rate, round(duration * rate), peak, 0, low_hz=low, high_hz=high)
    marker_peak = peak if marker_peak is None else marker_peak
    lower, upper = (500, min(6000, rate / 4)) if marker_band is None else marker_band
    if (not 0 < marker_peak <= 0.5 or not 20 <= lower < upper < rate / 2
            or type(marker_repeats) is not int or not 1 <= marker_repeats <= 4):
        raise ValueError("Invalid ESS marker amplitude, frequency band or repetition count")
    length = rate // 10
    parts = [
        stimulus("periodic_pink_noise", rate, length, marker_peak, 91 + index,
                 low_hz=lower, high_hz=upper) * tukey(length)
        for index in range(marker_repeats)
    ]
    silence = np.zeros(rate // 25)
    marker = np.concatenate([part for index, tone in enumerate(parts)
                             for part in ((silence, tone) if index else (tone,))])
    guard = np.zeros(rate // 4)
    gap = np.zeros(rate // 2)
    start = len(guard) + len(marker) + len(gap)
    end_marker = start + len(sweep) + len(gap)
    playback = np.concatenate((guard, marker, gap, sweep, gap, marker, guard))
    return playback.astype(np.float32), sweep, marker, start, end_marker


def _locate(recording, marker, lo, hi):
    lo = max(0, lo)
    segment = recording[lo:min(len(recording), hi + len(marker))]
    if len(segment) < len(marker):
        raise ValueError("Missing ESS timing marker")
    corr = correlate(segment, marker, mode="valid")
    energy = fftconvolve(segment**2, np.ones(len(marker)), mode="valid")
    score = abs(corr) / np.sqrt(np.maximum(energy, 1e-12) * np.sum(marker**2))
    index = int(np.argmax(score))
    if not np.isfinite(score[index]) or score[index] < 0.2:
        raise ValueError("ESS timing marker has low correlation")
    return lo + index, float(score[index])


def _marker_arrivals(recording, marker, rate, arrival):
    length = rate // 10
    stride = length + rate // 25
    radius = round(rate * 0.012)
    arrivals, scores = [], []
    for offset in range(0, len(marker) - length + 1, stride):
        center = arrival + offset
        try:
            position, score = _locate(recording, marker[offset:offset + length],
                                      center - radius, center + radius)
        except ValueError:
            continue
        arrivals.append((offset, position))
        scores.append(score)
    return arrivals, scores


def _locate_group(recording, marker, rate, lo, hi):
    try:
        return _locate(recording, marker, lo, hi)
    except ValueError:
        length = rate // 10
        stride = length + rate // 25
        candidates = []
        for offset in range(0, len(marker) - length + 1, stride):
            try:
                position, _ = _locate(recording, marker[offset:offset + length],
                                      lo + offset, hi + offset)
            except ValueError:
                continue
            arrivals, scores = _marker_arrivals(recording, marker, rate, position - offset)
            if len(arrivals) >= 2:
                start = int(round(np.median([arrival - part for part, arrival in arrivals])))
                candidates.append((len(arrivals), float(np.mean(scores)), start))
        if candidates:
            _, score, start = max(candidates)
            return start, score
        raise


def analyze_sweep(recording, capture_rate, rate, probe, low=20, high=20000, window=0.15):
    _, sweep, marker, start, end_marker = probe
    recording = np.asarray(recording, dtype=float)
    if (recording.ndim != 1 or not recording.size or not np.all(np.isfinite(recording))
            or np.max(abs(recording)) >= 0.999):
        raise ValueError("Invalid or clipped ESS recording")
    divisor = gcd(rate, capture_rate)
    recording = resample_poly(recording, rate // divisor, capture_rate // divisor)
    first_sent = rate // 4
    first, first_score = _locate_group(recording, marker, rate, 0, rate * 2)
    spacing = end_marker - first_sent
    last, last_score = _locate_group(recording, marker, rate, first + spacing - rate // 8,
                                    first + spacing + rate // 8)
    first_parts, first_scores = _marker_arrivals(recording, marker, rate, first)
    last_parts, last_scores = _marker_arrivals(recording, marker, rate, last)
    timing_residual = 0.0
    if len(marker) > rate // 10:
        if min(len(first_parts), len(last_parts)) < 2:
            raise ValueError("Insufficient consistent ESS timing markers")
        pairs = np.array([
            (sent + offset, received)
            for sent, parts in ((first_sent, first_parts), (end_marker, last_parts))
            for offset, received in parts
        ])
        slope, intercept = np.polyfit(pairs[:, 0], pairs[:, 1], 1)
        timing_residual = float(np.max(np.abs(pairs[:, 1] - (
            slope * pairs[:, 0] + intercept
        ))) / rate)
        if timing_residual > 0.003:
            raise ValueError("ESS timing markers disagree by more than 3 ms")
        first = slope * first_sent + intercept
        last = slope * end_marker + intercept
    ratio = (last - first) / spacing
    if abs(ratio - 1) > 0.005:
        raise ValueError("ESS capture clock drift exceeds 5000 ppm")
    scale = len(sweep) / np.log(high / low)
    before = round(rate * 0.005)
    after = round(rate * window)
    if after <= before or after + before >= round(scale * np.log(5 / 4)) or window > 0.45:
        raise ValueError("ESS window overlaps adjacent harmonic responses or exceeds the guard")
    positions = first + ratio * (np.arange(start, start + len(sweep) + after) - first_sent)
    if positions[-1] >= len(recording):
        raise ValueError("Truncated ESS recording")
    aligned = np.interp(positions, np.arange(len(recording)), recording)
    inverse = sweep[::-1] * np.exp(-np.arange(len(sweep)) / scale)
    reference = fftconvolve(sweep, inverse)
    center = len(sweep) - 1
    impulse = fftconvolve(aligned, inverse) / reference[center]
    reference /= reference[center]
    taper = tukey(before + after, 0.05)
    offsets = np.rint(scale * np.log(np.arange(1, 6))).astype(int)
    windows = np.stack([impulse[center-offset-before:center-offset+after] * taper
                        for offset in offsets])
    nfft = max(rate, len(taper))
    spectra = abs(np.fft.rfft(windows, n=nfft))
    ref = abs(np.fft.rfft(reference[center-before:center+after] * taper, n=nfft))
    frequency = np.fft.rfftfreq(nfft, 1 / rate)
    valid = ((frequency >= max(low, 3 / window))
             & (frequency <= min(high, 0.45 * min(rate, capture_rate)))
             & (ref > 0.01 * ref.max()))
    thd_valid = valid & (frequency * 5 <= min(high, 0.45 * min(rate, capture_rate)))
    distortion = np.full_like(frequency, np.nan)
    harmonic_power = sum(np.interp(frequency * order, frequency, spectra[order-1])**2
                         for order in range(2, 6))
    distortion[thd_valid] = (np.sqrt(harmonic_power[thd_valid])
                             / np.maximum(spectra[0, thd_valid], 1e-12))
    response = np.full_like(frequency, np.nan)
    response[valid] = 20 * np.log10(np.maximum(spectra[0, valid] / ref[valid], 1e-12))
    return dict(frequencies_hz=frequency, response_db=response, valid=valid,
                harmonic_distortion=distortion, harmonic_distortion_valid=thd_valid,
                impulse_response=windows[0],
                impulse_time_seconds=(np.arange(len(taper)) - before) / rate,
                drift_ppm=(ratio-1)*1e6, delay_seconds=(first-first_sent)/rate,
                marker_correlation=np.array([first_score, last_score]),
                marker_part_correlation=np.array(first_scores + last_scores),
                marker_timing_residual_seconds=timing_residual)
