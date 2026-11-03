# Architecture

HXAudio MCP is a **local stdio MCP server**. It translates tool calls into validated HXAudio settings updates without interacting with the desktop UI.

## Components

```text
MCP client (Codex, Claude Desktop, etc.)
    |
    | stdio
    v
server.py       Tool registration and request parameters
    |
    v
core.py         Device selection, DSP operations, transactions, backups
    |      \
    |       +-- models.py    PEQ filter validation and serialization
    |       +-- hxconfig.py  HXCF v1 preset reading
    v
registry.py     Windows registry access and event signaling
    |
    +-- HKCU\Software\HXAudio\VirtualAudio
    +-- HKLM\Software\HXAudio\VirtualAudio (APO mode)
    +-- HKLM\...\DspNotify\Generation
    |
    v
HXAudio APO     Reloads settings for the active audio endpoint
```

`cli.py` provides the `serve`, `doctor`, `devices`, and `eq` commands. `server.py` exposes the DSP operations as MCP tools. There is no network listener or external service dependency at runtime.

## Device profiles and PEQ data

HXAudio stores per-device PEQ settings using a suffix derived from the device's `profile_key`. The suffix is a 64-bit FNV-1a hash of its UTF-16 code units, represented as 16 uppercase hexadecimal characters.

The relevant registry values are:

| Name | Type | Purpose |
| --- | --- | --- |
| `DspPeqDeviceId_<suffix>` | `REG_SZ` | Original device profile key. |
| `DspPeqBands_<suffix>` | `REG_MULTI_SZ` | Ordered PEQ filter list. |
| `DspPeqBands` | `REG_MULTI_SZ` | Global or legacy PEQ curve. |
| `DspPreampDb` | `REG_SZ` | Global preamp gain. |
| `DspPeqPreampDb_<suffix>` | `REG_SZ` | Per-device preamp, when available. |

A PEQ entry is stored as:

```text
1|PK|1000|-2.5|1
```

The fields are `enabled|type|frequency_hz|gain_db|q`. The controller reads the current device configuration and preserves the order of filters. Additional DSP values can be inspected without changing unknown settings.

## Updating settings

1. Resolve the destination `profile_key` and validate the requested parameters.
2. Build a complete write set and check registry write permissions.
3. Write settings to `HKCU`. In APO mode, mirror the relevant DSP values to `HKLM`.
4. Increment `HKLM\Software\HXAudio\VirtualAudio\DspNotify\Generation` and signal `Local\HXAudioDspSettingsChanged` when available.
5. Return the applied generation and available runtime diagnostics.

The controller performs each tool operation under a process-local lock. If a write fails, it attempts to restore affected registry values to their prior state. APO runtime status is read from `HKLM\Software\HXAudio\Apo\RuntimeStatus`; active endpoints may report different generations.

The HXAudio EQ GUI can remain open during MCP updates. To avoid overwriting one another's changes, do not save an outdated GUI configuration after an MCP modification.

## Backups and preset imports

- Backups store DSP registry values as JSON under `%LOCALAPPDATA%\HXAudioMCP\backups`. Restores require an exact `backup_id` and `confirm=true`.
- `.hxconfig` support reads HXCF v1 files. Import operations extract **PEQ filters only**, leaving unrelated settings and device-volume data unchanged.
- `HXAUDIO_MCP_READ_ONLY=1` prevents write operations; inspection and status tools remain available.

## Source layout

| Path | Responsibility |
| --- | --- |
| `src/hxaudio_mcp/server.py` | MCP tool definitions and stdio entry point. |
| `src/hxaudio_mcp/core.py` | HXAudio operations, device selection, transactions, backups. |
| `src/hxaudio_mcp/registry.py` | Registry abstraction and Windows implementation. |
| `src/hxaudio_mcp/models.py` | PEQ parameter validation and encoding. |
| `src/hxaudio_mcp/hxconfig.py` | HXCF v1 configuration reader. |
| `src/hxaudio_mcp/cli.py` | Command-line interface. |
| `skills/hxaudio-eq/SKILL.md` | Optional operating guidance for AI agents. |
