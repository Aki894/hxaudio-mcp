---
name: hxaudio-eq
summary: Read and adjust HXAudio APO EQ with the local HXAudio MCP server.
---

# HXAudio EQ

Use the tools provided by `hxaudio-mcp`. This skill describes the workflow; the MCP server performs all audio-setting operations.

## Procedure

1. Use `doctor` and `list_audio_devices` to identify the desired playback device. Use its exact `profile_key` for all device-specific operations.
2. Call `get_eq` to read the existing curve. Before substantial changes, call `backup_eq` and keep the returned `backup_id`.
3. Use `update_eq_band` for a single-filter adjustment, `add_eq_band` / `remove_eq_band` for individual changes, or `set_eq` to replace the full curve. All filter indices are zero-based.
4. Set each filter using `enabled`, `type`, `frequency_hz`, `gain_db`, and `q`. Filter types: `PK`, `LS`, `HS`, `LP`, `HP`, `NO`.
5. Check `get_preamp` when applying positive EQ gain. Use `get_virtual_bass` / `set_virtual_bass` when the user requests changes to virtual bass.
6. Read back with `get_eq`. Use `get_runtime_status` or `wait_for_apo_apply` to inspect application status; `apply_eq` is available to request another reload.
7. To restore saved settings, call `restore_eq_backup` with the exact `backup_id` and `confirm=true`.

## Notes

- The MCP server works with the locally installed HXAudio APO and may require elevated registry permissions for writes.
- The HXAudio GUI may remain open, but avoid saving outdated GUI settings over newer MCP changes.
- Use `inspect_dsp_settings` to inspect additional parameters without modifying unknown values.
- `inspect_hxconfig` can read an HXCF v1 preset; `import_eq_from_hxconfig` imports its PEQ filters into the selected device.
- When performing response calibration, prefer measured data, preserve adequate preamp headroom, and verify the result after changing filters.
