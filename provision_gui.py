"""Tkinter window of meshtastic-provision: pick the port, board, version
and profile, then "Flash", "Configure only" or "Check". The work runs on a
worker thread and reports into the log pane; the buttons are disabled
meanwhile. Tkinter comes with uv's Python 3.13, so nothing else is needed.

    uv run provision_gui.py
"""

from __future__ import annotations

import queue
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, scrolledtext, ttk

import provision
from provision import FlashParams, Progress, ProvisionError, RunParams

INTRO = (
    "This tool prepares a Meshtastic node plugged in over USB.\n"
    "Flash erases the whole flash, installs the chosen firmware (the board's factory image, "
    "its OTA loader and file system, as the web flasher does), then applies the profile. "
    "The node restarts with default settings and a new private key: other nodes will have "
    "to learn its key again.\n"
    "Configure only applies the profile to the node as it is; Check just reports the "
    "settings that differ. Close meshtastic-desktop first: it holds the port."
)

BACKUP_DIR = provision.DEFAULT_PROFILE.parent / "backups"


class App:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        root.title("Meshtastic node provisioning")
        root.minsize(720, 560)
        self.events: queue.Queue[tuple[str, object]] = queue.Queue()
        self.worker: threading.Thread | None = None
        self.boards: list[provision.Board] = []
        self.chip: provision.ChipInfo | None = None
        self._build()
        self.root.after(100, self._poll)
        self._refresh_ports()
        self._start(self._load_lists, busy=False)

    # -- layout ---------------------------------------------------------

    def _build(self) -> None:
        pad = {"padx": 8, "pady": 4}
        frame = ttk.Frame(self.root, padding=8)
        frame.pack(fill="both", expand=True)
        frame.columnconfigure(1, weight=1)

        intro = ttk.Label(frame, text=INTRO, wraplength=680, justify="left")
        intro.grid(row=0, column=0, columnspan=4, sticky="w", **pad)

        ttk.Label(frame, text="Port").grid(row=1, column=0, sticky="w", **pad)
        self.port = ttk.Combobox(frame, state="readonly")
        self.port.grid(row=1, column=1, sticky="ew", **pad)
        ttk.Button(frame, text="Refresh", command=self._refresh_ports).grid(row=1, column=2, **pad)
        self.detect_button = ttk.Button(frame, text="Detect", command=self._detect)
        self.detect_button.grid(row=1, column=3, **pad)

        self.chip_label = ttk.Label(frame, text="Chip: not read yet")
        self.chip_label.grid(row=2, column=1, columnspan=3, sticky="w", **pad)

        ttk.Label(frame, text="Board").grid(row=3, column=0, sticky="w", **pad)
        self.board = ttk.Combobox(frame, state="readonly", values=["loading..."])
        self.board.grid(row=3, column=1, columnspan=3, sticky="ew", **pad)

        ttk.Label(frame, text="Version").grid(row=4, column=0, sticky="w", **pad)
        self.version = ttk.Combobox(frame, state="readonly", values=["loading..."])
        self.version.grid(row=4, column=1, columnspan=3, sticky="ew", **pad)

        ttk.Label(frame, text="Profile").grid(row=5, column=0, sticky="w", **pad)
        self.profile = ttk.Entry(frame)
        self.profile.grid(row=5, column=1, columnspan=2, sticky="ew", **pad)
        if provision.DEFAULT_PROFILE.is_file():
            self.profile.insert(0, str(provision.DEFAULT_PROFILE))
        ttk.Button(frame, text="Browse", command=self._browse_profile).grid(row=5, column=3, **pad)

        self.backup = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            frame,
            text=f"Back up the node's settings to {BACKUP_DIR} before erasing (needs a working node)",
            variable=self.backup,
        ).grid(row=6, column=0, columnspan=4, sticky="w", **pad)

        buttons = ttk.Frame(frame)
        buttons.grid(row=7, column=0, columnspan=4, sticky="w", **pad)
        self.flash_button = ttk.Button(buttons, text="Flash", command=self._flash)
        self.flash_button.pack(side="left", padx=(0, 8))
        self.configure_button = ttk.Button(buttons, text="Configure only", command=lambda: self._configure(check=False))
        self.configure_button.pack(side="left", padx=(0, 8))
        self.check_button = ttk.Button(buttons, text="Check", command=lambda: self._configure(check=True))
        self.check_button.pack(side="left")

        self.bar = ttk.Progressbar(frame, mode="determinate", maximum=1000)
        self.bar.grid(row=8, column=0, columnspan=4, sticky="ew", **pad)
        self.bar_label = ttk.Label(frame, text="")
        self.bar_label.grid(row=9, column=0, columnspan=4, sticky="w", **pad)

        self.log = scrolledtext.ScrolledText(frame, height=14, state="disabled", wrap="word")
        self.log.grid(row=10, column=0, columnspan=4, sticky="nsew", **pad)
        self.log.tag_configure("error", foreground="#b00020")
        self.log.tag_configure("ok", foreground="#1b7f2a")
        frame.rowconfigure(10, weight=1)

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
        boards = self.boards
        if self.chip:
            boards = [b for b in boards if b.mcu == self.chip.chip]
        self.board["values"] = [str(b) for b in boards]
        if boards:
            self.board.current(0)
        else:
            self.board.set("")

    def _detect(self) -> None:
        def work() -> None:
            port = self._selected_port()
            info = provision.detect_chip(port, self._progress())
            self.events.put(("chip", info))

        self._start(work)

    def _browse_profile(self) -> None:
        initial = self.profile.get() or str(provision.DEFAULT_PROFILE.parent)
        path = filedialog.askopenfilename(
            title="Choose a profile", initialdir=str(Path(initial).parent), filetypes=[("YAML", "*.yaml *.yml"), ("All", "*")]
        )
        if path:
            self.profile.delete(0, "end")
            self.profile.insert(0, path)

    def _profile_path(self, required: bool) -> Path | None:
        text = self.profile.get().strip()
        if not text:
            if required:
                raise ProvisionError("choose a profile first")
            return None
        return Path(text)

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
        def work() -> None:
            port = self._selected_port()
            board_text = self.board.get()
            if not board_text or board_text == "loading...":
                raise ProvisionError("choose a board (press Detect to read the chip first)")
            board = board_text.rsplit("(", 1)[-1].rstrip(")") if "(" in board_text else board_text
            version = self.version.get().split(" ", 1)[0]
            if not version or version == "loading...":
                raise ProvisionError("choose a firmware version")
            backup = None
            if self.backup.get():
                backup = BACKUP_DIR / f"node-{time.strftime('%Y%m%d-%H%M%S')}.yaml"
            params = RunParams(
                port=port,
                profile=self._profile_path(required=False),
                flash=FlashParams(board=board, version=version, backup=backup),
                confirm=self._confirm,
            )
            result = provision.run(params, self._progress())
            self.events.put(("ok" if result.ok else "error", "done: the node is ready" if result.ok else "done, with settings still differing"))

        self._start(work)

    def _configure(self, check: bool) -> None:
        def work() -> None:
            params = RunParams(port=self._selected_port(), profile=self._profile_path(required=True), check_only=check)
            result = provision.run(params, self._progress())
            if check:
                self.events.put(("ok" if result.ok else "log", "check finished"))
            else:
                self.events.put(("ok" if result.ok else "error", "configuration finished" if result.ok else "settings still differ"))

        self._start(work)


def main() -> None:
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
