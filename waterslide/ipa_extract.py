"""Extract game resources from the original Waterslide Extreme .ipa.

The .ipa is a zip archive containing ``Payload/Glide.app``.  Despite the
binary (``Glide``) being FairPlay-encrypted (LC_ENCRYPTION_INFO, cryptid=1),
the level files (``content/level/track_0N.prt``), the sound effects and the
symbol table are plaintext, which is what this port needs.  The Mach-O load
commands are parsed here and the code region is optionally sanity-checked
with capstone (ARM/Thumb) before extraction proceeds.
"""

from __future__ import annotations

import os
import struct
import zipfile
from dataclasses import dataclass, field
from typing import Dict, List, Optional

DEFAULT_IPA = os.path.join("IPA", "Waterslide Extreme (iOS).ipa")
APP_DIR = "Waterslide/Payload/Glide.app"

LC_ENCRYPTION_INFO = 0x21
LC_SYMTAB = 0x2

# Files we extract by default (relative to Glide.app).
DEFAULT_INCLUDES = (
    "config.cfg",
    "gb.lang",
    "Info.plist",
    "icon.png",
    "content/level/track_01.prt",
    "content/level/track_02.prt",
    "content/level/track_03.prt",
    "content/level/track_04.prt",
    "content/level/track_05.prt",
    "content/level/track_06.prt",
    "content/level/track_07.prt",
    "content/level/track_08.prt",
    "content/level/track_09.prt",
    "content/sounds/3-2-1.wav",
    "content/sounds/Brake.wav",
    "content/sounds/Crab.wav",
    "content/sounds/Evil-Duck.wav",
    "content/sounds/Female-Hit-Crab.wav",
    "content/sounds/Female-Loop-The-Loop.wav",
    "content/sounds/Hit-Cheveron.wav",
    "content/sounds/Male-Fall-Out-Of-Slide.wav",
    "content/sounds/Male-Hit-Crab.wav",
    "content/sounds/Male-Loop-The-Loop.wav",
    "content/sounds/Menu-Button-Press.wav",
    "content/sounds/Pick-Up-Diamond.wav",
    "content/sounds/Pick-Up-God-Mode.wav",
    "content/sounds/Pick-Up-Heart.wav",
    "content/sounds/evening_no_intro.mp3",
    "content/sounds/female-fall.wav",
    "content/sounds/hit-duck-god-mode.wav",
    "content/sounds/start.wav",
)


@dataclass
class MachOInfo:
    """Parsed 32-bit Mach-O header / load commands of the Glide binary."""

    cputype: int = 0
    cpusubtype: int = 0
    ncmds: int = 0
    segments: List[Dict[str, object]] = field(default_factory=list)
    symoff: int = 0
    nsyms: int = 0
    stroff: int = 0
    strsize: int = 0
    cryptoff: int = 0
    cryptsize: int = 0
    cryptid: int = 0

    @property
    def fairplay_encrypted(self) -> bool:
        return self.cryptsize > 0 and self.cryptid != 0

    @property
    def is_arm(self) -> bool:
        return self.cputype == 12  # CPU_TYPE_ARM


def parse_macho(binary: bytes) -> MachOInfo:
    """Parse the load commands of a 32-bit little-endian Mach-O image."""
    if len(binary) < 28:
        raise ValueError("file too small to be a Mach-O image")
    magic, cputype, cpusub, _ftype, ncmds, _sizeof, _flags, _res = struct.unpack_from(
        "<8I", binary, 0
    )
    if magic != 0xFEEDFACE:
        raise ValueError("not a 32-bit little-endian Mach-O image (bad magic)")
    info = MachOInfo(cputype=cputype, cpusubtype=cpusub, ncmds=ncmds)
    off = 28
    for _ in range(ncmds):
        cmd, size = struct.unpack_from("<II", binary, off)
        if size < 8 or off + size > len(binary):
            break
        if cmd == 0x1:  # LC_SEGMENT (32-bit; 0x19 is LC_SEGMENT_64)
            name = binary[off + 8 : off + 24].split(b"\0")[0].decode("ascii", "replace")
            vmaddr, vmsize, fileoff, filesize = struct.unpack_from("<4I", binary, off + 24)
            info.segments.append(
                {
                    "name": name,
                    "vmaddr": vmaddr,
                    "vmsize": vmsize,
                    "fileoff": fileoff,
                    "filesize": filesize,
                }
            )
        elif cmd == LC_SYMTAB:
            info.symoff, info.nsyms, info.stroff, info.strsize = struct.unpack_from(
                "<4I", binary, off + 8
            )
        elif cmd == LC_ENCRYPTION_INFO:
            info.cryptoff, info.cryptsize, info.cryptid = struct.unpack_from(
                "<3I", binary, off + 8
            )
        off += size
    return info


def extract_symbols(binary: bytes, info: MachOInfo, limit: Optional[int] = None) -> List[str]:
    """Return symbol names from the (plaintext) symbol table."""
    names: List[str] = []
    if not info.symoff or not info.stroff:
        return names
    for i in range(info.nsyms):
        entry = info.symoff + i * 16
        if entry + 16 > len(binary):
            break
        n_strx = struct.unpack_from("<I", binary, entry)[0]
        if n_strx == 0 or n_strx >= info.strsize:
            continue
        start = info.stroff + n_strx
        end = binary.find(b"\0", start, info.stroff + info.strsize)
        if end < 0:
            continue
        name = binary[start:end].decode("ascii", "replace")
        if name:
            names.append(name)
            if limit is not None and len(names) >= limit:
                break
    return names


