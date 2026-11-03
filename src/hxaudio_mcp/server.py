"""Local-only MCP server. The MCP SDK uses stdio transport by default."""
from __future__ import annotations

import os
from typing import Any

from mcp.server import MCPServer

from .core import HXAudioController
from .registry import WindowsRegistry


controller = HXAudioController(
    WindowsRegistry(), read_only=os.environ.get("HXAUDIO_MCP_READ_ONLY", "0") == "1"
)
server = MCPServer(
    "HXAudio APO",
    version="0.1.0",
    instructions=(
        "Controls the local Windows HXAudio APO equalizer. Start with doctor and list_devices. "
        "Explicitly choose a profile_key when multiple endpoints exist. All band indices are zero-based. "
        "Before replacing a full PEQ curve, create_backup. "
        "PEQ updates can change listening volume; "
        "use conservative gains and appropriate headroom."
    ),
)


@server.tool()
def doctor() -> dict[str, Any]:
    """Check APO installation mode, audio devices, registry state, and runtime diagnostics."""
    return controller.doctor()


@server.tool()
def list_audio_devices() -> list[dict[str, Any]]:
    """List profiles and stable profile_key identifiers for each HXAudio audio endpoint."""
    return controller.list_devices()


@server.tool()
def get_eq(profile_key: str | None = None) -> dict[str, Any]:
    """Read current PEQ filter list; profile_key selects the audio device."""
    return controller.get_eq(profile_key)


@server.tool()
def set_eq(bands: list[dict[str, Any]], profile_key: str | None = None) -> dict[str, Any]:
    """Replace all PEQ bands at once. Each band has enabled,type,frequency_hz,gain_db,q.

    Types: PK, LS, HS, LP, HP, NO.
    Backup before using this destructive replacement operation.
    """
    return controller.set_eq(bands, profile_key)


@server.tool()
def update_eq_band(index: int, changes: dict[str, Any],
                   profile_key: str | None = None) -> dict[str, Any]:
    """Patch one zero-based PEQ band; only fields in changes are modified."""
    return controller.update_band(index, changes, profile_key)


@server.tool()
def add_eq_band(band: dict[str, Any], profile_key: str | None = None,
                index: int | None = None) -> dict[str, Any]:
    """Add a PEQ filter, optionally inserting at a zero-based index."""
    return controller.add_band(band, profile_key, index)


@server.tool()
def remove_eq_band(index: int, profile_key: str | None = None) -> dict[str, Any]:
    """Remove one zero-based PEQ filter."""
    return controller.remove_band(index, profile_key)


@server.tool()
def clear_eq(profile_key: str | None = None) -> dict[str, Any]:
    """Remove all PEQ filters on a single device; create a backup first."""
    return controller.set_eq([], profile_key)


@server.tool()
def get_preamp(profile_key: str | None = None) -> dict[str, Any]:
    """Read device-specific preamp (formal build) or fall back to the global preamp."""
    return controller.get_preamp(profile_key)


@server.tool()
def set_preamp(preamp_db: float, profile_key: str | None = None,
               device_specific: bool | None = None) -> dict[str, Any]:
    """Set ±30 dB preamp. device_specific=None auto-detects the formal-build key;

    True forces device-specific, False forces open-source global DspPreampDb.
    """
    return controller.set_preamp(preamp_db, profile_key, device_specific)


@server.tool()
def get_virtual_bass() -> dict[str, Any]:
    """Read available virtual-bass parameters, including formal-build strength."""
    return controller.get_virtual_bass()


@server.tool()
def set_virtual_bass(enabled: bool | None = None, amount: int | None = None,
                     mode: int | None = None, cutoff_hz: float | None = None,
                     subsonic_hz: float | None = None,
                     strength: int | None = None) -> dict[str, Any]:
    """Update virtual-bass parameters without altering unspecified values.

    Amount: 0..200; mode: 0..2; strength: 0..1000 (formal-build observed field).
    """
    return controller.set_virtual_bass(enabled, amount, mode, cutoff_hz, subsonic_hz, strength)


@server.tool()
def inspect_dsp_settings() -> dict[str, Any]:
    """Read-only discovery of additional proprietary-build DSP fields (no guessing semantics)."""
    return controller.list_dsp_settings()


@server.tool()
def apply_eq() -> dict[str, Any]:
    """Notify APO listener and HXAudio router that the current DSP configuration changed."""
    return controller.apply_eq()


@server.tool()
def get_runtime_status() -> dict[str, Any]:
    """Read reported APO settings generation and runtime diagnostic probes."""
    return controller.runtime_status()


@server.tool()
def wait_for_apo_apply(generation: int, timeout_seconds: float = 2.0) -> dict[str, Any]:
    """Observe an APO runtime generation after a write; 'not_observed' means unavailable."""
    return controller.wait_for_generation(generation, timeout_seconds)


@server.tool()
def backup_eq() -> dict[str, Any]:
    """Back up current DSP registry values to a local per-user JSON snapshot; return backup ID."""
    return controller.create_backup()


@server.tool()
def restore_eq_backup(backup_id: str, confirm: bool = False) -> dict[str, Any]:
    """Restore a local snapshot. Explicit confirm=true required; replaces backed-up DSP values."""
    return controller.restore_backup(backup_id, confirm)


@server.tool()
def inspect_hxconfig(path: str, source_profile_key: str | None = None) -> dict[str, Any]:
    """Inspect PEQ from a local HXCF v1 .hxconfig export without importing settings."""
    from .hxconfig import extract_peq_bands
    return extract_peq_bands(path, source_profile_key)


@server.tool()
def import_eq_from_hxconfig(path: str, profile_key: str | None = None,
                            source_profile_key: str | None = None) -> dict[str, Any]:
    """Import only PEQ bands from a local HXCF v1 export into a selected device.

    Does not import opaque proprietary fields or its device-volume section.
    """
    return controller.import_export_eq(path, profile_key, source_profile_key)


def run() -> None:
    server.run(transport="stdio")
