"""Desk Control: a small Fluent desktop app for a Linak standing desk."""
import asyncio
import json
import logging
import logging.handlers
import os
import sys
from pathlib import Path

from PySide6.QtCore import QPoint, Qt, QTimer
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication, QDialog, QHBoxLayout, QListWidgetItem, QSystemTrayIcon, QVBoxLayout, QWidget,
)
from qfluentwidgets import (
    Action, BodyLabel, CaptionLabel, CardWidget, DisplayLabel, DoubleSpinBox, FluentIcon as FIF,
    FluentWidget, IconWidget, IndeterminateProgressRing, InfoBar, InfoBarPosition, LineEdit, ListWidget,
    MessageBoxBase, PrimaryPushButton, RoundMenu, TransparentPushButton,
    PushButton, SmoothScrollArea, StrongBodyLabel, SubtitleLabel, SystemTrayMenu, Theme, TransparentToolButton,
    setTheme, setThemeColor,
)
from qframelesswindow.utils import getSystemAccentColor
import qasync

if sys.platform == "win32":
    from bleak.backends.winrt.util import allow_sta
    allow_sta()

import alexa_link
import ipc
from desk import MAX_POSITION, LinakDesk, NotLinakError, scan_devices, verify_desk
from icons import tray_icon, window_icon

SETTINGS_PATH = Path(os.environ.get("APPDATA", Path.home())) / "DeskControl" / "settings.json"
LOG_PATH = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "desk-control" / "desk-control.log"
DEFAULT_SETTINGS = {"address": None, "desk_name": None, "offset_mm": 620.0, "presets": [], "tray_hint_shown": False,
                    "alexa": True}


def load_settings() -> dict:
    try:
        return {**DEFAULT_SETTINGS, **json.loads(SETTINGS_PATH.read_text())}
    except (OSError, ValueError):
        return dict(DEFAULT_SETTINGS)


def save_settings(settings: dict) -> None:
    SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS_PATH.write_text(json.dumps(settings, indent=2))


def spawn(coro, on_error=None) -> asyncio.Task:
    task = asyncio.ensure_future(coro)

    def done(t: asyncio.Task):
        if not t.cancelled() and t.exception() and on_error:
            on_error(t.exception())
    task.add_done_callback(done)
    return task


class HeightDialog(MessageBoxBase):
    """Asks for a height in cm, optionally with a name."""

    def __init__(self, title: str, height_cm: float, parent, name: str | None = None, hint: str = ""):
        super().__init__(parent)
        self.viewLayout.addWidget(SubtitleLabel(title, self))
        if hint:
            hint_label = CaptionLabel(hint, self)
            hint_label.setWordWrap(True)
            self.viewLayout.addWidget(hint_label)
        self.name_edit = None
        if name is not None:
            self.name_edit = LineEdit(self)
            self.name_edit.setPlaceholderText("Name, e.g. Standing")
            self.name_edit.setText(name)
            self.viewLayout.addWidget(self.name_edit)
        self.height_box = DoubleSpinBox(self)
        self.height_box.setRange(30, 200)
        self.height_box.setDecimals(1)
        self.height_box.setSingleStep(0.5)
        self.height_box.setSuffix(" cm")
        self.height_box.setValue(height_cm)
        self.viewLayout.addWidget(self.height_box)
        self.widget.setMinimumWidth(320)
        if self.name_edit:
            self.name_edit.setFocus()

    def validate(self) -> bool:
        return self.name_edit is None or bool(self.name_edit.text().strip())


async def run_dialog(dialog: QDialog) -> bool:
    """Show a dialog without a nested event loop, so asyncio (and Bluetooth) keep running."""
    done = asyncio.get_running_loop().create_future()
    dialog.finished.connect(lambda result: done.done() or done.set_result(result == QDialog.Accepted))
    dialog.open()
    return await done


