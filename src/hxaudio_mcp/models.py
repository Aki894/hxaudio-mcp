"""Typed PEQ model, validation, and firmware-compatible serialization."""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Any

FILTER_TYPES = frozenset({"PK", "LS", "HS", "LP", "HP", "NO"})
MAX_PEQ_BANDS = 256
MAX_PEAK_GAIN_DB = 40.0
MAX_PREAMP_DB = 30.0


@dataclass(frozen=True, slots=True)
class Band:
    enabled: bool = True
    type: str = "PK"
    frequency_hz: float = 1000.0
    gain_db: float = 0.0
    q: float = 0.9

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Band":
        if not isinstance(data, dict):
            raise ValueError("Each PEQ band must be a JSON object")
        allowed = {"enabled", "type", "frequency_hz", "gain_db", "q"}
        unknown = set(data) - allowed
        if unknown:
            raise ValueError(f"Unknown PEQ fields: {sorted(unknown)}")
        return cls(**data).validated()

    @classmethod
    def parse(cls, line: str) -> "Band":
        fields = line.split("|")
        if len(fields) != 5 or fields[0] not in {"0", "1"}:
            raise ValueError(f"Malformed HXAudio PEQ line: {line!r}")
        try:
            band = cls(
                enabled=fields[0] == "1",
                type=fields[1],
                frequency_hz=float(fields[2]),
                gain_db=float(fields[3]),
                q=float(fields[4]),
            )
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Malformed HXAudio PEQ line: {line!r}") from exc
        return band.validated()

    def validated(self) -> "Band":
        if type(self.enabled) is not bool:
            raise ValueError("enabled must be a boolean")
        if self.type not in FILTER_TYPES:
            raise ValueError(f"Unknown filter type {self.type!r}; allowed: {sorted(FILTER_TYPES)}")
        for key in ("frequency_hz", "gain_db", "q"):
            val = getattr(self, key)
            if isinstance(val, bool) or not isinstance(val, (int, float)) or not math.isfinite(val):
                raise ValueError(f"{key} must be a finite number")
        if self.frequency_hz <= 0:
            raise ValueError("frequency_hz must be > 0")
        if self.q <= 0:
            raise ValueError("q must be > 0")
        if abs(self.gain_db) > MAX_PEAK_GAIN_DB:
            raise ValueError(f"gain_db must be in [-{MAX_PEAK_GAIN_DB}, {MAX_PEAK_GAIN_DB}]")
        return self

    def serialize(self) -> str:
        self.validated()
        return "|".join((
            "1" if self.enabled else "0", self.type,
            format(self.frequency_hz, ".17g"),
            format(self.gain_db, ".17g"),
            format(self.q, ".17g"),
        ))

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def validate_preamp(value: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value):
        raise ValueError("preamp_db must be a finite number")
    if not -MAX_PREAMP_DB <= value <= MAX_PREAMP_DB:
        raise ValueError(f"preamp_db must be in [-{MAX_PREAMP_DB}, {MAX_PREAMP_DB}]")
    return float(value)


def parse_bands(lines: list[str]) -> list[Band]:
    return [Band.parse(line) for line in lines]
