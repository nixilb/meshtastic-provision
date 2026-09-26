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
    QStackedWidget,
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
import theme
from profile_form import ProfileForm
from provision import FlashParams, Progress, ProvisionError, RunParams

INTRO = (
    "Prepare a Meshtastic node plugged in over USB. "
    "A node plugged in is detected by itself: it says which board it is (a blank board cannot: choose it "
    "by hand) and its chip is read. 'Flash & configure' erases the whole flash, installs the chosen firmware, then applies the settings on "
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
        self.resize(1400, 800)
        self.events = Events()
        self.worker: threading.Thread | None = None
        self.boards: list[provision.Board] = []
        self.chip: provision.ChipInfo | None = None
        self.identity: provision.NodeIdentity | None = None
        self._answer: dict[str, bool] = {}
        self._answered = threading.Event()
        self._build()
        self._update_rows()
        theme.install(self)
        self._connect()
        self._refresh_ports()
        self._start(self._load_lists, busy=False)
        # Watch the USB serial ports: a node plugged in is detected by
        # itself, an unplugged one clears what was read.
        self._known_ports = {p.device for p in provision.serial_ports()}
        self._watch = QTimer(self)
        self._watch.timeout.connect(self._watch_ports)
        self._watch.start(1000)
        if self._watch_app():
            self._known_ports = set()  # detected once the app is closed
        elif self._known_ports:
            QTimer.singleShot(500, lambda: self._port_present(self.port.currentData(), delay_ms=0))

    # -- layout ---------------------------------------------------------

    def _build(self) -> None:
        # Two pages: the tool, or, while meshtastic-desktop runs, only an
        # alert asking to quit it (it holds the serial port and would
        # overwrite the settings written for it).
        self.pages = QStackedWidget()
        self.setCentralWidget(self.pages)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        self.pages.addWidget(splitter)
        alert = QLabel(
            "meshtastic-desktop is running.\n\n"
            "Quit it to prepare a node: it holds the node's USB port.\n"
            "This window goes on by itself once it is closed."
        )
        alert.setAlignment(Qt.AlignmentFlag.AlignCenter)
        alert.setWordWrap(True)
        font = alert.font()
        font.setPointSizeF(font.pointSizeF() * 1.4)
        alert.setFont(font)
        self.pages.addWidget(alert)

        left = QWidget()
        column = QVBoxLayout(left)
        column.setContentsMargins(12, 12, 8, 12)
        intro = QLabel(INTRO)
        intro.setWordWrap(True)
        column.addWidget(intro)

        # Nothing plugged in: a single line says what to do.
        self.plug_hint = QLabel("Plug a Meshtastic node, or a new board, in over USB.")
        # 2026-09-26: no style sheet here: a styled widget keeps the palette
        # it was polished with, so the text stayed dark on the dark theme.
        font = self.plug_hint.font()
        font.setPointSizeF(font.pointSizeF() * 1.25)
        self.plug_hint.setFont(font)
        self.plug_hint.setContentsMargins(4, 12, 4, 12)
        column.addWidget(self.plug_hint)

        # One frame for the plugged node: what it is, what to install on it,
        # and the actions. Shown only while a node is plugged in; each row
        # only when it has something to say (see _update_rows).
        self.device_box = QGroupBox("Plugged node")
        self.device_form = form = QFormLayout(self.device_box)
        self.port = QComboBox()
        # A port picked by hand (several nodes plugged in) is detected too.
        self.port.activated.connect(lambda _index: self._port_present(self.port.currentData(), delay_ms=0))
        form.addRow("Port", self.port)
        self.node_label = QLabel("")
        self.node_label.setWordWrap(True)
        form.addRow("Node", self.node_label)
        # The chip is still read (it narrows the board list and guards the
        # flash) but not shown: it means nothing to most users.
        self.chip_label = QLabel("")
        self.board = QComboBox()
        self.board.setPlaceholderText("choose the board's model")
        self.board.currentIndexChanged.connect(lambda _index: (self._pick_version(), self._update_buttons()))
        form.addRow("Board", self.board)
        self.version = QComboBox()
        self.version.setPlaceholderText("set once a board is chosen")
        self.version.currentIndexChanged.connect(lambda _index: self._update_buttons())
        form.addRow("Firmware", self.version)
        self.backup = QCheckBox("Back up the node's current settings before erasing")
        self.backup.setChecked(True)
        form.addRow("", self.backup)

        # The actions, each shown only when it can run (see _update_buttons).
        buttons = QHBoxLayout()
        self.flash_button = QPushButton("Flash && configure")  # "&&" shows one "&" (a single one marks a shortcut)
        self.flash_button.clicked.connect(self._flash)
        self.configure_button = QPushButton("Configure only")
        self.configure_button.clicked.connect(lambda: self._configure(check=False))
        self.check_button = QPushButton("Check")
        self.check_button.clicked.connect(lambda: self._configure(check=True))
        for button in (self.flash_button, self.configure_button, self.check_button):
            buttons.addWidget(button)
        buttons.addStretch(1)
        form.addRow(buttons)
        column.addWidget(self.device_box)

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
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([640, 760])

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
        try:
            provision.LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
            with open(provision.LOG_PATH, "a", encoding="utf-8") as f:
                f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {text}\n")
        except OSError as err:
            # Shown once in the pane; the window works on without the file.
            if not getattr(self, "_log_file_failed", False):
                self._log_file_failed = True
                self.log.appendPlainText(f"cannot write {provision.LOG_PATH}: {err}")

    def _set_bar(self, done: int, total: int, label: str) -> None:
        self.bar.setValue(0 if total <= 0 else min(1000, done * 1000 // total))
        self.bar_label.setText(label if total > 0 else "")

    def _set_busy(self, busy: bool) -> None:
        self.busy = busy
        self._update_buttons()

    def _update_rows(self) -> None:
        """The frame while a node is plugged in, else the hint; inside it,
        the port only when there is a choice, the node line once it says
        something, the backup only for a node that answered."""
        plugged = self.port.count() > 0
        self.plug_hint.setVisible(not plugged)
        self.device_box.setVisible(plugged)
        self.device_form.setRowVisible(self.port, self.port.count() > 1)
        self.device_form.setRowVisible(self.node_label, bool(self.node_label.text()))
        self.device_form.setRowVisible(self.backup, self.identity is not None)
        self._update_buttons()

    def _update_buttons(self) -> None:
        """Show each action only when it can run: none while a step runs;
        Flash once a board and a version are chosen; Configure only and
        Check once a Meshtastic node has answered."""
        idle = not getattr(self, "busy", False)
        self.flash_button.setVisible(idle and self.board.currentIndex() >= 0 and self.version.currentIndex() >= 0)
        answered = idle and self.identity is not None
        self.configure_button.setVisible(answered)
        self.check_button.setVisible(answered)

    def _set_lists(self, payload: object) -> None:
        self.boards, versions = payload  # type: ignore[misc]
        self._fill_boards()
        self.versions = versions
        self.version.clear()
        self.version.setPlaceholderText("set once a board is chosen")
        for release in versions:
            self.version.addItem(str(release), release.version)
        self._pick_version()

    def _pick_version(self) -> None:
        """No version without a board; once a board is chosen (detected or
        by hand), the newest stable release, alphas left out."""
        if self.board.currentIndex() < 0:
            self.version.setCurrentIndex(-1)
            return
        stable = next((i for i, r in enumerate(getattr(self, "versions", [])) if not r.prerelease), -1)
        self.version.setCurrentIndex(stable)

    def _set_node(self, identity: object) -> None:
        self.identity = identity  # type: ignore[assignment]
        if identity is None:
            self.node_label.setText("No Meshtastic firmware on this board: choose its model below.")
        else:
            board = identity.pio_env or identity.hw_model or "unknown board"
            firmware = f", firmware {identity.firmware}" if identity.firmware else ""
            self.node_label.setText(f"{identity.long_name or identity.node_id} ({board}){firmware}")
        self._fill_boards()
        self._update_rows()

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
        # 2026-09-26: with a placeholder text, QComboBox no longer selects
        # its first item by itself; the port stayed unselected while
        # listed, so neither detection nor the actions found it.
        index = self.port.findData(current)
        self.port.setCurrentIndex(index if index >= 0 else (0 if self.port.count() else -1))
        self._update_rows()

    def _watch_app(self) -> bool:
        """Show the alert page while meshtastic-desktop runs. Returns
        whether it runs."""
        running = bool(provision.app_running())
        self.pages.setCurrentIndex(1 if running else 0)
        return running

    def _watch_ports(self) -> None:
        """Every second: react to meshtastic-desktop and to a port
        appearing or disappearing."""
        if self._watch_app():
            return
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
                self.identity = None
                self.chip = None
                self.node_label.setText("")
                self._fill_boards()
                self._update_rows()
        for device in sorted(added):
            self._append(f"{device} plugged in")
            self.port.setCurrentIndex(self.port.findData(device))
            # Give the node time to boot before asking it who it is.
            self._port_present(device, delay_ms=5000)

    def _port_present(self, device: str | None, delay_ms: int) -> None:
        """A port is there: once the node had time to boot, make sure it
        can be opened, then detect."""
        if not device:
            return
        self._append(f"detecting {device}" + (f" in {delay_ms // 1000} s" if delay_ms else ""))
        QTimer.singleShot(delay_ms, lambda: self._access_then_detect(device, tries=6))

    def _access_then_detect(self, device: str, tries: int) -> None:
        # 2026-09-26: the port shows up before udev has given the user its
        # ACL (uaccess), a moment later; checking at once asked for a
        # permission the user already had. Retry for a few seconds before
        # concluding it is missing.
        if device != self.port.currentData():
            return  # unplugged or another port chosen meanwhile
        try:
            provision.check_port_access(device)
        except provision.PortAccessError:
            if tries > 0:
                QTimer.singleShot(500, lambda: self._access_then_detect(device, tries - 1))
                return
            if not self._ensure_access(device):
                return
        self._detect()

    def _ensure_access(self, device: str) -> bool:
        """The port must be openable. When it is not, a udev rule granting
        USB serial ports to the logged-in user is offered (the system asks
        for the password) and applies at once. Should it not be enough (no
        systemd-logind), the port's group is offered, which needs a restart
        of the tool with the group active."""
        try:
            provision.check_port_access(device)
            return True
        except provision.PortAccessError as err:
            first = err
        answer = QMessageBox.question(
            self,
            "Serial port access",
            f"{device} cannot be opened by your user, so the node cannot be reached.\n\n"
            "Allow the user logged in at this desktop to use USB serial ports? The system "
            "asks for your password; nothing needs to be restarted.",
        )
        if answer != QMessageBox.StandardButton.Yes:
            self._append(f"error: {first}", "error")
            return False
        try:
            provision.install_udev_rule(graphical=True)
            provision.check_port_access(device)
            self._append("USB serial ports allowed for the logged-in user")
            return True
        except provision.PortAccessError:
            self._append("the udev rule did not open the port: falling back to the group")
        except ProvisionError as failure:
            QMessageBox.warning(self, "Serial port access", str(failure))
            return False
        if not first.granted:
            answer = QMessageBox.question(
                self,
                "Serial port access",
                f"{device} belongs to the group {first.group}, which your user is not in.\n\n"
                "Add you to that group now? The system asks for your password, then this tool "
                "restarts with the group active.",
            )
            if answer != QMessageBox.StandardButton.Yes:
                self._append(f"error: {first}", "error")
                return False
            try:
                provision.add_user_to_group(first.group, graphical=True)
            except ProvisionError as failure:
                QMessageBox.warning(self, "Serial port access", str(failure))
                return False
            self._append(f"you are now in the group {first.group}")
        self._append(f"restarting with the group {first.group} active")
        self._restart_with_group(first.group)
        return False

    def _restart_with_group(self, group: str) -> None:
        try:
            self.form.save()
        except ProvisionError as err:
            self._append(f"settings not saved: {err}", "error")
        self._watch.stop()
        try:
            provision.relaunch_with_group(group)  # replaces this process
        except (ProvisionError, OSError) as err:
            QMessageBox.warning(self, "Serial port access", f"{err}")
            self._watch.start(1000)

    def _selected_port(self) -> str:
        device = self.port.currentData()
        if not device:
            raise ProvisionError("no node plugged in: plug it in over USB")
        return device

    def _load_lists(self) -> None:
        boards = provision.boards()
        versions = provision.releases()
        self.events.lists.emit((boards, versions))

    def _detect(self) -> None:
        if self.worker and self.worker.is_alive():
            self._append("busy: the node will be detected once the current step is over")
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
        backup = BACKUP_DIR / f"node-{time.strftime('%Y%m%d-%H%M%S')}.yaml" if self.backup.isChecked() and self.identity is not None else None

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
    app.setOrganizationName("meshtastic-provision")
    window = Window()
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
