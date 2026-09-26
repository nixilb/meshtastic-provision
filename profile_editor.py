"""Form to fill a profile without editing YAML by hand: a Tkinter dialog
listing every setting of the example profile with a widget of the right
kind (text, masked text, checkbox, number, choice among the protobuf enum
values), reading the existing file and writing it back validated.

Settings the file holds beyond the form (a field added by hand) are kept
untouched; the form only sets its own paths. The dialog is opened from the
main window (`provision_gui.py`).
"""

from __future__ import annotations

import copy
import tempfile
import tkinter as tk
from dataclasses import dataclass
from pathlib import Path
from tkinter import messagebox, ttk
from typing import Any, Callable

import yaml
from meshtastic.protobuf import channel_pb2, config_pb2

import provision
from provision import ProvisionError

# The example profile, the source of a new file's defaults.
EXAMPLE_PROFILE = Path(__file__).with_name("node-profile.example.yaml")


@dataclass(frozen=True)
class Field:
    """One setting of the form."""

    path: str  # dotted path in the profile; `channels[0]` is a list entry
    label: str
    kind: str  # `str`, `secret`, `bool`, `int` or `enum`
    help: str = ""
    choices: tuple[str, ...] = ()  # for `enum`


def _enum_names(descriptor: Any) -> tuple[str, ...]:
    return tuple(v.name for v in descriptor.values)


FIELDS: tuple[tuple[str, tuple[Field, ...]], ...] = (
    (
        "Node",
        (
            Field("owner", "Name", "str", "long name, shown in the mesh"),
            Field("owner_short", "Short name", "str", "4 characters at most"),
            Field("config.device.tzdef", "Time zone", "str", "POSIX form, e.g. CET-1CEST,M3.5.0,M10.5.0/3"),
        ),
    ),
    (
        "LoRa",
        (
            Field("config.lora.region", "Region", "enum", choices=_enum_names(config_pb2.Config.LoRaConfig.RegionCode.DESCRIPTOR)),
            Field("config.lora.ignore_mqtt", "Ignore packets from MQTT", "bool", "must be off for the app's MQTT proxy"),
            Field("config.lora.config_ok_to_mqtt", "Allow forwarding to MQTT", "bool"),
        ),
    ),
    (
        "Wi-Fi",
        (
            Field("config.network.wifi_enabled", "Wi-Fi on", "bool", "on an ESP32, Wi-Fi turns Bluetooth off"),
            Field("config.network.wifi_ssid", "SSID", "str"),
            Field("config.network.wifi_psk", "Password", "secret", "8 characters or more"),
        ),
    ),
    (
        "MQTT",
        (
            Field("module_config.mqtt.enabled", "MQTT on", "bool"),
            Field("module_config.mqtt.address", "Broker", "str", "mqtt.meshtastic.org is the public one"),
            Field("module_config.mqtt.username", "User", "str"),
            Field("module_config.mqtt.password", "Password", "secret"),
            Field("module_config.mqtt.encryption_enabled", "Encrypted packets", "bool"),
            Field("module_config.mqtt.root", "Topic root", "str", "e.g. msh/EU_868"),
            Field("module_config.mqtt.proxy_to_client_enabled", "Proxy through the app", "bool", "the app connects to the broker for the node"),
            Field("module_config.mqtt.map_reporting_enabled", "Map reports", "bool", "publishes the position in clear"),
            Field("module_config.mqtt.map_report_settings.publish_interval_secs", "Map report interval (s)", "int"),
            Field("module_config.mqtt.map_report_settings.position_precision", "Map report precision", "int", "1 to 32 bits"),
        ),
    ),
    (
        "Primary channel",
        (
            Field("channels[0].role", "Role", "enum", choices=_enum_names(channel_pb2.Channel.Role.DESCRIPTOR)),
            Field("channels[0].settings.uplink_enabled", "Send to MQTT", "bool"),
            Field("channels[0].settings.downlink_enabled", "Receive from MQTT", "bool"),
            Field("channels[0].settings.module_settings.position_precision", "Position precision", "int", "bits, 13 is about 1.5 km"),
        ),
    ),
)


