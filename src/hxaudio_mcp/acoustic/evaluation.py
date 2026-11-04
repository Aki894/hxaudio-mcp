"""Confidence-weighted comparison of acoustic response shapes."""
import numpy as np

from .measurement import MeasurementResult


def compare_tones(before: MeasurementResult, after: MeasurementResult) -> dict:
    """Return raw and level-aligned deltas at identical trusted tone frequencies."""
    for measurement in (before, after):
        if (measurement.metadata["config"]["stimulus"] != "stepped_sine"
                or measurement.quality_metrics["state"] not in {"valid", "warning"}):
            raise ValueError("Comparison requires usable stepped-sine captures")
    for field in ("profile_key", "input", "output", "input_channel", "output_channel", "config"):
        if before.metadata[field] != after.metadata[field]:
            raise ValueError(f"Stepped-sine comparison mismatch: {field}")
    if not np.array_equal(before.frequencies_hz, after.frequencies_hz):
        raise ValueError("Stepped-sine frequencies must match")
    trusted = (before.confidence >= 0.5) & (after.confidence >= 0.5)
    if np.count_nonzero(trusted) < 2:
        raise ValueError("Insufficient common trusted frequencies")
    delta = after.response_db[trusted] - before.response_db[trusted]
    offset = float(np.median(delta))
    return {"frequencies_hz": before.frequencies_hz[trusted].tolist(),
            "delta_db": delta.tolist(), "level_shift_db": offset,
            "relative_delta_db": (delta - offset).tolist()}


def compare_to_target(
    before: MeasurementResult,
    after: MeasurementResult,
    frequency: np.ndarray,
    target: np.ndarray,
) -> dict[str, float | int]:
    """Compare both responses on the same logarithmic grid and trusted bins."""
    if any(take.quality_metrics.get("state") not in {"valid", "warning"}
           for take in (before, after)):
        raise ValueError("Comparison requires usable measurements")
    responses = [
        np.interp(frequency, take.frequencies_hz, take.response_db, left=np.nan, right=np.nan)
        for take in (before, after)
    ]
    weights = np.minimum(*[
        np.interp(frequency, take.frequencies_hz, take.confidence, left=0, right=0)
        for take in (before, after)
    ])
    common = (weights >= 0.5) & np.isfinite(target) & np.isfinite(responses).all(axis=0)
    if np.count_nonzero(common) < 24:
        raise ValueError("Insufficient common trusted frequency coverage")
    scores = []
    offsets = []
    for response in responses:
        delta = response[common] - target[common]
        offset = np.average(delta, weights=weights[common])
        offsets.append(float(offset))
        scores.append(float(np.sqrt(np.average((delta - offset)**2, weights=weights[common]))))
    return {"before_rmse_db": scores[0], "after_rmse_db": scores[1],
            "level_shift_db": offsets[1] - offsets[0], "common_bins": int(common.sum())}

# Fixed acceptance range, independent of the optimizer's available frequency span.
ACCEPTANCE_GRID = np.geomspace(40, 8000, 385)
ACCEPTANCE_BANDS = (("low", 40, 200), ("mid", 200, 2000), ("high", 2000, 8000))


def evaluate_bands(before: MeasurementResult, after: MeasurementResult,
                   target: np.ndarray) -> dict:
    """Score one matched capture pair with a single level offset per take."""
    frequency = ACCEPTANCE_GRID
    responses = np.stack([
        np.interp(frequency, take.frequencies_hz, take.response_db, left=np.nan, right=np.nan)
        for take in (before, after)
    ])
    weights = np.minimum(*[
        np.interp(frequency, take.frequencies_hz, take.confidence, left=0, right=0)
        for take in (before, after)
    ])
    common = (weights >= 0.5) & np.isfinite(target) & np.isfinite(responses).all(axis=0)
    error = responses - target
    if common.any():
        error -= np.average(error[:, common], axis=1, weights=weights[common])[:, None]
    threshold = max(0.03, *(take.quality_metrics.get("repeatability_median_sd_db") or 0
                            for take in (before, after)))

    def score(mask):
        if not mask.any():
            return dict(before_rmse_db=None, after_rmse_db=None, improvement_db=None)
        first, last = np.sqrt(np.average(error[:, mask] ** 2, axis=1, weights=weights[mask]))
        return dict(before_rmse_db=float(first), after_rmse_db=float(last),
                    improvement_db=float(first - last))

    bands = {}
    for name, lower, upper in ACCEPTANCE_BANDS:
        in_band = (frequency >= lower) & (
            (frequency <= upper) if name == "high" else (frequency < upper)
        )
        # Check each log-frequency quarter so a single dense cluster cannot pass.
        quarters = np.array_split(common[in_band], 4)
        fractions = [float(section.mean()) for section in quarters]
        covered = min(fractions) >= 0.5
        scores = score(common & in_band) if covered else score(np.zeros_like(common))
        change = scores["improvement_db"]
        status = ("insufficient_coverage" if change is None else
                  "improved" if change > threshold else
                  "worsened" if change < -threshold else "within_repeatability")
        bands[name] = dict(scores, range_hz=[lower, upper],
                           trusted_bins=int((common & in_band).sum()),
                           coverage_fraction=float(common[in_band].mean()),
                           quarter_coverage=fractions, status=status)
    return dict(score(common), repeatability_threshold_db=threshold,
                common_bins=int(common.sum()), bands=bands)
