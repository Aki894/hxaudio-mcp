"""Write portable calibration reports."""
import html
import json
from pathlib import Path

import numpy as np


def report(path: Path, summary: dict, profile: dict, curves: dict) -> dict:
    files = {}
    for name, payload in (("summary", summary), ("eq", profile),
                          ("iterations", summary["iterations"])):
        file = path / f"{name}.json"
        file.write_text(json.dumps(payload, indent=2, allow_nan=False), encoding="utf-8")
        files[name] = str(file)
    np.savez(path / "measurements.npz", **curves)
    files["measurements"] = str(path / "measurements.npz")
    lines = []
    if curves:
        f = curves["frequency"]
        x = 40 + 700 * np.log(f / f[0]) / np.log(f[-1] / f[0])
        series = [(name, values) for name, values in curves.items() if name != "frequency"]
        extent = max([1, *(float(np.max(np.abs(v[np.isfinite(v)])))
                           for _, v in series if np.isfinite(v).any())])
        colors = ("#0072b2", "#009e73", "#d55e00")
        for index, ((name, values), color) in enumerate(zip(series, colors, strict=True)):
            points = " ".join(f"{a:.1f},{150 - 130 * b / extent:.1f}"
                              for a, b in zip(x, values, strict=True) if np.isfinite(b))
            lines.append(f'<polyline fill="none" stroke="{color}" points="{points}"/>')
            lines.append(f'<text x="{40 + 130 * index}" y="295" fill="{color}">{name}</text>')
    file = path / "report.html"
    details = html.escape(json.dumps(summary, indent=2))
    file.write_text(f'<!doctype html><meta charset="utf-8"><title>HXAudio calibration</title>'
                    f'<h1>HXAudio calibration</h1><svg viewBox="0 0 780 310">'
                    f'{"".join(lines)}</svg><pre>{details}</pre>', encoding="utf-8")
    files["html"] = str(file)
    return files
