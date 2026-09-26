"""The settings form of the window (Qt): every setting of the example
profile with a widget of the right kind (text, masked text with a Show
button, checkbox, number, choice among the protobuf enum values) and a
checkbox saying whether the profile sets it; an unticked setting keeps its
value on the node and its widget is greyed.

The form is the user's view of the profile; the YAML file behind it
(`provision.DEFAULT_PROFILE`) is read when the window opens and written,
validated, before each action and when the window closes. Settings the
file holds beyond the form (a field added by hand) are kept untouched.
"""

from __future__ import annotations

import copy
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from meshtastic.protobuf import channel_pb2, config_pb2
from PySide6.QtCore import Qt
from PySide6.QtGui import QPalette
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QScrollArea,
    QSpinBox,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

import provision
from provision import ProvisionError

# The example profile, the source of a new file's defaults.
EXAMPLE_PROFILE = provision.resource_path("node-profile.example.yaml")


@dataclass(frozen=True)
class Field:
    """One setting of the form."""

    path: str  # dotted path in the profile; `channels[0]` is a list entry
    label: str
    kind: str  # `str`, `secret`, `bool`, `int` or `enum`
    help: str = ""
    choices: tuple[str, ...] = ()  # for `enum`
    maximum: int = 1_000_000_000  # for `int`


# The long explanation of each setting, shown as the tooltip of its row
# (label, value and checkbox). Written for someone who has never used
# Meshtastic.
TIPS: dict[str, str] = {
    "owner": "The node's name as other people see it in their Meshtastic apps and on the maps. "
    "Choose something that identifies you or the place, e.g. 'Anna - Rue des Lilas'.",
    "owner_short": "A short tag of up to 4 characters, shown where there is little room: map "
    "markers, the node's small screen, message bubbles. Letters, digits or an emoji.",
    "config.device.tzdef": "The node's time zone, so the times on its screen and in its logs are "
    "local. The default is France and most of Western Europe, summer time included. Only change "
    "it if the node is elsewhere.",
    "config.lora.region": "The radio band the node may legally transmit on. It depends on the "
    "country: EU_868 for France and the European Union, US for North America, and so on. A node "
    "with no region set stays silent. A wrong region breaks the law and reaches no one.",
    "config.lora.ignore_mqtt": "When ticked on, the node throws away every message that came from "
    "the Internet (MQTT). Keep it OFF: meshtastic-desktop brings the Internet's messages to the "
    "node, and with this on they are silently dropped.",
    "config.lora.config_ok_to_mqtt": "Lets other people's Internet gateways pass your node's "
    "messages on to the Internet. Keep it on so your messages travel further than your radio "
    "reaches.",
    "config.network.wifi_enabled": "Connects the node to a Wi-Fi network by itself, so it can reach "
    "the Internet without this computer. On most boards Wi-Fi turns Bluetooth off: the phone app "
    "can then no longer connect to the node over Bluetooth. USB keeps working.",
    "config.network.wifi_ssid": "The name of the Wi-Fi network the node joins, exactly as your "
    "phone shows it (upper and lower case matter). Only 2.4 GHz networks work.",
    "config.network.wifi_psk": "The password of that Wi-Fi network, at least 8 characters. It is "
    "stored on the node and in a file only you can read on this computer.",
    "module_config.mqtt.enabled": "MQTT is how Meshtastic nodes talk over the Internet, through a "
    "server called a broker. With it on, your node exchanges messages with nodes far beyond "
    "radio range.",
    "module_config.mqtt.address": "The broker's address. mqtt.meshtastic.org is the public broker "
    "run by the Meshtastic project; keep it unless your group runs its own.",
    "module_config.mqtt.username": "The broker's user name. 'meshdev' is the public broker's, "
    "shared by everyone.",
    "module_config.mqtt.password": "The broker's password. 'large4cats' is the public broker's, "
    "shared by everyone; it is not a secret.",
    "module_config.mqtt.encryption_enabled": "Sends messages to the Internet still encrypted with "
    "the channel's key, as they are over the radio. Keep it on: without it anyone reading the "
    "broker could read your messages.",
    "module_config.mqtt.root": "The broker's topic your node publishes to and listens on; it "
    "groups the nodes of one region. msh/EU_868 is Europe's. It must match the region above.",
    "module_config.mqtt.proxy_to_client_enabled": "Lets meshtastic-desktop, on this computer, carry "
    "the node's Internet traffic when the node has no Wi-Fi of its own. Keep it on: this is what "
    "links your node to the Internet through the app.",
    "module_config.mqtt.map_reporting_enabled": "Publishes your node on the public Meshtastic maps "
    "(name, board and position at the precision below), in clear, readable by anyone. Off by "
    "default for privacy.",
    "module_config.mqtt.map_report_settings.publish_interval_secs": "How often the node updates its "
    "place on the public map, in seconds. 3600 is once an hour.",
    "module_config.mqtt.map_report_settings.position_precision": "How precisely the public map "
    "shows your position, in bits: 32 is exact, 16 about 700 m, 13 about 1.5 km, 11 about 6 km. "
    "Lower is more private.",
    "channels[0].role": "The main channel, the one everybody in the region shares (LongFast). "
    "Leave it PRIMARY.",
    "channels[0].settings.uplink_enabled": "Sends the messages of this channel to the Internet "
    "(MQTT) too, so nodes elsewhere receive them. Needed to talk beyond radio range.",
    "channels[0].settings.downlink_enabled": "Lets the messages of this channel coming from the "
    "Internet (MQTT) reach your node. Needed to receive from beyond radio range.",
    "channels[0].settings.module_settings.position_precision": "How precisely your node shares its "
    "position on this channel, in bits: 32 is exact, 16 about 700 m, 13 about 1.5 km, 0 shares "
    "nothing. Everyone on the channel can see it.",
    "app.auto_connect": "meshtastic-desktop connects to this node by itself when it starts, and "
    "reconnects after the cable is unplugged and plugged back.",
    "app.mqtt_observer": "meshtastic-desktop listens to the Internet broker on its own, read only, "
    "to show the nodes it hears about in the node list and on the map, even far away.",
    "app.mqtt_observer_all_regions": "Listens to every region of the world instead of yours only. "
    "Shows many more nodes on the map; uses more of the Internet connection.",
    "app.map_world_nodes": "Shows on meshtastic-desktop's map the nodes heard through the Internet, "
    "not only those your radio heard.",
    "app.map_gateway_links": "Draws on the map a line between each node and the Internet gateway "
    "that relayed it, to see how messages travel.",
}


