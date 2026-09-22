#!/usr/bin/env python
"""Disassemble the original Glide binary with symbol annotations.

The FairPlay encryption (cryptid=1) protects only [cryptoff, cryptoff +
cryptsize); everything else - notably the Objective-C shim code, some
runtime glue, and the whole __DATA/__LINKEDIT regions - is plaintext and
disassembles cleanly.  This tool:

* parses the Mach-O load commands and symbol table;
* builds an address -> symbol map (including ObjC method names);
* disassembles a chosen region (default: everything outside the encrypted
  range) with capstone, annotating instructions that branch to or from
  known symbols;
* supports searching symbols and disassembling around a symbol/address.

Examples:
    python tools/disasm.py --stats
    python tools/disasm.py --symbols main
    python tools/disasm.py --addr 0x2104 --length 64
    python tools/disasm.py --region text --out build/disasm_text.txt
"""

import argparse
import os
import struct
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from waterslide.ipa_extract import APP_DIR, DEFAULT_IPA, parse_macho  # noqa: E402

CPU_ARCH_ABI64 = 0x01000000
CPU_TYPE_X86 = 7
CPU_TYPE_ARM = 12
LC_SYMTAB = 0x2


def load_binary(path: str = "build/ipa_extract/Glide.bin") -> bytes:
    if os.path.exists(path):
        with open(path, "rb") as fh:
            return fh.read()
    import zipfile

    with zipfile.ZipFile(DEFAULT_IPA) as zf:
        return zf.read(APP_DIR + "/Glide")


def load_symbols(binary: bytes):
    """Return {value: [names]} and a flat list of (value, name) pairs."""
    info = parse_macho(binary)
    by_addr = defaultdict(list)
    flat = []
    if not info.symoff or not info.stroff:
        return by_addr, flat, info
    for i in range(info.nsyms):
        entry = info.symoff + i * 16
        if entry + 16 > len(binary):
            break
        n_strx, n_type, n_sect = struct.unpack_from("<IBB", binary, entry)
        n_value = struct.unpack_from("<I", binary, entry + 8)[0]
        if n_strx == 0 or n_strx >= info.strsize:
            continue
        start = info.stroff + n_strx
        end = binary.find(b"\0", start, info.stroff + info.strsize)
        if end < 0:
            continue
        name = binary[start:end].decode("ascii", "replace")
        if not name:
            continue
        by_addr[n_value].append(name)
        flat.append((n_value, name, n_type))
    return by_addr, flat, info


def demangle(name: str) -> str:
    """Very light __ZN demangling: extract length-prefixed identifiers."""
    if not name.startswith("__ZN"):
        return name
    body = name[4:]
    parts = []
    while body:
        digits = ""
        while body and body[0].isdigit():
            digits += body[0]
            body = body[1:]
        if not digits:
            break
        n = int(digits)
        if n <= 0 or n > len(body):
            break
        parts.append(body[:n])
        body = body[n:]
    return "::".join(parts) if parts else name


def symbol_label(by_addr, addr: int):
    names = by_addr.get(addr)
    if not names:
        return None
    return demangle(names[0])