def _split(path: str) -> list[str | int]:
    """`channels[0].settings.x` -> ["channels", 0, "settings", "x"]."""
    keys: list[str | int] = []
    for part in path.split("."):
        if part.endswith("]"):
            name, index = part[:-1].split("[")
            keys.extend([name, int(index)])
        else:
            keys.append(part)
    return keys


def _get(data: dict[str, Any], path: str) -> Any:
    node: Any = data
    for key in _split(path):
        if isinstance(key, int):
            entries = node if isinstance(node, list) else []
            node = next((e for e in entries if isinstance(e, dict) and e.get("index") == key), None)
        elif isinstance(node, dict):
            node = node.get(key)
        else:
            node = None
        if node is None:
            return None
    return node


def _set(data: dict[str, Any], path: str, value: Any) -> None:
    node: Any = data
    keys = _split(path)
    for key, following in zip(keys[:-1], keys[1:]):
        if isinstance(key, int):
            entry = next((e for e in node if isinstance(e, dict) and e.get("index") == key), None)
            if entry is None:
                entry = {"index": key}
                node.append(entry)
            node = entry
        else:
            if key not in node or node[key] is None:
                node[key] = [] if isinstance(following, int) else {}
            node = node[key]
    node[keys[-1]] = value


def _unset(data: dict[str, Any], path: str) -> None:
    """Remove `path` and the mappings it leaves empty."""
    chain: list[tuple[Any, str | int]] = []  # (container, key) down to the leaf
    node: Any = data
    for key in _split(path):
        if isinstance(key, int):
            if not isinstance(node, list):
                return
            entry = next((e for e in node if isinstance(e, dict) and e.get("index") == key), None)
            if entry is None:
                return
            chain.append((node, node.index(entry)))
            node = entry
        else:
            if not isinstance(node, dict) or key not in node:
                return
            chain.append((node, key))
            node = node[key]
    container, key = chain[-1]
    del container[key]
    for container, key in reversed(chain[:-1]):
        child = container[key]
        empty = child in ({}, []) or (isinstance(child, dict) and set(child) == {"index"})
        if not empty:
            break
        del container[key]


