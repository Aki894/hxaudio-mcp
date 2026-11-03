"""Read-only parser for the observed HXCF v1 exported configuration format.

The trailing device-volume section is deliberately left uninterpreted.
Proprietary exports are never modified or redistributed by this project.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Any

MAX_FILE_BYTES = 8 * 1024 * 1024
MAX_ENTRIES = 100_000


@dataclass(frozen=True)
class ConfigEntry:
    scope: int
    name: str
    value_type: int
    value: Any


def parse_bytes(data: bytes) -> dict[str, Any]:
    if len(data) > MAX_FILE_BYTES:
        raise ValueError("Export file exceeds 8 MiB")
    if len(data) < 16 or data[:4] != b"HXCF":
        raise ValueError("Missing HXCF file signature")
    version, flags, count = struct.unpack_from("<III", data, 4)
    if version != 1:
        raise ValueError(f"Unsupported HXCF version {version}")
    if count > MAX_ENTRIES:
        raise ValueError("Entry count too large")
    offset = 16
    entries = []

    def take(size: int) -> bytes:
        nonlocal offset
        if size < 0 or size > len(data) - offset:
            raise ValueError("Truncated HXCF entry")
        part = data[offset:offset + size]
        offset += size
        return part

    for _ in range(count):
        scope, value_type, name_chars = struct.unpack("<III", take(12))
        if name_chars > 65535:
            raise ValueError("Unreasonable HXCF registry name length")
        name = take(name_chars * 2).decode("utf-16-le")
        (value_length,) = struct.unpack("<I", take(4))
        raw = take(value_length)
        if value_type == 4:
            if len(raw) != 4:
                raise ValueError("Malformed DWORD in HXCF export")
            value = struct.unpack("<I", raw)[0]
        elif value_type == 1:
            if len(raw) % 2:
                raise ValueError("Malformed UTF-16 string in HXCF export")
            value = raw.decode("utf-16-le").rstrip("\x00")
        elif value_type == 7:
            if len(raw) % 2:
                raise ValueError("Malformed UTF-16 multi-string in HXCF export")
            value = raw.decode("utf-16-le").rstrip("\x00").split("\x00") if raw else []
        else:
            # Do not expose or guess meanings for unsupported binary values.
            value = {"raw_hex": raw.hex()}
        entries.append(ConfigEntry(scope, name, value_type, value))
    return {
        "format": "HXCF", "version": version, "flags": flags,
        "entries": entries, "trailing_bytes": len(data) - offset,
    }


def read_hxconfig(path: str) -> dict[str, Any]:
    file = Path(path).expanduser()
    if file.suffix.lower() != ".hxconfig":
        raise ValueError("Only .hxconfig exports are accepted")
    if file.stat().st_size > MAX_FILE_BYTES:
        raise ValueError("Export file exceeds 8 MiB")
    return parse_bytes(file.read_bytes())


def extract_peq_bands(path: str, source_profile_key: str | None = None) -> dict[str, Any]:
    from .models import parse_bands
    result = read_hxconfig(path)
    entries = {entry.name: entry for entry in result["entries"]}
    available = []
    for name, entry in entries.items():
        if name == "DspPeqBands" or name.startswith("DspPeqBands_"):
            if entry.value_type != 7:
                continue
            suffix = name[len("DspPeqBands_"):] if name != "DspPeqBands" else ""
            id_entry = entries.get(f"DspPeqDeviceId_{suffix}") if suffix else None
            available.append({"name": name, "profile_key": id_entry.value if id_entry else None})
    if source_profile_key is not None:
        choices = [x for x in available if x["profile_key"] == source_profile_key]
    else:
        choices = available
    if not choices:
        raise ValueError("No matching PEQ curve in export")
    if len(choices) > 1:
        raise ValueError("Multiple PEQ curves in export; specify source_profile_key")
    entry = entries[choices[0]["name"]]
    return {
        "bands": [b.to_dict() for b in parse_bands(entry.value)],
        "source_profile_key": choices[0]["profile_key"],
        "available_curves": available,
        "format_version": result["version"],
    }