class DeskPickerDialog(MessageBoxBase):
    """Scans for Bluetooth devices and checks the chosen one is a Linak desk controller."""

    def __init__(self, parent):
        super().__init__(parent)
        self.chosen: dict | None = None
        self.devices: list[dict] = []
        self.busy = False
        self._task: asyncio.Task | None = None

        self.viewLayout.addWidget(SubtitleLabel("Choose your desk", self))
        hint = BodyLabel(
            "Put the desk in pairing mode, then pick it below. On an IKEA Idåsen, hold the Bluetooth button "
            "on the handset until its light flashes blue. Desks usually show up as “Desk” and a number.", self)
        hint.setWordWrap(True)
        self.viewLayout.addWidget(hint)
        self.list = ListWidget(self)
        self.list.setMinimumHeight(220)
        self.list.currentRowChanged.connect(lambda _: self._update_buttons())
        self.viewLayout.addWidget(self.list)

        row = QHBoxLayout()
        self.ring = IndeterminateProgressRing(self)
        self.ring.setFixedSize(16, 16)
        self.ring.hide()
        self.status = CaptionLabel("", self)
        self.status.setWordWrap(True)
        self.rescan = PushButton(FIF.SYNC, "Scan again", self)
        self.rescan.clicked.connect(lambda: self._run(self.scan()))
        row.addWidget(self.ring)
        row.addWidget(self.status, 1)
        row.addWidget(self.rescan)
        self.viewLayout.addLayout(row)

        self.yesButton.setText("Connect")
        self.widget.setMinimumWidth(340)
        self.finished.connect(lambda _: self._task and self._task.cancel())
        self._update_buttons()
        QTimer.singleShot(0, lambda: self._run(self.scan()))

    def _run(self, coro) -> None:
        self._task = spawn(coro, lambda e: self.error("Bluetooth error", str(e) or type(e).__name__))

    def error(self, title: str, message: str) -> None:
        InfoBar.error(title, message, parent=self, position=InfoBarPosition.TOP, duration=10000)

    def _set_busy(self, busy: bool, text: str) -> None:
        self.busy = busy
        self.ring.setVisible(busy)
        self.status.setText(text)
        self.rescan.setEnabled(not busy)
        self.list.setEnabled(not busy)
        self._update_buttons()

    def _update_buttons(self) -> None:
        self.yesButton.setEnabled(not self.busy and self.list.currentRow() >= 0)

    async def scan(self) -> None:
        self._set_busy(True, "Scanning for Bluetooth devices…")
        self.list.clear()
        try:
            self.devices = await scan_devices()
        finally:
            for device in self.devices:
                item = QListWidgetItem(f"{device['name']}  ({device['address']})")
                if device["likely"]:
                    item.setIcon(FIF.PIN.icon())
                self.list.addItem(item)
            if self.devices and self.devices[0]["likely"]:
                self.list.setCurrentRow(0)
            self._set_busy(False, f"Found {len(self.devices)} device(s)." if self.devices
                           else "No devices found. Is Bluetooth on and the desk in pairing mode?")

    def validate(self) -> bool:
        # Connecting takes a while, so verify asynchronously and accept() when done.
        if not self.busy and self.list.currentRow() >= 0:
            self._run(self.verify(self.devices[self.list.currentRow()]))
        return False

    async def verify(self, device: dict) -> None:
        self._set_busy(True, f"Pairing with {device['name']}… this can take up to 30 seconds.")
        try:
            await verify_desk(device["address"], device["name"])
        except NotLinakError as e:
            self._set_busy(False, "")
            self.error("Not a desk controller", f"{e} Choose your desk's Linak controller instead.")
            return
        except Exception as e:
            self._set_busy(False, "")
            self.error(f"Couldn't connect to {device['name']}",
                       f"{str(e) or type(e).__name__}. If this is your desk, put it in pairing mode and try again.")
            return
        self.chosen = device
        self.accept()


