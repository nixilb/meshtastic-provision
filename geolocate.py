"""This computer's position, for the "My position" button of the form.

Two free sources, tried in order, the result always saying which one
answered and how precise it is, so the user knows whether to correct it:

1. Wi-Fi: the access points NetworkManager sees, looked up by BeaconDB
   (the crowd-sourced successor of Mozilla Location Service; tens of
   metres where its volunteers have mapped the networks, nothing
   elsewhere). Access points that opted out (`_nomap`) and randomised
   addresses (mobile hotspots) are never sent, as in meshtastic-desktop's
   positon.rs.
2. The public IP address, looked up by ipwho.is: the town, at best.

meshtastic-desktop tries Positon first; its test key is refused
(2026-09-26, HTTP 403), so it is not used here.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass

import requests

from provision import USER_AGENT, ProvisionError
from wifi import _fields

BEACONDB_URL = "https://api.beacondb.net/v1/geolocate"
IP_URL = "https://ipwho.is/"
TIMEOUT_S = 15


@dataclass(frozen=True)
class Fix:
    latitude: float
    longitude: float
    accuracy_m: float | None  # None: unknown (town level)
    source: str  # shown to the user

    def describe(self) -> str:
        if self.accuracy_m is None:
            return f"from {self.source}: the town only, check it"
        if self.accuracy_m >= 1000:
            return f"from {self.source}, within {self.accuracy_m / 1000:.0f} km: check it"
        return f"from {self.source}, within {self.accuracy_m:.0f} m"


def _access_points() -> list[dict[str, object]]:
    if shutil.which("nmcli") is None:
        return []
    result = subprocess.run(
        ["nmcli", "-t", "-f", "BSSID,SSID,SIGNAL", "dev", "wifi", "list", "--rescan", "no"],
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    points = []
    for line in result.stdout.splitlines():
        parts = _fields(line)
        if len(parts) < 3:
            continue
        bssid, ssid, signal = parts[0].lower(), parts[1], parts[2]
        try:
            first_octet = int(bssid.split(":")[0], 16)
            strength = int(signal)
        except ValueError:
            continue
        if ssid.endswith("_nomap") or first_octet & 0x02:
            continue
        # nmcli gives 0-100 %; the API wants dBm (the usual rough mapping).
        points.append({"macAddress": bssid, "signalStrength": strength // 2 - 100})
    return points


def _by_wifi(session: requests.Session) -> Fix | None:
    points = _access_points()
    if len(points) < 2:  # the service needs two to locate
        return None
    response = session.post(
        BEACONDB_URL, json={"wifiAccessPoints": points, "considerIp": False}, timeout=TIMEOUT_S
    )
    if response.status_code == 404:  # none of these networks is known
        return None
    response.raise_for_status()
    data = response.json()
    return Fix(data["location"]["lat"], data["location"]["lng"], float(data.get("accuracy", 0)) or None, "the Wi-Fi networks around")


def _by_ip(session: requests.Session) -> Fix:
    response = session.get(IP_URL, timeout=TIMEOUT_S)
    response.raise_for_status()
    data = response.json()
    if not data.get("success", True):
        raise ProvisionError(f"locating by Internet address: {data.get('message', 'refused')}")
    return Fix(float(data["latitude"]), float(data["longitude"]), None, "your Internet address")


def locate() -> Fix:
    session = requests.Session()
    session.headers["User-Agent"] = USER_AGENT
    try:
        return _by_wifi(session) or _by_ip(session)
    except (requests.RequestException, KeyError, ValueError) as err:
        raise ProvisionError(f"could not find this computer's position: {err}") from err


# Coordinates in a Google Maps link, most precise first: the place's pin
# (`!3d<lat>!4d<lon>` in the data part), a search or query
# (`q=`/`query=`/`ll=`), then the map's centre (`@<lat>,<lon>,<zoom>z`).
_COORDINATE_PATTERNS = (
    r"!3d(-?\d+(?:\.\d+)?)!4d(-?\d+(?:\.\d+)?)",
    r"[?&](?:q|query|ll|center|destination)=(-?\d+(?:\.\d+)?)(?:,|%2C)\s*(-?\d+(?:\.\d+)?)",
    r"/search/(-?\d+(?:\.\d+)?),\+?(-?\d+(?:\.\d+)?)",
    r"@(-?\d+(?:\.\d+)?),(-?\d+(?:\.\d+)?)",
)
# Short links, which must be followed to reach the coordinates.
_SHORT_HOSTS = ("maps.app.goo.gl", "goo.gl")


def coordinates_in(url: str) -> tuple[float, float] | None:
    """The latitude and longitude a Google Maps link points at, or None
    when it holds none (a short link, a place name only)."""
    import re
    from urllib.parse import unquote

    # A link through Google's consent page carries the real one encoded in
    # its `continue=` parameter: decode before searching.
    text = unquote(unquote(url))
    for pattern in _COORDINATE_PATTERNS:
        match = re.search(pattern, text)
        if match:
            latitude, longitude = float(match.group(1)), float(match.group(2))
            if -90 <= latitude <= 90 and -180 <= longitude <= 180:
                return latitude, longitude
    return None


def is_short_link(url: str) -> bool:
    from urllib.parse import urlparse

    return urlparse(url.strip()).hostname in _SHORT_HOSTS


def follow(url: str) -> str:
    """The full link a short one leads to (network)."""
    session = requests.Session()
    session.headers["User-Agent"] = USER_AGENT
    try:
        response = session.get(url.strip(), allow_redirects=True, timeout=TIMEOUT_S)
    except requests.RequestException as err:
        raise ProvisionError(f"could not open the link: {err}") from err
    return response.url
