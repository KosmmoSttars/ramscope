"""Builds RamScope.exe (one file) with PyInstaller.

    python tools/build.py

Result: RamScope.exe in the project folder — a portable single file with Python, Qt and the app inside.
It needs no install and writes nothing to the registry or disk. The build runs the exe's
--self-check afterwards and fails if it cannot read memory data.
"""

import io
import shutil
import struct
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

ICON = ROOT / "assets" / "ramscope.ico"
WORK = ROOT / "build"
ICON_SIZES = (16, 24, 32, 48, 64, 128, 256)

# Qt parts RamScope never uses. Paths are as PyInstaller places them inside the bundle.
TRIM = (
    "PyQt6/Qt6/bin/opengl32sw.dll",       # software OpenGL; everything is raster-painted
    "PyQt6/Qt6/translations",             # Qt's own UI translations; the app is English-only
    "PyQt6/Qt6/plugins/tls",              # no networking
    "PyQt6/Qt6/plugins/imageformats/qpdf.dll",
    "PyQt6/Qt6/bin/Qt6Pdf.dll",
    "libcrypto-3-x64.dll",
    "libssl-3-x64.dll",
)

SPEC = """import fnmatch
TRIM = {trim!r}

def keep(entry):
    dest = entry[0].replace("\\\\", "/")
    return not any(fnmatch.fnmatch(dest, pat) or dest.startswith(pat + "/") for pat in TRIM)

a = Analysis([{main!r}], pathex=[{root!r}], excludes=["tkinter"])
a.binaries = [b for b in a.binaries if keep(b)]
a.datas = [d for d in a.datas if keep(d)]
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, a.binaries, a.datas, [], name="RamScope", icon={icon!r},
          version={version!r}, console=False, upx=False)
"""

# Version resource: what Explorer's Properties → Details and Task Manager show for the exe.
VERSION_INFO = """VSVersionInfo(
  ffi=FixedFileInfo(filevers={nums}, prodvers={nums}, mask=0x3f, flags=0x0, OS=0x40004,
                    fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([StringTable('040904B0', [
      StringStruct('CompanyName', 'KosmoStars'),
      StringStruct('FileDescription', 'RamScope'),
      StringStruct('FileVersion', '{ver}'),
      StringStruct('InternalName', 'RamScope'),
      StringStruct('LegalCopyright', 'KosmoStars'),
      StringStruct('OriginalFilename', 'RamScope.exe'),
      StringStruct('ProductName', 'RamScope (portable)'),
      StringStruct('ProductVersion', '{ver}')])]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
"""


def render_icon() -> None:
    """Draws the app icon at every size and packs the PNGs into a multi-size .ico."""
    from PyQt6.QtCore import QBuffer, QIODevice, Qt
    from PyQt6.QtGui import QImage, QPainter
    from PyQt6.QtWidgets import QApplication

    from ramscope.widgets import draw_chip

    app = QApplication.instance() or QApplication([])
    images = []
    for size in ICON_SIZES:
        img = QImage(size, size, QImage.Format.Format_ARGB32)
        img.fill(Qt.GlobalColor.transparent)
        p = QPainter(img)
        draw_chip(p, size)
        p.end()
        buf = QBuffer()
        buf.open(QIODevice.OpenModeFlag.WriteOnly)
        img.save(buf, "PNG")
        images.append((size, bytes(buf.data())))
    del app

    # ICO container: header, one directory entry per size, then the PNG blobs.
    out = io.BytesIO()
    out.write(struct.pack("<HHH", 0, 1, len(images)))
    offset = 6 + 16 * len(images)
    for size, data in images:
        dim = 0 if size >= 256 else size  # 0 means 256 in the ICO format
        out.write(struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32, len(data), offset))
        offset += len(data)
    for _, data in images:
        out.write(data)
    ICON.parent.mkdir(parents=True, exist_ok=True)
    ICON.write_bytes(out.getvalue())
    print(f"icon   {ICON.relative_to(ROOT)} ({len(images)} sizes)")


def main() -> None:
    render_icon()
    WORK.mkdir(exist_ok=True)
    from ramscope import __version__

    nums = tuple(int(x) for x in __version__.split(".")) + (0,)
    version_file = WORK / "version_info.txt"
    version_file.write_text(VERSION_INFO.format(nums=nums, ver=__version__), encoding="utf-8")
    spec = WORK / "RamScope.spec"
    spec.write_text(SPEC.format(trim=TRIM, main=str(ROOT / "main.py"), root=str(ROOT), icon=str(ICON),
                                version=str(version_file)), encoding="utf-8")
    subprocess.run([
        sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
        "--distpath", str(WORK / "dist"), "--workpath", str(WORK / "tmp"), str(spec),
    ], check=True, cwd=ROOT)
    target = ROOT / "RamScope.exe"
    shutil.copy2(WORK / "dist" / "RamScope.exe", target)

    check = subprocess.run([str(target), "--self-check"])
    report = ROOT / "ramscope_check.txt"
    print(report.read_text(encoding="utf-8"))
    report.unlink()
    if check.returncode != 0:
        raise SystemExit("self-check failed: see the report above")
    print(f"done   {target.relative_to(ROOT)} ({target.stat().st_size / 2**20:.0f} MB)")


if __name__ == "__main__":
    main()