HELP_HTML = """
<h3>Getting started</h3>
<p>Desk Control works with Linak Bluetooth desk controllers, such as the one in the IKEA Idåsen.
The first time you start it, put the desk in pairing mode (on an Idåsen, hold the Bluetooth button on the
handset until the light flashes blue) and choose it from the list. The app remembers your desk and connects
to it automatically from then on. Connecting can take up to 30 seconds.</p>
<h3>Moving the desk</h3>
<ul>
<li><b>Up / Down</b>: hold the button to move, release to stop, just like the handset.</li>
<li><b>Go</b>: type a height and click Go to move straight to it.</li>
<li><b>Stop</b>: stops the desk at any time. <b>Esc</b> or <b>Space</b> does the same.</li>
</ul>
<h3>Presets</h3>
<p>Move the desk to a height you like and click <b>Save current</b> to name it. Click a preset to move the desk
there; use the bin icon to delete it.</p>
<h3>Calibrating the height</h3>
<p>The desk only reports how far it is above its lowest position. The app assumes an Idåsen, whose lowest
height is 62 cm. For other desks, or to fine-tune, measure from the floor to the top of the desk and enter
it in <b>Settings → Calibrate height</b>.</p>
<h3>Tray and Widgets board</h3>
<p>Closing the window keeps Desk Control running in the tray, connected to the desk. Right-click the tray
icon for your presets, Stop and Quit. The app also starts in the tray when you sign in to Windows.</p>
<p>To add the widget, press <b>Win+W</b>, click <b>+</b>, find <b>Desk Control</b> and pin the
<b>Desk</b> widget. It shows the height and your presets while the app is running.</p>
<h3>Alexa</h3>
<p>With an Alexa skill of your own set up (see <code>alexa/README.md</code> in the project), you can say
“Alexa, set the desk to Stand”. <b>Settings → Alexa</b> turns the link off and on. After changing presets,
say “Alexa, discover devices”.</p>
<h3>Troubleshooting</h3>
<ul>
<li>Only one device can be connected to the desk at a time. Close the desk's phone app if it won't connect.</li>
<li>To switch to a different desk, use <b>Settings → Change desk</b>.</li>
<li>If the desk was paired with another computer, put it in pairing mode again and choose it here.</li>
<li>Connection details are logged to <code>%LOCALAPPDATA%\\desk-control\\desk-control.log</code>.</li>
</ul>
"""


class HelpDialog(MessageBoxBase):
    def __init__(self, parent):
        super().__init__(parent)
        self.viewLayout.addWidget(SubtitleLabel("How to use Desk Control", self))
        area = SmoothScrollArea(self)
        area.setWidgetResizable(True)
        area.setStyleSheet("QScrollArea { border: none; background: transparent; }")
        text = BodyLabel(HELP_HTML, self)
        text.setTextFormat(Qt.RichText)
        text.setWordWrap(True)
        text.setStyleSheet("background: transparent;")
        area.setWidget(text)
        area.setMinimumSize(320, 380)
        self.viewLayout.addWidget(area)
        self.yesButton.setText("Close")
        self.cancelButton.hide()


class PresetCard(CardWidget):
    def __init__(self, name: str, height_cm: float, on_move, on_delete, parent=None):
        super().__init__(parent)
        self.setFixedHeight(56)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 8, 8, 8)
        layout.setSpacing(12)
        icon = IconWidget(FIF.PIN, self)
        icon.setFixedSize(16, 16)
        layout.addWidget(icon)
        layout.addWidget(StrongBodyLabel(name, self))
        layout.addStretch(1)
        layout.addWidget(BodyLabel(f"{height_cm:.1f} cm", self))
        delete = TransparentToolButton(FIF.DELETE, self)
        delete.setToolTip("Delete preset")
        delete.setAccessibleName(f"Delete {name}")
        delete.clicked.connect(on_delete)
        layout.addWidget(delete)
        self.clicked.connect(on_move)
        self.setToolTip(f"Move to {name}")


def hold_button(icon, text, parent) -> PushButton:
    """A tall button that moves the desk while held down, like the handset."""
    button = PushButton(icon, text, parent)
    button.setMinimumHeight(48)
    button.setToolTip("Hold to move")
    return button


