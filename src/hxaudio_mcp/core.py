"""Transactional HXAudio APO controller; independent of MCP transport."""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import tempfile
import threading
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .hxconfig import extract_peq_bands
from .models import Band, MAX_PEQ_BANDS, parse_bands, validate_preamp
from .registry import (KEY_PATH, NOTIFY_PATH, RUNTIME_PATH, REG_DWORD,
                       REG_MULTI_SZ, REG_SZ, RegValue, Registry)

_DSP_WRITE_LOCK = threading.RLock()

BACKUP_VERSION = 1
PROFILE_KEYS = ("PlaybackEndpointProfiles", "TargetEndpointProfiles")
MAX_REGISTRY_TEXT_BYTES = 1_000_000
BACKUP_ID_PATTERN = re.compile(r"^[a-f0-9]{32}$")


def device_suffix(profile_key: str) -> str:
    """HXAudio FNV-1a variant over UTF-16 code units, 64-bit unsigned."""
    result = 1469598103934665603
    data = profile_key.encode("utf-16-le")
    for offset in range(0, len(data), 2):
        result ^= int.from_bytes(data[offset:offset + 2], "little")
        result = (result * 1099511628211) & 0xFFFFFFFFFFFFFFFF
    return f"{result:016X}"


def _safe_registry_value(value: Any) -> tuple[str | int | list[str], int]:
    if isinstance(value, bool):
        raise ValueError("Raw values cannot be boolean")
    if isinstance(value, int) and 0 <= value <= 0xFFFFFFFF:
        return value, REG_DWORD
    if isinstance(value, str) and "\x00" not in value:
        return value, REG_SZ
    if isinstance(value, list) and all(isinstance(x, str) and "\x00" not in x for x in value):
        return value, REG_MULTI_SZ
    raise ValueError("Registry setting value must be DWORD, string, or list of strings")


def _safe_reg_size(entry: RegValue) -> None:
    if entry.type not in {REG_SZ, REG_DWORD, REG_MULTI_SZ}:
        raise ValueError(f"Unsupported registry data type {entry.type}")
    if entry.type == REG_DWORD:
        if type(entry.value) is not int or not (0 <= entry.value <= 0xFFFFFFFF):
            raise ValueError("DWORD registry value out of range")
    elif entry.type == REG_SZ:
        if not isinstance(entry.value, str) or "\x00" in entry.value:
            raise ValueError("Invalid registry text")
        if (len(entry.value) + 1) * 2 > MAX_REGISTRY_TEXT_BYTES:
            raise ValueError("Registry text value too large")
    else:
        if not isinstance(entry.value, list) or any(not isinstance(x, str) or "\x00" in x for x in entry.value):
            raise ValueError("Invalid REG_MULTI_SZ value")
        if (2 + sum(len(x) + 1 for x in entry.value)) * 2 > MAX_REGISTRY_TEXT_BYTES:
            raise ValueError("PEQ configuration exceeds safe registry value size")


@dataclass(frozen=True)
class DSPProfile:
    profile_key: str | None
    bands: list[Band]
    preamp_db: float
    virtual_bass_enabled: bool | None
    revision: str

    def to_dict(self) -> dict[str, Any]:
        return {**asdict(self), "bands": [band.to_dict() for band in self.bands]}


