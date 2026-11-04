"""Independent PortAudio input and output streams."""
import threading
import time
from contextlib import ExitStack
from typing import Protocol

import numpy as np


class AudioBackend(Protocol):
    def list_devices(self) -> list[dict]: ...
    def open_output(self, device: int, rate: int, channels: int): ...
    def open_input(self, device: int, rate: int, channels: int, callback=None): ...
    def play_signal(self, signal: np.ndarray, rate: int, output: int) -> None: ...
    def record_audio(self, frames: int, rate: int, capture: int) -> np.ndarray: ...
    def close(self) -> None: ...
    def play_and_record(self, signal: np.ndarray, rate: int, output: int, capture: int,
                        output_channel: int, input_channel: int) -> tuple[np.ndarray, int]: ...


class SoundDeviceBackend:
    def __init__(self) -> None:
        import sounddevice

        self.sd = sounddevice

    def list_devices(self) -> list[dict]:
        hosts = self.sd.query_hostapis()
        return [
            {**device, "host_api": hosts[device["hostapi"]]["name"]}
            for device in self.sd.query_devices()
        ]

    def open_output(self, device: int, rate: int, channels: int):
        self.sd.check_output_settings(device, channels=channels, dtype="float32", samplerate=rate)
        return self.sd.OutputStream(device=device, channels=channels, samplerate=rate)

    def open_input(self, device: int, rate: int, channels: int, callback=None):
        self.sd.check_input_settings(device, channels=channels, dtype="float32", samplerate=rate)
        return self.sd.InputStream(device=device, channels=channels, samplerate=rate,
                                   dtype="float32", callback=callback)

    def play_signal(self, signal: np.ndarray, rate: int, output: int) -> None:
        data = np.asarray(signal, dtype=np.float32)
        if data.ndim == 1:
            channels = min(2, self.sd.query_devices(output, "output")["max_output_channels"])
            data = np.repeat(data[:, None], channels, axis=1)
        with self.open_output(output, rate, data.shape[1]) as stream:
            if stream.write(data):
                raise RuntimeError("Output stream underflow")

    def record_audio(self, frames: int, rate: int, capture: int) -> np.ndarray:
        if frames <= 0:
            raise ValueError("Recording requires a positive frame count")
        done = threading.Event()
        recorded = np.empty(frames, dtype=np.float32)
        position = 0
        overflow = False

        def record(indata, frame_count, timing, status):
            nonlocal position, overflow
            overflow |= bool(status.input_overflow)
            count = min(frame_count, frames - position)
            recorded[position:position + count] = indata[:count, 0]
            position += count
            if position == frames:
                done.set()
                raise self.sd.CallbackStop

        with self.open_input(capture, rate, 1, callback=record):
            if not done.wait(frames / rate + 3):
                raise RuntimeError("Capture did not finish on schedule")
        if overflow:
            raise RuntimeError("Input stream overflow")
        return recorded

    def close(self) -> None:
        """Operations own their streams and close them before returning."""

    def play_and_record(self, signal: np.ndarray, rate: int, output: int, capture: int,
                        output_channel: int = 0, input_channel: int = 0):
        if min(output_channel, input_channel) < 0:
            raise ValueError("Channel indices must be nonnegative")
        output_info = self.sd.query_devices(output, "output")
        output_channels = min(2, output_info["max_output_channels"])
        output_channels = max(output_channels, output_channel + 1)
        data = np.zeros((len(signal), output_channels), dtype=np.float32)
        data[:, output_channel] = signal
        input_info = self.sd.query_devices(capture, "input")
        input_channels = max(min(2, input_info["max_input_channels"]), input_channel + 1)
        native_rate = int(input_info["default_samplerate"])
        try:
            self.sd.check_input_settings(
                capture, channels=input_channels, dtype="float32", samplerate=rate
            )
            native_rate = rate
        except self.sd.PortAudioError:
            pass
        preroll = native_rate // 2
        frames = round(len(signal) * native_rate / rate)
        ready = threading.Event()

        recorded = np.empty(frames + 2 * preroll, dtype=np.float32)
        finished = threading.Event()
        position = 0
        overflowed = False

        def record(indata, frame_count, timing, status):
            nonlocal position, overflowed
            overflowed |= bool(status.input_overflow)
            count = min(frame_count, len(recorded) - position)
            recorded[position:position + count] = indata[:count, input_channel]
            position += count
            if position >= preroll:
                ready.set()
            if position == len(recorded):
                finished.set()
                raise self.sd.CallbackStop

        with ExitStack() as stack:
            source = self.open_output(output, rate, output_channels)
            stack.callback(source.close)
            sink = self.open_input(capture, native_rate, input_channels, callback=record)
            stack.enter_context(sink)
            if not ready.wait(3):
                raise RuntimeError("Capture stream did not start")
            started = time.monotonic()
            source.start()
            source.write(data)
            source.stop()
            elapsed = time.monotonic() - started
            expected = len(signal) / rate
            if elapsed < expected * 0.95:
                raise RuntimeError(
                    f"Playback ended early: {elapsed:.2f}s for a {expected:.2f}s stimulus"
                )
            if not finished.wait(3):
                raise RuntimeError("Capture did not finish on schedule")
            if overflowed:
                raise RuntimeError("Input stream overflow")
            return recorded, native_rate
