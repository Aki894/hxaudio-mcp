"""Aggregate aligned spatial measurements while retaining position evidence."""
import numpy as np

from .measurement import MeasurementResult


def aggregate(takes: list[MeasurementResult]) -> MeasurementResult:
    if not takes or any(t.quality_metrics["state"] == "invalid" for t in takes):
        raise ValueError("Every position requires a usable measurement")
    first = takes[0]
    if any(not np.array_equal(t.frequencies_hz, first.frequencies_hz) for t in takes):
        raise ValueError("Spatial measurements require a common frequency grid")
    values = np.stack([t.response_db for t in takes])
    weights = np.stack([t.confidence for t in takes])
    weights = np.where(np.isfinite(values), weights, 0)
    values = np.nan_to_num(values)
    total = weights.sum(axis=0)
    mean = (values * weights).sum(axis=0) / np.where(total > 0, total, 1)
    spread = np.sqrt(((values - mean)**2 * weights).sum(axis=0)
                     / np.where(total > 0, total, 1))
    mean[total == 0] = np.nan
    quality = {
        "state": "warning" if any(t.quality_metrics["state"] == "warning" for t in takes)
        else "valid",
        "repeatability_median_sd_db": max(
            t.quality_metrics.get("repeatability_median_sd_db") or 0 for t in takes
        ),
        "spatial_median_sd_db": float(np.median(spread[total > 0])) if np.any(total > 0) else None,
    }
    return MeasurementResult(first.frequencies_hz, mean, mean,
                             weights.mean(axis=0) * np.exp(-spread / 3), quality, first.metadata)
