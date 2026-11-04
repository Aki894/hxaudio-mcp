"""Complex response of the biquads implemented in HXAudio Apo/HXAudioApo.cpp."""
from collections.abc import Sequence

import numpy as np

from ..models import Band


def coefficients(band: Band, rate: int) -> tuple[np.ndarray, np.ndarray]:
    band.validated()
    if not band.enabled or band.frequency_hz >= rate / 2:
        return np.array([1., 0., 0.]), np.array([1., 0., 0.])
    omega = 2 * np.pi * band.frequency_hz / rate
    c = np.cos(omega)
    alpha = np.sin(omega) / (2 * band.q)
    a = 10 ** (band.gain_db / 40)
    denominator = (1 + alpha, -2 * c, 1 - alpha)
    if band.type in {"PK", "LS", "HS"} and abs(band.gain_db) < 1e-5:
        return np.array([1., 0., 0.]), np.array([1., 0., 0.])
    if band.type == "PK":
        numerator = (1 + alpha * a, -2 * c, 1 - alpha * a)
        denominator = (1 + alpha / a, -2 * c, 1 - alpha / a)
    elif band.type in {"LS", "HS"}:
        sign = 1 if band.type == "HS" else -1
        beta = 2 * np.sqrt(a) * alpha
        plus, minus = a + 1, a - 1
        numerator = a * np.array((plus + sign * minus * c + beta,
                                  -2 * sign * (minus + sign * plus * c),
                                  plus + sign * minus * c - beta))
        denominator = (plus - sign * minus * c + beta,
                       2 * sign * (minus - sign * plus * c),
                       plus - sign * minus * c - beta)
    elif band.type == "NO":
        numerator = (1, -2 * c, 1)
    else:
        sign = 1 if band.type == "HP" else -1
        numerator = (1 + sign * c) / 2 * np.array([1, -2 * sign, 1])
    scale = denominator[0]
    return np.asarray(numerator) / scale, np.asarray(denominator) / scale


def cascade(bands: Sequence[Band], frequency: np.ndarray, rate: int) -> np.ndarray:
    delay = np.exp(-2j * np.pi * frequency / rate)
    result = np.ones_like(delay)
    for band in bands:
        b, a = coefficients(band, rate)
        result *= (b[0] + delay * (b[1] + delay * b[2])) / (
            a[0] + delay * (a[1] + delay * a[2])
        )
    return result


def response_db(bands: Sequence[Band], frequency: np.ndarray, rate: int) -> np.ndarray:
    return 20 * np.log10(np.maximum(np.abs(cascade(bands, frequency, rate)), 1e-12))
