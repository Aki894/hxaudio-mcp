"""Bounded PEQ fitting on a log-frequency grid with confidence weighting."""
from dataclasses import dataclass

import numpy as np
from scipy.ndimage import gaussian_filter1d
from scipy.optimize import least_squares

from ..models import Band, validate_preamp
from .biquad import response_db
from .measurement import MeasurementResult
from .targets import target_curve


@dataclass(frozen=True)
class OptimizationConfig:
    max_filters: int = 8
    max_boost_db: float = 3
    max_cut_db: float = 8
    min_q: float = 0.35
    max_q: float = 5
    max_iterations: int = 5
    headroom_db: float = 1

    def __post_init__(self):
        if not (type(self.max_filters) is int and 1 <= self.max_filters <= 16
                and type(self.max_iterations) is int and 1 <= self.max_iterations <= 20
                and 0 < self.max_boost_db <= 12 and 0 < self.max_cut_db <= 24
                and 0.1 <= self.min_q < self.max_q <= 12 and 0 <= self.headroom_db <= 12):
            raise ValueError("Optimization configuration out of range")


@dataclass
class OptimizationResult:
    filters: list[Band]
    preamp_db: float
    frequencies_hz: np.ndarray
    predicted_response: np.ndarray
    predicted_rmse_db: float
    target_response: np.ndarray
    optimization_config: OptimizationConfig
    diagnostics: dict


