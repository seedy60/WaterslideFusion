"""Tests for the .ipa extraction and Mach-O analysis."""

import os

import pytest

from waterslide.ipa_extract import (
    DEFAULT_IPA,
    MachOInfo,
    extract,
    extract_symbols,
    parse_macho,
    verify_with_capstone,
)

HAS_IPA = os.path.exists(DEFAULT_IPA)
BIN = "build/ipa_extract/Glide.bin"


def _binary() -> bytes:
    if os.path.exists(BIN):
        with open(BIN, "rb") as fh:
            return fh.read()
    import zipfile

    with zipfile.ZipFile(DEFAULT_IPA) as zf:
        return zf.read("Waterslide/Payload/Glide.app/Glide")


@pytest.mark.skipif(not HAS_IPA and not os.path.exists(BIN), reason="ipa/fixtures not present")
class TestMacho:
    def test_parses_header(self):
        info = parse_macho(_binary())
        assert info.is_arm
        assert info.ncmds > 10

    def test_segments_found(self):
        info = parse_macho(_binary())
        names = [s["name"] for s in info.segments]
        assert "__TEXT" in names and "__DATA" in names

    def test_fairplay_flag(self):
        info = parse_macho(_binary())
        assert info.fairplay_encrypted
        assert info.cryptoff == 4096
        assert info.cryptsize > 0

    def test_symbols_readable(self):
        binary = _binary()
        info = parse_macho(binary)
        syms = extract_symbols(binary, info)
        assert len(syms) > 1000
        joined = "\n".join(syms)
        assert "_main" in joined
        # engine class names from the original sources
        assert "AbyssEngine" in joined or "PRSpline" in joined

    def test_bad_magic_rejected(self):
        with pytest.raises(ValueError):
            parse_macho(b"\x00" * 64)

    def test_capstone_check(self):
        binary = _binary()
        info = parse_macho(binary)
        report = verify_with_capstone(binary, info)
        if report.get("available"):
            assert report["decoded"] >= 1, "capstone should decode unencrypted code"

    def test_full_extract_to_tmp(self, tmp_path):
        if not HAS_IPA:
            pytest.skip("ipa not present")
        rep = extract(DEFAULT_IPA, str(tmp_path / "assets"))
        files = [rel for rel, st in rep["files"] if st != "missing"]
        assert any("track_01.prt" in f for f in files)
        assert any(f.endswith("gb.lang") for f in files)
        assert (tmp_path / "assets" / "content" / "level" / "track_09.prt").exists()
