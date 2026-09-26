"""Core of meshtastic-provision: flash a Meshtastic node over USB and apply
a settings profile, with no user interface of its own.

The command line (`provision_cli.py`) and the Qt window
(`provision_gui.py`) both drive [`run`] and receive its progress through a
[`Progress`] object, so every step lives here once.

Steps, in the order [`run`] performs them:

1. Guard: the serial port must not be held by another process (the
   meshtastic-desktop app keeps it open while connected).
2. Flash (optional): download the board's manifest and images from
   Meshtastic's official builds, check their MD5, check that the chip on the
   port is of the board's family with enough flash, optionally back up the
   node's settings, then erase the flash and write the factory image, the
   OTA loader and the file system image, like the firmware's
   `bin/device-install.sh` and the web flasher.
3. Configure: connect to the node, compare its settings with the profile,
   write the sections that differ inside one settings transaction (the node
   reboots once, at the commit), reconnect and compare again.

Only ESP32 boards are flashed. Secrets that identify the node (its private
key, channel keys) are never read from a profile: see [`load_profile`].
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import requests
import serial.tools.list_ports
import yaml
from esptool.logger import TemplateLogger
from google.protobuf.descriptor import FieldDescriptor

# Where per-board firmware files are published (manifest and images).
FILES_URL = "https://raw.githubusercontent.com/meshtastic/meshtastic.github.io/master"
# GitHub API listing of firmware releases.
RELEASES_URL = "https://api.github.com/repos/meshtastic/firmware/releases?per_page=30"
# Meshtastic's hardware list: every board with its build target.
BOARDS_URL = "https://api.meshtastic.org/resource/deviceHardware"
HTTP_TIMEOUT = 120
USER_AGENT = "meshtastic-provision/0.1"

# Downloaded images are kept here and checked against the manifest's MD5 on
# every use, so a corrupt file is never written.
CACHE_DIR = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "meshtastic-provision"
# The profile the tools propose by default: next to the app's own settings.
DEFAULT_PROFILE = (
    Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "meshtastic" / "node-profile.yaml"
)

# Every line the window shows in its log pane is also appended here, so a
# failed run can be read afterwards.
LOG_PATH = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state")) / "meshtastic-provision" / "provision.log"
# Serial speed for flashing (the ESP32-S3 USB bridges handle it well).
FLASH_BAUD = 921_600
# The firmware reboots 5 s after a committed settings transaction that needs
# it; wait longer before reconnecting so the reboot does not cut the
# verification short.
REBOOT_GRACE_S = 8
# How long to wait for a node to answer on its port after a reboot or a
# fresh install (first boot of a new firmware takes 15 to 30 s).
NODE_WAIT_S = 120

# Sections of the profile the tools accept.
PROFILE_KEYS = {"owner", "owner_short", "config", "module_config", "channels", "app"}
# meshtastic-desktop's settings a profile may set (`app` section): the
# booleans of `~/.config/meshtastic/settings.json` that make the app use
# the node and the broker (docs/node-setup.md, section 5).
APP_SETTING_KEYS = {"auto_connect", "mqtt_observer", "mqtt_observer_all_regions", "map_world_nodes", "map_gateway_links"}
APP_SETTINGS_PATH = (
    Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "meshtastic" / "settings.json"
)
# Never taken from a profile: the node's own keys.
FORBIDDEN_CONFIG_SECTIONS = {"security"}
FORBIDDEN_CHANNEL_FIELDS = {"psk"}


class ProvisionError(Exception):
    """A step failed; the message is meant for the user."""


def resource_path(name: str) -> Path:
    """A file shipped next to the code: beside the sources when run with
    `uv run`, in the bundle's directory when frozen by PyInstaller."""
    import sys

    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return base / name


# ---------------------------------------------------------------------------
# Progress reporting
# ---------------------------------------------------------------------------


# The stages of a run, announced through Progress.step in this order (a
# run without a flash starts at STEP_SETTINGS; a check only verifies).
STEP_DOWNLOAD = "download"
STEP_ERASE = "erase"
STEP_WRITE = "write"
STEP_RESTART = "restart"
STEP_SETTINGS = "settings"
STEP_VERIFY = "verify"
FLASH_STEPS = (STEP_DOWNLOAD, STEP_ERASE, STEP_WRITE, STEP_RESTART, STEP_SETTINGS, STEP_VERIFY)
CONFIGURE_STEPS = (STEP_SETTINGS, STEP_VERIFY)
CHECK_STEPS = (STEP_VERIFY,)


class Progress:
    """Where the steps report: a line of log, a progress bar position, or
    the stage the run enters.

    `log` receives one line at a time; `bar` receives (done, total, label),
    with `total` 0 to hide the bar and -1 for a wait of unknown length;
    `step` receives one of the STEP_* names.
    """

    def __init__(
        self,
        log: Callable[[str], None] | None = None,
        bar: Callable[[int, int, str], None] | None = None,
        step: Callable[[str], None] | None = None,
    ) -> None:
        self._log = log or (lambda text: None)
        self._bar = bar or (lambda done, total, label: None)
        self._step = step or (lambda name: None)

    def log(self, text: str) -> None:
        self._log(text)

    def bar(self, done: int, total: int, label: str = "") -> None:
        self._bar(done, total, label)

    def busy(self, label: str) -> None:
        """A wait whose length is unknown (erasing, a reboot)."""
        self._bar(0, -1, label)

    def step(self, name: str) -> None:
        self._step(name)


# ---------------------------------------------------------------------------
# Serial ports
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SerialPort:
    device: str
    description: str

    def __str__(self) -> str:
        return f"{self.device} ({self.description})"


def serial_ports() -> list[SerialPort]:
    """USB serial ports, `/dev/ttyUSB*` and `/dev/ttyACM*`, sorted by name."""
    ports = [
        SerialPort(p.device, p.description or p.hwid or "")
        for p in serial.tools.list_ports.comports()
        if p.vid is not None
    ]
    return sorted(ports, key=lambda p: p.device)


