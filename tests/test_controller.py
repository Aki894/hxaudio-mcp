import json

import pytest

from hxaudio_mcp.core import HXAudioController, device_suffix
from hxaudio_mcp.registry import KEY_PATH, NOTIFY_PATH, RUNTIME_PATH, REG_DWORD, REG_MULTI_SZ, REG_SZ, RegValue
from conftest import PROFILE


def band(freq=1000, gain=0, q=0.9, typ="PK"):
    return {"enabled": True, "type": typ, "frequency_hz": freq, "gain_db": gain, "q": q}


def test_profile_hash_matches_uploaded_export():
    assert device_suffix("CONTAINER|{11111111-2222-3333-4444-555555555555}|测试扬声器") == "8158F2B202E24922"


def test_single_profile_autodetected(harness):
    c, r = harness
    assert c.list_devices()[0]["profile_key"] == PROFILE
    assert c.get_eq()["profile_key"] == PROFILE


def test_set_eq_mirrored_generation(harness):
    c, r = harness
    result = c.set_eq([band(5000, 5), band(380, -3)])
    suffix = device_suffix(PROFILE)
    name = "DspPeqBands_" + suffix
    assert result["band_count"] == 2
    assert r.read("user", KEY_PATH, name) == r.read("machine", KEY_PATH, name)
    assert r.read("machine", NOTIFY_PATH, "Generation").value == 11
    assert r.signals == 1
    assert c.get_eq()["band_count"] == 2
    assert c.get_eq()["bands"][1]["gain_db"] == -3


def test_peq_band_count_boundary(harness):
    c, r = harness
    points = [band(20 + 20*i, 0.5) for i in range(256)]
    assert c.set_eq(points)["band_count"] == 256
    assert c.get_eq()["band_count"] == 256
    before = dict(r.items)
    before_signals = r.signals
    with pytest.raises(ValueError, match="256"):
        c.set_eq(points + [band(8000, 0.5)])
    with pytest.raises(ValueError, match="256"):
        c.add_band(band(8100, 0.5))
    assert r.items == before
    assert r.signals == before_signals


def test_patch_insert_delete(harness):
    c, _ = harness
    c.set_eq([band(500, -1)])
    c.add_band(band(2000, 2), index=0)
    assert c.get_eq()["bands"][0]["frequency_hz"] == 2000
    c.update_band(0, {"gain_db": 4, "enabled": False})
    assert c.get_eq()["bands"][0]["gain_db"] == 4
    assert c.get_eq()["bands"][0]["enabled"] is False
    c.remove_band(1)
    assert c.get_eq()["band_count"] == 1


def test_invalid_updates_leave_registry_unchanged(harness):
    c, r = harness
    c.set_eq([band()])
    before = dict(r.items)
    with pytest.raises(ValueError):
        c.set_eq([band(), band(gain=41)])
    with pytest.raises(IndexError):
        c.update_band(99, {"q": 1})
    assert r.items == before


def test_transaction_rollback(harness):
    c, r = harness
    c.set_eq([band(500, 1)])
    old = dict(r.items)
    # Force failure after user writes; machine write of the same name fails too.
    r.fail_write_name = "DspPeqBands_" + device_suffix(PROFILE)
    with pytest.raises(PermissionError):
        c.set_eq([band(700, 2)])
    assert r.items == old


def test_write_permission_denied_preflight(harness):
    c, r = harness
    old = dict(r.items)
    r.denied_hives.add("machine")
    with pytest.raises(PermissionError):
        c.set_eq([band()])
    assert r.items == old


def test_device_preamp(harness):
    c, r = harness
    # Licensed build has an existing per-device preamp field.
    premium_name = "DspPeqPreampDb_" + device_suffix(PROFILE)
    r.write("user", KEY_PATH, premium_name, RegValue("0.5", REG_SZ))
    result = c.set_preamp(-4.2)
    assert result["setting"] == premium_name
    assert c.get_preamp()["preamp_db"] == pytest.approx(-4.2)
    assert r.read("machine", KEY_PATH, result["setting"]).value == "-4.2000000000000002"
    c.set_preamp(-3, device_specific=False)
    assert c.get_preamp()["preamp_db"] == pytest.approx(-4.2)


def test_virtual_bass_preserves_unknown_fields(harness):
    c, r = harness
    r.write("user", KEY_PATH, "DspNewPremiumFeature", RegValue(777, REG_DWORD))
    c.set_virtual_bass(enabled=False, amount=50, strength=136, cutoff_hz=120)
    assert c.get_virtual_bass()["DspVirtualBassStrength"]["value"] == 136
    assert r.read("user", KEY_PATH, "DspNewPremiumFeature").value == 777
    assert r.read("machine", KEY_PATH, "DspVirtualBassCutoffHzText").value == "120"


