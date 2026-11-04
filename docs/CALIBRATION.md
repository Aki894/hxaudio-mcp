# Acoustic calibration

Install the optional dependencies with `python -m pip install -e ".[calibration]"`
and restart the MCP host. See the [README](../README.md) for stdio configuration.

1. Place the microphone at the listening position. For iPhone capture, send
   its microphone through SonoBus and route Windows SonoBus playback to **CABLE
   Input**. Disable capture processing and acoustic monitoring.
2. Call `calibration_check` to find the WASAPI speaker output, capture input
   and matching HXAudio profile. For VB-CABLE, select **CABLE Output** as capture.
   Pass these three identifiers to the same tool for an audible route probe.
3. Set a comfortable speaker level and keep it, the capture gain and the
   microphone position fixed across comparable measurements. The digital
   stimulus peak is independent of the physical sound level.

## MCP tools

| Tool | Use |
| --- | --- |
| `calibration_measure` | Capture periodic pink noise by default. Use `output_channel` and `input_channel` to select zero-based channels. |
| `calibration_optimize` | Preview a PEQ candidate for the measured profile revision. Pass `other_channel_id` for shared stereo EQ. |
| `calibration_compare_tones` | Compare two stepped-sine captures at common trusted frequencies. |
| `calibration_run` | Start a closed-loop calibration. Set `keep=false` to restore the starting profile after a trial. |
| `calibration_status`, `calibration_report` | Check progress and locate results by session ID. |
| `calibration_cancel`, `calibration_resume`, `calibration_recover` | Cancel, continue a placement sequence, or recover an interrupted session. |

For shared stereo EQ, make two pink-noise captures with identical settings and
DSP revision. Change only `output_channel` from 0 to 1. Pass their IDs as
`measurement_id` and `other_channel_id`. The fitter uses common confidence,
aligns channel levels independently, protects deep dips in either channel and
returns one set of filters for the endpoint. The candidate JSON includes the
per-channel predicted errors and the expected revision. Use `apply_dsp_profile`
after reviewing the candidate, then measure again.
Use `calibration_run(stereo=true)` to fit a shared EQ and verify each output
channel separately. At each microphone position, the tool captures channel 0
and then channel 1 before requesting the next placement. The default
`stereo=false` captures output channel 0 only.

### Closed-loop acceptance

The acceptance grid spans 40–8,000 Hz, with bands of 40–200 Hz,
200–2,000 Hz, and 2,000–8,000 Hz. At least half of the log-spaced bins in
each quarter of each band must be trusted in both captures. The tool fits
one level offset per capture across the shared trusted bins and uses that
same offset when scoring each band.

A candidate requires coverage for every position and output channel,
improvement beyond repeatability in at least one band, and an overall
improvement above repeatability across the capture pairs. A band that
worsens beyond repeatability at any measured position or channel rejects
the candidate. The target is reached when all measured bands meet
`goal_db`. Reports include per-pair band coverage, error, and verdicts
for each verified candidate and a baseline-to-retained comparison when
the target curve is available.

For a stepped-sine retest, pass the same ascending `tones_hz` list to
`calibration_measure` before and after applying EQ. Use at least two
repetitions and 0.45 seconds per tone. For example, use
`tones_hz=[250,400,500,1000,2000,4000,6300]`, `duration_seconds=4.2`
and `repetitions=2`. Standalone MCP captures allow up to 30 seconds of total
stimulus playback per job. Adjust `transport_delay_seconds` for the route's
delay beyond the 0.5-second capture preroll. The analyzer checks narrowband
signal-to-noise ratio, within-tone stability and agreement across takes.
Call `calibration_compare_tones` with the two measurement IDs to get raw and
level-aligned deltas. Results describe the combined speaker and capture route.

Set `background=true` to poll or cancel a standalone measurement by session
ID. Cancellation and timeouts are checked at capture boundaries. Raw WAVs,
metadata and response arrays are written locally under
`%LOCALAPPDATA%\HXAudioMCP\calibrations`.

## CLI sweep diagnostics

List devices with `python -m hxaudio_mcp.acoustic.cli devices`. Replace the
example indices with the current WASAPI endpoints.

```powershell
python -m hxaudio_mcp.acoustic.cli ess --output 24 --capture 26 --output-channel 0 --marker-peak 0.35 --marker-band 700 5500 --marker-repeats 3 --npz sweep.npz
```

The sweep and timing markers have independent digital peaks. The repeated
marker mode fits an effective clock ratio across the accepted arrivals and
rejects fits with more than 3 ms of marker residual. Use a marker band with
strong reception on the selected route. The NPZ includes recording and
playback arrays, timing diagnostics and, on successful analysis, gated impulse
and harmonic response data. If analysis rejects the recording, the CLI
preserves the raw arrays and error in the requested NPZ.

Delay includes capture preroll, buffering and propagation. Timing estimates
can also reflect changes in transport buffering. Set `--window` to choose the
post-arrival impulse window. A longer window retains more low-frequency detail
and must fit between harmonic arrivals. For stepped-sine CLI capture, use
`measure --tones-hz` with `--duration`, `--repetitions` and
`--transport-delay`. See [Architecture](ARCHITECTURE.md#background-calibration)
for session recovery.
