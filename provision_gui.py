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
from PySide6.QtGui import QColor, QFont, QFontDatabase, QPalette, QTextCharFormat, QTextCursor
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
    QToolButton,
    QVBoxLayout,
    QWidget,
)

import provision
import theme
from profile_form import ProfileForm
from provision import FlashParams, Progress, ProvisionError, RunParams

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
    step = Signal(str)


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
        buttons.addWidget(self.configure_button)
        buttons.addWidget(self.check_button)
        buttons.addStretch(1)
        buttons.addWidget(self.flash_button)  # the main action, on the right
        form.addRow(buttons)
        column.addWidget(self.device_box)

        # The run: its stages, a warning while the flash is written, the
        # progress bar and the outcome. Hidden until an action runs.
        self.steps_row = QHBoxLayout()
        self.step_labels: dict[str, QLabel] = {}
        column.addLayout(self.steps_row)
        self.banner = QLabel("Do not unplug the node: its firmware is being written.")
        banner_font = self.banner.font()
        banner_font.setBold(True)
        self.banner.setFont(banner_font)
        self.banner.setVisible(False)
        column.addWidget(self.banner)
        self.bar = QProgressBar()
        self.bar.setRange(0, 1000)
        self.bar.setTextVisible(False)
        self.bar.setVisible(False)
        column.addWidget(self.bar)
        self.bar_label = QLabel("")
        column.addWidget(self.bar_label)
        self.result_label = QLabel("")
        self.result_label.setWordWrap(True)
        result_font = self.result_label.font()
        result_font.setPointSizeF(result_font.pointSizeF() * 1.15)
        result_font.setBold(True)
        self.result_label.setFont(result_font)
        column.addWidget(self.result_label)
        self.run_steps: tuple[str, ...] = ()
        self.current_step: str | None = None

        # The technical log, folded by default.
        self.details = QToolButton()
        self.details.setText("Details")
        self.details.setCheckable(True)
        self.details.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.details.setArrowType(Qt.ArrowType.RightArrow)
        self.details.setAutoRaise(True)
        self.details.toggled.connect(self._show_details)
        column.addWidget(self.details)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        self.log.setMaximumBlockCount(5000)
        self.log.setVisible(False)
        column.addWidget(self.log, 1)
        # Keeps everything at the top while the log is folded.
        self.left_column = column
        column.addStretch(1)
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
        self.events.step.connect(self._enter_step)
        self.events.error.connect(self._run_failed)
        self.events.ok.connect(self._run_succeeded)

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
        return Progress(log=self.events.log.emit, bar=self.events.bar.emit, step=self.events.step.emit)

    # -- the run's stages ---------------------------------------------------

    STEP_NAMES = {
        provision.STEP_DOWNLOAD: "Download",
        provision.STEP_ERASE: "Erase",
        provision.STEP_WRITE: "Write",
        provision.STEP_RESTART: "Restart",
        provision.STEP_SETTINGS: "Settings",
        provision.STEP_VERIFY: "Check",
    }

    def _begin_run(self, steps: tuple[str, ...]) -> None:
        """Lay out the stages of the run about to start, all pending."""
        while self.steps_row.count():
            item = self.steps_row.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self.step_labels = {}
        for index, name in enumerate(steps):
            if index:
                arrow = QLabel("›")
                arrow.setForegroundRole(QPalette.ColorRole.PlaceholderText)
                self.steps_row.addWidget(arrow)
            label = QLabel()
            self.step_labels[name] = label
            self.steps_row.addWidget(label)
        if steps:
            self.steps_row.addStretch(1)
        self.run_steps = steps
        self.current_step = None
        self.result_label.setText("")
        for name in steps:
            self._paint_step(name, "pending")

    def _clear_run(self) -> None:
        """Forget the last run's stages and outcome (a new node)."""
        self._begin_run(())
        self.bar.setVisible(False)
        self.bar_label.setText("")

    def _paint_step(self, name: str, state: str) -> None:
        label = self.step_labels.get(name)
        if label is None:
            return
        mark = {"pending": "○", "active": "▶", "done": "✓", "failed": "✗"}[state]
        label.setText(f"{mark} {self.STEP_NAMES[name]}")
        font = label.font()
        font.setBold(state in ("active", "failed"))
        label.setFont(font)
        colour = {"done": "#43a047", "failed": "#e53935"}.get(state)
        palette = label.palette()
        if colour:
            palette.setColor(QPalette.ColorRole.WindowText, QColor(colour))
            label.setPalette(palette)
        else:
            label.setPalette(QApplication.palette())
            label.setForegroundRole(
                QPalette.ColorRole.PlaceholderText if state == "pending" else QPalette.ColorRole.WindowText
            )

    def _enter_step(self, name: str) -> None:
        if name not in self.step_labels:
            return
        for earlier in self.run_steps[: self.run_steps.index(name)]:
            self._paint_step(earlier, "done")
        self._paint_step(name, "active")
        self.current_step = name
        # Unplugging while the flash is erased or written leaves the board
        # without firmware (a new flash recovers it).
        self.banner.setVisible(name in (provision.STEP_ERASE, provision.STEP_WRITE))
        self._paint_banner()

    def _paint_banner(self) -> None:
        palette = self.banner.palette()
        palette.setColor(QPalette.ColorRole.Window, QColor("#b26a00"))
        palette.setColor(QPalette.ColorRole.WindowText, QColor("#ffffff"))
        self.banner.setPalette(palette)
        self.banner.setAutoFillBackground(True)
        self.banner.setContentsMargins(8, 6, 8, 6)

    def _run_failed(self, text: str) -> None:
        if not self.run_steps:
            return  # an error outside a run (detection): the log has it
        if self.current_step:
            self._paint_step(self.current_step, "failed")
        self.banner.setVisible(False)
        self._set_result(text, "#e53935")

    def _run_succeeded(self, text: str) -> None:
        if not self.run_steps:
            return
        for name in self.run_steps:
            self._paint_step(name, "done")
        self.banner.setVisible(False)
        self._set_result(text, "#43a047")

    def _set_result(self, text: str, colour: str) -> None:
        palette = self.result_label.palette()
        palette.setColor(QPalette.ColorRole.WindowText, QColor(colour))
        self.result_label.setPalette(palette)
        self.result_label.setText(text[:1].upper() + text[1:])

    def _show_details(self, shown: bool) -> None:
        self.details.setArrowType(Qt.ArrowType.DownArrow if shown else Qt.ArrowType.RightArrow)
        self.log.setVisible(shown)
        self.left_column.setStretch(self.left_column.count() - 1, 0 if shown else 1)

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
        """A position (total > 0), a wait of unknown length (total < 0,
        animated), or nothing (total 0)."""
        if total < 0:
            self.bar.setRange(0, 0)
        else:
            self.bar.setRange(0, 1000)
            self.bar.setValue(0 if total == 0 else min(1000, done * 1000 // total))
        self.bar.setVisible(total != 0)
        self.bar_label.setText(label if total != 0 else "")

    def _set_busy(self, busy: bool) -> None:
        """While a step runs, the buttons hide and what could be edited is
        greyed: changing it would not affect the run."""
        self.busy = busy
        for widget in (self.form, self.port, self.board, self.version, self.backup):
            widget.setEnabled(not busy)
        if not busy:
            self.banner.setVisible(False)
            self.run_steps = ()  # the strip stays shown; later errors are not the run's
        self._update_buttons()

    def _update_rows(self) -> None:
        """The frame while a node is plugged in, else the hint; inside it,
        the port only when there is a choice, the node line, board and
        firmware once detection has spoken, the backup only for a node that
        answered."""
        plugged = self.port.count() > 0
        self.plug_hint.setVisible(not plugged)
        self.device_box.setVisible(plugged)
        self.device_form.setRowVisible(self.port, self.port.count() > 1)
        detected = bool(self.node_label.text())  # the node answered, or said nothing
        self.device_form.setRowVisible(self.node_label, detected)
        # Board and firmware only once detection has spoken: before, the
        # board may still be filled in by the node itself.
        self.device_form.setRowVisible(self.board, detected)
        self.device_form.setRowVisible(self.version, detected)
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
                if not getattr(self, "busy", False):
                    self._clear_run()
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
        """Before an action: the action reads the saved settings, so unsaved
        changes are offered to be saved first (nothing is saved without the
        user's say). Returns whether to go on."""
        if not self.form.dirty:
            return True
        answer = QMessageBox.question(
            self,
            "Unsaved settings",
            "The settings on the right have changes that are not saved. The node gets the saved "
            "settings.\n\nSave the changes first?",
            QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Save,
        )
        return answer == QMessageBox.StandardButton.Save and self.form.save_clicked()

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
                self.events.ok.emit("the node is ready")
            else:
                self.events.error.emit("the firmware is installed but some settings differ: see Details")

        self._begin_run(provision.FLASH_STEPS)
        self._start(work)

    def _configure(self, check: bool) -> None:
        if not self._save_settings():
            return

        def work() -> None:
            params = RunParams(port=self._selected_port(), profile=provision.DEFAULT_PROFILE, check_only=check)
            result = provision.run(params, self._progress())
            if result.ok:
                self.events.ok.emit("the node matches the settings" if check else "the node is configured")
            else:
                self.events.error.emit(f"{len(result.remaining)} setting(s) differ from the saved ones: see Details")

        self._begin_run(provision.CHECK_STEPS if check else provision.CONFIGURE_STEPS)
        self._start(work)

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt naming
        """Unsaved settings: save, discard or stay."""
        if self.form.dirty:
            answer = QMessageBox.question(
                self,
                "Unsaved settings",
                "The settings have changes that are not saved.",
                QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Save,
            )
            if answer == QMessageBox.StandardButton.Cancel or (
                answer == QMessageBox.StandardButton.Save and not self.form.save_clicked()
            ):
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