class HXAudioController:
    def __init__(self, registry: Registry, backup_dir: Path | None = None,
                 read_only: bool = False):
        self.registry = registry
        self.lock = _DSP_WRITE_LOCK
        self.read_only = read_only
        if backup_dir is None:
            root = os.environ.get("LOCALAPPDATA") or str(Path.home() / ".local" / "share")
            backup_dir = Path(root) / "HXAudioMCP" / "backups"
        self.backup_dir = Path(backup_dir)

    def is_apo_mode(self) -> bool:
        mode = self.registry.read("machine", KEY_PATH, "InstallationMode")
        return bool(mode and mode.type == REG_SZ and str(mode.value).upper() == "APO")

    def _read(self, name: str) -> RegValue | None:
        return self.registry.read("user", KEY_PATH, name) or self.registry.read("machine", KEY_PATH, name)

    def _affected_hives(self) -> list[str]:
        return ["user", "machine"] if self.is_apo_mode() else ["user"]

    def _record_change(self, changes: list[tuple[str, str, str, RegValue | None]],
                       name: str, value: RegValue | None) -> None:
        if not name.startswith("Dsp"):
            raise ValueError("Only DSP setting names may be modified")
        if value is not None:
            _safe_reg_size(value)
        for hive in self._affected_hives():
            changes.append((hive, KEY_PATH, name, value))

    def _transaction(self, changes: list[tuple[str, str, str, RegValue | None]],
                     notify: bool = True) -> dict[str, Any]:
        if self.read_only:
            raise PermissionError("MCP server is in read-only mode")
        # Validate the complete write set before touching either registry hive.
        for _, _, name, value in changes:
            if (name == "DspPeqBands" or name.startswith("DspPeqBands_")) and value is not None:
                if value.type != REG_MULTI_SZ or not isinstance(value.value, list):
                    raise ValueError("PEQ band setting must be REG_MULTI_SZ")
                if len(value.value) > MAX_PEQ_BANDS:
                    raise ValueError(f"PEQ supports at most {MAX_PEQ_BANDS} bands")
        with self.lock:
            generation: int | None = None
            if notify and self.is_apo_mode():
                old_generation = self.registry.read("machine", NOTIFY_PATH, "Generation")
                generation = ((int(old_generation.value) if old_generation else 0) + 1) & 0xFFFFFFFF
                changes = [*changes, ("machine", NOTIFY_PATH, "Generation",
                                     RegValue(generation, REG_DWORD))]
            # Preflight permissions to avoid partially mirrored HKCU/HKLM settings.
            for hive, key in {(h, k) for h, k, _, _ in changes}:
                self.registry.check_writable(hive, key)
            old_values = {(h, k, n): self.registry.read(h, k, n) for h, k, n, _ in changes}
            completed: list[tuple[str, str, str]] = []
            try:
                for hive, key, name, value in changes:
                    if value is None:
                        self.registry.delete(hive, key, name)
                    else:
                        _safe_reg_size(value)
                        self.registry.write(hive, key, name, value)
                    completed.append((hive, key, name))
            except Exception as exc:
                rollback_errors = []
                for hive, key, name in reversed(completed):
                    original = old_values[(hive, key, name)]
                    try:
                        if original is None:
                            self.registry.delete(hive, key, name)
                        else:
                            self.registry.write(hive, key, name, original)
                    except Exception as rb_exc:
                        rollback_errors.append(str(rb_exc))
                if rollback_errors:
                    raise RuntimeError(f"Registry update failed; rollback also failed: {rollback_errors}") from exc
                raise
            event_sent = bool(self.registry.signal_dsp_event()) if notify else False
            return {
                "written_values": len(changes) - (1 if generation is not None else 0),
                "hives": self._affected_hives(),
                "apo_generation": generation,
                "router_event_sent": event_sent,
                "runtime_confirmation": self._runtime_confirmation(generation),
            }

    def _runtime_confirmation(self, generation: int | None,
                              profile_key: str | None = None) -> dict[str, Any]:
        if generation is None:
            return {"state": "generation_not_used"}
        if profile_key is not None:
            name = f"ApoSettingsGeneration_{device_suffix(profile_key)}"
            value = self.registry.read("machine", RUNTIME_PATH, name)
            actual = value.value if value and value.type == REG_DWORD else None
            state = "not_observed" if actual is None else "pending"
            if actual == generation:
                state = "observed"
            return {"state": state, "profile_key": profile_key,
                    "expected_generation": generation, "actual_generation": actual}
        active = self.registry.values("machine", RUNTIME_PATH)
        observations = {key: value.value for key, value in active.items()
                        if key.startswith("ApoSettingsGeneration_") and value.type == REG_DWORD}
        if not observations:
            return {"state": "not_observed", "note": "APO may be idle or this build has no runtime probe"}
        # Multiple endpoints can legitimately have different active generations.
        caught_up = [name for name, value in observations.items() if value == generation]
        return {"state": "observed" if caught_up else "pending", "active": observations,
                "caught_up_endpoints": caught_up}

    def runtime_status(self) -> dict[str, Any]:
        raw = self.registry.values("machine", RUNTIME_PATH)
        relevant = {name: value.value for name, value in raw.items()
                    if name.startswith(("ApoSettingsGeneration_", "ApoSettingsProcessId_"))}
        generation = self.registry.read("machine", NOTIFY_PATH, "Generation")
        return {"apo_mode": self.is_apo_mode(),
                "notified_generation": generation.value if generation else None,
                "runtime": relevant}

    def wait_for_generation(self, generation: int, timeout_seconds: float = 2.0,
                            profile_key: str | None = None) -> dict[str, Any]:
        if not 0 <= timeout_seconds <= 10:
            raise ValueError("timeout_seconds must be 0..10")
        if profile_key is not None:
            self._choose_profile(profile_key)
        deadline = time.monotonic() + timeout_seconds
        while True:
            status = self._runtime_confirmation(generation, profile_key)
            if status["state"] == "observed" or time.monotonic() >= deadline:
                if profile_key is not None:
                    status["timed_out"] = status["state"] != "observed"
                return status
            time.sleep(0.05)

    def list_devices(self) -> list[dict[str, Any]]:
        profiles = set()
        registry_profiles = {}
        for hive in ("user", "machine"):
            values = self.registry.values(hive, KEY_PATH)
            for key in PROFILE_KEYS:
                v = values.get(key)
                if v and v.type == REG_MULTI_SZ:
                    profiles.update(v.value)
            for key, v in values.items():
                if key.startswith("DspPeqDeviceId_") and v.type == REG_SZ:
                    suffix = key[len("DspPeqDeviceId_"):]
                    registry_profiles.setdefault(v.value, set()).add(suffix)
                    profiles.add(v.value)
        result = []
        for profile in sorted(profiles):
            if not profile:
                continue
            suffix = device_suffix(profile)
            id_ref = self._read("DspPeqDeviceId_" + suffix)
            bands = self._read("DspPeqBands_" + suffix)
            configured = bool(id_ref and id_ref.value == profile and bands and bands.type == REG_MULTI_SZ)
            result.append({"profile_key": profile, "suffix": suffix,
                           "configured": configured,
                           "band_count": len(bands.value) if configured else None,
                           "display_name": profile.rsplit("|", 1)[-1],
                           "stored_suffixes": sorted(registry_profiles.get(profile, set()))})
        return result

    def _choose_profile(self, profile_key: str | None) -> str | None:
        if profile_key is not None:
            if not isinstance(profile_key, str) or not profile_key.strip():
                raise ValueError("profile_key must be a nonempty string")
            if "\x00" in profile_key:
                raise ValueError("Invalid profile_key")
            return profile_key
        configured = [d for d in self.list_devices() if d["configured"]]
        if len(configured) == 1:
            return configured[0]["profile_key"]
        if len(configured) > 1:
            raise ValueError("Multiple configured devices; specify profile_key explicitly")
        devices = self.list_devices()
        if len(devices) == 1:
            return devices[0]["profile_key"]
        if devices:
            raise ValueError("Multiple audio devices; specify profile_key explicitly")
        return None  # Legacy global PEQ

    def _band_setting_names(self, profile: str | None) -> tuple[str, str | None]:
        if profile is None:
            return "DspPeqBands", None
        suffix = device_suffix(profile)
        return f"DspPeqBands_{suffix}", f"DspPeqDeviceId_{suffix}"

    def get_eq(self, profile_key: str | None = None) -> dict[str, Any]:
        with self.lock:
            profile = self._choose_profile(profile_key)
            name, id_name = self._band_setting_names(profile)
            v = self._read(name)
            source = name
            if profile is not None:
                id_value = self._read(id_name)
                if id_value is None or id_value.value != profile or v is None:
                    v, source = self._read("DspPeqBands"), "DspPeqBands"
            bands = parse_bands(v.value) if v and v.type == REG_MULTI_SZ else []
            return {"profile_key": profile, "source": source,
                    "bands": [band.to_dict() for band in bands], "band_count": len(bands),
                    "apo_mode": self.is_apo_mode(),
                    "machine_synced": self._is_mirrored(name) if source == name else None}

    def _is_mirrored(self, name: str) -> bool:
        return self.registry.read("user", KEY_PATH, name) == self.registry.read("machine", KEY_PATH, name)

    def set_eq(self, bands: list[dict[str, Any]], profile_key: str | None = None) -> dict[str, Any]:
        if not isinstance(bands, list):
            raise ValueError("bands must be a list")
        if len(bands) > MAX_PEQ_BANDS:
            raise ValueError(f"PEQ supports at most {MAX_PEQ_BANDS} bands")
        parsed = [Band.from_dict(b) for b in bands]
        encoded = [band.serialize() for band in parsed]
        with self.lock:
            profile = self._choose_profile(profile_key)
            name, id_name = self._band_setting_names(profile)
            changes: list[tuple[str, str, str, RegValue | None]] = []
            if id_name is not None:
                self._record_change(changes, id_name, RegValue(profile, REG_SZ))
            self._record_change(changes, name, RegValue(encoded, REG_MULTI_SZ))
            report = self._transaction(changes)
            return {"profile_key": profile, "band_count": len(parsed), **report}

    def update_band(self, index: int, patch: dict[str, Any], profile_key: str | None = None) -> dict[str, Any]:
        if type(index) is not int:
            raise ValueError("index must be a zero-based integer")
        with self.lock:
            state = self.get_eq(profile_key)
            bands = state["bands"]
            if not 0 <= index < len(bands):
                raise IndexError(f"index {index} outside 0..{len(bands)-1}")
            merged = {**bands[index], **patch}
            bands[index] = Band.from_dict(merged).to_dict()
            return self.set_eq(bands, state["profile_key"])

    def add_band(self, band: dict[str, Any], profile_key: str | None = None,
                 index: int | None = None) -> dict[str, Any]:
        with self.lock:
            state = self.get_eq(profile_key)
            bands = state["bands"]
            if index is None:
                index = len(bands)
            if type(index) is not int or not 0 <= index <= len(bands):
                raise IndexError("Insertion index out of range")
            bands.insert(index, Band.from_dict(band).to_dict())
            return self.set_eq(bands, state["profile_key"])

    def remove_band(self, index: int, profile_key: str | None = None) -> dict[str, Any]:
        with self.lock:
            state = self.get_eq(profile_key)
            bands = state["bands"]
            if type(index) is not int or not 0 <= index < len(bands):
                raise IndexError("Deletion index out of range")
            bands.pop(index)
            return self.set_eq(bands, state["profile_key"])

    def get_preamp(self, profile_key: str | None = None) -> dict[str, Any]:
        profile = self._choose_profile(profile_key)
        names = ([f"DspPeqPreampDb_{device_suffix(profile)}"] if profile else []) + ["DspPreampDb"]
        for name in names:
            v = self._read(name)
            if v and v.type == REG_SZ:
                try:
                    gain = float(v.value)
                    if math.isfinite(gain):
                        return {"profile_key": profile, "preamp_db": gain, "source": name}
                except ValueError:
                    pass
        return {"profile_key": profile, "preamp_db": 0.0, "source": "default"}

    def set_preamp(self, gain_db: float, profile_key: str | None = None,
                   device_specific: bool | None = None) -> dict[str, Any]:
        value = validate_preamp(gain_db)
        with self.lock:
            profile = self._choose_profile(profile_key)
            device_name = f"DspPeqPreampDb_{device_suffix(profile)}" if profile else None
            if device_specific is None:
                # The open-source APO only uses the global key. Licensed builds
                # may expose a per-device preamp; select that only if detected.
                device_specific = bool(device_name and self._read(device_name))
            name = device_name if device_name and device_specific else "DspPreampDb"
            changes: list[tuple[str, str, str, RegValue | None]] = []
            self._record_change(changes, name, RegValue(format(value, ".17g"), REG_SZ))
            return {"profile_key": profile, "preamp_db": value, "setting": name,
                    **self._transaction(changes)}

    def get_dsp_profile(self, profile_key: str | None = None) -> DSPProfile:
        with self.lock:
            profile = self._choose_profile(profile_key)
            bands_name, id_name = self._band_setting_names(profile)
            names = {"DspPeqBands", "DspPreampDb", *self.get_virtual_bass()}
            if id_name:
                names.update((bands_name, id_name, f"DspPeqPreampDb_{device_suffix(profile)}"))
            snapshot = {
                hive: {name: asdict(value) if (value := self.registry.read(hive, KEY_PATH, name))
                       else None for name in sorted(names)}
                for hive in ("user", "machine")
            }
            encoded = json.dumps([profile, self.is_apo_mode(), snapshot], sort_keys=True)
            bass = self._read("DspVirtualBassEnabled")
            return DSPProfile(
                profile, [Band.from_dict(band) for band in self.get_eq(profile)["bands"]],
                self.get_preamp(profile)["preamp_db"],
                bool(bass.value) if bass and bass.type == REG_DWORD else None,
                hashlib.sha256(encoded.encode("utf-8")).hexdigest(),
            )

    def apply_dsp_profile(self, bands: list[dict[str, Any]], preamp_db: float,
                          expected_revision: str, profile_key: str | None = None,
                          virtual_bass_enabled: bool | None = None,
                          timeout_seconds: float = 2.0) -> dict[str, Any]:
        if not isinstance(bands, list) or len(bands) > MAX_PEQ_BANDS:
            raise ValueError(f"bands must be a list of at most {MAX_PEQ_BANDS} bands")
        parsed = [Band.from_dict(band) for band in bands]
        gain = validate_preamp(preamp_db)
        if virtual_bass_enabled is not None and type(virtual_bass_enabled) is not bool:
            raise ValueError("virtual_bass_enabled must be boolean")
        if not 0 <= timeout_seconds <= 10:
            raise ValueError("timeout_seconds must be 0..10")
        with self.lock:
            current = self.get_dsp_profile(profile_key)
            profile = current.profile_key
            if current.revision != expected_revision:
                return {"state": "revision_conflict", "profile": current.to_dict()}
            name, id_name = self._band_setting_names(profile)
            changes: list[tuple[str, str, str, RegValue | None]] = []
            if id_name:
                self._record_change(changes, id_name, RegValue(profile, REG_SZ))
            encoded_bands = [band.serialize() for band in parsed]
            self._record_change(changes, name, RegValue(encoded_bands, REG_MULTI_SZ))
            device_preamp = f"DspPeqPreampDb_{device_suffix(profile)}" if profile else None
            preamp_name = "DspPreampDb"
            if device_preamp and self._read(device_preamp):
                preamp_name = device_preamp
            self._record_change(changes, preamp_name, RegValue(format(gain, ".17g"), REG_SZ))
            if virtual_bass_enabled is not None:
                self._record_change(changes, "DspVirtualBassEnabled",
                                    RegValue(int(virtual_bass_enabled), REG_DWORD))
            report = self._transaction(changes)
            generation = report["apo_generation"]
            if generation is not None:
                report["runtime_confirmation"] = self.wait_for_generation(
                    generation, timeout_seconds, profile
                )
            consistent = all(self.registry.read(hive, key, name) == value
                             for hive, key, name, value in changes)
            return {"state": "written" if consistent else "readback_mismatch",
                    "registry_consistent": consistent, "acoustic_confirmed": None,
                    "profile": self.get_dsp_profile(profile).to_dict(), **report}

    def get_virtual_bass(self) -> dict[str, Any]:
        keys = ("DspVirtualBassEnabled", "DspVirtualBassAmount", "DspVirtualBassStrength",
                "DspVirtualBassMode", "DspVirtualBassCutoffHzText", "DspVirtualBassSubsonicHzText")
        return {name: (asdict(v) if (v := self._read(name)) else None) for name in keys}

    def set_virtual_bass(self, enabled: bool | None = None, amount: int | None = None,
                         mode: int | None = None, cutoff_hz: float | None = None,
                         subsonic_hz: float | None = None,
                         strength: int | None = None) -> dict[str, Any]:
        def integer(name: str, x: int | None, lower: int, upper: int) -> None:
            if x is not None and (type(x) is not int or not lower <= x <= upper):
                raise ValueError(f"{name} must be integer {lower}..{upper}")
        integer("amount", amount, 0, 200)
        integer("mode", mode, 0, 2)
        integer("strength", strength, 0, 1000)
        if enabled is not None and type(enabled) is not bool:
            raise ValueError("enabled must be boolean")
        for name, x in (("cutoff_hz", cutoff_hz), ("subsonic_hz", subsonic_hz)):
            if x is not None and (type(x) not in (int, float) or not math.isfinite(x) or x <= 0):
                raise ValueError(f"{name} must be >0 and finite")
        values = {
            "DspVirtualBassEnabled": int(enabled) if enabled is not None else None,
            "DspVirtualBassAmount": amount, "DspVirtualBassMode": mode,
            "DspVirtualBassStrength": strength,
        }
        changes: list[tuple[str, str, str, RegValue | None]] = []
        for name, x in values.items():
            if x is not None:
                self._record_change(changes, name, RegValue(x, REG_DWORD))
        for name, x in (("DspVirtualBassCutoffHzText", cutoff_hz),
                        ("DspVirtualBassSubsonicHzText", subsonic_hz)):
            if x is not None:
                self._record_change(changes, name, RegValue(format(float(x), ".17g"), REG_SZ))
        if not changes:
            raise ValueError("Specify at least one virtual bass parameter")
        return {"updated": sorted({name for _, _, name, _ in changes}),
                **self._transaction(changes)}

    def list_dsp_settings(self) -> dict[str, Any]:
        """Read-only view of formal-build extensions without assuming their semantics."""
        output: dict[str, Any] = {}
        for hive in ("user", "machine"):
            output[hive] = {name: asdict(value) for name, value in
                            self.registry.values(hive, KEY_PATH).items()
                            if name.startswith("Dsp")}
        return output

    def apply_eq(self) -> dict[str, Any]:
        return self._transaction([], notify=True)

    def doctor(self) -> dict[str, Any]:
        return {
            "platform": os.name,
            "apo_mode": self.is_apo_mode(),
            "read_only": self.read_only,
            "devices": self.list_devices(),
            "runtime_status": self.runtime_status(),
            "note": "APO writes may require elevation. The HXAudio GUI may remain open; avoid competing saves from multiple editors.",
        }

    def _backup_values(self) -> dict[str, dict[str, dict[str, Any]]]:
        return {
            hive: {name: asdict(v) for name, v in self.registry.values(hive, KEY_PATH).items()
                   if name.startswith("Dsp") and v.type in {REG_SZ, REG_MULTI_SZ, REG_DWORD}}
            for hive in self._affected_hives()
        }

    def create_backup(self) -> dict[str, Any]:
        with self.lock:
            contents = {"version": BACKUP_VERSION, "created": time.time(),
                        "hives": self._backup_values()}
            self.backup_dir.mkdir(parents=True, exist_ok=True)
            key = uuid.uuid4().hex
            path = self.backup_dir / f"{key}.json"
            fd, temp_path = tempfile.mkstemp(dir=self.backup_dir, suffix=".tmp")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(contents, f, ensure_ascii=False, indent=2)
                os.replace(temp_path, path)
            finally:
                if os.path.exists(temp_path):
                    os.unlink(temp_path)
            return {"backup_id": key, "file": str(path),
                    "values": sum(len(x) for x in contents["hives"].values())}

    def restore_backup(self, backup_id: str, confirm: bool = False) -> dict[str, Any]:
        if not confirm:
            raise ValueError("Set confirm=true after reviewing the backup ID")
        if not BACKUP_ID_PATTERN.fullmatch(backup_id):
            raise ValueError("Invalid backup ID")
        payload = json.loads((self.backup_dir / f"{backup_id}.json").read_text("utf-8"))
        if payload.get("version") != BACKUP_VERSION:
            raise ValueError("Unsupported backup version")
        data = payload.get("hives")
        if not isinstance(data, dict) or not data or any(h not in ("user", "machine") for h in data):
            raise ValueError("Invalid backup contents")
        changes = []
        with self.lock:
            for hive, desired in data.items():
                if hive == "machine" and not self.is_apo_mode():
                    raise ValueError("The backup requires APO mode")
                if not isinstance(desired, dict):
                    raise ValueError("Malformed backup")
                current = self.registry.values(hive, KEY_PATH)
                for name in current:
                    if name.startswith("Dsp") and name not in desired:
                        changes.append((hive, KEY_PATH, name, None))
                for name, raw in desired.items():
                    if not isinstance(name, str) or not name.startswith("Dsp") or "\\" in name:
                        raise ValueError("Invalid setting name in backup")
                    if not isinstance(raw, dict) or set(raw) != {"value", "type"}:
                        raise ValueError("Invalid registry value in backup")
                    value = RegValue(raw["value"], raw["type"])
                    _safe_reg_size(value)
                    changes.append((hive, KEY_PATH, name, value))
            return {"backup_id": backup_id, **self._transaction(changes)}

    def import_export_eq(self, path: str, profile_key: str | None = None,
                         source_profile_key: str | None = None) -> dict[str, Any]:
        extracted = extract_peq_bands(path, source_profile_key)
        result = self.set_eq(extracted["bands"], profile_key)
        return {"imported_from": "HXCF", "export_profile_key": extracted["source_profile_key"],
                **result}