def port_holders(port: str) -> list[tuple[int, str]]:
    """Processes holding `port` open, as (pid, command name), read from
    `/proc/*/fd`. Processes of other users cannot be inspected and are not
    listed."""
    target = os.path.realpath(port)
    holders = []
    for entry in os.scandir("/proc"):
        if not entry.name.isdigit():
            continue
        fd_dir = os.path.join(entry.path, "fd")
        try:
            fds = os.listdir(fd_dir)
        except OSError:
            continue
        for fd in fds:
            try:
                if os.readlink(os.path.join(fd_dir, fd)) == target:
                    with open(os.path.join(entry.path, "comm"), encoding="utf-8") as f:
                        holders.append((int(entry.name), f.read().strip()))
                    break
            except OSError:
                continue
    return holders


class PortAccessError(ProvisionError):
    """The user may not open the port: not in its group, or the group was
    granted but is not active in this session yet (no new login since)."""

    def __init__(self, port: str, group: str, granted: bool) -> None:
        self.port = port
        self.group = group
        self.granted = granted
        if granted:
            text = (
                f"{port} belongs to the group {group}, which was given to you but is not active "
                f"in this session yet: log out and back in, or restart this tool with `sg {group} -c ...`"
            )
        else:
            text = (
                f"{port} belongs to the group {group}, which you are not in: run "
                f"`sudo usermod -aG {group} $USER`, then log out and back in"
            )
        super().__init__(text)


def port_group(port: str) -> str:
    """The group owning the serial device (`dialout` on Debian and
    Raspberry Pi OS, `uucp` on Arch)."""
    import grp

    try:
        return grp.getgrgid(os.stat(port).st_gid).gr_name
    except (OSError, KeyError):
        return "dialout"


def user_in_group(group: str) -> bool:
    """Whether the user was given `group` (in the system's group file),
    active in this session or not."""
    import grp
    import pwd

    user = pwd.getpwuid(os.getuid())
    try:
        entry = grp.getgrnam(group)
    except KeyError:
        return False
    return user.pw_name in entry.gr_mem or user.pw_gid == entry.gr_gid


def check_port_access(port: str) -> None:
    """Raise [`PortAccessError`] when `port` cannot be opened."""
    if os.access(port, os.R_OK | os.W_OK):
        return
    group = port_group(port)
    raise PortAccessError(port, group, granted=user_in_group(group))


# The udev rule that lets the logged-in user open USB serial ports (see
# packaging/70-meshtastic-provision.rules, the same text).
UDEV_RULE_PATH = "/etc/udev/rules.d/70-meshtastic-provision.rules"
UDEV_RULE = (
    "# Installed by meshtastic-provision: the user logged in at the desktop may open\n"
    "# USB serial ports (Meshtastic nodes) without belonging to the dialout group.\n"
    'SUBSYSTEM=="tty", KERNEL=="ttyUSB[0-9]*|ttyACM[0-9]*", TAG+="uaccess"\n'
)


def _elevated(command: list[str], graphical: bool) -> None:
    """Run `command` as root: through the desktop's password prompt
    (`pkexec`) or `sudo` on the terminal."""
    import shutil
    import subprocess

    elevate = "pkexec" if graphical else "sudo"
    if shutil.which(elevate) is None:
        raise ProvisionError(f"{elevate} is not installed: run as root: {' '.join(command)}")
    result = subprocess.run([elevate, *command], capture_output=True, text=True, check=False)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip() or f"exit code {result.returncode}"
        raise ProvisionError(f"{' '.join(command[:2])}: {detail}")


def install_udev_rule(graphical: bool) -> None:
    """Write [`UDEV_RULE`] to [`UDEV_RULE_PATH`] as root and apply it to the
    devices already plugged in. The access takes effect at once, with no
    new login and no restart, on desktops run by systemd-logind (Ubuntu,
    Debian, Raspberry Pi OS)."""
    import time

    script = (
        f"printf '%s' \"$1\" > {UDEV_RULE_PATH} && chmod 644 {UDEV_RULE_PATH} "
        "&& udevadm control --reload-rules && udevadm trigger --subsystem-match=tty --action=add"
    )
    _elevated(["sh", "-c", script, "meshtastic-provision", UDEV_RULE], graphical)
    time.sleep(1)  # udev applies the ACL asynchronously


def add_user_to_group(group: str, graphical: bool) -> None:
    """`usermod -aG group user` with administrator rights: through the
    desktop's password prompt (`pkexec`) or `sudo` on the terminal."""
    import pwd

    user = pwd.getpwuid(os.getuid()).pw_name
    _elevated(["usermod", "-aG", group, user], graphical)


def relaunch_with_group(group: str) -> None:
    """Replace this process by the same command run with `group` active
    (`sg`), so a group granted during the session applies without a new
    login. Never returns on success."""
    import shlex
    import shutil
    import sys

    if shutil.which("sg") is None:
        raise ProvisionError(f"`sg` is not installed: log out and back in for the group {group} to apply")
    command = shlex.join([sys.executable, *sys.argv])
    os.execvp("sg", ["sg", group, "-c", command])


def ensure_port_free(port: str) -> None:
    """Refuse to go on while `port` cannot be opened (see
    [`check_port_access`]) or another process holds it: the app, the
    meshtastic CLI or a serial monitor would break the flash or the
    configuration."""
    if not os.path.exists(port):
        raise ProvisionError(f"{port} does not exist: is the node plugged in?")
    check_port_access(port)
    holders = port_holders(port)
    if holders:
        names = ", ".join(f"{name} (pid {pid})" for pid, name in holders)
        raise ProvisionError(f"{port} is held by {names}: close it first (stop meshtastic-desktop if it runs)")


# ---------------------------------------------------------------------------
# Published firmware: releases, boards, manifests, images
# ---------------------------------------------------------------------------


def _http() -> requests.Session:
    session = requests.Session()
    session.headers["User-Agent"] = USER_AGENT
    return session


@dataclass(frozen=True)
class Release:
    """A published firmware release."""

    version: str  # without the leading `v`, e.g. `2.7.26.54e0d8d`
    prerelease: bool

    def __str__(self) -> str:
        return f"{self.version} (alpha)" if self.prerelease else self.version


