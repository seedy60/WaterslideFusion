"""Build Waterslide Fusion as a one-folder PyInstaller bundle + zip.

Usage:
    uv run python build.py

Produces:
    dist/WaterslideFusion/       the runnable bundle (WaterslideFusion.exe)
    dist/WaterslideFusion.zip    zipped for distribution
"""

import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).parent.resolve()
DIST = ROOT / "dist"
BUILD = ROOT / "build"
APP_NAME = "WaterslideFusion"
ENTRY = ROOT / "run_game.py"


def find_upx():
    """Try to find UPX on PATH or common locations (same as Seaview's build)."""
    for candidate in ["upx", r"C:\upx\upx.exe", r"C:\tools\upx\upx.exe"]:
        if shutil.which(candidate):
            return str(Path(shutil.which(candidate)).parent)
    return None

HIDDEN_IMPORTS = [
    # Speech stack: prism's native layer is a cffi extension loaded by path,
    # and ao2's outputs are imported dynamically - none of this is visible
    # to PyInstaller's static analysis.
    "prism", "prism.core", "prism.common", "prism.custom", "prism.log",
    "prism._dispatch", "prism._native", "prism._prism_cffi",
    "cffi", "_cffi_backend",
    "accessible_output2", "accessible_output2.outputs.auto",
    "accessible_output2.outputs.base",  # shared parent of every output class
    "accessible_output2.outputs.nvda", "accessible_output2.outputs.jaws",
    "accessible_output2.outputs.sapi5", "accessible_output2.outputs.sapi4",
    "accessible_output2.outputs.system_access",
    "accessible_output2.outputs.dolphin", "accessible_output2.outputs.pc_talker",
    "accessible_output2.outputs.window_eyes", "accessible_output2.outputs.e_speak",
    # Game stack
    "pygame", "pygame.mixer", "numpy", "capstone",
]


def build_datas():
    """--add-data args: original content (levels + sounds)."""
    args = []
    content = ROOT / "assets" / "content"
    if content.is_dir():
        args += ["--add-data", f"{content}{os.pathsep}content"]
    return args


def build_prism_natives():
    """--add-binary args for prism/_native (prism.dll + _prism_cffi.pyd).

    Shipped as binaries so PyInstaller scans their PE imports and pulls in
    cffi's _cffi_backend automatically; speech.py additionally pre-loads
    the .pyd by path because the frozen importer ignores prism's runtime
    __path__ trick.
    """
    import prism

    native = Path(prism.__file__).parent / "_native"
    if not native.is_dir():
        print(f"WARNING: {native} not found; Prism will not work in the exe.",
              file=sys.stderr)
        return []
    args = []
    for f in sorted(native.iterdir()):
        if f.suffix.lower() in (".pyd", ".dll"):
            args += ["--add-binary", f"{f}{os.pathsep}prism/_native"]
            print(f"  bundling Prism native: {f.name}")
    return args


def verify_speech_bundle(output_dir):
    """Fail the build if Prism's native layer is missing.

    A silent speech loss only shows up for a blind player after shipping,
    so it is checked here, at build time.
    """
    import accessible_output2

    required = ["prism.dll", "_prism_cffi.pyd"]
    found = {name: [] for name in required}
    for root, _, files in os.walk(output_dir):
        for f in files:
            if f in found:
                found[f].append(str(Path(root) / f))
    missing = [name for name, hits in found.items() if not hits]
    if missing:
        print(f"Prism was NOT bundled correctly: missing {', '.join(missing)}.",
              file=sys.stderr)
        sys.exit(1)
    for name, hits in found.items():
        rel = Path(hits[0]).relative_to(output_dir)
        print(f"  Prism OK: {name} -> {rel}")

    # ao2's outputs are imported dynamically (auto walks the package), so a
    # missing module would silently break the engine switch in the exe.
    outputs_dir = None
    for root, dirs, _ in os.walk(output_dir):
        if root.endswith(os.path.join("accessible_output2", "outputs")):
            outputs_dir = root
            break
    bundled = set(os.listdir(outputs_dir)) if outputs_dir else set()
    expected = [p.name for p in (
        Path(accessible_output2.__file__).parent / "outputs").glob("*.py")]
    missing = [name for name in expected if name not in bundled]
    if missing:
        print(f"accessible_output2 outputs NOT bundled: missing {', '.join(missing)}.",
              file=sys.stderr)
        sys.exit(1)
    print(f"  ao2 OK: {len(expected)} output modules bundled")


def make_zip(folder, zip_path):
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for root, _, files in os.walk(folder):
            for f in files:
                full = Path(root) / f
                zf.write(full, Path(APP_NAME) / full.relative_to(folder))
    size_mb = zip_path.stat().st_size / (1024 * 1024)
    print(f"Created {zip_path} ({size_mb:.1f} MB)")


def main():
    # Clean previous builds.  ignore_errors: a stale empty dist folder can
    # stay pinned by another process's CWD or a scanner handle - its
    # contents still get deleted, which is all a fresh build requires.
    for d in [DIST, BUILD]:
        shutil.rmtree(d, ignore_errors=True)

    # Prism must actually load here, not just import: a broken cffi layer
    # would only surface as silent speech loss in the shipped binary.
    try:
        import prism

        prism.Context()
    except Exception as e:
        print("Prism is not usable in this environment "
              "(run: uv sync, then uv run python build.py).", file=sys.stderr)
        print(f"Error: {type(e).__name__}: {e}", file=sys.stderr)
        sys.exit(1)

    upx_dir = find_upx()
    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--name", APP_NAME,
        "--windowed",
        "--optimize", "2",
        "--noconfirm",
        "--clean",
        "--paths", str(ROOT),
        "--noupx" if not upx_dir else f"--upx-dir={upx_dir}",
        "--version-file", "vdata.txt",
        str(ENTRY),
    ]
    for mod in HIDDEN_IMPORTS:
        cmd += ["--hidden-import", mod]
    cmd += build_datas()
    cmd += build_prism_natives()

    # Prism's own modules + natives as a belt-and-braces collect.  Same for
    # accessible_output2: its Auto output enumerates every output module, so
    # one missing module silently breaks the engine switch in the exe.
    cmd += ["--collect-all", "prism"]
    cmd += ["--collect-all", "accessible_output2"]

    # Trim weight that a game bundle never needs.
    for mod in ["tkinter", "_tkinter", "unittest", "test", "setuptools",
                "pip", "wheel", "matplotlib", "IPython"]:
        cmd += ["--exclude-module", mod]

    print("Running PyInstaller...")
    result = subprocess.run(cmd, cwd=str(ROOT))
    if result.returncode != 0:
        print("PyInstaller failed!", file=sys.stderr)
        sys.exit(1)

    output_dir = DIST / APP_NAME
    if not output_dir.is_dir():
        print(f"Expected output dir {output_dir} not found!", file=sys.stderr)
        sys.exit(1)

    verify_speech_bundle(output_dir)
    make_zip(output_dir, DIST / f"{APP_NAME}.zip")

    print("Build complete!")
    print(f"  Folder: {output_dir}")
    print(f"  Archive: {DIST / (APP_NAME + '.zip')}")


if __name__ == "__main__":
    main()