def test_backup_and_restore(harness):
    c, _ = harness
    c.set_eq([band(1000, -1)])
    c.set_virtual_bass(enabled=True)
    backup = c.create_backup()
    c.set_eq([band(500, -2)])
    c.set_virtual_bass(enabled=False)
    with pytest.raises(ValueError):
        c.restore_backup(backup["backup_id"])
    c.restore_backup(backup["backup_id"], confirm=True)
    assert c.get_eq()["bands"][0]["frequency_hz"] == 1000
    assert c.get_virtual_bass()["DspVirtualBassEnabled"]["value"] == 1


def test_backup_ids_validate(harness):
    c, _ = harness
    with pytest.raises(ValueError):
        c.restore_backup("../escape", confirm=True)


def test_runtime_generation_probe(harness):
    c, r = harness
    r.write("machine", RUNTIME_PATH, "ApoSettingsGeneration_ABC", RegValue(10, REG_DWORD))
    result = c.set_eq([band()])
    assert result["runtime_confirmation"]["state"] == "pending"
    r.write("machine", RUNTIME_PATH, "ApoSettingsGeneration_ABC", RegValue(11, REG_DWORD))
    assert c.wait_for_generation(11, 0)["state"] == "observed"


def test_multiple_devices_requires_explicit_key(harness):
    c, r = harness
    r.write("user", KEY_PATH, "PlaybackEndpointProfiles", RegValue([PROFILE, "Device 2"], REG_MULTI_SZ))
    with pytest.raises(ValueError, match="Multiple"):
        c.get_eq()
    c.set_eq([band()], profile_key=PROFILE)
    assert c.get_eq(PROFILE)["band_count"] == 1


def test_read_only(harness):
    _, r = harness
    c = HXAudioController(r, read_only=True)
    assert c.list_devices()
    with pytest.raises(PermissionError):
        c.set_eq([band()])


def test_public_preamp_falls_back_to_global(harness):
    c, r = harness
    result = c.set_preamp(-6)
    assert result["setting"] == "DspPreampDb"
    assert c.get_preamp()["preamp_db"] == -6
    assert r.read("machine", KEY_PATH, "DspPreampDb").value == "-6"


def test_apply_dsp_profile_checks_target_and_conflict(harness):
    controller, registry = harness
    other = "other endpoint"
    registry.write("user", KEY_PATH, "PlaybackEndpointProfiles",
                   RegValue([PROFILE, other], REG_MULTI_SZ))
    with pytest.raises(ValueError, match="Multiple"):
        controller.get_dsp_profile()
    revision = controller.get_dsp_profile(PROFILE).revision
    registry.write("machine", RUNTIME_PATH, f"ApoSettingsGeneration_{device_suffix(other)}",
                   RegValue(11, REG_DWORD))
    result = controller.apply_dsp_profile([band(gain=-6)], -4, revision, PROFILE, False, 0)
    assert result["registry_consistent"]
    assert result["profile"]["preamp_db"] == -4
    assert result["profile"]["virtual_bass_enabled"] is False
    assert result["runtime_confirmation"]["state"] == "not_observed"
    assert result["runtime_confirmation"]["timed_out"]
    assert registry.signals == 1
    assert controller.wait_for_generation(11, 0)["state"] == "observed"
    registry.write("machine", RUNTIME_PATH, f"ApoSettingsGeneration_{device_suffix(PROFILE)}",
                   RegValue(11, REG_DWORD))
    assert controller.wait_for_generation(11, 0, PROFILE)["state"] == "observed"
    assert controller.apply_dsp_profile([], 0, revision, PROFILE)["state"] == "revision_conflict"
    assert registry.signals == 1


def test_apply_dsp_profile_rolls_back(harness, monkeypatch):
    controller, registry = harness
    revision = controller.get_dsp_profile().revision
    before = dict(registry.items)
    write = registry.write

    def fail_preamp(hive, key, name, value):
        if hive == "machine" and name == "DspPreampDb":
            raise OSError("injected failure")
        write(hive, key, name, value)

    monkeypatch.setattr(registry, "write", fail_preamp)
    with pytest.raises(OSError, match="injected failure"):
        controller.apply_dsp_profile([band()], -4, revision)
    assert registry.items == before
    assert registry.signals == 0
