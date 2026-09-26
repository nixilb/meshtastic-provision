"""Qt window of meshtastic-provision (PySide6). Left: the node (port,
Detect, board, version), the actions (Flash, Configure only, Check),
progress and log. Right: the settings form, applied to the node by every
action. The settings are kept in `provision.DEFAULT_PROFILE`, written
before each action and when the window closes; the file never shows in
the window.

The work runs on a worker thread and reports through Qt signals into the
log pane; the buttons are disabled meanwhile.

    uv run provision_gui.py
"""

from __future__ import annotations

import sys
import threading
import time
from typing import Callable

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QFontDatabase, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

import provision
from profile_form import ProfileForm
from provision import FlashParams, Progress, ProvisionError, RunParams

INTRO = (
    "Prepare a Meshtastic node plugged in over USB. Close meshtastic-desktop first: it holds the port. "
    "Detect asks the node which board it is (a blank board cannot answer: choose it by hand) and reads "
    "the chip. Flash erases the whole flash, installs the chosen firmware, then applies the settings on "
    "the right; the node restarts with a new private key, which other nodes will have to learn again. "
    "Configure only applies the settings to the node as it is; Check just reports the ones that differ."
)

BACKUP_DIR = provision.DEFAULT_PROFILE.parent / "backups"


class Events(QObject):
    """Signals the worker thread emits; Qt delivers them on the GUI thread."""

    log = Signal(str)
    error = Signal(str)
    ok = Signal(str)
    bar = Signal(int, int, str)
    idle = Signal()
    lists = Signal(object)
    node = Signal(object)
    chip = Signal(object)
    ask = Signal(str)


