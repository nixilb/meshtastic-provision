"""The usages a node can be prepared for (doc/Usages.md), as settings.

A usage is a starting point for the profile: choosing one in the window
writes its settings into the form, which stays editable. Every usage sets
the same paths ([`USAGE_PATHS`]), so switching from one usage to another
replaces all of them and leaves nothing behind; a path a usage leaves at
`KEEP` is removed from the profile, and the node keeps its own value.

Values follow doc/Usages.md, section "Réglages" of each usage, and the
firmware's rules at v2.7.26: on the default channel (LongFast with the
public key), the firmware raises the position interval to one hour at
least and telemetry intervals to 30 minutes (NodeDB.cpp, "Coerce ... on
defaults"), so no usage asks for less.
"""

from dataclasses import dataclass
from typing import Any

# A path's value when the usage leaves it to the node.
KEEP = None

HOUR = 3600
HALF_DAY = 12 * HOUR


@dataclass(frozen=True)
class Usage:
    key: str  # stored in the profile as `usage`
    label: str  # the list's entry
    description: str  # what the usage is for and how the node serves it
    # How to put the node to work: power, placement, the other nodes and
    # equipment the usage needs, in the order to do them.
    setup: tuple[str, ...]
    settings: dict[str, Any]  # path (as the form writes it) -> value or KEEP

    @property
    def needs_psram(self) -> bool:
        """Whether it turns Store & Forward on (see provision.needs_psram)."""
        return self.settings[_PATHS["store_forward"]] is True

    def setting_lines(self) -> list[tuple[str, str]]:
        """The settings it writes, as (label, value) for the window. A
        setting left to the node is not listed, nor the details of a
        feature it turns off (the broker's options with MQTT off...)."""
        values = {name: self.settings[path] for name, path in _PATHS.items()}
        lines = []
        for name, (label, unit) in _LABELS.items():
            value = values[name]
            parent = _PARENTS.get(name)
            if value is KEEP or (parent and values[parent] is not True):
                continue
            lines.append((label, _show(value, unit)))
        return lines


# Short names of the paths a usage sets, to keep the table readable.
_PATHS = {
    "role": "config.device.role",
    "gps": "config.position.gps_mode",
    "position_secs": "config.position.position_broadcast_secs",
    "smart": "config.position.position_broadcast_smart_enabled",
    "screen_secs": "config.display.screen_on_secs",
    "mqtt": "module_config.mqtt.enabled",
    "proxy": "module_config.mqtt.proxy_to_client_enabled",
    "map_report": "module_config.mqtt.map_reporting_enabled",
    "map_location": "module_config.mqtt.map_report_settings.should_report_location",
    "uplink": "channels[0].settings.uplink_enabled",
    "downlink": "channels[0].settings.downlink_enabled",
    "environment": "module_config.telemetry.environment_measurement_enabled",
    "environment_secs": "module_config.telemetry.environment_update_interval",
    "device_secs": "module_config.telemetry.device_update_interval",
    "store_forward": "module_config.store_forward.enabled",
    "store_forward_server": "module_config.store_forward.is_server",
    "store_forward_heartbeat": "module_config.store_forward.heartbeat",
    "range_test": "module_config.range_test.enabled",
    "range_test_sender": "module_config.range_test.sender",
    "range_test_save": "module_config.range_test.save",
}

USAGE_PATHS: tuple[str, ...] = tuple(_PATHS.values())