class DeskWindow(FluentWidget):
    def __init__(self):
        super().__init__()
        self.settings = load_settings()
        self.desk: LinakDesk | None = None
        self._run_task: asyncio.Task | None = None
        self._quitting = False
        self.closed = asyncio.Event()
        self.control = ipc.ControlServer(self.handle_request)
        self.alexa: alexa_link.AlexaLink | None = None

        self.setWindowTitle("Desk Control")
        self.setWindowIcon(window_icon())
        self.resize(400, 640)
        self.setMinimumSize(360, 520)

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 48, 24, 20)
        root.setSpacing(12)

        # Header: connection status + calibration
        header = QHBoxLayout()
        self.status_icon = IconWidget(FIF.BLUETOOTH, self)
        self.status_icon.setFixedSize(16, 16)
        self.status_label = CaptionLabel("Starting…", self)
        header.addWidget(self.status_icon)
        header.addWidget(self.status_label, 1)
        self.choose_button = TransparentPushButton(FIF.BLUETOOTH, "Choose desk", self)
        self.choose_button.clicked.connect(lambda: spawn(self.choose_desk(), self.show_error))
        self.choose_button.hide()
        header.addWidget(self.choose_button)
        help_button = TransparentToolButton(FIF.HELP, self)
        help_button.setToolTip("Help")
        help_button.setAccessibleName("Help")
        help_button.clicked.connect(self.show_help)
        header.addWidget(help_button)
        self.settings_button = TransparentToolButton(FIF.SETTING, self)
        self.settings_button.setToolTip("Settings")
        self.settings_button.setAccessibleName("Settings")
        self.settings_button.clicked.connect(self.show_settings_menu)
        header.addWidget(self.settings_button)
        root.addLayout(header)

        # Current height
        height_row = QHBoxLayout()
        height_row.addStretch(1)
        self.height_label = DisplayLabel("--", self)
        height_row.addWidget(self.height_label, 0, Qt.AlignBottom)
        unit = SubtitleLabel("cm", self)
        unit.setContentsMargins(0, 0, 0, 12)
        height_row.addWidget(unit, 0, Qt.AlignBottom)
        height_row.addStretch(1)
        root.addLayout(height_row)
        self.motion_label = CaptionLabel("", self)
        self.motion_label.setAlignment(Qt.AlignCenter)
        root.addWidget(self.motion_label)

        # Manual controls
        manual = QHBoxLayout()
        manual.setSpacing(8)
        self.down_button = hold_button(FIF.DOWN, "Down", self)
        self.up_button = hold_button(FIF.UP, "Up", self)
        for button, direction in ((self.down_button, -1), (self.up_button, 1)):
            button.pressed.connect(lambda d=direction: self.hold(d))
            button.released.connect(self.stop)
            manual.addWidget(button)
        root.addLayout(manual)

        goto = QHBoxLayout()
        goto.setSpacing(8)
        self.target_box = DoubleSpinBox(self)
        self.target_box.setRange(30, 200)
        self.target_box.setDecimals(1)
        self.target_box.setSingleStep(0.5)
        self.target_box.setSuffix(" cm")
        self.target_box.setValue(100)
        goto.addWidget(self.target_box, 1)
        self.goto_button = PushButton(FIF.SEND, "Go", self)
        self.goto_button.clicked.connect(lambda: self.move_to_cm(self.target_box.value()))
        goto.addWidget(self.goto_button)
        self.stop_button = PrimaryPushButton(FIF.PAUSE, "Stop", self)
        self.stop_button.setToolTip("Stop (Esc)")
        self.stop_button.clicked.connect(self.stop)
        goto.addWidget(self.stop_button)
        root.addLayout(goto)

        # Presets
        presets_header = QHBoxLayout()
        presets_header.setContentsMargins(0, 12, 0, 0)
        presets_header.addWidget(SubtitleLabel("Presets", self), 1)
        self.save_button = PushButton(FIF.ADD, "Save current", self)
        self.save_button.clicked.connect(self.add_preset)
        presets_header.addWidget(self.save_button)
        root.addLayout(presets_header)

        self.presets_area = SmoothScrollArea(self)
        self.presets_area.setWidgetResizable(True)
        self.presets_area.setStyleSheet("QScrollArea { border: none; background: transparent; }")
        self.presets_widget = QWidget()
        self.presets_widget.setStyleSheet("background: transparent;")
        self.presets_layout = QVBoxLayout(self.presets_widget)
        self.presets_layout.setContentsMargins(0, 0, 0, 0)
        self.presets_layout.setSpacing(6)
        self.presets_area.setWidget(self.presets_widget)
        root.addWidget(self.presets_area, 1)

        QShortcut(QKeySequence(Qt.Key_Escape), self, activated=self.stop)
        QShortcut(QKeySequence(Qt.Key_Space), self, activated=self.stop)

        self.tray = QSystemTrayIcon(tray_icon(light_taskbar()), self)
        self.tray.setToolTip("Desk Control")
        self.tray.activated.connect(self.on_tray_activated)
        self.tray_menu = SystemTrayMenu(parent=self)
        self.tray.setContextMenu(self.tray_menu)
        self.tray.show()

        self.render_presets()
        self.set_controls_enabled(False)
        if self.settings["alexa"]:
            self.start_alexa()
        QTimer.singleShot(0, lambda: spawn(self.connect_desk(), self.show_error))

    # --- unit conversion -------------------------------------------------
    def to_cm(self, position: int) -> float:
        return (self.settings["offset_mm"] + position / 10) / 10

    def to_position(self, cm: float) -> int:
        return round((cm * 10 - self.settings["offset_mm"]) * 10)

    def current_cm(self) -> float | None:
        if self.desk and self.desk.position is not None:
            return self.to_cm(self.desk.position)
        return None

    # --- desk wiring -----------------------------------------------------
    async def connect_desk(self) -> None:
        if self.settings.get("address"):
            self.start_desk(self.settings["address"])
        else:
            self.on_state("nodesk", "")
            await self.choose_desk()

    def start_desk(self, address: str) -> None:
        self.desk = LinakDesk(address)
        self.desk.on_position = self.on_position
        self.desk.on_state = self.on_state
        self._run_task = spawn(self.desk.run(), self.show_error)

    async def stop_desk(self) -> None:
        if self._run_task:
            self._run_task.cancel()
            await asyncio.wait({self._run_task}, timeout=5)
        self._run_task = None
        self.desk = None
        self.height_label.setText("--")
        self.on_state("nodesk", "")

    async def choose_desk(self) -> None:
        previous = self.settings.get("address")
        # The desk accepts one connection at a time, so let go of it while pairing.
        await self.stop_desk()
        self.show_window()
        dialog = DeskPickerDialog(self)
        if await run_dialog(dialog) and dialog.chosen:
            self.settings["address"] = dialog.chosen["address"]
            self.settings["desk_name"] = dialog.chosen["name"]
            save_settings(self.settings)
            self.start_desk(dialog.chosen["address"])
        elif previous:
            self.start_desk(previous)

    def show_settings_menu(self) -> None:
        menu = RoundMenu(parent=self)
        menu.addAction(Action(FIF.BLUETOOTH, "Change desk…", menu,
                              triggered=lambda: spawn(self.choose_desk(), self.show_error)))
        calibrate = Action(FIF.EDIT, "Calibrate height…", menu, triggered=self.calibrate)
        calibrate.setEnabled(bool(self.desk and self.desk.connected))
        menu.addAction(calibrate)
        if alexa_link.configured():
            alexa = Action(FIF.MICROPHONE, "Alexa", menu, checkable=True)
            alexa.setChecked(bool(self.settings["alexa"]))
            alexa.toggled.connect(self.set_alexa)
            menu.addAction(alexa)
        menu.addSeparator()
        menu.addAction(Action(FIF.HELP, "Help", menu, triggered=self.show_help))
        button = self.settings_button
        menu.exec(button.mapToGlobal(QPoint(button.width() - menu.sizeHint().width(), button.height())))

    def start_alexa(self) -> None:
        if not alexa_link.configured():
            return
        try:
            self.alexa = alexa_link.AlexaLink(self.handle_request)
            self.alexa.start()
        except Exception as e:
            logging.exception("alexa: couldn't start")
            self.alexa = None
            self.show_error(e)
            return
        self.report_alexa()

    def set_alexa(self, enabled: bool) -> None:
        self.settings["alexa"] = enabled
        save_settings(self.settings)
        if enabled:
            self.start_alexa()
        elif self.alexa:
            self.alexa.stop()
            self.alexa = None

    def report_alexa(self) -> None:
        if not self.alexa:
            return
        self.alexa.report({
            "desk": self.desk.state if self.desk else "nodesk",
            "height_cm": None if self.current_cm() is None else round(self.current_cm(), 1),
            "min_cm": round(self.to_cm(0), 1),
            "max_cm": round(self.to_cm(MAX_POSITION), 1),
            "presets": [{"name": p["name"], "height_cm": p["height_cm"]} for p in self.settings["presets"]],
        })

    def show_help(self) -> None:
        self.show_window()
        HelpDialog(self).open()

    def on_state(self, state: str, detail: str) -> None:
        desk_name = self.settings.get("desk_name") or "desk"
        text = {
            "nodesk": "No desk selected",
            "connecting": f"Connecting to {desk_name}…",
            "connected": f"Connected to {desk_name}",
            "disconnected": "Disconnected",
        }[state]
        self.choose_button.setVisible(state == "nodesk")
        self.status_label.setText(text)
        self.status_label.setToolTip(detail)
        logging.info("%s %s", state, detail)
        self.status_icon.setIcon(FIF.ACCEPT if state == "connected" else FIF.BLUETOOTH)
        self.set_controls_enabled(state == "connected")
        if state != "connected":
            self.motion_label.setText("")
            self.tray.setToolTip(f"Desk Control · {text}")
        self.render_tray_menu()
        self.report_alexa()

    def on_position(self, position: int, speed: int) -> None:
        if self.height_label.text() == "--":
            self.target_box.setValue(self.to_cm(position))
        self.height_label.setText(f"{self.to_cm(position):.1f}")
        self.tray.setToolTip(f"Desk Control · {self.to_cm(position):.1f} cm")
        if speed > 0:
            self.motion_label.setText("Moving up…")
        elif speed < 0:
            self.motion_label.setText("Moving down…")
        else:
            self.motion_label.setText("")
            self.report_alexa()

    def set_controls_enabled(self, enabled: bool) -> None:
        for w in (self.up_button, self.down_button, self.goto_button, self.stop_button,
                  self.save_button, self.presets_widget):
            w.setEnabled(enabled)

    def show_error(self, error: Exception) -> None:
        InfoBar.error("Desk error", str(error) or type(error).__name__, parent=self,
                      position=InfoBarPosition.BOTTOM, duration=5000)

    # --- actions ---------------------------------------------------------
    def hold(self, direction: int) -> None:
        logging.info("ui: hold %+d pressed", direction)
        if self.desk and self.desk.connected:
            spawn(self.desk.hold(direction), self.show_error)

    def stop(self) -> None:
        logging.info("ui: stop")
        if self.desk and self.desk.connected:
            spawn(self.desk.stop(), self.show_error)

    def move_to_cm(self, cm: float) -> None:
        if not (self.desk and self.desk.connected):
            return
        self.target_box.setValue(cm)
        spawn(self.desk.move_to(self.to_position(cm)), self.show_error)

    def add_preset(self) -> None:
        current = self.current_cm()
        if current is None:
            return
        dialog = HeightDialog("Save preset", current, self, name="")
        if dialog.exec():
            self.settings["presets"].append({
                "name": dialog.name_edit.text().strip(),
                "height_cm": round(dialog.height_box.value(), 1),
            })
            self.settings["presets"].sort(key=lambda p: p["height_cm"])
            save_settings(self.settings)
            self.render_presets()

    def delete_preset(self, index: int) -> None:
        del self.settings["presets"][index]
        save_settings(self.settings)
        self.render_presets()

    def calibrate(self) -> None:
        current = self.current_cm()
        if current is None:
            return
        dialog = HeightDialog(
            "Calibrate height", current, self,
            hint="Measure from the floor to the top of the desk and enter it here. "
                 "The desk only reports its height above its lowest position.",
        )
        if dialog.exec():
            self.settings["offset_mm"] = dialog.height_box.value() * 10 - self.desk.position / 10
            save_settings(self.settings)
            self.on_position(self.desk.position, self.desk.speed)
            self.render_presets()

    def render_presets(self) -> None:
        while self.presets_layout.count():
            item = self.presets_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        presets = self.settings["presets"]
        if not presets:
            empty = CaptionLabel("No presets yet. Move the desk, then choose Save current.", self.presets_widget)
            empty.setWordWrap(True)
            self.presets_layout.addWidget(empty)
        for i, preset in enumerate(presets):
            self.presets_layout.addWidget(PresetCard(
                preset["name"], preset["height_cm"],
                on_move=lambda h=preset["height_cm"]: self.move_to_cm(h),
                on_delete=lambda i=i: self.delete_preset(i),
                parent=self.presets_widget,
            ))
        self.presets_layout.addStretch(1)
        self.render_tray_menu()
        self.report_alexa()

    def render_tray_menu(self) -> None:
        menu = self.tray_menu
        menu.clear()
        connected = bool(self.desk and self.desk.connected)
        for preset in self.settings["presets"]:
            action = Action(FIF.PIN, f"{preset['name']}  ·  {preset['height_cm']:.1f} cm", menu,
                            triggered=lambda _=False, h=preset["height_cm"]: self.move_to_cm(h))
            action.setEnabled(connected)
            menu.addAction(action)
        if self.settings["presets"]:
            menu.addSeparator()
        stop = Action(FIF.PAUSE, "Stop", menu, triggered=self.stop)
        stop.setEnabled(connected)
        menu.addAction(stop)
        menu.addAction(Action(FIF.HOME, "Open Desk Control", menu, triggered=self.show_window))
        menu.addSeparator()
        menu.addAction(Action(FIF.CLOSE, "Quit", menu, triggered=self.quit))

    def on_tray_activated(self, reason) -> None:
        if reason == QSystemTrayIcon.Trigger:
            self.show_window()

    def show_window(self) -> None:
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def quit(self) -> None:
        self._quitting = True
        self.tray.hide()
        self.close()

    # --- control channel (widget, second launch) -------------------------
    def handle_request(self, request: dict) -> dict:
        cmd = request.get("cmd")
        if cmd == "state":
            return {
                "ok": True,
                "state": self.desk.state if self.desk else "nodesk",
                "height_cm": None if self.current_cm() is None else round(self.current_cm(), 1),
                "speed": self.desk.speed if self.desk else 0,
                "presets": [{"name": p["name"], "height_cm": p["height_cm"]} for p in self.settings["presets"]],
            }
        if cmd == "show":
            self.show_window()
            return {"ok": True}
        if cmd == "quit":
            QTimer.singleShot(0, self.quit)
            return {"ok": True}
        if not (self.desk and self.desk.connected):
            return {"ok": False, "error": "Desk is not connected"}
        if cmd == "stop":
            self.stop()
        elif cmd == "preset":
            self.move_to_cm(self.settings["presets"][int(request["index"])]["height_cm"])
        elif cmd == "move":
            self.move_to_cm(float(request["height_cm"]))
        else:
            return {"ok": False, "error": f"Unknown command {cmd!r}"}
        return {"ok": True}

    def closeEvent(self, event) -> None:
        if not self._quitting:
            event.ignore()
            self.hide()
            if not self.settings.get("tray_hint_shown"):
                self.tray.showMessage("Desk Control is still running",
                                      "It stays connected in the tray for the widget. Use Quit in the tray menu to exit.")
                self.settings["tray_hint_shown"] = True
                save_settings(self.settings)
            return
        self.closed.set()
        super().closeEvent(event)

    async def shutdown(self) -> None:
        """Release the desk's connection; it only accepts one at a time."""
        if self.alexa:
            self.alexa.stop()
        if self._run_task:
            self._run_task.cancel()
            await asyncio.wait({self._run_task}, timeout=5)