def optimize(measurement: MeasurementResult, rate: int, mode: str = "neutral",
             config: OptimizationConfig = OptimizationConfig(),
             points: tuple[np.ndarray, np.ndarray] | None = None,
             existing: tuple[Band, ...] = (),
             other_channel: MeasurementResult | None = None) -> OptimizationResult:
    if measurement.quality_metrics.get("state") not in {"valid", "warning"}:
        raise ValueError("Optimization requires a usable measurement")
    f, y, c = (np.asarray(a, dtype=float) for a in (
        measurement.frequencies_hz, measurement.response_db, measurement.confidence
    ))
    if (f.ndim != 1 or f.shape != y.shape or f.shape != c.shape
            or not np.isfinite(f).all() or np.any(np.diff(f) <= 0)
            or not np.isfinite(c).all() or np.any((c < 0) | (c > 1))
            or not 8000 <= rate <= 384000):
        raise ValueError("Invalid response grid, confidence or sample rate")
    trusted = np.isfinite(y) & (c >= 0.5) & (f >= 20) & (f <= rate * 0.45)
    if trusted.sum() < 20 or f[trusted][-1] < 2 * f[trusted][0]:
        raise ValueError("Insufficient broadband measurement coverage")
    grid = np.geomspace(f[trusted][0], f[trusted][-1], 384)
    observed = np.interp(grid, f[trusted], y[trusted])
    weights = np.interp(grid, f, np.where(trusted, c, 0))
    channels = observed[None, :]
    if other_channel is not None:
        if other_channel.quality_metrics.get("state") not in {"valid", "warning"}:
            raise ValueError("Stereo fitting requires two usable measurements")
        other_f = other_channel.frequencies_hz
        other_y = other_channel.response_db
        other_c = other_channel.confidence
        valid = np.isfinite(other_y) & (other_c >= 0.5)
        if np.count_nonzero(valid) < 20:
            raise ValueError("Insufficient trusted coverage in second channel")
        other = np.interp(grid, other_f[valid], other_y[valid])
        weights = np.minimum(weights, np.interp(
            grid, other_f, np.where(valid, other_c, 0), left=0, right=0,
        ))
        channels = np.stack((observed, other))
    weights[weights < 0.5] = 0
    if np.count_nonzero(weights) < 24:
        raise ValueError("Insufficient trusted log-frequency coverage")
    if other_channel is not None:
        anchor = (grid >= 300) & (grid <= 3000) & (weights > 0)
        if not anchor.any():
            anchor = weights > 0
        channels -= np.average(channels[:, anchor], weights=weights[anchor], axis=1)[:, None]
        observed = channels.mean(axis=0)
    target = target_curve(grid, observed, weights, mode, points)
    no_boost = ((grid < 150)
                | np.any(channels < gaussian_filter1d(channels, 8, axis=1) - 6, axis=0))
    no_boost |= weights == 0
    root_weight = np.sqrt(weights / weights.sum())
    log_grid = np.log(grid)
    trusted_bins = np.flatnonzero(weights > 0)
    runs = np.split(trusted_bins, np.flatnonzero(np.diff(trusted_bins) > 1) + 1)
    bounds_by_bin = {i: (log_grid[run[0]], log_grid[run[-1]])
                     for run in runs if len(run) > 1 for i in run}
    eligible = np.isin(np.arange(len(grid)), list(bounds_by_bin))

    def bands(values: np.ndarray) -> list[Band]:
        return [Band(type="PK", frequency_hz=float(np.exp(log_hz)),
                     gain_db=float(gain), q=float(q)) for log_hz, gain, q in values.reshape(-1, 3)]

    def shape_error(correction):
        error = channels + correction - target
        if other_channel is not None:
            error -= np.average(error, weights=weights, axis=1)[:, None]
        return error

    def weighted_error(correction):
        return (shape_error(correction) * root_weight / np.sqrt(len(channels))).ravel()

    def residual(values: np.ndarray) -> np.ndarray:
        correction = response_db(bands(values), grid, rate)
        gain = values[1::3]
        q = values[2::3]
        high = np.clip(np.log2(np.exp(values[::3]) / 2000), 0, None)
        return np.r_[weighted_error(correction),
                     np.maximum(correction[no_boost] - 0.25, 0) * 0.5,
                     0.04 * gain, 0.02 * gain * q * (1 + high)]

    values = np.array([])
    initial_error = float(np.linalg.norm(weighted_error(0)))
    error = initial_error
    center_bounds = []
    for _ in range(min(config.max_filters, config.max_iterations)):
        correction = response_db(bands(values), grid, rate)
        remaining = -shape_error(correction).mean(axis=0)
        remaining = np.where(no_boost, np.minimum(remaining, 0), remaining)
        remaining = np.where(eligible, remaining, 0)
        center = int(np.argmax(np.abs(remaining) * root_weight))
        gain = np.clip(remaining[center], -config.max_cut_db, config.max_boost_db)
        if abs(gain) < 0.15:
            break
        new_bounds = [*center_bounds, bounds_by_bin[center]]
        low = [value for lower, _ in new_bounds
               for value in (lower, -config.max_cut_db, config.min_q)]
        high = [value for _, upper in new_bounds
                for value in (upper, config.max_boost_db, config.max_q)]
        seed = np.r_[values, np.log(grid[center]), gain, np.clip(1.2, config.min_q, config.max_q)]
        candidate = least_squares(residual, seed, bounds=(low, high), max_nfev=100).x
        corrected = response_db(bands(candidate), grid, rate)
        if np.any(corrected[no_boost] > 0.5):
            # Try a cut-only addition while retaining the last feasible cascade.
            center = int(np.argmin(remaining * root_weight))
            if remaining[center] >= 0:
                break
            new_bounds[-1] = bounds_by_bin[center]
            gain = np.clip(remaining[center], -config.max_cut_db, 0)
            candidate = np.r_[values, np.log(grid[center]), gain,
                              np.clip(1.2, config.min_q, config.max_q)]
            corrected = response_db(bands(candidate), grid, rate)
        candidate_error = float(np.linalg.norm(weighted_error(corrected)))
        if error - candidate_error < 0.03:
            break
        values, error = candidate, candidate_error
        center_bounds = new_bounds

    filters = bands(values)
    peak_grid = np.r_[0., np.geomspace(1, rate / 2, 16384),
                      [band.frequency_hz for band in (*existing, *filters)
                       if band.frequency_hz < rate / 2]]
    peak = float(np.max(response_db((*existing, *filters), peak_grid, rate)))
    preamp = validate_preamp(-max(0, peak) - config.headroom_db)
    return OptimizationResult(
        filters, preamp, grid, observed + response_db(filters, grid, rate), error, target, config,
        {"initial_rmse_db": initial_error, "cascade_peak_db": peak, "target_mode": mode,
         "weighted_bins": int(np.count_nonzero(weights)),
         "channel_rmse_db": np.sqrt(np.sum(
             (shape_error(response_db(filters, grid, rate)) * root_weight) ** 2, axis=1,
         )).tolist(),
         "source_revision": measurement.metadata.get("revision"),
         "measurement_id": measurement.metadata.get("measurement_id")},
    )
