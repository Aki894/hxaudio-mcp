"""Compare two measurements on their common confident frequency grid."""
import csv
import json
from pathlib import Path

import numpy as np


def compare(before: Path, after: Path, output: Path) -> dict:
    metadata = [
        json.loads((path / "measurement.json").read_text(encoding="utf-8"))
        for path in (before, after)
    ]
    if any(item["quality_metrics"]["state"] == "invalid" for item in metadata):
        raise ValueError("Both measurements must have usable captures")
    for field in ("profile_key", "config", "input", "output", "input_channel", "output_channel"):
        if metadata[0][field] != metadata[1][field]:
            raise ValueError(f"Measurements differ in {field}")
    with np.load(before / "response.npz") as base, np.load(after / "response.npz") as changed:
        frequencies = base["frequencies_hz"]
        if not np.array_equal(frequencies, changed["frequencies_hz"]):
            raise ValueError("Measurement grids differ")
        valid = (base["confidence"] >= 0.5) & (changed["confidence"] >= 0.5)
        frequencies = frequencies[valid]
        delta = changed["raw_response_db"][valid] - base["raw_response_db"][valid]
    center = (frequencies >= 950) & (frequencies <= 1050)
    anchors = ((frequencies >= 200) & (frequencies <= 350)) | (
        (frequencies >= 3000) & (frequencies <= 6000)
    )
    if not center.any() or not anchors.any():
        raise ValueError("Insufficient confident bins around 1 kHz or in the comparison bands")
    offset = float(np.median(delta[anchors]))
    with output.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow(["frequency_hz", "delta_db", "delta_relative_db"])
        writer.writerows(zip(frequencies, delta, delta - offset, strict=True))
    return {"delta_csv": str(output), "off_band_delta_db": offset,
            "near_1khz_delta_db": float(np.median(delta[center])),
            "near_1khz_relative_delta_db": float(np.median(delta[center]) - offset)}
