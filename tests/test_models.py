import pytest

from hxaudio_mcp.models import Band, parse_bands, validate_preamp


def test_parse_real_world_band():
    b = Band.parse("1|PK|380|-3|0.59999999999999998")
    assert b.enabled and b.frequency_hz == 380 and b.gain_db == -3
    assert b.q == pytest.approx(0.6)
    assert Band.parse(b.serialize()) == b


def test_disabled_filter():
    assert Band.parse("0|HS|8000|0|0.707").enabled is False


def test_all_types():
    for code in ("PK", "LS", "HS", "LP", "HP", "NO"):
        assert Band(type=code).serialize().split("|")[1] == code


@pytest.mark.parametrize("wrong", [
    "1|XYZ|1000|0|0.9", "2|PK|1000|0|1", "1|PK|nan|0|1",
    "1|PK|0|0|1", "1|PK|1000|41|1", "1|PK|1000|0|0", "junk",
])
def test_invalid_band(wrong):
    with pytest.raises(ValueError):
        Band.parse(wrong)


def test_malicious_payload_rejected():
    with pytest.raises(ValueError):
        Band.from_dict({"type": "PK", "frequency_hz": 100, "random": "x"})
    with pytest.raises(ValueError):
        Band.from_dict({"enabled": 1})


def test_preamp_validation():
    assert validate_preamp(-6.0) == -6.0
    for bad in (-31, 31, float("inf"), True):
        with pytest.raises(ValueError):
            validate_preamp(bad)