# How the window names each setting, and the unit of its value (`s` for
# an interval in seconds, `d` for a duration in seconds, both shown in
# minutes or hours; `on` for a switch), in display order.
_LABELS: dict[str, tuple[str, str]] = {
    "role": ("Role", ""),
    "gps": ("GPS", ""),
    "position_secs": ("Position broadcast", "s"),
    "smart": ("Extra position when moved", "on"),
    "screen_secs": ("Screen timeout", "d"),
    "mqtt": ("MQTT", "on"),
    "proxy": ("Through meshtastic-desktop (proxy)", "on"),
    "uplink": ("Public channel sent to the broker", "on"),
    "downlink": ("Public channel received from the broker", "on"),
    "map_report": ("On the public map", "on"),
    "map_location": ("Position in map reports", "on"),
    "environment": ("Environment sensors", "on"),
    "environment_secs": ("Environment readings", "s"),
    "device_secs": ("Battery and load report", "s"),
    "store_forward": ("Store & Forward", "on"),
    "store_forward_server": ("Store & Forward server", "on"),
    "store_forward_heartbeat": ("Store & Forward announced", "on"),
    "range_test": ("Range test", "on"),
    "range_test_sender": ("Range test message", "s"),
    "range_test_save": ("Range test saved to a file", "on"),
}
assert set(_LABELS) == set(_PATHS)

# A setting only listed when this switch is on.
_PARENTS = {
    "proxy": "mqtt",
    "uplink": "mqtt",
    "downlink": "mqtt",
    "map_report": "mqtt",
    "map_location": "map_report",
    "environment_secs": "environment",
    "store_forward_server": "store_forward",
    "store_forward_heartbeat": "store_forward",
    "range_test_sender": "range_test",
    "range_test_save": "range_test",
}


def _show(value: Any, unit: str) -> str:
    if unit == "on":
        return "on" if value else "off"
    if unit in ("s", "d"):
        if value == 0:
            return "never"
        if value % HOUR == 0:
            amount = f"{value // HOUR} h"
        elif value % 60 == 0:
            amount = f"{value // 60} min"
        else:
            amount = f"{value} s"
        return f"every {amount}" if unit == "s" else amount
    return str(value)


# The value of every path in a usage that does not mention it: the
# firmware's defaults for the role, position and screen, every optional
# module and MQTT off.
_NEUTRAL: dict[str, Any] = {
    "role": "CLIENT",
    "gps": KEEP,  # board dependent (a GPS or not): only tracking asks for it
    "position_secs": HOUR,
    "smart": True,
    "screen_secs": 600,
    "mqtt": False,
    "proxy": False,
    "map_report": False,
    "map_location": False,
    "uplink": False,
    "downlink": False,
    "environment": False,
    "environment_secs": KEEP,
    "device_secs": KEEP,
    "store_forward": False,
    "store_forward_server": False,
    "store_forward_heartbeat": False,
    "range_test": False,
    "range_test_sender": 0,
    "range_test_save": False,
}
assert set(_NEUTRAL) == set(_PATHS)

# A node that never moves: its position said twice a day, no smart
# broadcast (it never travels), the screen lit for a minute.
_FIXED: dict[str, Any] = {"position_secs": HALF_DAY, "smart": False, "screen_secs": 60}


def _usage(key: str, label: str, description: str, fixed: bool = False, **overrides: Any) -> Usage:
    """A usage: the neutral values, those of a fixed node if `fixed`, then
    `overrides` (short names)."""
    values = {**_NEUTRAL, **(_FIXED if fixed else {}), **overrides}
    return Usage(
        key,
        label,
        " ".join(description.split()),
        _SETUP[key],
        {_PATHS[name]: value for name, value in values.items()},
    )