def _tooltip(field: Field) -> str:
    """Rich text, so Qt wraps it; the checkbox's meaning comes last."""
    body = TIPS.get(field.path, field.help)
    return (
        f"<p style='max-width: 380px'><b>{field.label}</b></p><p>{body}</p>"
        "<p><i>Ticked: written to the node. Unticked: the node keeps its own value.</i></p>"
    )


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
            Field("config.network.wifi_ssid", "Network", "str"),
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
            Field("module_config.mqtt.map_reporting_enabled", "Show this node on the public map", "bool", "position in clear, readable by anyone"),
            Field("module_config.mqtt.map_report_settings.publish_interval_secs", "Public map: update interval", "int", "seconds"),
            Field("module_config.mqtt.map_report_settings.position_precision", "Public map: position precision", "int", "1 to 32 bits", maximum=32),
        ),
    ),
    (
        "Primary channel",
        (
            Field("channels[0].role", "Role", "enum", choices=_enum_names(channel_pb2.Channel.Role.DESCRIPTOR)),
            Field("channels[0].settings.uplink_enabled", "Send to MQTT", "bool"),
            Field("channels[0].settings.downlink_enabled", "Receive from MQTT", "bool"),
            Field("channels[0].settings.module_settings.position_precision", "Position precision", "int", "bits, 13 is about 1.5 km", maximum=32),
        ),
    ),
    (
        "meshtastic-desktop (the app on this computer)",
        (
            Field("app.auto_connect", "Connect to this node at start-up", "bool", "and reconnect after a disconnect"),
            Field("app.mqtt_observer", "MQTT observer", "bool", "the app's own read-only broker connection"),
            Field("app.mqtt_observer_all_regions", "Observe every region", "bool", "nodes of every topic root on the map"),
            Field("app.map_world_nodes", "MQTT world on the map", "bool"),
            Field("app.map_gateway_links", "Gateway links on the map", "bool"),
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


class _Row:
    """The widgets of one setting."""

    def __init__(self, field: Field, present: QCheckBox, widget: QWidget) -> None:
        self.field = field
        self.present = present
        self.widget = widget

    def value(self) -> Any:
        if self.field.kind == "bool":
            return self.widget.isChecked()  # type: ignore[attr-defined]
        if self.field.kind == "enum":
            return self.widget.currentText()  # type: ignore[attr-defined]
        if self.field.kind == "int":
            return self.widget.value()  # type: ignore[attr-defined]
        return self.widget.text()  # type: ignore[attr-defined]

    def set(self, current: Any) -> None:
        self.present.setChecked(current is not None)
        self.widget.setEnabled(current is not None)
        if self.field.kind == "bool":
            self.widget.setChecked(bool(current))  # type: ignore[attr-defined]
        elif self.field.kind == "enum":
            index = self.widget.findText(str(current)) if current is not None else 0  # type: ignore[attr-defined]
            self.widget.setCurrentIndex(max(index, 0))  # type: ignore[attr-defined]
        elif self.field.kind == "int":
            self.widget.setValue(int(current) if current is not None else 0)  # type: ignore[attr-defined]
        else:
            self.widget.setText("" if current is None else str(current))  # type: ignore[attr-defined]


class ProfileForm(QWidget):
    """The form, embedded in the window. `load` fills it from the file (or
    the example for a new one), `save` validates and writes it."""

    def __init__(self, path: Path, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.path = path
        self.data: dict[str, Any] = {}
        self.rows: dict[str, _Row] = {}
        self._build()
        self.load()

    def load(self) -> None:
        source = self.path if self.path.is_file() else EXAMPLE_PROFILE
        try:
            with open(source, encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
        except (OSError, yaml.YAMLError) as err:
            raise ProvisionError(f"cannot read the saved settings ({source}): {err}") from err
        if not isinstance(data, dict):
            raise ProvisionError(f"the saved settings ({source}) are not a mapping")
        if not self.path.is_file():
            # New settings start from the example, minus its placeholders.
            for path in ("config.network.wifi_ssid", "config.network.wifi_psk"):
                _unset(data, path)
        self.data = data
        for row in self.rows.values():
            row.set(_get(data, row.field.path))

    def _build(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        note = QLabel("Ticked settings are written to the node; an unticked one keeps the node's value.")
        note.setWordWrap(True)
        outer.addWidget(note)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        # The form fits the pane's width: the help texts wrap instead.
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll = scroll
        outer.addWidget(scroll, 1)
        inner = QWidget()
        scroll.setWidget(inner)
        column = QVBoxLayout(inner)
        for group, fields in FIELDS:
            box = QGroupBox(group)
            grid = QGridLayout(box)
            grid.setColumnStretch(2, 3)
            grid.setColumnStretch(3, 2)
            for r, field in enumerate(fields):
                present = QCheckBox()
                label = QLabel(field.label)
                widget = self._widget(field)
                tip = _tooltip(field)
                for part in (present, label, widget):
                    part.setToolTip(tip)
                present.toggled.connect(widget.setEnabled)
                grid.addWidget(present, r, 0)
                grid.addWidget(label, r, 1)
                grid.addWidget(widget, r, 2)
                if field.help:
                    hint = QLabel(field.help)
                    hint.setWordWrap(True)
                    # A palette role, not a style sheet, so the grey follows
                    # the light/dark switch.
                    hint.setForegroundRole(QPalette.ColorRole.PlaceholderText)
                    grid.addWidget(hint, r, 3)
                self.rows[field.path] = _Row(field, present, widget)
            column.addWidget(box)
        column.addStretch(1)

    def _widget(self, field: Field) -> QWidget:
        if field.kind == "bool":
            return QCheckBox()
        if field.kind == "enum":
            combo = QComboBox()
            combo.addItems(field.choices)
            return combo
        if field.kind == "int":
            spin = QSpinBox()
            spin.setRange(0, field.maximum)
            return spin
        if field.kind == "secret":
            return _SecretEdit()
        edit = QLineEdit()
        edit.setClearButtonEnabled(True)
        edit.setMinimumWidth(120)
        return edit

    def collect(self) -> dict[str, Any]:
        """The profile as the form shows it; raises on an empty text."""
        data = copy.deepcopy(self.data)  # keeps the file's key order
        for row in self.rows.values():
            field = row.field
            if not row.present.isChecked():
                _unset(data, field.path)
                continue
            value = row.value()
            if field.kind in ("str", "secret"):
                value = str(value).strip()
                if not value:
                    raise ProvisionError(f"{field.label}: empty; untick it to leave the node's value")
            _set(data, field.path, value)
        return data

    def save(self) -> Path:
        """Validate the form exactly as a run would, then write it. Returns
        the file the actions read."""
        data = self.collect()
        text = "# Settings written by meshtastic-provision's window. Field names are\n"
        text += "# those of Meshtastic's protobufs; see node-profile.example.yaml.\n"
        text += yaml.safe_dump(data, sort_keys=False, allow_unicode=True)
        with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False, encoding="utf-8") as tmp:
            tmp.write(text)
        try:
            provision.load_profile(Path(tmp.name))
        except ProvisionError as err:
            raise ProvisionError(str(err).replace(f"profile {tmp.name}: ", "")) from None
        finally:
            Path(tmp.name).unlink()
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(text, encoding="utf-8")
            self.path.chmod(0o600)  # it holds the Wi-Fi and broker passwords
        except OSError as err:
            raise ProvisionError(f"cannot save the settings: {err}") from err
        self.data = data
        return self.path


class _SecretEdit(QWidget):
    """A masked line edit with a Show button."""

    def __init__(self) -> None:
        super().__init__()
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.edit = QLineEdit()
        self.edit.setEchoMode(QLineEdit.EchoMode.Password)
        show = QToolButton()
        show.setText("Show")
        show.setCheckable(True)
        show.toggled.connect(
            lambda on: self.edit.setEchoMode(QLineEdit.EchoMode.Normal if on else QLineEdit.EchoMode.Password)
        )
        layout.addWidget(self.edit, 1)
        layout.addWidget(show)
        self.setFocusProxy(self.edit)

    def text(self) -> str:
        return self.edit.text()

    def setText(self, text: str) -> None:  # noqa: N802 - Qt naming, as QLineEdit
        self.edit.setText(text)
