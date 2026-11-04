---
name: hxaudio-calibration
description: Measure, optimize, and verify HXAudio speaker EQ on Windows using the acoustic calibration MCP tools.
---

Use calibration_check to identify the HXAudio profile, WASAPI playback endpoint,
and capture endpoint. Confirm the microphone position and choose the target.
For an unattended trial, start calibration_run in fixed mode with keep=false.
Poll calibration_status and inspect calibration_report when finished. Retain
the best accepted configuration with keep=true when requested.

For multi mode, follow each numbered placement prompt and call
calibration_resume. For moving mode, move the microphone slowly over the same
path for each pass. If capture quality drops, inspect the measurement artifacts
and check the SonoBus and virtual cable routing before retrying. Use
calibration_cancel to stop and restore. If a server process stopped mid-job,
inspect the persisted status and use calibration_recover.

See references/methodology.md for measurement and scoring details.

## Execution and acceptance

1. Confirm profile, intended listening position, target and whether to keep the
   result. Use Neutral when requested, Warm for a tilted target, or explicit
   frequency/gain pairs for Reference or Custom.
2. List endpoints with calibration_check. Repeat with profile_key, output and
   capture to run an audible three-second probe. Inspect probe quality and its
   effective band. Fix silence, dropout or clipping before a full run. Choose
   peak using capture headroom and background level. A probe has one take.
3. Use calibration_measure for a repeatable baseline. Use calibration_optimize
   for a reviewable candidate without a DSP write. A stale DSP revision requires
   remeasurement. Raw WAV, arrays and candidate JSON stay in local artifacts.
4. For a closed-loop run, set rounds and gain/filter limits explicitly. The
   runner backs up before applying and guards each write against revision
   conflicts. Poll status with the returned session ID instead of repeating
   calibration_run after a client timeout.
5. On cancellation, wait for terminal state and inspect restoration and runtime
   confirmation. If another client changed DSP, preserve its revision and
   inspect the session evidence before trying recovery. If the process stopped,
   use calibration_recover only for the interrupted session.
6. Compare baseline and best accepted verification on the same trusted band.
   Inspect repeatability, level shift, stop reason, backup ID and report paths.
   With keep=false verify restoration. With keep=true keep the backup ID so
   the operator can restore the original profile.

Use calibration_measure with background=true for an independent cancellable
baseline. Poll calibration_status using the returned session_id, and read the
measurement ID, quality and artifact directory from the completed result.
calibration_cancel stops at the next capture boundary and preserves any
completed WAVs. timeout_seconds is also checked at capture boundaries.
Wait for the terminal state before starting another measurement on that route.
If a synchronous call returns a session ID before completion, poll that ID
instead of issuing another measurement. Standalone measurements do not write DSP.