# What to do, besides provisioning this node, for each usage to work.
_SETUP: dict[str, tuple[str, ...]] = {
    "tracking": (
        "Power it from a battery (a LiPo in a case, or a USB power bank): it travels with "
        "what it tracks.",
        "Use a board with a GPS (T-Beam, T-Echo...) and give its antenna a view of the sky; "
        "without a GPS, pair a phone that shares its position with the Meshtastic app.",
        "Whoever follows it needs a node of their own, paired with the Meshtastic app, on the "
        "same region and channel, and within reach of the mesh.",
        "To follow it from the Internet as well, a gateway in range: a node set up as "
        "\"Node attached to a PC\" or \"Community relay and gateway\".",
    ),
    "off-grid": (
        "Power it from its battery; charge every node before going out.",
        "Pair it with its owner's phone: Meshtastic app, Bluetooth, the PIN shown on the "
        "node's screen (123456 on boards without a screen).",
        "One node per person, all on the same region and radio preset.",
        "Share a private channel: one member creates it in the app (name and random key), "
        "the others scan its QR code; keep LongFast as the primary channel.",
        "Over a large area, add a relay on a high point (\"Emergency relay\" or "
        "\"Community relay and gateway\").",
        "Try the messages and the range together before going out.",
    ),
    "emergency": (
        "Use a board with PSRAM (Station G2, T-Beam...): Store & Forward needs it.",
        "Install it on a high point with a clear view (roof, water tower), in a weatherproof "
        "case, with an outdoor antenna.",
        "Power it from a battery and a solar panel sized for several days without sun, or "
        "from the mains with a backup battery.",
        "Set its fixed position in the form.",
        "Prepare the nodes of the team or the residents in advance with \"Off-grid "
        "messaging\", and share the team's private channel before it is needed.",
        "Check the coverage from the relay's place with a range test.",
    ),
    "community": (
        "Agree on the place and the role with the local Meshtastic community: relays too "
        "close to each other waste the mesh's airtime.",
        "Install it on a high, clear spot, in an outdoor case with a good antenna, powered "
        "from the mains or a solar panel.",
        "Give it a 2.4 GHz Wi-Fi network within reach (Wi-Fi fields of the form): it "
        "connects to the broker by itself.",
        "Set its fixed position in the form, then check that it shows on the public map.",
    ),
    "sensor": (
        "Wire the I²C sensors (a BME280 for temperature, humidity and pressure, for "
        "example) to the board's I²C pins before it starts: the firmware looks for them at "
        "start-up.",
        "Power it from a battery and a small solar panel, or from the mains.",
        "Set its fixed position in the form.",
        "Make sure a node or a gateway receives it: its readings are only useful if someone "
        "hears them.",
        "For Home Assistant: a private MQTT broker (Mosquitto), MQTT turned on in the form "
        "towards that broker, and JSON output on the gateway (module_config.mqtt.json_enabled "
        "in the profile; ESP32 only).",
    ),
    "range-sender": (
        "Power it from a battery: it is carried around, on foot or in a vehicle.",
        "Use a board with a GPS, or pair a phone that shares its position, so that each "
        "message tells where it was sent from.",
        "Prepare a second node with \"Range test: receiver\" and leave it in place.",
        "Walk or drive around the area to measure; the node stops sending after 8 hours.",
    ),
    "range-receiver": (
        "Place it where the coverage is measured from (a future relay's place, for example), "
        "powered by USB or the mains.",
        "Give it Wi-Fi (Wi-Fi fields of the form): the results are read from its web page, "
        "in the file rangetest.csv.",
        "Prepare a second node with \"Range test: sender\" and carry it around.",
    ),
    "pc": (
        "Plug it into the computer over USB: the cable powers it and carries its data.",
        "Install meshtastic-desktop and let it connect to the node at start-up (set by this "
        "tool with the settings).",
        "Keep the computer on and the app running (closing its window only hides it in the "
        "tray): without the app, the node loses MQTT.",
        "Place the node near a window, or give it an outdoor antenna: indoors, the radio "
        "reaches a few hundred metres at best.",
        "Set its fixed position in the form.",
    ),
}