def light_taskbar() -> bool:
    if sys.platform != "win32":
        return False
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as key:
            return bool(winreg.QueryValueEx(key, "SystemUsesLightTheme")[0])
    except OSError:
        return False


async def main(background: bool) -> None:
    window = DeskWindow()
    if not window.control.listen():
        logging.warning("control pipe unavailable: %s", window.control.server.errorString())
    if not background:
        window.show()
    await window.closed.wait()
    await window.shutdown()


if __name__ == "__main__":
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(message)s",
        handlers=[logging.handlers.RotatingFileHandler(LOG_PATH, maxBytes=1_000_000, backupCount=1)],
    )
    app = QApplication(sys.argv)
    background = "--background" in sys.argv
    # Only one instance may hold the desk's connection; a second launch just shows the first.
    if ipc.send({"cmd": "state" if background else "show"}) is not None:
        sys.exit(0)
    # main() quits once the desk connection is released, not when the window closes.
    app.setQuitOnLastWindowClosed(False)
    setTheme(Theme.AUTO)
    if sys.platform == "win32":
        setThemeColor(getSystemAccentColor())
    try:
        qasync.run(main(background))
    except asyncio.CancelledError:
        pass
    # Bluetooth worker threads can keep the process alive after the window closes.
    os._exit(0)
