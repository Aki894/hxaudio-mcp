"""Thin command-line entry point; no stdout logging in stdio MCP mode."""
from __future__ import annotations

import argparse
import json
import os
import sys


def main() -> None:
    parser = argparse.ArgumentParser(description="HXAudio Windows APO MCP bridge")
    parser.add_argument("command", nargs="?", choices=["serve", "doctor", "devices", "eq"],
                        default="serve")
    parser.add_argument("--profile-key", default=None)
    args = parser.parse_args()
    if sys.platform != "win32":
        parser.error("HXAudio registry bridge runs on Windows only")
    if args.command == "serve":
        from .server import run
        run()
        return
    from .core import HXAudioController
    from .registry import WindowsRegistry
    controller = HXAudioController(WindowsRegistry(),
                                   read_only=os.environ.get("HXAUDIO_MCP_READ_ONLY", "0") == "1")
    try:
        response = {"doctor": controller.doctor, "devices": controller.list_devices,
                    "eq": lambda: controller.get_eq(args.profile_key)}[args.command]()
    except Exception as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        raise SystemExit(1) from exc
    print(json.dumps(response, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
