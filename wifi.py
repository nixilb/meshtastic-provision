"""The Wi-Fi networks this computer sees, to pick the node's network.

Read from NetworkManager (`nmcli`, on Ubuntu, Debian and Raspberry Pi OS);
a scan takes a few seconds, so callers run it off the GUI thread. ESP32
nodes join 2.4 GHz networks only: a network seen on 5 GHz only is flagged.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass

from provision import ProvisionError


@dataclass(frozen=True)
class Network:
    ssid: str
    signal: int  # 0 to 100, the best access point of the network
    band_24: bool  # seen on 2.4 GHz, so a node can join it
    secured: bool

    def __str__(self) -> str:
        lock = "" if self.secured else ", open"
        return f"{self.ssid}  ({self.signal} %{lock})"


def _fields(line: str) -> list[str]:
    """Split a `nmcli -t` line: ':' separates fields, '\\:' is a colon
    inside one, '\\\\' a backslash."""
    fields, current, escaped = [], [], False
    for char in line:
        if escaped:
            current.append(char)
            escaped = False
        elif char == "\\":
            escaped = True
        elif char == ":":
            fields.append("".join(current))
            current = []
        else:
            current.append(char)
    fields.append("".join(current))
    return fields


def scan() -> list[Network]:
    """The named networks around, strongest first, one entry per name."""
    if shutil.which("nmcli") is None:
        raise ProvisionError("cannot list Wi-Fi networks: NetworkManager (nmcli) is not installed")
    try:
        result = subprocess.run(
            ["nmcli", "-t", "-f", "SSID,SIGNAL,FREQ,SECURITY", "dev", "wifi", "list", "--rescan", "auto"],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
    except subprocess.TimeoutExpired as err:
        raise ProvisionError("the Wi-Fi scan took too long") from err
    if result.returncode != 0:
        raise ProvisionError(f"Wi-Fi scan: {(result.stderr or result.stdout).strip()}")
    networks: dict[str, Network] = {}
    for line in result.stdout.splitlines():
        parts = _fields(line)
        if len(parts) < 4 or not parts[0]:
            continue  # hidden networks have no name
        ssid, signal, freq, security = parts[0], parts[1], parts[2], parts[3]
        try:
            strength = int(signal)
            mhz = int(freq.split()[0])
        except (ValueError, IndexError):
            continue
        seen = networks.get(ssid)
        networks[ssid] = Network(
            ssid=ssid,
            signal=max(strength, seen.signal if seen else 0),
            band_24=mhz < 3000 or bool(seen and seen.band_24),
            secured=bool(security.strip()) or bool(seen and seen.secured),
        )
    return sorted(networks.values(), key=lambda n: (-n.signal, n.ssid.lower()))
