import struct

import pytest

from hxaudio_mcp.hxconfig import extract_peq_bands, parse_bytes


def export_sample(entries):
    buffer = bytearray(b"HXCF" + struct.pack("<III", 1, 0, len(entries)))
    for name, typ, value in entries:
        encoded_name = name.encode("utf-16-le")
        if typ == 4:
            raw = struct.pack("<I", value)
        elif typ == 1:
            raw = (value + "\0").encode("utf-16-le")
        elif typ == 7:
            raw = ("\0".join(value) + "\0\0").encode("utf-16-le")
        else:
            raise ValueError("test helper")
        buffer += struct.pack("<III", 0, typ, len(encoded_name)//2)
        buffer += encoded_name + struct.pack("<I", len(raw)) + raw
    return bytes(buffer)


def test_parse_hxcf_with_extra_tail(tmp_path):
    raw = export_sample([
        ("DspPeqDeviceId_ABCD", 1, "Example Speaker"),
        ("DspPeqBands_ABCD", 7, ["1|PK|5000|5|0.5", "0|LS|120|-2|0.707"]),
        ("DspVirtualBassAmount", 4, 80),
    ]) + b"UNINTERPRETED_TAIL"
    parsed = parse_bytes(raw)
    assert len(parsed["entries"]) == 3
    assert parsed["trailing_bytes"] == len(b"UNINTERPRETED_TAIL")
    path = tmp_path / "local.hxconfig"
    path.write_bytes(raw)
    extracted = extract_peq_bands(str(path))
    assert extracted["source_profile_key"] == "Example Speaker"
    assert extracted["bands"][0]["gain_db"] == 5


def test_multiple_curves_must_choose(tmp_path):
    raw = export_sample([
        ("DspPeqDeviceId_AAAA", 1, "Speaker A"),
        ("DspPeqBands_AAAA", 7, ["1|PK|1000|1|1"]),
        ("DspPeqDeviceId_BBBB", 1, "Speaker B"),
        ("DspPeqBands_BBBB", 7, ["1|PK|5000|2|1"]),
    ])
    path = tmp_path / "two.hxconfig"
    path.write_bytes(raw)
    with pytest.raises(ValueError, match="Multiple"):
        extract_peq_bands(str(path))
    assert extract_peq_bands(str(path), "Speaker B")["bands"][0]["gain_db"] == 2


def test_invalid_export():
    with pytest.raises(ValueError, match="signature"):
        parse_bytes(b"wrong")
    with pytest.raises(ValueError, match="Truncated"):
        parse_bytes(b"HXCF" + struct.pack("<III", 1, 0, 1))
