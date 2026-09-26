"""Named time zones for the settings form.

A Meshtastic node stores its time zone as a POSIX rule
(`config.device.tzdef`, e.g. `CET-1CEST,M3.5.0,M10.5.0/3`), which nobody can
read. The form shows IANA names (`Europe/Paris`) instead and converts with
the system's time zone database: every TZif file ends with the POSIX rule
of its zone (RFC 8536, section 3.3, the footer), which is what the node
needs.
"""

from __future__ import annotations

import os
import zoneinfo
from functools import cache
from pathlib import Path


@cache
def names() -> tuple[str, ...]:
    """The IANA zone names of the system's database, sorted, without the
    legacy aliases (`Etc/...`, `US/...`, single words like `GMT`)."""
    return tuple(
        sorted(
            name
            for name in zoneinfo.available_timezones()
            if "/" in name and not name.startswith(("Etc/", "US/", "SystemV/", "posix/", "right/"))
        )
    )


def _tzif(name: str) -> Path | None:
    for base in zoneinfo.TZPATH:
        path = Path(base) / name
        if path.is_file():
            return path
    return None


@cache
def posix(name: str) -> str | None:
    """The POSIX rule of zone `name`, read from its TZif file's footer;
    None when the zone or its footer is missing."""
    path = _tzif(name)
    if path is None:
        return None
    data = path.read_bytes()
    if not data.startswith(b"TZif") or not data.endswith(b"\n"):
        return None
    footer = data[data.rstrip(b"\n").rfind(b"\n") + 1 :].strip()
    return footer.decode("ascii") or None


def local_name() -> str | None:
    """The computer's own zone (`/etc/localtime` or `$TZ`), when named."""
    tz = os.environ.get("TZ", "").lstrip(":")
    if tz in names():
        return tz
    try:
        target = os.path.realpath("/etc/localtime")
    except OSError:
        return None
    for base in zoneinfo.TZPATH:
        prefix = os.path.realpath(base) + os.sep
        if target.startswith(prefix):
            name = target[len(prefix) :]
            return name if name in names() else None
    return None


def name_for(rule: str) -> str | None:
    """A zone whose POSIX rule is `rule`: the computer's own when it
    matches (so France shows Europe/Paris, not Europe/Amsterdam), else the
    first by name; None when no zone has that rule."""
    local = local_name()
    if local and posix(local) == rule:
        return local
    return next((name for name in names() if posix(name) == rule), None)