class ProfileEditor(tk.Toplevel):
    """The dialog. `on_saved(path)` is called after a successful write."""

    def __init__(self, parent: tk.Misc, path: Path, on_saved: Callable[[Path], None]) -> None:
        super().__init__(parent)
        self.title(f"Profile: {path}")
        self.path = path
        self.on_saved = on_saved
        self.data = self._load()
        self.vars: dict[str, tk.Variable] = {}
        self.present: dict[str, tk.BooleanVar] = {}
        self._build()
        self.transient(parent)
        self.grab_set()

    def _load(self) -> dict[str, Any]:
        source = self.path if self.path.is_file() else EXAMPLE_PROFILE
        try:
            with open(source, encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
        except (OSError, yaml.YAMLError) as err:
            messagebox.showerror("Profile", f"cannot read {source}: {err}", parent=self)
            data = {}
        if not self.path.is_file():
            # A new file starts from the example, minus its placeholders.
            for path in ("config.network.wifi_ssid", "config.network.wifi_psk"):
                _unset(data, path)
        return data if isinstance(data, dict) else {}

    def _build(self) -> None:
        note = ttk.Label(
            self,
            text="Tick a setting to write it to the profile; an unticked one is left as it is on the node.",
            wraplength=640,
        )
        note.grid(row=0, column=0, columnspan=4, sticky="w", padx=8, pady=8)
        canvas = tk.Canvas(self, highlightthickness=0, width=680, height=520)
        scrollbar = ttk.Scrollbar(self, orient="vertical", command=canvas.yview)
        inner = ttk.Frame(canvas)
        inner.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=inner, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.grid(row=1, column=0, columnspan=3, sticky="nsew")
        scrollbar.grid(row=1, column=3, sticky="ns")
        self.rowconfigure(1, weight=1)
        self.columnconfigure(0, weight=1)
        inner.columnconfigure(2, weight=1)

        row = 0
        for group, fields in FIELDS:
            ttk.Label(inner, text=group, font=("TkDefaultFont", 10, "bold")).grid(
                row=row, column=0, columnspan=4, sticky="w", padx=8, pady=(12, 2)
            )
            row += 1
            for field in fields:
                current = _get(self.data, field.path)
                present = tk.BooleanVar(value=current is not None)
                self.present[field.path] = present
                ttk.Checkbutton(inner, variable=present).grid(row=row, column=0, sticky="w", padx=(8, 0))
                ttk.Label(inner, text=field.label).grid(row=row, column=1, sticky="w", padx=4)
                widget = self._widget(inner, field, current)
                widget.grid(row=row, column=2, sticky="ew", padx=4, pady=2)
                if field.help:
                    ttk.Label(inner, text=field.help, foreground="#666").grid(row=row, column=3, sticky="w", padx=(4, 8))
                row += 1

        buttons = ttk.Frame(self)
        buttons.grid(row=2, column=0, columnspan=4, sticky="e", padx=8, pady=8)
        ttk.Button(buttons, text="Cancel", command=self.destroy).pack(side="right", padx=(8, 0))
        ttk.Button(buttons, text="Save", command=self._save).pack(side="right")

    def _widget(self, parent: tk.Misc, field: Field, current: Any) -> tk.Widget:
        if field.kind == "bool":
            var = tk.BooleanVar(value=bool(current))
            self.vars[field.path] = var
            return ttk.Checkbutton(parent, variable=var)
        if field.kind == "enum":
            var = tk.StringVar(value=str(current) if current is not None else field.choices[0])
            self.vars[field.path] = var
            return ttk.Combobox(parent, textvariable=var, values=field.choices, state="readonly")
        var = tk.StringVar(value="" if current is None else str(current))
        self.vars[field.path] = var
        return ttk.Entry(parent, textvariable=var, show="*" if field.kind == "secret" else "")

    def _collect(self) -> dict[str, Any]:
        data = copy.deepcopy(self.data)  # keeps the file's key order
        for _group, fields in FIELDS:
            for field in fields:
                if not self.present[field.path].get():
                    _unset(data, field.path)
                    continue
                raw = self.vars[field.path].get()
                if field.kind == "bool":
                    value: Any = bool(raw)
                elif field.kind == "int":
                    try:
                        value = int(str(raw).strip())
                    except ValueError:
                        raise ProvisionError(f"{field.label}: expected a whole number, got {raw!r}") from None
                else:
                    value = str(raw).strip()
                    if not value:
                        raise ProvisionError(f"{field.label}: empty; untick it to leave the node's value")
                _set(data, field.path, value)
        return data

    def _save(self) -> None:
        try:
            data = self._collect()
            text = "# Profile written by meshtastic-provision's editor. Field names are\n"
            text += "# those of Meshtastic's protobufs; see node-profile.example.yaml.\n"
            text += yaml.safe_dump(data, sort_keys=False, allow_unicode=True)
            # Validate exactly as a run would, before touching the file.
            with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False, encoding="utf-8") as tmp:
                tmp.write(text)
            try:
                provision.load_profile(Path(tmp.name))
            finally:
                Path(tmp.name).unlink()
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(text, encoding="utf-8")
            self.path.chmod(0o600)  # it holds the Wi-Fi and broker passwords
        except ProvisionError as err:
            messagebox.showerror("Profile", str(err).replace(f"profile {self.path}: ", ""), parent=self)
            return
        except OSError as err:
            messagebox.showerror("Profile", f"cannot write {self.path}: {err}", parent=self)
            return
        self.on_saved(self.path)
        self.destroy()
