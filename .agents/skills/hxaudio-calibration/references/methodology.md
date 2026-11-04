# Acoustic measurement and evaluation

The periodic pink-noise stimulus supports stationary spectral ratios when the
playback and capture devices use independent clocks. ESS is appropriate when
impulse response and harmonic separation are needed. That workflow requires
clock drift and timing correction.

Keep microphone placement repeatable. Treat an uncalibrated phone as a relative
measurement chain. Playback preroll supplies a background spectrum, and repeated
takes establish stability. Prefer shared trusted frequencies when comparing
before and after. Remove the overall level offset for shape comparison and
report the offset separately.

Spatial averaging reduces sensitivity to a single position. Keep individual
position records to inspect disagreement. Avoid boosting deep cancellation
nulls, and leave preamp margin for the combined filter response. For listening
A/B, level-match and compare the same programme material at the same position.

The stationary workflow optimizes level-aligned target error over frequencies
trusted in both measurements. Improvement must clear the repeatability margin.
Report band coverage, error and offset together. Preserve the original DSP
revision and backup ID before changing EQ; verify the restored revision and
APO generation after recovery. Compare spatial results only when positions,
routing and playback gain match between passes.

A microphone calibration curve describes frequency-dependent sensitivity and
is specific to the microphone and orientation. Absolute SPL also needs a
sensitivity reference. Use ESS for gated impulse and distortion diagnostics
and the repeated pink-noise workflow for closed-loop EQ. A gated ESS result
loses low-frequency resolution as the time window shortens.