def releases() -> list[Release]:
    """Published releases, newest first, skipping drafts and empty ones."""
    try:
        response = _http().get(RELEASES_URL, timeout=HTTP_TIMEOUT)
        response.raise_for_status()
        entries = response.json()
    except (requests.RequestException, ValueError) as err:
        raise ProvisionError(f"GitHub: {err}") from err
    return [
        Release(entry["tag_name"].removeprefix("v"), bool(entry.get("prerelease")))
        for entry in entries
        if not entry.get("draft") and entry.get("assets")
    ]


@dataclass(frozen=True)
class Board:
    """A board a full install can target."""

    platformio_target: str  # build target, e.g. `heltec-v3`
    display_name: str  # e.g. `Heltec V3`
    architecture: str  # e.g. `esp32-s3`

    @property
    def mcu(self) -> str:
        """The chip family as manifests and esptool name it (`esp32s3`)."""
        return self.architecture.replace("-", "")

    def __str__(self) -> str:
        return f"{self.display_name} ({self.platformio_target})" if self.display_name else self.platformio_target


def boards() -> list[Board]:
    """The actively supported ESP32 boards of Meshtastic's hardware list,
    sorted by name."""
    try:
        response = _http().get(BOARDS_URL, timeout=HTTP_TIMEOUT)
        response.raise_for_status()
        entries = response.json()
    except (requests.RequestException, ValueError) as err:
        raise ProvisionError(f"hardware list: {err}") from err
    result: dict[str, Board] = {}
    for entry in entries:
        board = Board(entry["platformioTarget"], entry.get("displayName", ""), entry["architecture"])
        if entry.get("activelySupported") and board.mcu.startswith("esp32"):
            result.setdefault(board.platformio_target, board)
    return sorted(result.values(), key=lambda b: str(b).lower())


@dataclass(frozen=True)
class BoardFile:
    """One file of a board's build."""

    name: str
    md5: str
    bytes: int
    part_name: str | None  # the partition it is written to, for partition images


@dataclass(frozen=True)
class Partition:
    name: str
    subtype: str
    offset: int
    size: int


@dataclass(frozen=True)
class Manifest:
    """A board's `.mt.json` manifest for one version."""

    version: str
    platformio_target: str
    mcu: str  # chip family, lower case, e.g. `esp32s3`
    files: tuple[BoardFile, ...]
    part: tuple[Partition, ...]

    def factory_image(self) -> BoardFile:
        """`firmware-<env>-<version>.factory.bin`: bootloader, partition
        table and application, written at 0."""
        name = f"firmware-{self.platformio_target}-{self.version}.factory.bin"
        for file in self.files:
            if file.name == name:
                return file
        raise ProvisionError(f"the manifest has no {name}")

    def partition_image(self, partition: str) -> tuple[BoardFile, int]:
        """The image written to `partition` and the partition's offset."""
        file = next((f for f in self.files if f.part_name == partition), None)
        part = next((p for p in self.part if p.name == partition), None)
        if file is None or part is None:
            raise ProvisionError(f"the manifest has no image for partition {partition}")
        return file, part.offset

    def flash_end(self) -> int:
        """Bytes of flash the partition table needs."""
        if not self.part:
            raise ProvisionError("the manifest has no partition table")
        return max(p.offset + p.size for p in self.part)


def _parse_hex(text: str) -> int:
    return int(text.strip(), 16)


def manifest(version: str, platformio_target: str) -> Manifest:
    """The manifest of `platformio_target`'s build of `version`."""
    url = f"{FILES_URL}/firmware-{version}/firmware-{platformio_target}-{version}.mt.json"
    try:
        response = _http().get(url, timeout=HTTP_TIMEOUT)
        if response.status_code == 404:
            raise ProvisionError(f"no {platformio_target} build published for {version}")
        response.raise_for_status()
        data = response.json()
    except (requests.RequestException, ValueError) as err:
        raise ProvisionError(f"manifest: {err}") from err
    if data.get("platformioTarget") != platformio_target:
        raise ProvisionError(f"the manifest is for {data.get('platformioTarget')}, not {platformio_target}")
    return Manifest(
        version=data["version"],
        platformio_target=data["platformioTarget"],
        mcu=data["mcu"].lower(),
        files=tuple(BoardFile(f["name"], f["md5"], int(f["bytes"]), f.get("part_name")) for f in data["files"]),
        part=tuple(
            Partition(p["name"], p.get("subtype", ""), _parse_hex(p["offset"]), _parse_hex(p.get("size", "0")))
            for p in data.get("part", [])
        ),
    )


def download(version: str, file: BoardFile, progress: Progress) -> Path:
    """`file` of `version`, from the cache or downloaded, checked against
    the manifest's size and MD5."""
    path = CACHE_DIR / f"firmware-{version}" / file.name
    if path.is_file() and _md5(path) == file.md5 and path.stat().st_size == file.bytes:
        progress.log(f"{file.name}: cached, MD5 checked")
        return path
    url = f"{FILES_URL}/firmware-{version}/{file.name}"
    progress.log(f"downloading {file.name} ({file.bytes // 1024} kB)")
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_suffix(path.suffix + ".part")
    try:
        with _http().get(url, stream=True, timeout=HTTP_TIMEOUT) as response:
            response.raise_for_status()
            done = 0
            with open(partial, "wb") as out:
                for chunk in response.iter_content(chunk_size=64 * 1024):
                    out.write(chunk)
                    done += len(chunk)
                    progress.bar(done, file.bytes, file.name)
    except requests.RequestException as err:
        raise ProvisionError(f"{file.name}: {err}") from err
    finally:
        progress.bar(0, 0)
    if partial.stat().st_size != file.bytes or _md5(partial) != file.md5:
        partial.unlink()
        raise ProvisionError(f"{file.name}: size or MD5 differ from the manifest, download discarded")
    partial.replace(path)
    return path


def _md5(path: Path) -> str:
    digest = hashlib.md5()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


# ---------------------------------------------------------------------------
# The ESP32 on the port: detection and flashing with esptool
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ChipInfo:
    chip: str  # family as manifests name it, e.g. `esp32s3`
    mac: str  # `aa:bb:cc:dd:ee:ff`
    flash_bytes: int

    def __str__(self) -> str:
        return f"{self.chip}, MAC {self.mac}, {self.flash_bytes // (1024 * 1024)} MB flash"


