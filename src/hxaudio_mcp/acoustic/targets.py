"""Generate level-normalized targets on the trusted measurement grid."""
import numpy as np
from scipy.ndimage import gaussian_filter1d


def target_curve(frequency: np.ndarray, measured: np.ndarray, weight: np.ndarray,
                 mode: str = "neutral",
                 points: tuple[np.ndarray, np.ndarray] | None = None) -> np.ndarray:
    if mode == "neutral":
        shape = np.zeros_like(frequency)
    elif mode == "warm":
        shape = np.interp(np.log2(frequency), np.log2([20, 150, 1000, 20000]), [3, 2, 0, -3])
    elif mode in {"reference", "custom"} and points is not None:
        x, y = (np.asarray(value, dtype=float) for value in points)
        if (x.ndim != 1 or x.shape != y.shape or len(x) < 2
                or not np.isfinite(x).all() or not np.isfinite(y).all()
                or x[0] <= 0 or np.any(np.diff(x) <= 0)):
            raise ValueError("Target points must be finite and strictly increasing in frequency")
        if x[0] > frequency[0] or x[-1] < frequency[-1]:
            raise ValueError("Target points must cover the optimization band")
        shape = np.interp(np.log2(frequency), np.log2(x), y)
        if mode == "reference":
            step = np.log2(frequency[1] / frequency[0])
            shape = gaussian_filter1d(shape, (1 / 3) / (2.355 * step))
    else:
        raise ValueError("Use neutral, warm, or provide points for reference/custom")
    anchor = (frequency >= 300) & (frequency <= 3000) & (weight > 0)
    if not anchor.any():
        anchor = weight > 0
    shape += np.average((measured - shape)[anchor], weights=weight[anchor])
    # Preserve deep cancellations and natural rolloff instead of boosting into them.
    broad = gaussian_filter1d(measured, 8)
    no_boost = ((frequency < 150) | (measured < broad - 6)) & (measured < shape)
    shape[no_boost] = measured[no_boost]
    return shape
