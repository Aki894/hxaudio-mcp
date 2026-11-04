"""Persisted calibration job state."""
import json
import threading
import uuid
from pathlib import Path


class Session:
    def __init__(self, root: Path, config: dict):
        self.id = uuid.uuid4().hex
        self.path = root / self.id
        self.path.mkdir(parents=True)
        self.io_lock = threading.Lock()
        self.cancel = threading.Event()
        self.resume = threading.Event()
        self.data = {"session_id": self.id, "state": "CREATED", "config": config,
                     "iterations": []}
        self.update("CREATED")

    def update(self, state: str, **fields) -> None:
        self.data.update(state=state, **fields)
        temporary = self.path / "session.tmp"
        temporary.write_text(json.dumps(self.data, indent=2, allow_nan=False), encoding="utf-8")
        with self.io_lock:
            temporary.replace(self.path / "session.json")

    def read(self) -> dict:
        with self.io_lock:
            return json.loads((self.path / "session.json").read_text(encoding="utf-8"))