class _EsptoolLog(TemplateLogger):
    """esptool's logging hooks (its `TemplateLogger` interface), forwarded
    to a [`Progress`]. esptool prints through a process-wide logger: one
    forwarder is installed once ([`_esptool_log`]) and each chip session
    points it at its own Progress."""

    def __init__(self, progress: Progress) -> None:
        self.progress = progress

    def print(self, *args: Any, **kwargs: Any) -> None:
        text = " ".join(str(a) for a in args).strip()
        if text:
            self.progress.log(f"esptool: {text}")

    def note(self, message: str) -> None:
        self.progress.log(f"esptool: {message}")

    def warning(self, message: str) -> None:
        self.progress.log(f"esptool warning: {message}")

    def error(self, message: str) -> None:
        self.progress.log(f"esptool error: {message}")

    def warn(self, message: str, suggestion: str | None = None) -> None:
        self.warning(message)

    def err(self, message: str, suggestion: str | None = None) -> None:
        self.error(message)

    def debug(self, *args: Any) -> None:
        pass

    def hint(self, message: str) -> None:
        pass

    def stage(self, finish: bool = False) -> None:
        pass

    def progress_bar(self, cur_iter: int, total_iters: int, prefix: str = "", suffix: str = "", bar_length: int = 30) -> None:
        self.progress.bar(cur_iter, total_iters, prefix.strip())

    def set_verbosity(self, verbosity: Any) -> None:
        pass


_ESPTOOL_FORWARDER: _EsptoolLog | None = None


def _esptool_log() -> _EsptoolLog:
    """The forwarder, installed as esptool's logger on first use.

    2026-09-26: installing a new one per session failed from the second
    session on ("'_LegacyLoggerAdapter' object has no attribute
    'set_logger'"): esptool's `log` is a proxy to the installed logger, so
    once ours was in place, `log.set_logger` reached our adapter instead of
    esptool's own logger."""
    global _ESPTOOL_FORWARDER
    if _ESPTOOL_FORWARDER is None:
        from esptool.logger import log

        _ESPTOOL_FORWARDER = _EsptoolLog(Progress())
        log.set_logger(_ESPTOOL_FORWARDER)
    return _ESPTOOL_FORWARDER


class _Chip:
    """A session with the ESP32 on a port: ROM bootloader, then esptool's
    stub at [`FLASH_BAUD`] with the flash attached. Restarts the chip and
    closes the port on exit, whatever ran before."""

    def __init__(self, port: str, progress: Progress) -> None:
        self.port = port
        self.progress = progress
        self.esp: Any = None

    def __enter__(self) -> "_Chip":
        from esptool.cmds import attach_flash, detect_chip, run_stub
        _esptool_log().progress = self.progress
        try:
            esp = detect_chip(self.port, baud=115_200)
            esp = run_stub(esp)
            esp.change_baud(FLASH_BAUD)
            attach_flash(esp)
        except Exception as err:  # esptool raises FatalError and OSError alike
            raise ProvisionError(f"connecting to the chip on {self.port}: {err} (is it an ESP32?)") from err
        self.esp = esp
        return self

    def __exit__(self, *exc: Any) -> None:
        from esptool.cmds import reset_chip

        try:
            reset_chip(self.esp, "hard-reset")
        finally:
            self.esp._port.close()
            self.progress.bar(0, 0)

    def info(self) -> ChipInfo:
        from esptool.cmds import detect_flash_size

        size = detect_flash_size(self.esp)
        match = re.fullmatch(r"(\d+)(MB|KB)", size or "")
        if not match:
            raise ProvisionError(f"unrecognised flash size {size!r}")
        flash_bytes = int(match.group(1)) * (1024 * 1024 if match.group(2) == "MB" else 1024)
        mac = self.esp.read_mac("BASE_MAC")
        return ChipInfo(
            chip=self.esp.CHIP_NAME.lower().replace("-", ""),
            mac=":".join(f"{b:02x}" for b in mac),
            flash_bytes=flash_bytes,
        )


def detect_chip(port: str, progress: Progress) -> ChipInfo:
    """Read the chip family, MAC address and flash size of the ESP32 on
    `port`, then restart it."""
    ensure_port_free(port)
    with _Chip(port, progress) as chip:
        info = chip.info()
    progress.log(f"chip on {port}: {info}")
    return info


def check_chip(info: ChipInfo, manifest: Manifest) -> None:
    """The chip must be of the manifest's family with enough flash for its
    partition table; the node's identity cannot be checked before a full
    install, as nothing on the chip says which node it is."""
    if info.chip != manifest.mcu:
        raise ProvisionError(f"the chip is an {info.chip}, the {manifest.platformio_target} build needs an {manifest.mcu}")
    needed = manifest.flash_end()
    if info.flash_bytes < needed:
        raise ProvisionError(
            f"the chip has {info.flash_bytes // (1024 * 1024)} MB of flash, the partition table needs {needed / (1024 * 1024):.1f} MB"
        )


def install_firmware(port: str, manifest: Manifest, progress: Progress) -> ChipInfo:
    """Full install: download the images, check the chip, erase the flash,
    write the factory image at 0, the OTA loader at `app1` and the file
    system at `spiffs`, then restart the chip. The caller has confirmed the
    erase."""
    factory = manifest.factory_image()
    loader, loader_offset = manifest.partition_image("app1")
    littlefs, littlefs_offset = manifest.partition_image("spiffs")
    progress.step(STEP_DOWNLOAD)
    images = [
        (0, download(manifest.version, factory, progress)),
        (loader_offset, download(manifest.version, loader, progress)),
        (littlefs_offset, download(manifest.version, littlefs, progress)),
    ]
    ensure_port_free(port)
    with _Chip(port, progress) as chip:
        from esptool.cmds import erase_flash, write_flash

        info = chip.info()
        progress.log(f"chip on {port}: {info}")
        check_chip(info, manifest)
        progress.step(STEP_ERASE)
        progress.log("erasing the flash")
        progress.busy("erasing the flash")
        try:
            erase_flash(chip.esp)
            progress.bar(0, 0)
            progress.step(STEP_WRITE)
            for offset, path in images:
                progress.log(f"writing {path.name} at 0x{offset:x}")
            # The factory image carries its own flash size in its header, so
            # keep it; esptool verifies each region after writing.
            write_flash(chip.esp, [(offset, str(path)) for offset, path in images], flash_size="keep")
        except Exception as err:
            raise ProvisionError(f"flashing: {err}") from err
    progress.log("flash written and verified, the node restarts")
    return info


