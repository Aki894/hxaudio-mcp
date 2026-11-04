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

## Guarded DSP updates

Use `get_dsp_profile` to read the current settings and revision. Pass that
revision as `expected_revision` to `apply_dsp_profile` with the desired bands
and preamp. A revision conflict returns the current profile and performs no
writes. In APO mode the combined write uses one Generation notification.
Registry readback and runtime confirmation are returned separately. The
optional virtual-bass setting is global, as is preamp on builds without a
device-specific preamp setting.

For Windows acceptance, run `doctor` and `list_devices`, select the Realtek
speaker profile and create a backup with `backup_eq`. Start quiet playback
through that endpoint. Read the profile, submit its existing bands and preamp
with its revision, and record the apply result. Verify `registry_consistent`,
the target profile, and matching expected and actual runtime generations.
If the target runtime value is missing, record the runtime snapshot and APO
build. Restore the backup with `restore_eq_backup` if any setting was changed.

## P1 PEQ candidate fitting

Fit a candidate from a broadband measurement with the current DSP revision.

```powershell
python -m hxaudio_mcp.acoustic.cli optimize '<measurement-dir>' --target neutral --json candidate.json
```

The target can be neutral, warm, reference, or custom. Reference and custom
targets take `--target-points` with a headerless two-column Hz,dB CSV covering
the fitted band. Reference curves are smoothed on the logarithmic grid.
The Python optimizer also accepts an aggregated MeasurementResult when the
caller has spatial measurements.

The fitter uses a confidence-weighted log-frequency grid, bounded incremental
peaking filters and joint least-squares refinement. The prediction describes
response shape before the overall preamp change. Headroom is computed from
the existing plus proposed cascade. The coefficient conventions follow
[HXAudio APO](https://github.com/HuberHaYu/HXAudio-Desktop/blob/main/Apo/HXAudioApo.cpp).
The command exports a guarded candidate payload and does not apply it.

The analyzer uses the capture preroll to estimate background power per
frequency bin. Bins below 10 dB of measured-to-background power ratio are
excluded, and repetition spread further reduces confidence. Transient noise
that occurs only during playback can still contaminate the estimate, so use
repeatable takes in a quiet environment. The PEQ fit confines filter centers
to contiguous trusted bands. Filter skirts can still extend across gaps.

### Background calibration

Install the calibration extra, restart the MCP server, and call
`calibration_check` to enumerate the current endpoint indices. Select the
Realtek WASAPI playback endpoint, the VB-CABLE WASAPI capture endpoint and
the matching HXAudio profile. Start with `calibration_run` using the neutral
target, five rounds and `keep=false` for a trial that restores the starting
configuration. Poll `calibration_status`, then retrieve files using
`calibration_report`. Use `keep=true` to retain the best accepted profile.

Fixed mode runs unattended. In multi mode, place the microphone at each
numbered position and call `calibration_resume` when prompted. Repeat the
same positions on verification. In moving mode, start moving the microphone
slowly at the prompt and use the same path for each pass. Cancellation
takes effect at a capture boundary. After an interrupted server process,
`calibration_recover` restores the starting profile if the job still owns
the current revision.
