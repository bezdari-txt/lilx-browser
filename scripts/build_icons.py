"""Generate the platform icon files from the project's logo.png.

    python scripts/build_icons.py

``logo.png`` (project root) is the single source of the lilx icon and is never
modified. Everything written here is derived from it and can be regenerated:

    packaging/icons/lilx.icns                         macOS (.app bundle, Finder, Dock)
    packaging/icons/lilx.ico                          Windows (.exe, shortcuts, taskbar)
    packaging/linux/icons/hicolor/<N>x<N>/apps/lilx.png   Linux icon theme sizes

Only Qt (already a dependency) is used for scaling and PNG encoding, so the script
runs the same on macOS, Linux and Windows. ICO and ICNS containers are written
directly: both simply embed PNG images (supported by Windows Vista+ and macOS 10.7+).
"""

from __future__ import annotations

import struct
import sys
from pathlib import Path

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, Qt
from PySide6.QtGui import QImage, QPainter

ROOT = Path(__file__).resolve().parent.parent
LOGO = ROOT / "logo.png"
ICONS_DIR = ROOT / "packaging" / "icons"
HICOLOR_DIR = ROOT / "packaging" / "linux" / "icons" / "hicolor"

ICO_SIZES = (16, 24, 32, 48, 64, 128, 256)
LINUX_SIZES = (16, 24, 32, 48, 64, 128, 256, 512)
# ICNS chunk types holding PNG data: (type, pixel size). "@2x" variants are the *_ones.
ICNS_CHUNKS = (
    (b"icp4", 16), (b"icp5", 32), (b"icp6", 64),
    (b"ic07", 128), (b"ic08", 256), (b"ic09", 512), (b"ic10", 1024),
    (b"ic11", 32), (b"ic12", 64), (b"ic13", 256), (b"ic14", 512),
)


def load_logo() -> QImage:
    image = QImage(str(LOGO))
    if image.isNull():
        sys.exit(f"cannot read {LOGO}")
    return image.convertToFormat(QImage.Format.Format_ARGB32)


def png_bytes(logo: QImage, size: int) -> bytes:
    scaled = logo.scaled(size, size, Qt.AspectRatioMode.KeepAspectRatio,
                         Qt.TransformationMode.SmoothTransformation)
    if scaled.width() != size or scaled.height() != size:  # non-square logo: centre it
        canvas = QImage(size, size, QImage.Format.Format_ARGB32)
        canvas.fill(Qt.GlobalColor.transparent)
        painter = QPainter(canvas)
        painter.drawImage((size - scaled.width()) // 2, (size - scaled.height()) // 2, scaled)
        painter.end()
        scaled = canvas
    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    scaled.save(buffer, "PNG")
    buffer.close()
    return bytes(data.data())


def write_ico(logo: QImage, target: Path) -> None:
    images = [(size, png_bytes(logo, size)) for size in ICO_SIZES]
    header = struct.pack("<HHH", 0, 1, len(images))  # reserved, type 1 = icon, count
    offset = 6 + 16 * len(images)
    entries, payload = b"", b""
    for size, data in images:
        dim = 0 if size >= 256 else size  # 0 means 256 in the ICO directory
        entries += struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32, len(data), offset)
        payload += data
        offset += len(data)
    target.write_bytes(header + entries + payload)


def write_icns(logo: QImage, target: Path) -> None:
    body = b""
    for kind, size in ICNS_CHUNKS:
        data = png_bytes(logo, size)
        body += kind + struct.pack(">I", 8 + len(data)) + data
    target.write_bytes(b"icns" + struct.pack(">I", 8 + len(body)) + body)


def write_linux_pngs(logo: QImage) -> None:
    for size in LINUX_SIZES:
        folder = HICOLOR_DIR / f"{size}x{size}" / "apps"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "lilx.png").write_bytes(png_bytes(logo, size))


def main() -> int:
    logo = load_logo()
    ICONS_DIR.mkdir(parents=True, exist_ok=True)
    write_icns(logo, ICONS_DIR / "lilx.icns")
    write_ico(logo, ICONS_DIR / "lilx.ico")
    write_linux_pngs(logo)
    for path in sorted([*ICONS_DIR.iterdir(), *HICOLOR_DIR.rglob("*.png")]):
        print(f"{path.relative_to(ROOT)}  ({path.stat().st_size // 1024} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