# ---------------------------------------------------------------------------
# The node's settings: connection, profile, comparison, writing
# ---------------------------------------------------------------------------


@dataclass
class Difference:
    """One setting whose value on the node differs from the profile."""

    path: str  # e.g. `config.lora.region`, `channels[0].settings.uplink_enabled`
    current: str
    wanted: str

    def __str__(self) -> str:
        return f"{self.path}: {self.current} -> {self.wanted}"


@dataclass
class Profile:
    """A validated profile: only the fields it names are compared and
    written; everything else on the node is left alone."""

    owner: str | None = None
    owner_short: str | None = None
    config: dict[str, dict[str, Any]] = field(default_factory=dict)
    module_config: dict[str, dict[str, Any]] = field(default_factory=dict)
    channels: list[dict[str, Any]] = field(default_factory=list)
    # meshtastic-desktop settings (see APP_SETTING_KEYS), applied to
    # settings.json after the node.
    app: dict[str, bool] = field(default_factory=dict)

    def is_empty(self) -> bool:
        return not (self.owner or self.owner_short or self.config or self.module_config or self.channels or self.app)


def load_profile(path: Path) -> Profile:
    """Read and validate a YAML profile. Refuses the `security` section, any
    channel `psk` and unknown top-level keys; nested fields are checked
    against the protobuf descriptors when compared."""
    try:
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except (OSError, yaml.YAMLError) as err:
        raise ProvisionError(f"profile {path}: {err}") from err
    if not isinstance(data, dict):
        raise ProvisionError(f"profile {path}: expected a mapping at the top level")
    unknown = set(data) - PROFILE_KEYS
    if unknown:
        raise ProvisionError(f"profile {path}: unknown keys {sorted(unknown)} (accepted: {sorted(PROFILE_KEYS)})")
    profile = Profile()
    for key in ("owner", "owner_short"):
        value = data.get(key)
        if value is not None:
            if not isinstance(value, str) or not value.strip():
                raise ProvisionError(f"profile {path}: {key} must be a non-empty string")
            setattr(profile, key, value.strip())
    for key in ("config", "module_config"):
        sections = data.get(key) or {}
        if not isinstance(sections, dict) or not all(isinstance(v, dict) for v in sections.values()):
            raise ProvisionError(f"profile {path}: {key} must map section names to mappings of fields")
        setattr(profile, key, sections)
    forbidden = FORBIDDEN_CONFIG_SECTIONS & set(profile.config)
    if forbidden:
        raise ProvisionError(f"profile {path}: config.{forbidden.pop()} is never taken from a profile (the node's keys)")
    channels = data.get("channels") or []
    if not isinstance(channels, list):
        raise ProvisionError(f"profile {path}: channels must be a list")
    for entry in channels:
        if not isinstance(entry, dict) or not isinstance(entry.get("index"), int) or not 0 <= entry["index"] <= 7:
            raise ProvisionError(f"profile {path}: each channel needs an index from 0 to 7")
        settings = entry.get("settings") or {}
        if FORBIDDEN_CHANNEL_FIELDS & set(settings):
            raise ProvisionError(f"profile {path}: channels[{entry['index']}].settings.psk is never taken from a profile")
        unknown = set(entry) - {"index", "role", "settings"}
        if unknown:
            raise ProvisionError(f"profile {path}: channels[{entry['index']}]: unknown keys {sorted(unknown)}")
    profile.channels = channels
    psk = profile.config.get("network", {}).get("wifi_psk")
    if psk is not None and len(str(psk)) < 8:
        raise ProvisionError(f"profile {path}: network.wifi_psk must be 8 characters or more")
    app = data.get("app") or {}
    if not isinstance(app, dict):
        raise ProvisionError(f"profile {path}: app must be a mapping")
    unknown = set(app) - APP_SETTING_KEYS
    if unknown:
        raise ProvisionError(f"profile {path}: app: unknown keys {sorted(unknown)} (accepted: {sorted(APP_SETTING_KEYS)})")
    for key, value in app.items():
        if not isinstance(value, bool):
            raise ProvisionError(f"profile {path}: app.{key} must be true or false")
    profile.app = app
    return profile


# ---------------------------------------------------------------------------
# meshtastic-desktop's own settings
# ---------------------------------------------------------------------------


def app_running() -> list[int]:
    """PIDs of meshtastic-desktop: a native executable named `meshtastic`
    (the Python CLI of the same name runs under python and is not it)."""
    pids = []
    for entry in os.scandir("/proc"):
        if not entry.name.isdigit():
            continue
        try:
            exe = os.readlink(os.path.join(entry.path, "exe"))
        except OSError:
            continue
        if os.path.basename(exe) == "meshtastic":
            pids.append(int(entry.name))
    return pids


def compare_app_settings(port: str, app: dict[str, bool]) -> list[Difference]:
    """The app settings of the profile whose value in settings.json differs,
    plus `last_address` when it is not the node's port."""
    current = _read_app_settings()
    diffs = []
    wanted_address = f"s{port}"
    if current.get("last_address") != wanted_address:
        diffs.append(Difference("app.last_address", repr(current.get("last_address")), repr(wanted_address)))
    for key, value in app.items():
        if current.get(key) != value:
            diffs.append(Difference(f"app.{key}", _show_bool(current.get(key)), _show_bool(value)))
    return diffs


def apply_app_settings(port: str, app: dict[str, bool], progress: Progress) -> list[Difference]:
    """Write the profile's app settings and the node's port as the address
    to connect to into settings.json, keeping everything else. The app
    reads the file at start-up only and rewrites it when it saves, so it
    must not be running."""
    import json

    diffs = compare_app_settings(port, app)
    if not diffs:
        progress.log("meshtastic-desktop is already set up for this node")
        return diffs
    pids = app_running()
    if pids:
        raise ProvisionError(f"meshtastic-desktop runs (pid {pids[0]}): stop it, its settings cannot be changed underneath it")
    settings = _read_app_settings()
    settings["last_address"] = f"s{port}"
    settings.update(app)
    APP_SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = APP_SETTINGS_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")
    tmp.replace(APP_SETTINGS_PATH)
    progress.log(f"meshtastic-desktop settings written ({APP_SETTINGS_PATH}): " + ", ".join(str(d) for d in diffs))
    return diffs


