"""Tkinter window of meshtastic-provision. Left: the node (port, Detect,
board, version), the actions (Flash, Configure only, Check), progress and
log. Right: the settings form, applied to the node by every action. The
settings are kept in `provision.DEFAULT_PROFILE`, written before each
action and when the window closes; the file never shows in the window.

The work runs on a worker thread and reports into the log pane; the
buttons are disabled meanwhile. Tkinter comes with uv's Python 3.13, so
nothing else is needed.

    uv run provision_gui.py
"""

from __future__ import annotations

import queue
import threading
import time
import tkinter as tk
from tkinter import messagebox, scrolledtext, ttk

import provision
from profile_form import ProfileForm
from provision import FlashParams, Progress, ProvisionError, RunParams

INTRO = (
    "Prepare a Meshtastic node plugged in over USB. Close meshtastic-desktop first: it holds the port.\n"
    "Detect asks the node which board it is (a blank board cannot answer: choose it by hand) and reads "
    "the chip. Flash erases the whole flash, installs the chosen firmware, then applies the settings on "
    "the right; the node restarts with a new private key, which other nodes will have to learn again. "
    "Configure only applies the settings to the node as it is; Check just reports the ones that differ."
)

BACKUP_DIR = provision.DEFAULT_PROFILE.parent / "backups"


