"""Desk glyph drawn in code, used for the window, tray and widget package icons.

Run as a script to write the widget package's PNG assets: python icons.py <dir>
"""
import sys
from pathlib import Path

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap

ACCENT = "#006fc4"


def desk_pixmap(size: int, color: str, background: str | None = None, scale: float = 1.0) -> QPixmap:
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.transparent)
    p = QPainter(pixmap)
    p.setRenderHint(QPainter.Antialiasing)
    if background:
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(background))
        p.drawRoundedRect(QRectF(0, 0, size, size), size * 0.2, size * 0.2)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(color))
    u = size * scale / 32
    o = size * (1 - scale) / 2
    rect = lambda x, y, w, h, r: p.drawRoundedRect(QRectF(o + x * u, o + y * u, w * u, h * u), r * u, r * u)
    rect(3, 8, 26, 4, 1.5)    # desktop
    rect(13.5, 12, 5, 13, 1)  # column
    rect(7, 25, 18, 3.5, 1.5)  # foot
    p.end()
    return pixmap


def window_icon() -> QIcon:
    icon = QIcon()
    for size in (16, 24, 32, 48, 64, 256):
        icon.addPixmap(desk_pixmap(size, "white", ACCENT, 0.8))
    return icon


def tray_icon(light_taskbar: bool) -> QIcon:
    icon = QIcon()
    for size in (16, 20, 24, 32, 48):
        icon.addPixmap(desk_pixmap(size, "black" if light_taskbar else "white"))
    return icon


def write_package_assets(folder: Path) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    tiles = {"Square44x44Logo.png": 44, "Square150x150Logo.png": 150, "StoreLogo.png": 50}
    for name, size in tiles.items():
        desk_pixmap(size, "white", ACCENT, 0.8).save(str(folder / name))
    desk_pixmap(48, ACCENT).save(str(folder / "WidgetIcon.png"))
    widget_preview().save(str(folder / "WidgetScreenshot.png"))


def widget_preview() -> QPixmap:
    """Shown in the Widgets board's picker."""
    from PySide6.QtGui import QFont
    pixmap = QPixmap(300, 304)
    pixmap.fill(QColor("#2c2c2c"))
    p = QPainter(pixmap)
    p.setRenderHint(QPainter.Antialiasing)
    p.drawPixmap(116, 40, desk_pixmap(68, ACCENT))
    p.setPen(QColor("white"))
    p.setFont(QFont("Segoe UI", 30, QFont.DemiBold))
    p.drawText(QRectF(0, 120, 300, 60), Qt.AlignCenter, "76.5 cm")
    p.setFont(QFont("Segoe UI", 11))
    for i, label in enumerate(("Sit", "Stand")):
        rect = QRectF(24 + i * 132, 210, 120, 36)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(ACCENT if i == 0 else "#3d3d3d"))
        p.drawRoundedRect(rect, 6, 6)
        p.setPen(QColor("white"))
        p.drawText(rect, Qt.AlignCenter, label)
    p.end()
    return pixmap


if __name__ == "__main__":
    from PySide6.QtGui import QGuiApplication
    app = QGuiApplication(sys.argv[:1])
    write_package_assets(Path(sys.argv[1]))