def _read_app_settings() -> dict[str, Any]:
    import json

    if not APP_SETTINGS_PATH.is_file():
        return {}
    try:
        data = json.loads(APP_SETTINGS_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError) as err:
        raise ProvisionError(f"{APP_SETTINGS_PATH}: {err}") from err
    if not isinstance(data, dict):
        raise ProvisionError(f"{APP_SETTINGS_PATH}: not a JSON object")
    return data


def _show_bool(value: Any) -> str:
    if value is None:
        return "unset"
    return "true" if value else "false"


def _coerce(fd: FieldDescriptor, value: Any, path: str) -> Any:
    """`value` from the profile as the protobuf field `fd` stores it."""
    # 2026-09-26: protobuf 7 dropped `FieldDescriptor.label`; `is_repeated`
    # is the replacement.
    if fd.is_repeated:
        raise ProvisionError(f"{path}: repeated fields are not supported")
    if fd.type == FieldDescriptor.TYPE_ENUM:
        if isinstance(value, str):
            entry = fd.enum_type.values_by_name.get(value)
            if entry is None:
                choices = ", ".join(sorted(v.name for v in fd.enum_type.values))
                raise ProvisionError(f"{path}: no value {value!r}; choices: {choices}")
            return entry.number
        if isinstance(value, int) and not isinstance(value, bool) and fd.enum_type.values_by_number.get(value):
            return value
        raise ProvisionError(f"{path}: expected an enum name, got {value!r}")
    if fd.type == FieldDescriptor.TYPE_BOOL:
        if isinstance(value, bool):
            return value
        raise ProvisionError(f"{path}: expected true or false, got {value!r}")
    if fd.type == FieldDescriptor.TYPE_STRING:
        if isinstance(value, str):
            return value
        raise ProvisionError(f"{path}: expected a string, got {value!r}")
    if fd.type == FieldDescriptor.TYPE_BYTES:
        raise ProvisionError(f"{path}: byte fields are not supported")
    if fd.type in (FieldDescriptor.TYPE_FLOAT, FieldDescriptor.TYPE_DOUBLE):
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return float(value)
        raise ProvisionError(f"{path}: expected a number, got {value!r}")
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    raise ProvisionError(f"{path}: expected an integer, got {value!r}")


# Fields whose values never reach a log line or a difference shown to the
# user (2026-09-26: the Wi-Fi password was written in clear to the log).
SECRET_FIELDS = {"wifi_psk", "password"}


def _show(fd: FieldDescriptor, value: Any) -> str:
    """`value` of field `fd` as the profile would write it; secrets are
    masked (set or empty only)."""
    if fd.name in SECRET_FIELDS:
        return "'***'" if value else "''"
    if fd.type == FieldDescriptor.TYPE_ENUM:
        entry = fd.enum_type.values_by_number.get(value)
        return entry.name if entry else str(value)
    if fd.type == FieldDescriptor.TYPE_BOOL:
        return "true" if value else "false"
    return repr(value)


def _walk(message: Any, wanted: dict[str, Any], path: str, apply: bool) -> list[Difference]:
    """Compare `wanted` (a profile mapping) with the protobuf `message`,
    recursing into nested messages; with `apply`, also set the differing
    fields on `message`."""
    diffs: list[Difference] = []
    for key, value in wanted.items():
        fd = message.DESCRIPTOR.fields_by_name.get(key)
        here = f"{path}.{key}"
        if fd is None:
            names = ", ".join(sorted(message.DESCRIPTOR.fields_by_name))
            raise ProvisionError(f"{here}: no such field; fields: {names}")
        if fd.message_type is not None:
            if not isinstance(value, dict):
                raise ProvisionError(f"{here}: expected a mapping of fields")
            diffs.extend(_walk(getattr(message, key), value, here, apply))
            continue
        target = _coerce(fd, value, here)
        current = getattr(message, key)
        if current != target:
            diffs.append(Difference(here, _show(fd, current), _show(fd, target)))
            if apply:
                setattr(message, key, target)
    return diffs


@dataclass
class NodeIdentity:
    """What a running node says about itself."""

    node_id: str
    long_name: str
    short_name: str
    firmware: str
    # Build target the firmware was made for (`MyNodeInfo.pio_env`, e.g.
    # `heltec-v3`); empty on firmware older than 2.5.
    pio_env: str
    hw_model: str  # e.g. `HELTEC_V3`
    mac: str | None  # `aa:bb:cc:dd:ee:ff`, the ESP32's base MAC

    def __str__(self) -> str:
        board = self.pio_env or self.hw_model or "unknown board"
        return f"{self.node_id} {self.long_name!r}, {board}, firmware {self.firmware or '?'}"


# How long a probe waits for a node to answer: a running node sends its
# settings within a few seconds, a blank chip never does.
PROBE_TIMEOUT_S = 12.0


def _serial_interface_class() -> type:
    import meshtastic.serial_interface

    class ProvisionSerialInterface(meshtastic.serial_interface.SerialInterface):
        """`SerialInterface` whose connection wait is configurable: the
        library's is fixed at 30 s, too long when probing a blank board."""

        connect_timeout = 30.0

        def _waitConnected(self, timeout: float = 30.0) -> None:  # noqa: N802 - library name
            super()._waitConnected(timeout=self.connect_timeout)

    return ProvisionSerialInterface


