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
            Field("module_config.mqtt.map_reporting_enabled", "Map reports", "bool", "publishes the position in clear"),
            Field("module_config.mqtt.map_report_settings.publish_interval_secs", "Map report interval", "int", "seconds"),
            Field("module_config.mqtt.map_report_settings.position_precision", "Map report precision", "int", "1 to 32 bits", maximum=32),
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
        outer.addWidget(scroll, 1)
        inner = QWidget()
        scroll.setWidget(inner)
        column = QVBoxLayout(inner)
        for group, fields in FIELDS:
            box = QGroupBox(group)
            grid = QGridLayout(box)
            grid.setColumnStretch(2, 1)
            for r, field in enumerate(fields):
                present = QCheckBox()
                present.setToolTip("Write this setting to the node")
                label = QLabel(field.label)
                widget = self._widget(field)
                present.toggled.connect(widget.setEnabled)
                grid.addWidget(present, r, 0)
                grid.addWidget(label, r, 1)
                grid.addWidget(widget, r, 2)
                if field.help:
                    hint = QLabel(field.help)
                    hint.setStyleSheet("color: palette(mid);")
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