class Window(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Meshtastic node provisioning")
        self.resize(1240, 760)
        self.events = Events()
        self.worker: threading.Thread | None = None
        self.boards: list[provision.Board] = []
        self.chip: provision.ChipInfo | None = None
        self.identity: provision.NodeIdentity | None = None
        self._answer: dict[str, bool] = {}
        self._answered = threading.Event()
        self._build()
        self._connect()
        self._refresh_ports()
        self._start(self._load_lists, busy=False)
        # Watch the USB serial ports: a node plugged in is detected by
        # itself, an unplugged one clears what was read.
        self._known_ports = {p.device for p in provision.serial_ports()}
        self._watch = QTimer(self)
        self._watch.timeout.connect(self._watch_ports)
        self._watch.start(1000)
        if self._known_ports:
            QTimer.singleShot(500, self._detect)

    # -- layout ---------------------------------------------------------

    def _build(self) -> None:
        splitter = QSplitter(Qt.Orientation.Horizontal)
        self.setCentralWidget(splitter)

        left = QWidget()
        column = QVBoxLayout(left)
        column.setContentsMargins(12, 12, 8, 12)
        intro = QLabel(INTRO)
        intro.setWordWrap(True)
        column.addWidget(intro)

        device = QGroupBox("Node")
        form = QFormLayout(device)
        port_row = QHBoxLayout()
        self.port = QComboBox()
        port_row.addWidget(self.port, 1)
        refresh = QPushButton("Refresh")
        refresh.clicked.connect(self._refresh_ports)
        port_row.addWidget(refresh)
        self.detect_button = QPushButton("Detect")
        self.detect_button.clicked.connect(self._detect)
        port_row.addWidget(self.detect_button)
        form.addRow("Port", port_row)
        self.node_label = QLabel("not asked yet (press Detect)")
        self.node_label.setWordWrap(True)
        form.addRow("Node", self.node_label)
        self.chip_label = QLabel("not read yet")
        form.addRow("Chip", self.chip_label)
        self.board = QComboBox()
        self.board.setPlaceholderText("loading the board list...")
        form.addRow("Board", self.board)
        self.version = QComboBox()
        self.version.setPlaceholderText("loading the versions...")
        form.addRow("Version", self.version)
        self.backup = QCheckBox("Back up the node's current settings before erasing (needs a working node)")
        self.backup.setChecked(True)
        form.addRow("", self.backup)
        column.addWidget(device)

        buttons = QHBoxLayout()
        self.flash_button = QPushButton("Flash")
        self.flash_button.clicked.connect(self._flash)
        self.configure_button = QPushButton("Configure only")
        self.configure_button.clicked.connect(lambda: self._configure(check=False))
        self.check_button = QPushButton("Check")
        self.check_button.clicked.connect(lambda: self._configure(check=True))
        for button in (self.flash_button, self.configure_button, self.check_button):
            buttons.addWidget(button)
        buttons.addStretch(1)
        column.addLayout(buttons)

        self.bar = QProgressBar()
        self.bar.setRange(0, 1000)
        self.bar.setTextVisible(False)
        column.addWidget(self.bar)
        self.bar_label = QLabel("")
        column.addWidget(self.bar_label)

        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        self.log.setMaximumBlockCount(5000)
        column.addWidget(self.log, 1)
        splitter.addWidget(left)

        right = QGroupBox("Settings")
        right_layout = QVBoxLayout(right)
        try:
            self.form = ProfileForm(provision.DEFAULT_PROFILE)
        except ProvisionError as err:
            QMessageBox.critical(self, "Settings", str(err))
            raise SystemExit(1) from err
        right_layout.addWidget(self.form)
        splitter.addWidget(right)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)

    def _connect(self) -> None:
        self.events.log.connect(lambda text: self._append(text))
        self.events.error.connect(lambda text: self._append(f"error: {text}", "error"))
        self.events.ok.connect(lambda text: self._append(text, "ok"))
        self.events.bar.connect(self._set_bar)
        self.events.idle.connect(lambda: self._set_busy(False))
        self.events.lists.connect(self._set_lists)
        self.events.node.connect(self._set_node)
        self.events.chip.connect(self._set_chip)
        self.events.ask.connect(self._ask)

    # -- background work --------------------------------------------------

    def _start(self, target: Callable[[], None], busy: bool = True) -> None:
        """Run `target()` on a worker thread; with `busy`, disable the
        buttons until it returns."""
        if self.worker and self.worker.is_alive():
            QMessageBox.information(self, "Busy", "A step is already running.")
            return
        if busy:
            self._set_busy(True)
        self.worker = threading.Thread(target=self._guard, args=(target, busy), daemon=True)
        self.worker.start()

    def _guard(self, target: Callable[[], None], busy: bool) -> None:
        try:
            target()
        except ProvisionError as err:
            self.events.error.emit(str(err))
        except SystemExit as err:  # the meshtastic library exits on some failures
            self.events.error.emit(f"the meshtastic library gave up: {err}")
        except Exception as err:  # noqa: BLE001 - shown to the user, never swallowed
            self.events.error.emit(f"{type(err).__name__}: {err}")
        finally:
            self.events.bar.emit(0, 0, "")
            if busy:
                self.events.idle.emit()

    def _progress(self) -> Progress:
        return Progress(log=self.events.log.emit, bar=self.events.bar.emit)

    # -- slots ------------------------------------------------------------

    def _append(self, text: str, tag: str | None = None) -> None:
        fmt = QTextCharFormat()
        if tag == "error":
            fmt.setForeground(QColor("#c62828"))
        elif tag == "ok":
            fmt.setForeground(QColor("#2e7d32"))
            fmt.setFontWeight(QFont.Weight.Bold)
        cursor = self.log.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        cursor.insertText(text + "\n", fmt)
        self.log.setTextCursor(cursor)
        self.log.ensureCursorVisible()

    def _set_bar(self, done: int, total: int, label: str) -> None:
        self.bar.setValue(0 if total <= 0 else min(1000, done * 1000 // total))
        self.bar_label.setText(label if total > 0 else "")

    def _set_busy(self, busy: bool) -> None:
        for button in (self.flash_button, self.configure_button, self.check_button, self.detect_button):
            button.setEnabled(not busy)

    def _set_lists(self, payload: object) -> None:
        self.boards, versions = payload  # type: ignore[misc]
        self._fill_boards()
        self.version.clear()
        self.version.setPlaceholderText("choose the firmware version to install")
        for release in versions:
            self.version.addItem(str(release), release.version)
        # No default: the version to install is an explicit choice.
        self.version.setCurrentIndex(-1)

    def _set_node(self, identity: object) -> None:
        self.identity = identity  # type: ignore[assignment]
        self.node_label.setText(str(identity) if identity else "nothing answers (blank board?)")
        self._fill_boards()

    def _set_chip(self, info: object) -> None:
        self.chip = info  # type: ignore[assignment]
        self.chip_label.setText(str(info))
        self._fill_boards()

    def _fill_boards(self) -> None:
        """The board list, reduced to the detected chip's family. Nothing is
        selected until the node reports its board or the user chooses one:
        a preselected board would look like a detection."""
        boards = self.boards
        if self.chip:
            boards = [b for b in boards if b.mcu == self.chip.chip]
        self.board.clear()
        self.board.setPlaceholderText("press Detect, or choose the board by hand")
        for board in boards:
            self.board.addItem(str(board), board.platformio_target)
        wanted = self.identity.pio_env if self.identity else ""
        self.board.setCurrentIndex(next((i for i, b in enumerate(boards) if b.platformio_target == wanted), -1))

    def _ask(self, summary: str) -> None:
        """The erase confirmation, on the GUI thread; the worker waits."""
        answer = QMessageBox.warning(
            self,
            "Erase and install",
            summary,
            QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        self._answer["yes"] = answer == QMessageBox.StandardButton.Ok
        self._answered.set()

    # -- actions ----------------------------------------------------------

    def _refresh_ports(self) -> None:
        current = self.port.currentData()
        self.port.clear()
        for port in provision.serial_ports():
            self.port.addItem(str(port), port.device)
        index = self.port.findData(current)
        if index >= 0:
            self.port.setCurrentIndex(index)

    def _watch_ports(self) -> None:
        """Every second: react to a port appearing or disappearing."""
        ports = {p.device for p in provision.serial_ports()}
        added = ports - self._known_ports
        removed = self._known_ports - ports
        if not added and not removed:
            return
        self._known_ports = ports
        self._refresh_ports()
        for device in sorted(removed):
            self._append(f"{device} unplugged")
            if device == self.port.currentData() or self.port.count() == 0:
                self._set_node(None)
                self.chip = None
                self.chip_label.setText("not read yet")
                self.node_label.setText("not asked yet (plug the node in, or press Detect)")
        for device in sorted(added):
            self._append(f"{device} plugged in: detecting in 5 s")
            self.port.setCurrentIndex(self.port.findData(device))
            # Give the node time to boot before asking it who it is.
            QTimer.singleShot(5000, self._detect)

    def _selected_port(self) -> str:
        device = self.port.currentData()
        if not device:
            raise ProvisionError("no serial port selected: plug the node in and press Refresh")
        return device

    def _load_lists(self) -> None:
        boards = provision.boards()
        versions = provision.releases()
        self.events.lists.emit((boards, versions))

    def _detect(self) -> None:
        if self.worker and self.worker.is_alive():
            self._append("busy: press Detect once the current step is over")
            return

        def work() -> None:
            port = self._selected_port()
            progress = self._progress()
            identity = provision.probe_node(port, progress)
            self.events.node.emit(identity)
            info = provision.detect_chip(port, progress)
            self.events.chip.emit(info)

        self._start(work)

    def _save_settings(self) -> bool:
        """Write the form before an action; on an invalid entry, say which
        and do nothing."""
        try:
            self.form.save()
        except ProvisionError as err:
            QMessageBox.warning(self, "Settings", str(err))
            return False
        return True

    def _confirm(self, summary: str) -> bool:
        """Called on the worker: ask on the GUI thread, wait for the answer."""
        self._answered.clear()
        self.events.ask.emit(summary)
        self._answered.wait()
        return self._answer.get("yes", False)

    def _flash(self) -> None:
        if not self._save_settings():
            return
        board = self.board.currentData()  # None: the running node must say which it is
        version = self.version.currentData()
        backup = BACKUP_DIR / f"node-{time.strftime('%Y%m%d-%H%M%S')}.yaml" if self.backup.isChecked() else None

        def work() -> None:
            if not version:
                raise ProvisionError("choose a firmware version")
            params = RunParams(
                port=self._selected_port(),
                profile=provision.DEFAULT_PROFILE,
                flash=FlashParams(version=version, board=board, backup=backup),
                confirm=self._confirm,
            )
            result = provision.run(params, self._progress())
            if result.ok:
                self.events.ok.emit("done: the node is ready")
            else:
                self.events.error.emit("done, with settings still differing")

        self._start(work)

    def _configure(self, check: bool) -> None:
        if not self._save_settings():
            return

        def work() -> None:
            params = RunParams(port=self._selected_port(), profile=provision.DEFAULT_PROFILE, check_only=check)
            result = provision.run(params, self._progress())
            if check:
                (self.events.ok if result.ok else self.events.log).emit("check finished")
            elif result.ok:
                self.events.ok.emit("configuration finished")
            else:
                self.events.error.emit("settings still differ")

        self._start(work)

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt naming
        """Keep the settings on close; an invalid entry asks before losing it."""
        try:
            self.form.save()
        except ProvisionError as err:
            answer = QMessageBox.question(self, "Settings", f"{err}\n\nClose anyway and lose the change?")
            if answer != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
        event.accept()


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("meshtastic-provision")
    window = Window()
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