def connect_node(port: str, timeout: int = 60, connect_timeout: float = 30.0) -> Any:
    """A `SerialInterface` to the node on `port`, with its settings and
    channels received. Raises [`ProvisionError`] when nothing answers
    within `connect_timeout` seconds; the port is released either way."""
    from meshtastic.mesh_interface import MeshInterface

    iface = _serial_interface_class()(devPath=port, connectNow=False, timeout=timeout)
    iface.connect_timeout = connect_timeout
    try:
        iface.connect()
        iface.waitForConfig()
        if not iface.localNode.waitForConfig("channels"):
            raise ProvisionError(f"{port}: the node did not send its channels")
        if iface.localNode.moduleConfig is None:
            raise ProvisionError(f"{port}: the node did not send its module settings")
    except (MeshInterface.MeshInterfaceError, OSError, SystemExit) as err:
        iface.close()
        raise ProvisionError(f"no node answers on {port}: {err}") from err
    except Exception:
        iface.close()
        raise
    return iface


def node_identity(iface: Any) -> NodeIdentity:
    import base64

    info = iface.getMyNodeInfo() or {}
    user = info.get("user") or {}
    metadata = iface.metadata
    mac = None
    if user.get("macaddr"):
        raw = base64.b64decode(user["macaddr"])
        if len(raw) == 6:
            mac = ":".join(f"{b:02x}" for b in raw)
    return NodeIdentity(
        node_id=user.get("id", "?"),
        long_name=user.get("longName", ""),
        short_name=user.get("shortName", ""),
        firmware=getattr(metadata, "firmware_version", "") if metadata else "",
        pio_env=getattr(iface.myInfo, "pio_env", "") if iface.myInfo else "",
        hw_model=user.get("hwModel", ""),
        mac=mac,
    )


def probe_node(port: str, progress: Progress) -> NodeIdentity | None:
    """The identity of the node running on `port`, or None when nothing
    answers (a blank or bricked board). The connection is closed again."""
    ensure_port_free(port)
    progress.log(f"probing the node on {port} (up to {PROBE_TIMEOUT_S:.0f} s; a blank board does not answer)")
    try:
        iface = connect_node(port, timeout=30, connect_timeout=PROBE_TIMEOUT_S)
    except ProvisionError as err:
        progress.log(f"no node answers: {err}")
        return None
    try:
        identity = node_identity(iface)
    finally:
        iface.close()
    progress.log(f"node: {identity}")
    return identity


def board_of(identity: NodeIdentity | None, chosen: str | None) -> str:
    """The build target to install: the node's own when it reports one,
    which a chosen one must then match; else the chosen one."""
    if identity and identity.pio_env:
        if chosen and chosen != identity.pio_env:
            raise ProvisionError(f"the node says it is a {identity.pio_env}, not a {chosen}")
        return identity.pio_env
    if identity and not identity.pio_env and not chosen:
        raise ProvisionError(f"the node's firmware does not report its board ({identity.hw_model or 'unknown model'}): give the board")
    if not chosen:
        raise ProvisionError("no node answers on the port, so the board must be given")
    return chosen


def wait_for_node(port: str, progress: Progress, first_delay: float, deadline: float = NODE_WAIT_S) -> Any:
    """Connect to the node once it is back after a reboot: wait
    `first_delay` seconds, then retry every 3 s up to `deadline` seconds."""
    progress.log(f"waiting for the node on {port}")
    progress.busy("waiting for the node to start")
    time.sleep(first_delay)
    started = time.monotonic()
    while True:
        if os.path.exists(port):
            try:
                return connect_node(port, timeout=30)
            except ProvisionError as err:
                last = str(err)
        else:
            last = f"{port} is not there"
        if time.monotonic() - started > deadline:
            raise ProvisionError(f"the node did not come back within {deadline:.0f} s: {last}")
        time.sleep(3)


def compare(iface: Any, profile: Profile) -> list[Difference]:
    """Every setting named by the profile whose value on the node differs."""
    node = iface.localNode
    diffs: list[Difference] = []
    state = node_identity(iface)
    if profile.owner is not None and state.long_name != profile.owner:
        diffs.append(Difference("owner", repr(state.long_name), repr(profile.owner)))
    if profile.owner_short is not None and state.short_name != profile.owner_short:
        diffs.append(Difference("owner_short", repr(state.short_name), repr(profile.owner_short)))
    for section, fields in profile.config.items():
        message = _section(node.localConfig, section, "config")
        diffs.extend(_walk(message, fields, f"config.{section}", apply=False))
    for section, fields in profile.module_config.items():
        message = _section(node.moduleConfig, section, "module_config")
        diffs.extend(_walk(message, fields, f"module_config.{section}", apply=False))
    for entry in profile.channels:
        channel = _channel(node, entry["index"])
        diffs.extend(_channel_walk(channel, entry, apply=False))
    return diffs


def _section(config: Any, section: str, kind: str) -> Any:
    fd = config.DESCRIPTOR.fields_by_name.get(section)
    if fd is None or fd.message_type is None:
        names = ", ".join(sorted(f.name for f in config.DESCRIPTOR.fields if f.message_type))
        raise ProvisionError(f"{kind}.{section}: no such section; sections: {names}")
    return getattr(config, section)


def _channel(node: Any, index: int) -> Any:
    channels = node.channels or []
    if index >= len(channels):
        raise ProvisionError(f"channels[{index}]: the node reported only {len(channels)} channels")
    return channels[index]


def _channel_walk(channel: Any, entry: dict[str, Any], apply: bool) -> list[Difference]:
    path = f"channels[{entry['index']}]"
    wanted: dict[str, Any] = {}
    if "role" in entry:
        wanted["role"] = entry["role"]
    if "settings" in entry:
        wanted["settings"] = entry["settings"]
    return _walk(channel, wanted, path, apply)


