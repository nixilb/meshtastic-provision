"""Command line of meshtastic-provision: flash a node and/or apply a
settings profile, printing each step. See README.md for the options.

    uv run provision_cli.py --port /dev/ttyUSB0 --profile node-profile.yaml --check
    uv run provision_cli.py --port /dev/ttyUSB0 --board heltec-v3 --flash 2.7.26.54e0d8d \\
        --profile node-profile.yaml [--backup before.yaml] [--yes]

Exit code 0 when the node matches the profile at the end, 1 when settings
still differ or a step failed, 2 for a usage error.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import provision
from provision import FlashParams, Progress, ProvisionError, RunParams


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Flash a Meshtastic node over USB and apply a settings profile.")
    parser.add_argument("--port", help="serial port of the node, e.g. /dev/ttyUSB0")
    parser.add_argument("--list-ports", action="store_true", help="list the USB serial ports and exit")
    parser.add_argument("--list-boards", action="store_true", help="list the ESP32 boards that can be installed and exit")
    parser.add_argument("--list-versions", action="store_true", help="list the published firmware versions and exit")
    parser.add_argument(
        "--detect",
        action="store_true",
        help="ask the node on the port which board it is, read the chip (family, MAC, flash size) and exit",
    )
    parser.add_argument(
        "--profile",
        type=Path,
        help=f"YAML profile to apply (default: {provision.DEFAULT_PROFILE} when it exists)",
    )
    parser.add_argument("--check", action="store_true", help="only report the settings that differ from the profile")
    parser.add_argument("--flash", metavar="VERSION", help="erase the flash and install this firmware version first")
    parser.add_argument(
        "--board",
        help="build target of the board to install, e.g. heltec-v3 (with --flash); "
        "taken from the running node when omitted, which must then answer",
    )
    parser.add_argument("--backup", type=Path, metavar="FILE", help="export the node's settings to FILE before the flash")
    parser.add_argument("--yes", action="store_true", help="do not ask before erasing the flash")
    args = parser.parse_args(argv)

    progress = Progress(log=lambda text: print(text, flush=True), bar=_bar)
    try:
        if args.list_ports:
            for port in provision.serial_ports():
                print(port)
            return 0
        if args.list_boards:
            for board in provision.boards():
                print(board)
            return 0
        if args.list_versions:
            for release in provision.releases():
                print(release)
            return 0
        if not args.port:
            parser.error("--port is required")
        _ensure_access(args.port, assume_yes=args.yes)
        if args.detect:
            provision.probe_node(args.port, progress)
            provision.detect_chip(args.port, progress)
            return 0

        profile = args.profile
        if profile is None and provision.DEFAULT_PROFILE.is_file():
            profile = provision.DEFAULT_PROFILE
        flash = None
        if args.flash:
            flash = FlashParams(version=args.flash, board=args.board, backup=args.backup)
        elif args.board or args.backup:
            parser.error("--board and --backup only make sense with --flash")
        if profile is None and flash is None:
            parser.error("nothing to do: give --profile and/or --flash")

        params = RunParams(
            port=args.port,
            profile=profile,
            flash=flash,
            check_only=args.check,
            confirm=(lambda summary: True) if args.yes else _ask,
        )
        result = provision.run(params, progress)
        return 0 if result.ok else 1
    except ProvisionError as err:
        print(f"error: {err}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return 1


def _ensure_access(port: str, assume_yes: bool) -> None:
    """The port must be openable. Offers the udev rule (sudo), which applies
    at once; then the port's group, which needs a restart under it."""
    if not os.path.exists(port):
        return  # reported later, with the plug-in hint
    try:
        provision.check_port_access(port)
        return
    except provision.PortAccessError as err:
        first = err
    print(f"{port} cannot be opened by your user.")
    if not assume_yes:
        answer = input("Allow the logged-in user to use USB serial ports (udev rule, sudo)? Type 'yes': ")
        if answer.strip().lower() != "yes":
            raise first
    provision.install_udev_rule(graphical=False)
    try:
        provision.check_port_access(port)
        print("USB serial ports allowed for the logged-in user")
        return
    except provision.PortAccessError:
        print("the udev rule did not open the port: falling back to the group")
    if not first.granted:
        if not assume_yes:
            answer = input(f"Add you to the group {first.group} now with sudo? Type 'yes': ")
            if answer.strip().lower() != "yes":
                raise first
        provision.add_user_to_group(first.group, graphical=False)
        print(f"you are now in the group {first.group}")
    print(f"restarting with the group {first.group} active", flush=True)
    provision.relaunch_with_group(first.group)  # replaces this process


def _ask(summary: str) -> bool:
    """Confirmation before the erase, on the terminal."""
    print(summary)
    try:
        answer = input("Type 'yes' to continue: ")
    except EOFError:
        return False
    return answer.strip().lower() == "yes"


_last_bar = ""


def _bar(done: int, total: int, label: str) -> None:
    """A one-line progress bar, redrawn in place, cleared when total is 0."""
    global _last_bar
    if total <= 0:
        if _last_bar:
            print("\r" + " " * len(_last_bar) + "\r", end="", flush=True)
            _last_bar = ""
        return
    width = 30
    filled = min(width, done * width // total)
    line = f"{label[:30]:<30} [{'#' * filled}{'.' * (width - filled)}] {100 * done // total:3d}%"
    print("\r" + line, end="", flush=True)
    _last_bar = line
    if done >= total:
        print(flush=True)
        _last_bar = ""


if __name__ == "__main__":
    sys.exit(main())