def verify_with_capstone(binary: bytes, info: MachOInfo) -> Dict[str, object]:
    """Disassemble a few instructions at _main with capstone as a sanity check.

    ``_main`` usually sits before the encrypted region, so real ARM/Thumb
    code should decode.  Returns a small report dictionary.
    """
    report: Dict[str, object] = {"available": False}
    try:
        import capstone  # type: ignore
    except ImportError:
        return report
    report["available"] = True
    md = capstone.Cs(capstone.CS_ARCH_ARM, capstone.CS_MODE_THUMB)
    # Locate _main's value from the symbol table.
    main_addr = None
    if info.symoff and info.stroff:
        for i in range(info.nsyms):
            entry = info.symoff + i * 16
            if entry + 16 > len(binary):
                break
            n_strx, n_type = struct.unpack_from("<IB", binary, entry)
            if n_strx == 0 or n_strx >= info.strsize:
                continue
            start = info.stroff + n_strx
            end = binary.find(b"\0", start, info.stroff + info.strsize)
            if end < 0:
                continue
            if binary[start:end] == b"_main":
                main_addr = struct.unpack_from("<I", binary, entry + 8)[0]
                break
    report["main_addr"] = main_addr
    if info.cryptoff:
        # _main is usually inside the FairPlay-encrypted region; disassemble
        # the first unencrypted code instead as a sanity check.
        target = main_addr if (main_addr is not None and main_addr < info.cryptoff) else info.cryptoff
    else:
        target = main_addr
    if target is None:
        report["decoded"] = 0
        return report
    off = target & ~1  # clear the thumb bit
    code = binary[off : off + 64]
    count = 0
    for _ins in md.disasm(code, target & ~1):
        count += 1
        if count >= 8:
            break
    report["decoded"] = count
    report["disasm_addr"] = target
    return report


def extract(
    ipa_path: str = DEFAULT_IPA,
    dest: str = "assets",
    includes: "tuple[str, ...]" = DEFAULT_INCLUDES,
    with_capstone: bool = True,
) -> Dict[str, object]:
    """Extract resources from the ipa into ``dest`` (default ``./assets``).

    Returns a report dict describing what was extracted.
    """
    report: Dict[str, object] = {"ipa": ipa_path, "dest": dest, "files": []}
    if not os.path.exists(ipa_path):
        raise FileNotFoundError(
            f"IPA not found at {ipa_path!r}; run with --ipa PATH to point at "
            "'Waterslide Extreme (iOS).ipa'"
        )
    os.makedirs(dest, exist_ok=True)
    with zipfile.ZipFile(ipa_path) as zf:
        # Binary: parse Mach-O, verify with capstone, save for reference.
        binary = zf.read(APP_DIR + "/Glide")
        info = parse_macho(binary)
        report["macho"] = {
            "cputype": info.cputype,
            "fairplay_encrypted": info.fairplay_encrypted,
            "cryptsize": info.cryptsize,
            "segments": [s["name"] for s in info.segments],
            "nsyms": info.nsyms,
        }
        if with_capstone:
            report["capstone"] = verify_with_capstone(binary, info)
        with open(os.path.join(dest, "Glide.macho.bin"), "wb") as fh:
            fh.write(binary)
        syms = extract_symbols(binary, info, limit=5000)
        with open(os.path.join(dest, "symbols.txt"), "w", encoding="utf-8") as fh:
            fh.write("\n".join(syms))
        report["symbols"] = len(syms)
        # Payload files.
        for rel in includes:
            arcname = APP_DIR + "/" + rel
            try:
                data = zf.read(arcname)
            except KeyError:
                report["files"].append((rel, "missing"))
                continue
            out = os.path.join(dest, *rel.split("/"))
            os.makedirs(os.path.dirname(out) or dest, exist_ok=True)
            with open(out, "wb") as fh:
                fh.write(data)
            report["files"].append((rel, len(data)))
    return report


def main(argv: Optional[List[str]] = None) -> int:
    import argparse

    p = argparse.ArgumentParser(description="Extract Waterslide Extreme resources from an .ipa")
    p.add_argument("--ipa", default=DEFAULT_IPA, help="path to 'Waterslide Extreme (iOS).ipa'")
    p.add_argument("--dest", default="assets", help="output directory (default: ./assets)")
    p.add_argument("--no-capstone", action="store_true", help="skip capstone disassembly check")
    args = p.parse_args(argv)
    rep = extract(args.ipa, args.dest, with_capstone=not args.no_capstone)
    macho = rep["macho"]
    print(f"Mach-O: cpu={macho['cputype']} fairplay_encrypted={macho['fairplay_encrypted']} "
          f"nsyms={macho['nsyms']} segments={macho['segments']}")
    cap = rep.get("capstone") or {}
    if cap.get("available"):
        print(f"capstone: probe at {cap.get('disasm_addr', cap.get('main_addr')):#x} "
              f"decoded {cap.get('decoded')} instructions (informational: the code region "
              "is FairPlay-encrypted; see tools/disasm.py for the plaintext regions)")
    else:
        print("capstone: not installed, skipped (uv add capstone)")
    print(f"extracted {sum(1 for _, s in rep['files'] if s != 'missing')} files to {args.dest}")
    for rel, status in rep["files"]:
        if status == "missing":
            print(f"  MISSING: {rel}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