class App:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        root.title("Meshtastic node provisioning")
        root.minsize(1100, 640)
        self.events: queue.Queue[tuple[str, object]] = queue.Queue()
        self.worker: threading.Thread | None = None
        self.boards: list[provision.Board] = []
        self.chip: provision.ChipInfo | None = None
        self.identity: provision.NodeIdentity | None = None
        self._build()
        root.protocol("WM_DELETE_WINDOW", self._close)
        self.root.after(100, self._poll)
        self._refresh_ports()
        self._start(self._load_lists, busy=False)

    # -- layout ---------------------------------------------------------

    def _build(self) -> None:
        pad = {"padx": 8, "pady": 4}
        outer = ttk.Frame(self.root, padding=8)
        outer.pack(fill="both", expand=True)
        outer.columnconfigure(0, weight=3)
        outer.columnconfigure(1, weight=2)
        outer.rowconfigure(0, weight=1)

        left = ttk.Frame(outer)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        left.columnconfigure(1, weight=1)

        ttk.Label(left, text=INTRO, wraplength=560, justify="left").grid(row=0, column=0, columnspan=4, sticky="w", **pad)

        ttk.Label(left, text="Port").grid(row=1, column=0, sticky="w", **pad)
        self.port = ttk.Combobox(left, state="readonly")
        self.port.grid(row=1, column=1, sticky="ew", **pad)
        ttk.Button(left, text="Refresh", command=self._refresh_ports).grid(row=1, column=2, **pad)
        self.detect_button = ttk.Button(left, text="Detect", command=self._detect)
        self.detect_button.grid(row=1, column=3, **pad)

        self.node_label = ttk.Label(left, text="Node: not asked yet (press Detect)")
        self.node_label.grid(row=2, column=1, columnspan=3, sticky="w", **pad)
        self.chip_label = ttk.Label(left, text="Chip: not read yet")
        self.chip_label.grid(row=3, column=1, columnspan=3, sticky="w", **pad)

        ttk.Label(left, text="Board").grid(row=4, column=0, sticky="w", **pad)
        self.board = ttk.Combobox(left, state="readonly", values=["loading..."])
        self.board.grid(row=4, column=1, columnspan=3, sticky="ew", **pad)

        ttk.Label(left, text="Version").grid(row=5, column=0, sticky="w", **pad)
        self.version = ttk.Combobox(left, state="readonly", values=["loading..."])
        self.version.grid(row=5, column=1, columnspan=3, sticky="ew", **pad)

        self.backup = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            left,
            text="Back up the node's current settings before erasing (needs a working node)",
            variable=self.backup,
        ).grid(row=6, column=0, columnspan=4, sticky="w", **pad)

        buttons = ttk.Frame(left)
        buttons.grid(row=7, column=0, columnspan=4, sticky="w", **pad)
        self.flash_button = ttk.Button(buttons, text="Flash", command=self._flash)
        self.flash_button.pack(side="left", padx=(0, 8))
        self.configure_button = ttk.Button(buttons, text="Configure only", command=lambda: self._configure(check=False))
        self.configure_button.pack(side="left", padx=(0, 8))
        self.check_button = ttk.Button(buttons, text="Check", command=lambda: self._configure(check=True))
        self.check_button.pack(side="left")

        self.bar = ttk.Progressbar(left, mode="determinate", maximum=1000)
        self.bar.grid(row=8, column=0, columnspan=4, sticky="ew", **pad)
        self.bar_label = ttk.Label(left, text="")
        self.bar_label.grid(row=9, column=0, columnspan=4, sticky="w", **pad)

        self.log = scrolledtext.ScrolledText(left, height=12, state="disabled", wrap="word")
        self.log.grid(row=10, column=0, columnspan=4, sticky="nsew", **pad)
        self.log.tag_configure("error", foreground="#b00020")
        self.log.tag_configure("ok", foreground="#1b7f2a")
        left.rowconfigure(10, weight=1)

        right = ttk.LabelFrame(outer, text="Settings", padding=4)
        right.grid(row=0, column=1, sticky="nsew")
        right.rowconfigure(0, weight=1)
        right.columnconfigure(0, weight=1)
        try:
            self.form = ProfileForm(right, provision.DEFAULT_PROFILE)
        except ProvisionError as err:
            messagebox.showerror("Settings", str(err))
            raise SystemExit(1) from err
        self.form.grid(row=0, column=0, sticky="nsew")

    # -- background work --------------------------------------------------

    def _start(self, target, busy: bool = True) -> None:
        """Run `target()` on a worker thread; with `busy`, disable the
        buttons until it returns."""
        if self.worker and self.worker.is_alive():
            messagebox.showinfo("Busy", "A step is already running.")
            return
        if busy:
            self._set_busy(True)
        self.worker = threading.Thread(target=self._guard, args=(target, busy), daemon=True)
        self.worker.start()

    def _guard(self, target, busy: bool) -> None:
        try:
            target()
        except ProvisionError as err:
            self.events.put(("error", str(err)))
        except SystemExit as err:  # the meshtastic library exits on some failures
            self.events.put(("error", f"the meshtastic library gave up: {err}"))
        except Exception as err:  # noqa: BLE001 - shown to the user, never swallowed
            self.events.put(("error", f"{type(err).__name__}: {err}"))
        finally:
            self.events.put(("bar", (0, 0, "")))
            if busy:
                self.events.put(("idle", None))

    def _progress(self) -> Progress:
        return Progress(
            log=lambda text: self.events.put(("log", text)),
            bar=lambda done, total, label: self.events.put(("bar", (done, total, label))),
        )

    def _poll(self) -> None:
        try:
            while True:
                kind, payload = self.events.get_nowait()
                if kind == "log":
                    self._append(str(payload))
                elif kind == "error":
                    self._append(f"error: {payload}", "error")
                elif kind == "ok":
                    self._append(str(payload), "ok")
                elif kind == "bar":
                    done, total, label = payload  # type: ignore[misc]
                    self.bar["value"] = 0 if total <= 0 else min(1000, done * 1000 // total)
                    self.bar_label["text"] = label if total > 0 else ""
                elif kind == "idle":
                    self._set_busy(False)
                elif kind == "lists":
                    self.boards, versions = payload  # type: ignore[misc]
                    self._fill_boards()
                    self.version["values"] = [str(v) for v in versions]
                    if versions:
                        self.version.current(0)
                elif kind == "node":
                    self.identity = payload  # type: ignore[assignment]
                    self.node_label["text"] = f"Node: {payload}" if payload else "Node: nothing answers (blank board?)"
                    self._fill_boards()
                elif kind == "chip":
                    self.chip = payload  # type: ignore[assignment]
                    self.chip_label["text"] = f"Chip: {payload}"
                    self._fill_boards()
        except queue.Empty:
            pass
        self.root.after(100, self._poll)

    def _append(self, text: str, tag: str | None = None) -> None:
        self.log["state"] = "normal"
        self.log.insert("end", text + "\n", tag or ())
        self.log.see("end")
        self.log["state"] = "disabled"

    def _set_busy(self, busy: bool) -> None:
        state = "disabled" if busy else "normal"
        for button in (self.flash_button, self.configure_button, self.check_button, self.detect_button):
            button["state"] = state

    # -- actions ----------------------------------------------------------

    def _refresh_ports(self) -> None:
        ports = provision.serial_ports()
        self.port["values"] = [str(p) for p in ports]
        if ports:
            self.port.current(0)
        else:
            self.port.set("")

    def _selected_port(self) -> str:
        text = self.port.get()
        if not text:
            raise ProvisionError("no serial port selected: plug the node in and press Refresh")
        return text.split(" ", 1)[0]

    def _load_lists(self) -> None:
        boards = provision.boards()
        versions = provision.releases()
        self.events.put(("lists", (boards, versions)))

    def _fill_boards(self) -> None:
        """The board list, reduced to the detected chip's family, with the
        board the node reported preselected."""
        boards = self.boards
        if self.chip:
            boards = [b for b in boards if b.mcu == self.chip.chip]
        self.board["values"] = [str(b) for b in boards]
        wanted = self.identity.pio_env if self.identity else ""
        index = next((i for i, b in enumerate(boards) if b.platformio_target == wanted), 0 if boards else None)
        if index is None:
            self.board.set("")
        else:
            self.board.current(index)

    def _detect(self) -> None:
        def work() -> None:
            port = self._selected_port()
            progress = self._progress()
            identity = provision.probe_node(port, progress)
            self.events.put(("node", identity))
            info = provision.detect_chip(port, progress)
            self.events.put(("chip", info))

        self._start(work)

    def _save_settings(self) -> bool:
        """Write the form before an action; on an invalid entry, say which
        and do nothing."""
        try:
            self.form.save()
        except ProvisionError as err:
            messagebox.showerror("Settings", str(err))
            return False
        return True

    def _confirm(self, summary: str) -> bool:
        """Ask on the main thread, block the worker until answered."""
        answer: dict[str, bool] = {}
        done = threading.Event()

        def ask() -> None:
            answer["yes"] = messagebox.askokcancel("Erase and install", summary, icon="warning")
            done.set()

        self.root.after(0, ask)
        done.wait()
        return answer.get("yes", False)

    def _flash(self) -> None:
        if not self._save_settings():
            return

        def work() -> None:
            port = self._selected_port()
            board_text = self.board.get()
            board: str | None = None
            if board_text and board_text != "loading...":
                board = board_text.rsplit("(", 1)[-1].rstrip(")") if "(" in board_text else board_text
            # No board chosen: the running node must say which it is.
            version = self.version.get().split(" ", 1)[0]
            if not version or version == "loading...":
                raise ProvisionError("choose a firmware version")
            backup = None
            if self.backup.get():
                backup = BACKUP_DIR / f"node-{time.strftime('%Y%m%d-%H%M%S')}.yaml"
            params = RunParams(
                port=port,
                profile=provision.DEFAULT_PROFILE,
                flash=FlashParams(version=version, board=board, backup=backup),
                confirm=self._confirm,
            )
            result = provision.run(params, self._progress())
            self.events.put(("ok" if result.ok else "error", "done: the node is ready" if result.ok else "done, with settings still differing"))

        self._start(work)

    def _configure(self, check: bool) -> None:
        if not self._save_settings():
            return

        def work() -> None:
            params = RunParams(port=self._selected_port(), profile=provision.DEFAULT_PROFILE, check_only=check)
            result = provision.run(params, self._progress())
            if check:
                self.events.put(("ok" if result.ok else "log", "check finished"))
            else:
                self.events.put(("ok" if result.ok else "error", "configuration finished" if result.ok else "settings still differ"))

        self._start(work)

    def _close(self) -> None:
        """Keep the settings on close; an invalid entry asks before losing it."""
        try:
            self.form.save()
        except ProvisionError as err:
            if not messagebox.askokcancel("Settings", f"{err}\n\nClose anyway and lose the change?"):
                return
        self.root.destroy()


def main() -> None:
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
