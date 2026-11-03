"""Read/write abstraction for HXAudio Windows registry settings."""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from typing import Any, Protocol

KEY_PATH = r"Software\HXAudio\VirtualAudio"
NOTIFY_PATH = KEY_PATH + r"\DspNotify"
RUNTIME_PATH = r"Software\HXAudio\Apo\RuntimeStatus"
REG_SZ = 1
REG_DWORD = 4
REG_MULTI_SZ = 7


@dataclass(frozen=True)
class RegValue:
    value: str | int | list[str]
    type: int


class Registry(Protocol):
    def read(self, hive: str, key: str, name: str) -> RegValue | None: ...
    def write(self, hive: str, key: str, name: str, value: RegValue) -> None: ...
    def delete(self, hive: str, key: str, name: str) -> None: ...
    def values(self, hive: str, key: str) -> dict[str, RegValue]: ...
    def check_writable(self, hive: str, key: str) -> None: ...
    def signal_dsp_event(self) -> bool: ...


class WindowsRegistry:
    """Actual winreg bridge. Requires elevation to edit HKLM APO settings."""

    def __init__(self) -> None:
        if sys.platform != "win32":
            raise RuntimeError("HXAudio registry control requires Windows")
        import winreg
        self.w = winreg

    def _root(self, hive: str) -> Any:
        if hive == "user":
            return self.w.HKEY_CURRENT_USER
        if hive == "machine":
            return self.w.HKEY_LOCAL_MACHINE
        raise ValueError("Invalid registry hive")

    def _access(self, hive: str, mode: int) -> int:
        return mode | (self.w.KEY_WOW64_64KEY if hive == "machine" else 0)

    def read(self, hive: str, key: str, name: str) -> RegValue | None:
        try:
            with self.w.OpenKey(self._root(hive), key, 0, self._access(hive, self.w.KEY_READ)) as h:
                value, kind = self.w.QueryValueEx(h, name)
                return RegValue(value, kind)
        except FileNotFoundError:
            return None

    def write(self, hive: str, key: str, name: str, value: RegValue) -> None:
        with self.w.CreateKeyEx(self._root(hive), key, 0,
                                self._access(hive, self.w.KEY_SET_VALUE)) as h:
            self.w.SetValueEx(h, name, 0, value.type, value.value)

    def delete(self, hive: str, key: str, name: str) -> None:
        try:
            with self.w.OpenKey(self._root(hive), key, 0,
                                self._access(hive, self.w.KEY_SET_VALUE)) as h:
                self.w.DeleteValue(h, name)
        except FileNotFoundError:
            pass

    def values(self, hive: str, key: str) -> dict[str, RegValue]:
        try:
            with self.w.OpenKey(self._root(hive), key, 0,
                                self._access(hive, self.w.KEY_READ)) as h:
                result = {}
                index = 0
                while True:
                    try:
                        name, value, kind = self.w.EnumValue(h, index)
                    except OSError as exc:
                        if getattr(exc, "winerror", None) == 259:
                            break
                        raise
                    result[name] = RegValue(value, kind)
                    index += 1
                return result
        except FileNotFoundError:
            return {}

    def check_writable(self, hive: str, key: str) -> None:
        # No values are changed; check write ACL on the existing settings key.
        with self.w.OpenKey(self._root(hive), key, 0,
                            self._access(hive, self.w.KEY_SET_VALUE)):
            pass

    def signal_dsp_event(self) -> bool:
        # Named event is also used by HXAudio's background routing process.
        import ctypes
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenEventW.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_wchar_p]
        kernel32.OpenEventW.restype = ctypes.c_void_p
        kernel32.SetEvent.argtypes = [ctypes.c_void_p]
        kernel32.SetEvent.restype = ctypes.c_int
        kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        kernel32.CloseHandle.restype = ctypes.c_int
        handle = kernel32.OpenEventW(0x0002, 0, r"Local\HXAudioDspSettingsChanged")
        if not handle:
            return False
        try:
            return bool(kernel32.SetEvent(handle))
        finally:
            kernel32.CloseHandle(handle)


class MemoryRegistry:
    """In-memory implementation for non-Windows unit tests and simulations."""
    def __init__(self) -> None:
        self.items: dict[tuple[str, str, str], RegValue] = {}
        self.fail_write_name: str | None = None
        self.denied_hives: set[str] = set()
        self.signals = 0

    def read(self, hive: str, key: str, name: str) -> RegValue | None:
        return self.items.get((hive, key, name))

    def write(self, hive: str, key: str, name: str, value: RegValue) -> None:
        if name == self.fail_write_name or hive in self.denied_hives:
            raise PermissionError(f"Write refused: {hive}\\{key}\\{name}")
        self.items[(hive, key, name)] = RegValue(
            list(value.value) if isinstance(value.value, list) else value.value, value.type)

    def delete(self, hive: str, key: str, name: str) -> None:
        self.items.pop((hive, key, name), None)

    def values(self, hive: str, key: str) -> dict[str, RegValue]:
        return {name: v for (h, k, name), v in self.items.items() if h == hive and k == key}

    def check_writable(self, hive: str, key: str) -> None:
        if hive in self.denied_hives:
            raise PermissionError("Registry hive is not writable")

    def signal_dsp_event(self) -> bool:
        self.signals += 1
        return True