def disasm_region(binary: bytes, by_addr, addr: int, length: int, cpu_type: int):
    import capstone

    if cpu_type == CPU_TYPE_ARM:
        md_arm = capstone.Cs(capstone.CS_ARCH_ARM, capstone.CS_MODE_ARM)
        md_thumb = capstone.Cs(capstone.CS_ARCH_ARM, capstone.CS_MODE_THUMB)
        mds = (md_arm, md_thumb)
    else:
        md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)
        mds = (md,)
    lines = []
    code = binary[addr : addr + length]
    for md in mds:
        lines = []
        ok = 0
        for ins in md.disasm(code, addr):
            ok += 1
            label = symbol_label(by_addr, ins.address)
            prefix = f"{label}: " if label else ""
            # annotate branch targets
            note = ""
            if ins.groups and any(g in (capstone.CS_GRP_JUMP, capstone.CS_GRP_CALL) for g in ins.groups):
                try:
                    target = int(ins.op_str.split("#")[-1].strip(), 0)
                except Exception:
                    target = None
                if target is not None:
                    tlabel = symbol_label(by_addr, target)
                    if tlabel:
                        note = f"   ; -> {tlabel}"
            lines.append(f"{ins.address:08x}  {prefix}{ins.mnemonic:10s} {ins.op_str}{note}")
        if ok >= max(1, length // 8):  # a mode that decoded most of the region
            break
    if not lines:
        lines = ["; (no instructions decoded here]"]
    return lines


def find_symbol(flat, query: str):
    q = query.lower()
    return [(v, n) for (v, n, _t) in flat if q in n.lower()]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--binary", default="build/ipa_extract/Glide.bin")
    ap.add_argument("--stats", action="store_true", help="show load commands + symbol stats")
    ap.add_argument("--symbols", metavar="QUERY", help="list symbols containing QUERY")
    ap.add_argument("--addr", help="disassemble at address (0x... accepted)")
    ap.add_argument("--symbol", help="disassemble at the first symbol matching QUERY")
    ap.add_argument("--length", type=lambda x: int(x, 0), default=96)
    ap.add_argument("--region", choices=["plain", "all"], default="plain",
                    help="'plain' = everything outside the FairPlay range")
    ap.add_argument("--out", help="write disassembly to a file")
    args = ap.parse_args()

    binary = load_binary(args.binary)
    info = parse_macho(binary)
    by_addr, flat, _ = load_symbols(binary)
    cpu = info.cputype

    if args.stats:
        enc_end = info.cryptoff + info.cryptsize
        print(f"cpu type      : {cpu} ({'armv6' if cpu == CPU_TYPE_ARM else hex(cpu)})")
        print(f"total size    : {len(binary)} bytes")
        if info.cryptid == 0 or info.cryptsize == 0:
            print("status        : decrypted dump - no FairPlay encryption present;")
            print("                the whole __TEXT segment disassembles cleanly")
        else:
            print(f"encrypted     : [{info.cryptoff:#x}, {enc_end:#x}) cryptid={info.cryptid}")
            print("                (a memory dump of the app should have cryptid=0 "
                  "or a plaintext range here)")
        print(f"symbols       : {len(flat)} ({len(by_addr)} unique addresses)")
        print(f"plaintext code: header {info.cryptoff:#x} + tail {len(binary) - enc_end:#x}")
        n_cpp = sum(1 for (_v, n, _t) in flat if n.startswith("__ZN") or n.startswith("__Z"))
        n_objc = sum(1 for (_v, n, _t) in flat if n.startswith("-[") or n.startswith("+[") or "_OBJC_" in n)
        print(f"  mangled C++ : {n_cpp}")
        print(f"  ObjC        : {n_objc}")
        return 0

    if args.symbols:
        hits = find_symbol(flat, args.symbols)
        for v, n in hits[:200]:
            print(f"{v:#010x}  {demangle(n)}")
        print(f"-- {len(hits)} matches", file=sys.stderr)
        return 0

    if args.addr or args.symbol:
        if args.addr:
            addr = int(args.addr, 0)
        else:
            hits = find_symbol(flat, args.symbol or "")
            if not hits:
                print("no matching symbol", file=sys.stderr)
                return 1
            addr = hits[0][0]
            print(f"; symbol: {demangle(hits[0][1])}")
        if info.cryptoff <= addr < info.cryptoff + info.cryptsize:
            print(f"; WARNING: {addr:#x} lies inside the FairPlay-encrypted region; "
                  "output will be garbage.", file=sys.stderr)
        lines = disasm_region(binary, by_addr, addr, args.length, cpu)
        out = "\n".join(lines)
        if args.out:
            with open(args.out, "w", encoding="utf-8") as fh:
                fh.write(out + "\n")
            print(f"wrote {len(lines)} instructions to {args.out}")
        else:
            print(out)
        return 0

    # default: disassemble all plaintext regions
    chunks = [(0, info.cryptoff)] if info.cryptoff else []
    enc_end = info.cryptoff + info.cryptsize
    if enc_end < len(binary):
        chunks.append((enc_end, len(binary) - enc_end))
    all_lines = ["; Glide (Waterslide Extreme) - plaintext region disassembly"]
    for base, size in chunks:
        if size <= 0:
            continue
        all_lines.append(f"; ---- region {base:#x} .. {base + size:#x} ({size} bytes)")
        all_lines += disasm_region(binary, by_addr, base, size, cpu)
    out = "\n".join(all_lines)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(out + "\n")
        print(f"wrote {len(all_lines)} lines to {args.out}")
    else:
        print(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