# Descriptions: doc/Usages.md, need then answer, for someone new to
# Meshtastic; what to know before choosing comes last.
USAGES: tuple[Usage, ...] = (
    _usage(
        "tracking",
        "Position tracking",
        """Know where the members of a group, a vehicle or an object are, with
        no mobile tracker subscription. The node broadcasts its position on
        the mesh, and every Meshtastic app within reach of the mesh shows it
        on its map. It sends a position every hour (the firmware's minimum on
        the public channel) and one more as soon as it has moved far enough.
        Needs a board with a GPS, or a paired phone sharing its position. A
        tracker cannot be sent direct messages.""",
        role="TRACKER",
        gps="ENABLED",
    ),
    _usage(
        "off-grid",
        "Off-grid messaging",
        """Keep a group in touch where the mobile network does not reach or
        is saturated: mountains, sea, forests, crowded events. Each person
        carries a node paired with their phone over Bluetooth; messages
        travel from node to node over LoRa, and the mesh relays them around
        obstacles through whichever node sees both sides. A node lasts days
        on a small battery, the phone can stay in airplane mode with
        Bluetooth on. MQTT is off: there is no Internet in the field.""",
    ),
    _usage(
        "emergency",
        "Emergency relay",
        """Keep a neighbourhood, a town or a rescue team communicating when
        mobile networks, power or the Internet fail. This node is a relay set
        up in advance on a high point (roof, water tower), on battery and
        solar panel. It relays after the other nodes, so it covers the gaps
        without taking hops from the existing routers (ROUTER_LATE role), and
        it keeps recent messages to replay them to nodes that were out of
        range or switched off (Store & Forward server). Store &
        Forward needs PSRAM: boards without it are refused.""",
        fixed=True,
        role="ROUTER_LATE",
        store_forward=True,
        store_forward_server=True,
        store_forward_heartbeat=True,
    ),
    _usage(
        "community",
        "Community relay and gateway",
        """Give a region a network anyone can join, and link it to distant
        meshes through the Internet. This node is a relay on a well placed,
        clear spot with its own Wi-Fi: it relays everyone's traffic after the
        other nodes (ROUTER_LATE role), publishes what it hears on the public
        channel to the MQTT broker and puts itself on the public map. It does
        not take the public channel's traffic back from the broker: tens of
        messages a second from the whole region would flood the local radio.""",
        fixed=True,
        role="ROUTER_LATE",
        mqtt=True,
        map_report=True,
        map_location=True,
        uplink=True,
    ),
    _usage(
        "sensor",
        "Sensors and telemetry",
        """Read measurements from places without power or network: a remote
        weather station, a water tank, a greenhouse, a beehive. Sensors wired
        to the node (I²C: temperature, humidity, pressure, air quality) are
        read and broadcast on the mesh, with the node's battery level, so the
        site's state can be followed from anywhere on the mesh (SENSOR role).
        MQTT is off: tick it to send the readings to a broker, where Home
        Assistant or Node-RED can pick them up.""",
        fixed=True,
        role="SENSOR",
        environment=True,
        environment_secs=1800,
        device_secs=HOUR,
    ),
    _usage(
        "range-sender",
        "Range test: sender",
        """Measure how far the radio really reaches. This node sends a
        numbered message every minute for 8 hours, while it is carried
        around; a second node, set to "Range test: receiver", records what
        arrives with the signal strength. The messages go out on the primary
        channel: on LongFast, everyone around sees them.""",
        range_test=True,
        range_test_sender=60,
    ),
    _usage(
        "range-receiver",
        "Range test: receiver",
        """Measure how far the radio really reaches. This node stays in place
        and records the messages of a second node set to "Range test: sender"
        in a file (rangetest.csv), with the signal strength (RSSI, SNR) and
        the sender's position, read afterwards from the node's web page, to
        map the real coverage.""",
        range_test=True,
        range_test_save=True,
    ),
    _usage(
        "pc",
        "Node attached to a PC",
        """A node plugged into a computer running meshtastic-desktop, the
        computer's window on the mesh. The app carries the node's MQTT traffic
        (client proxy) and filters what comes down from the broker, so the
        node reaches the rest of the world through the Internet without
        being flooded. The public channel goes to and comes from the broker,
        and the node shows on the public map. The app must stay connected:
        without it, the node keeps the radio but loses MQTT.""",
        fixed=True,
        mqtt=True,
        proxy=True,
        map_report=True,
        map_location=True,
        uplink=True,
        downlink=True,
    ),
)

BY_KEY: dict[str, Usage] = {usage.key: usage for usage in USAGES}
