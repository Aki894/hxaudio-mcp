from pathlib import Path

import pytest

from hxaudio_mcp.core import HXAudioController
from hxaudio_mcp.registry import KEY_PATH, NOTIFY_PATH, REG_DWORD, REG_MULTI_SZ, REG_SZ, MemoryRegistry, RegValue

PROFILE = "CONTAINER|{11111111-2222-3333-4444-555555555555}|Speakers (Example Audio)"


@pytest.fixture
def harness(tmp_path: Path):
    r = MemoryRegistry()
    r.write("machine", KEY_PATH, "InstallationMode", RegValue("APO", REG_SZ))
    r.write("machine", NOTIFY_PATH, "Generation", RegValue(10, REG_DWORD))
    r.write("user", KEY_PATH, "PlaybackEndpointProfiles", RegValue([PROFILE], REG_MULTI_SZ))
    c = HXAudioController(r, backup_dir=tmp_path)
    return c, r
