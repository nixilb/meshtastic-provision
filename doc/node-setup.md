# Setting up a new node and this computer

Everything that was configured, outside the code, to bring a Heltec V3 from
"unknown USB device" to a node that exchanges messages through the public
MQTT broker with this app (done on 2026-09-26, firmware 2.7.26, app on
Ubuntu). Redo it in this order for the next board. Commands are run from
the repository root; the app must be stopped whenever the Meshtastic CLI
uses the serial port, both cannot open it at once.

## 1. Host: serial port access

Plug the node in and check that the kernel sees it (a CP2102 bridge on the
Heltec V3):

```sh
ls -l /dev/ttyUSB* /dev/serial/by-id/
journalctl -k -f        # "new USB device found" must appear when plugging
```

No `/dev/ttyUSB*` means a charge-only cable, an unpowered hub port or a
node that is off; nothing in the app can help until the device exists.

The port belongs to group `dialout`. Add yourself once, then log out and
back in:

```sh
sudo usermod -aG dialout "$USER"
```

Until the next login the group is missing from the running session; the
app can still be launched with it:

```sh
sg dialout -c ./target/release/meshtastic
```

## 2. Host: the Meshtastic CLI

The Python CLI is not installed; `uvx` runs it on demand (first call
downloads it):

```sh
uvx meshtastic --port /dev/ttyUSB0 --info
```

## 3. Firmware

Done from the app, no connection needed: Settings, "Install firmware"
(also on the Connect screen, for a device that cannot be connected). Choose the version (2.7.26.54e0d8d), the port
(`/dev/ttyUSB0`) and the board (`heltec-v3`). The app downloads the
factory image, the OTA loader and the LittleFS image, erases the whole
flash and writes them (about 4.4 MB, one minute). The warning "no node
MAC address to check; identity not checked" is expected for a node never
connected before.

A full erase empties the node's configuration and node database: every
setting below must be redone.

## 4. Node configuration (CLI, app stopped)

Region first; the node reboots after each `--set` of the LoRa or MQTT
section, wait about ten seconds before the next command:

```sh
uvx meshtastic --port /dev/ttyUSB0 --set lora.region EU_868
```

Then check `ignore_mqtt`: on this board it read `true` right after the
region was set (it was absent before). With it set the firmware decodes
the packets the app hands it from the broker and drops them silently
("Msg came in via MQTT from ..." in the firmware log, then nothing).

```sh
uvx meshtastic --port /dev/ttyUSB0 --get lora.ignore_mqtt --get lora.config_ok_to_mqtt
uvx meshtastic --port /dev/ttyUSB0 --set lora.ignore_mqtt false
uvx meshtastic --port /dev/ttyUSB0 --set lora.config_ok_to_mqtt true
```

`config_ok_to_mqtt` marks our own packets as fine to relay through MQTT,
so other gateways forward them.

MQTT through the app (the node has no Wi-Fi configured, the app bridges
it to the broker; `mqtt.enabled` is already true by default, with the
public broker `mqtt.meshtastic.org`, `meshdev` / `large4cats`, root
`msh/EU_868`, encryption on):

```sh
uvx meshtastic --port /dev/ttyUSB0 --set mqtt.proxy_to_client_enabled true \
  --ch-index 0 --ch-set uplink_enabled true --ch-set downlink_enabled true
```

Uplink lets the node's own packets go to the broker, downlink lets the
broker's LongFast traffic reach the node (the app's downlink filter keeps
only what the node can decode).

Read everything back once the node is up again:

```sh
uvx meshtastic --port /dev/ttyUSB0 --get lora.region --get lora.ignore_mqtt \
  --get lora.config_ok_to_mqtt --get mqtt.enabled --get mqtt.proxy_to_client_enabled
uvx meshtastic --port /dev/ttyUSB0 --info | grep -A1 '^Channels'
```

Expected: `lora.region: 3`, `lora.ignore_mqtt: False`,
`lora.config_ok_to_mqtt: True`, both `mqtt.*: True`, and channel 0 with
`"uplinkEnabled": true, "downlinkEnabled": true`.

## 5. App settings

In `~/.config/meshtastic/settings.json`, read at start-up only (stop the
app to edit it, or use the Settings tab, which saves on change):

| Key | Value | In the app |
|---|---|---|
| `last_address` | `"s/dev/ttyUSB0"` | set by the first successful connection |
| `auto_connect` | `true` | App settings, Behaviour, "Connect automatically" |
| `mqtt_observer` | `true` | App settings, MQTT observer |
| `mqtt_observer_all_regions` | `true` | App settings, MQTT observer, "Every region" |
| `map_world_nodes` | `true` | Map, "MQTT world" button |
| `map_gateway_links` | `true` (default) | Map, gateway links button |

The observer (read-only, the app's own broker connection) lists on the
map and in the node list the nodes of every topic root; without it the
map only shows nodes the radio heard and their gateways, which is nothing
on a node that has just been erased. It is independent of the node's
MQTT settings of section 4, which are what make messages flow.

## 6. Launch and check

```sh
sg dialout -c ./target/release/meshtastic     # plain launch after a re-login
```

The app enforces a single instance: stop the running one first.

In `~/.local/share/meshtastic/logs/meshtastic.log`, within a few seconds:

- `[CORE] #2008 handshake complete node_num=...`
- `[MQTT] #2320` / `#2322`: observer starting and connected
- `[MQTT] #2301` / `#2304`: client proxy starting and connected to the
  broker (only when `proxy_to_client_enabled` is on)

`~/.local/share/meshtastic/logs/meshtastic.err.txt` should stay empty.

Then, in the app:

- Nodes learned through MQTT appear within seconds (the node list, and
  the map with "MQTT world").
- A message sent on Primary shows "MQTT" with our topic beside our name
  and "Delivered" under the bubble within a second or two. "Sending" for
  90 s then "no acknowledgement" means the broker echo is not reaching the
  node; MaxRetransmit means the radio path only was tried.
- Messages from other nodes on LongFast arrive in Primary as they are
  written in the region.

Radio side: with the firmware log on (`DEVICE` at `debug` in
`~/.config/meshtastic/logging.json`, hot-reloaded), the
`[DeviceTelemetry]` lines give `channel_utilization`. Zero after ten
minutes means the radio heard no LoRa transmission at all: check the
antenna before concluding that nobody is in range.

## 7. Left to do on this machine

- Host position: GeoClue has no Wi-Fi geolocation URL configured, so it
  falls back to the ISP's location of the public IP (25 km accuracy),
  which the app then sets as the node's fixed position. Either point
  GeoClue at BeaconDB (README, "Host location") or enable "Locate with
  Positon" in App settings, Location, or enter the coordinates by hand in
  Device settings.
- Log out and back in so `dialout` applies to the session and the app can
  be launched without `sg`.
