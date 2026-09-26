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
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from meshtastic.protobuf import channel_pb2, config_pb2
from PySide6.QtCore import QEvent, QObject, Qt, Signal
from PySide6.QtGui import QPalette
from PySide6.QtWidgets import (
    QAbstractScrollArea,
    QApplication,
    QCheckBox,
    QComboBox,
    QCompleter,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

import provision
import timezones
import wifi
from provision import ProvisionError

# The example profile, the source of a new file's defaults.
EXAMPLE_PROFILE = provision.resource_path("node-profile.example.yaml")


@dataclass(frozen=True)
class Field:
    """One setting of the form."""

    path: str  # dotted path in the profile; `channels[0]` is a list entry
    label: str
    kind: str  # `str`, `secret`, `bool`, `int`, `enum`, `timezone` or `ssid`
    help: str = ""
    choices: tuple[str, ...] = ()  # for `enum`
    minimum: int = 0  # for `int`
    # For `int`. 2026-09-26: the firmware turns some intervals into
    # milliseconds in a uint32 (clamped), so about 49.7 days at most.
    maximum: int = 1_000_000_000


# The long explanation of each setting, shown as the tooltip of its row
# (label, value and checkbox). Written for someone who has never used
# Meshtastic.
TIPS: dict[str, str] = {
    "owner": "The node's name as other people see it in their Meshtastic apps and on the maps. "
    "Choose something that identifies you or the place, e.g. 'Anna - Rue des Lilas'.",
    "owner_short": "A short tag of up to 4 characters, shown where there is little room: map "
    "markers, the node's small screen, message bubbles. Letters, digits or an emoji.",
    "config.device.tzdef": "The node's time zone, so the times on its screen and in its logs are "
    "local, summer time included. Choose the zone of the place where the node is, named after its "
    "largest city (Europe/Paris for France). Type part of the name to find it.",
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
    "(name, board and position at the precision below), in clear, readable by anyone. On by "
    "default; lower the precision below to show only the area.",
    "module_config.mqtt.map_report_settings.publish_interval_secs": "How often the node updates its "
    "place on the public map, in seconds: 86400 is once a day, the default. The node also reports "
    "at every start-up. The firmware allows one hour (3600) to about 49 days.",
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
            Field("config.device.tzdef", "Time zone", "timezone", "type a city to search"),
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
            Field("config.network.wifi_ssid", "Network", "ssid", "type it, or pick a nearby one"),
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
            Field("module_config.mqtt.map_report_settings.publish_interval_secs", "Public map: update interval", "int", "seconds, 86400 = a day", minimum=3600, maximum=4_294_967),
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
        if self.field.kind == "timezone":
            return self.widget.rule()  # type: ignore[attr-defined]
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
        elif self.field.kind == "timezone":
            self.widget.set_rule(None if current is None else str(current))  # type: ignore[attr-defined]
        else:
            self.widget.setText("" if current is None else str(current))  # type: ignore[attr-defined]


class ProfileForm(QWidget):
    """The form, embedded in the window. `load` fills it from the file (or
    the example for a new one); the Save button validates and writes it.
    Nothing is saved by itself: `dirty` tells whether the form holds
    changes the file does not have."""

    changed = Signal()

    def __init__(self, path: Path, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.path = path
        self.data: dict[str, Any] = {}
        self.rows: dict[str, _Row] = {}
        self.dirty = False
        self._loading = False
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
        self._loading = True
        try:
            for row in self.rows.values():
                row.set(_get(data, row.field.path))
        finally:
            self._loading = False
        self._set_dirty(not self.path.is_file())  # a new file is not saved yet

    def _touched(self) -> None:
        if not self._loading:
            self._set_dirty(True)

    def _set_dirty(self, dirty: bool) -> None:
        self.dirty = dirty
        self.save_button.setEnabled(dirty)
        self.status.setText("Unsaved changes" if dirty else "Saved")
        self.status.setForegroundRole(
            QPalette.ColorRole.WindowText if dirty else QPalette.ColorRole.PlaceholderText
        )
        self.changed.emit()

    def save_clicked(self) -> bool:
        """The Save button: validate and write; an invalid entry is named
        and nothing is written. Returns whether it saved."""
        try:
            self.save()
        except ProvisionError as err:
            QMessageBox.warning(self, "Settings", str(err))
            return False
        return True

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
                present.toggled.connect(self._touched)
                self._watch(field, widget)
            column.addWidget(box)
        column.addStretch(1)

        bottom = QHBoxLayout()
        self.status = QLabel("")
        bottom.addWidget(self.status, 1)
        self.save_button = QPushButton("Save")
        self.save_button.clicked.connect(self.save_clicked)
        bottom.addWidget(self.save_button)
        outer.addLayout(bottom)

    def _watch(self, field: Field, widget: QWidget) -> None:
        """Mark the form dirty on any edit of `widget`."""
        if field.kind == "bool":
            widget.toggled.connect(self._touched)  # type: ignore[attr-defined]
        elif field.kind == "enum":
            widget.currentIndexChanged.connect(self._touched)  # type: ignore[attr-defined]
        elif field.kind == "int":
            widget.valueChanged.connect(self._touched)  # type: ignore[attr-defined]
        elif field.kind in ("secret", "ssid"):
            widget.edit.textChanged.connect(self._touched)  # type: ignore[attr-defined]
        elif field.kind == "timezone":
            widget.currentTextChanged.connect(self._touched)  # type: ignore[attr-defined]
        else:
            widget.textChanged.connect(self._touched)  # type: ignore[attr-defined]

    def _widget(self, field: Field) -> QWidget:
        if field.kind == "bool":
            return QCheckBox()
        if field.kind == "enum":
            combo = QComboBox()
            combo.addItems(field.choices)
            _no_wheel(combo)
            return combo
        if field.kind == "int":
            spin = QSpinBox()
            spin.setRange(field.minimum, field.maximum)
            _no_wheel(spin)
            return spin
        if field.kind == "secret":
            return _SecretEdit()
        if field.kind == "timezone":
            box = _TimezoneBox()
            _no_wheel(box)
            return box
        if field.kind == "ssid":
            return _SsidEdit()
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
            if field.kind in ("str", "secret", "ssid"):
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
        self._set_dirty(False)
        return self.path


class _SsidEdit(QWidget):
    """The Wi-Fi network's name: typed, or picked from the networks this
    computer sees (a menu filled by a scan in the background, a few
    seconds). Networks seen on 5 GHz only are shown greyed: the node
    cannot join them."""

    scanned = Signal(object)  # list[wifi.Network] or the error text

    def __init__(self) -> None:
        super().__init__()
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.edit = QLineEdit()
        self.edit.setClearButtonEnabled(True)
        self.edit.setMinimumWidth(120)
        self.nearby = QToolButton()
        self.nearby.setText("Nearby")
        self.nearby.setToolTip("Networks this computer sees; the node needs a 2.4 GHz one")
        self.nearby.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.menu = QMenu(self.nearby)
        self.menu.aboutToShow.connect(self._scan)
        self.nearby.setMenu(self.menu)
        self.scanned.connect(self._fill)
        self._scanning = False
        layout.addWidget(self.edit, 1)
        layout.addWidget(self.nearby)
        self.setFocusProxy(self.edit)

    def text(self) -> str:
        return self.edit.text()

    def setText(self, text: str) -> None:  # noqa: N802 - Qt naming, as QLineEdit
        self.edit.setText(text)

    def _scan(self) -> None:
        if self._scanning:
            return
        self._scanning = True
        self.menu.clear()
        self.menu.addAction("Looking for networks…").setEnabled(False)

        def work() -> None:
            try:
                self.scanned.emit(wifi.scan())
            except ProvisionError as err:
                self.scanned.emit(str(err))

        threading.Thread(target=work, daemon=True).start()

    def _fill(self, result: object) -> None:
        self._scanning = False
        self.menu.clear()
        if isinstance(result, str):
            self.menu.addAction(result).setEnabled(False)
            return
        if not result:
            self.menu.addAction("No network found").setEnabled(False)
            return
        for network in result:  # type: ignore[union-attr]
            label = str(network) if network.band_24 else f"{network}  5 GHz only: the node cannot join it"
            action = self.menu.addAction(label)
            action.setEnabled(network.band_24)
            action.triggered.connect(lambda _checked=False, ssid=network.ssid: self.edit.setText(ssid))


class _TimezoneBox(QComboBox):
    """Named time zones (`Europe/Paris`), searchable by typing any part of
    the name; the node gets the zone's POSIX rule (see timezones.py). A
    rule no named zone has (set by hand elsewhere) is kept as a last entry
    so it is not lost."""

    def __init__(self) -> None:
        super().__init__()
        self.setEditable(True)
        self.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.addItems(timezones.names())
        completer = self.completer()
        completer.setFilterMode(Qt.MatchFlag.MatchContains)
        completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        completer.setCompletionMode(QCompleter.CompletionMode.PopupCompletion)
        self._custom: str | None = None

    def set_rule(self, rule: str | None) -> None:
        """Show the zone of `rule`; the computer's own zone when None."""
        if rule is None:
            name = timezones.local_name() or "Europe/Paris"
        else:
            name = timezones.name_for(rule)
            if name is None:
                self._custom = rule
                label = f"custom rule: {rule}"
                if self.findText(label) < 0:
                    self.addItem(label)
                name = label
        self.setCurrentText(name)

    def rule(self) -> str:
        """The POSIX rule of the shown zone; raises on an unknown name."""
        text = self.currentText().strip()
        if self._custom and text == f"custom rule: {self._custom}":
            return self._custom
        rule = timezones.posix(text) if text in timezones.names() else None
        if rule is None:
            raise ProvisionError(f"Time zone: {text!r} is not a known zone; pick one from the list")
        return rule


class _WheelGuard(QObject):
    """Lets the mouse wheel scroll the form instead of changing a value.

    2026-09-26: scrolling the settings pane over a choice list or a number
    changed it silently (the primary channel became SECONDARY, 3600 became
    3599, 13 became 0) and the change went to the node. A widget now takes
    the wheel only once clicked (focused)."""

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802 - Qt naming
        if event.type() == QEvent.Type.Wheel and not watched.hasFocus():
            # Hand the wheel to the scroll area around, which scrolls.
            area = watched.parentWidget()
            while area is not None and not isinstance(area, QAbstractScrollArea):
                area = area.parentWidget()
            if area is not None:
                QApplication.sendEvent(area.viewport(), event)
            return True
        return False


_WHEEL_GUARD = _WheelGuard()


def _no_wheel(widget: QWidget) -> None:
    widget.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
    widget.installEventFilter(_WHEEL_GUARD)


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