def apply_profile(iface: Any, profile: Profile, progress: Progress) -> list[Difference]:
    """Write every setting of the profile that differs, inside one settings
    transaction so the node reboots once, at the commit. Returns what was
    written; nothing is sent when nothing differs."""
    node = iface.localNode
    diffs = compare(iface, profile)
    if not diffs:
        return diffs
    progress.log("opening a settings transaction")
    node.beginSettingsTransaction()
    time.sleep(0.5)
    state = node_identity(iface)
    long_name = profile.owner if profile.owner is not None and profile.owner != state.long_name else None
    short_name = profile.owner_short if profile.owner_short is not None and profile.owner_short != state.short_name else None
    if long_name or short_name:
        progress.log(f"setting the owner to {long_name or state.long_name!r} / {short_name or state.short_name!r}")
        node.setOwner(long_name=long_name, short_name=short_name)
        time.sleep(0.5)
    for section, fields in profile.config.items():
        message = _section(node.localConfig, section, "config")
        changed = _walk(message, fields, f"config.{section}", apply=True)
        if changed:
            progress.log(f"writing config.{section}: " + ", ".join(str(d) for d in changed))
            node.writeConfig(section)
            time.sleep(0.5)
    for section, fields in profile.module_config.items():
        message = _section(node.moduleConfig, section, "module_config")
        changed = _walk(message, fields, f"module_config.{section}", apply=True)
        if changed:
            progress.log(f"writing module_config.{section}: " + ", ".join(str(d) for d in changed))
            node.writeConfig(section)
            time.sleep(0.5)
    for entry in profile.channels:
        channel = _channel(node, entry["index"])
        changed = _channel_walk(channel, entry, apply=True)
        if changed:
            progress.log(f"writing channel {entry['index']}: " + ", ".join(str(d) for d in changed))
            node.writeChannel(entry["index"])
            time.sleep(0.5)
    progress.log("committing: the node saves and reboots if a change needs it")
    node.commitSettingsTransaction()
    time.sleep(1)
    return diffs


def backup_settings(iface: Any, path: Path, progress: Progress) -> None:
    """Export the node's settings as the meshtastic CLI's `--export-config`
    YAML, which `meshtastic --configure` can restore."""
    from meshtastic.__main__ import export_config

    text = export_config(iface)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    progress.log(f"settings backed up to {path}")


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


@dataclass
class FlashParams:
    version: str  # e.g. `2.7.26.54e0d8d`
    # Build target, e.g. `heltec-v3`. None: taken from the running node,
    # which must then answer. Given: must match the node when one answers.
    board: str | None = None
    backup: Path | None = None


@dataclass
class RunParams:
    port: str
    profile: Path | None
    flash: FlashParams | None = None
    check_only: bool = False
    # Asked once before the flash is erased, with a summary; False aborts.
    confirm: Callable[[str], bool] = lambda summary: True


@dataclass
class RunResult:
    state: NodeIdentity | None
    written: list[Difference]
    remaining: list[Difference]

    @property
    def ok(self) -> bool:
        return not self.remaining


def run(params: RunParams, progress: Progress) -> RunResult:
    """Perform the requested steps; see the module documentation."""
    _quiet_libraries()
    profile = load_profile(params.profile) if params.profile else Profile()
    ensure_port_free(params.port)

    if params.flash:
        if params.check_only:
            raise ProvisionError("--check and --flash cannot be combined")
        # Ask the running node, if any, which board it is, before touching
        # the chip (detecting it restarts the node); back up in the same
        # connection.
        identity = probe_node(params.port, progress)
        if params.flash.backup:
            if identity is None:
                raise ProvisionError("no node answers, so its settings cannot be backed up: retry without the backup")
            iface = connect_node(params.port)
            try:
                backup_settings(iface, params.flash.backup, progress)
            finally:
                iface.close()
            time.sleep(1)
        board = board_of(identity, params.flash.board)
        progress.log(f"board: {board}" + (" (reported by the node)" if identity and identity.pio_env else " (chosen)"))
        progress.log(f"reading the manifest of {board} {params.flash.version}")
        man = manifest(params.flash.version, board)
        info = detect_chip(params.port, progress)
        check_chip(info, man)
        if identity and identity.mac and identity.mac != info.mac:
            raise ProvisionError(f"the chip on {params.port} ({info.mac}) is not the node that answered ({identity.mac})")
        summary = (
            f"Erase the whole flash of the {info.chip} on {params.port} (MAC {info.mac}) "
            f"and install {board} {params.flash.version}? "
            "The node restarts with default settings and a new private key."
        )
        if not params.confirm(summary):
            raise ProvisionError("flash cancelled")
        install_firmware(params.port, man, progress)
        progress.step(STEP_RESTART)
        iface = wait_for_node(params.port, progress, first_delay=15)
        progress.bar(0, 0)
    else:
        progress.log(f"connecting to the node on {params.port}")
        iface = connect_node(params.port)

    try:
        state = node_identity(iface)
        progress.log(f"node: {state}")
        if profile.is_empty():
            progress.log("no profile: nothing to configure")
            return RunResult(state, [], [])
        if params.check_only:
            progress.step(STEP_VERIFY)
            remaining = compare(iface, profile)
            if profile.app:
                remaining += compare_app_settings(params.port, profile.app)
            _report(remaining, progress)
            return RunResult(state, [], remaining)
        progress.step(STEP_SETTINGS)
        written = apply_profile(iface, profile, progress)
    finally:
        iface.close()

    progress.step(STEP_VERIFY)
    if written:
        # The firmware changes some settings by itself when others are set
        # (2026-09-26: setting the region for the first time forces
        # lora.ignore_mqtt on, AdminModule.cpp:841), so what still differs
        # after the reboot is written again, once.
        for attempt in (1, 2):
            iface = wait_for_node(params.port, progress, first_delay=REBOOT_GRACE_S)
            progress.bar(0, 0)
            try:
                state = node_identity(iface)
                remaining = compare(iface, profile)
                if not remaining or attempt == 2:
                    break
                progress.log(
                    f"{len(remaining)} setting(s) changed back by the firmware, writing them again: "
                    + ", ".join(d.path for d in remaining)
                )
                written += apply_profile(iface, profile, progress)
            finally:
                iface.close()
    else:
        progress.log("the node already matches the profile")
        remaining = []
    if profile.app:
        # The app last: it is told to connect to this port at start-up.
        written += apply_app_settings(params.port, profile.app, progress)
        remaining += compare_app_settings(params.port, profile.app)
    _report(remaining, progress)
    return RunResult(state, written, remaining)


def _report(remaining: list[Difference], progress: Progress) -> None:
    if remaining:
        progress.log(f"{len(remaining)} setting(s) differ from the profile:")
        for diff in remaining:
            progress.log(f"  {diff}")
    else:
        progress.log("the node matches the profile")


def _quiet_libraries() -> None:
    """The meshtastic library logs its protocol chatter at INFO."""
    logging.getLogger("meshtastic").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
