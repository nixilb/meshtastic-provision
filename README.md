# meshtastic-provision

Prepare a Meshtastic node plugged in over USB in one go: install the
firmware (optional), then apply a settings profile and verify it. A
companion to [meshtastic-desktop](../meshtastic-desktop_git), which expects
its node configured as the example profile describes (MQTT client proxy on
the public broker, region EU_868). Python, with a command line and a small
window; no interface of its own is needed on the node.

## Install

Only [uv](https://docs.astral.sh/uv/) is needed; it fetches Python 3.13
(whose build ships Tkinter, for the window) and the libraries `esptool`,
`meshtastic`, `pyyaml`, `requests` and `pyserial` on the first run.

```sh
curl -LsSf https://astral.sh/uv/install.sh | sh
cd meshtastic-provision
uv run provision_cli.py --list-ports     # first run: creates .venv
```

The user must be allowed to open the serial port: on Debian, Ubuntu and
Raspberry Pi OS, `sudo usermod -aG dialout $USER` then log in again.

Raspberry Pi: works on the 64-bit Raspberry Pi OS (aarch64; the compiled
dependencies have wheels for it). The 32-bit OS would have to compile
`cryptography`, which needs Rust: avoid it. The command line runs over SSH
on a Pi without desktop; the window needs one, or `ssh -X`.

## Usage

Close meshtastic-desktop first: it holds the serial port, and both tools
refuse to start while another process does.

### Window

```sh
uv run provision_gui.py
```

Pick the port (Refresh after plugging the node), press Detect to read the
chip (family, MAC, flash size; the board list is then reduced to that
family), choose the board, the firmware version and the profile, then:

- Flash: after a confirmation, erases the flash, installs the firmware, waits
  for the node to boot, applies the profile and verifies it. With the
  backup box ticked, the node's current settings are first exported to
  `~/.config/meshtastic/backups/node-<date>.yaml` (this needs a working
  node; untick it for a blank or bricked board).
- Configure only: applies the profile to the node as it is.
- Check: reports the settings that differ, writes nothing.

The log pane shows every step; errors are in red.

### Command line

```sh
uv run provision_cli.py --list-ports | --list-boards | --list-versions
uv run provision_cli.py --port /dev/ttyUSB0 --detect
uv run provision_cli.py --port /dev/ttyUSB0 --profile node-profile.yaml --check
uv run provision_cli.py --port /dev/ttyUSB0 --profile node-profile.yaml
uv run provision_cli.py --port /dev/ttyUSB0 --board heltec-v3 --flash 2.7.26.54e0d8d \
    --profile node-profile.yaml [--backup before.yaml] [--yes]
```

`--profile` defaults to `~/.config/meshtastic/node-profile.yaml` when that
file exists. `--flash` asks for a typed `yes` before erasing unless `--yes`
is given. Exit code 0 when the node matches the profile at the end, 1
otherwise.

## What a flash does

Like the firmware's `bin/device-install.sh` and the web flasher:

1. Reads the board's manifest for the version
   (`firmware-<version>/firmware-<board>-<version>.mt.json` on
   `meshtastic.github.io`): file names, MD5s and the partition table.
2. Downloads the factory image (bootloader, partition table, application),
   the OTA loader (partition `app1`) and the file system image (partition
   `spiffs`) into `~/.cache/meshtastic-provision/`, checking size and MD5
   on every use.
3. Reads the chip with esptool: its family must be the manifest's
   (`esp32s3` for a Heltec V3) and its flash large enough for the partition
   table. The node's identity cannot be checked before a full install:
   choose the board carefully.
4. Erases the whole flash, writes the three images at their offsets and
   verifies them, then restarts the chip.
5. Waits for the node to answer on the port (first boot: 15 to 30 s) and
   goes on with the profile.

The node comes back with default settings and a new private key: other
nodes must learn its public key again before direct messages reach it.
Unplugging during the write leaves the board without firmware; a second
Flash recovers it.

Only ESP32 boards are supported. nRF52 boards (UF2 through the bootloader
drive) are not, yet.

## Profile

A YAML file; `node-profile.example.yaml` is documented field by field and
matches the node meshtastic-desktop is developed with. Copy it to
`~/.config/meshtastic/node-profile.yaml`, fill in the Wi-Fi and broker
secrets, and keep that copy out of any repository (`.gitignore` already
excludes `node-profile.yaml` here).

```yaml
owner: nixilb_01          # long name
owner_short: NX01         # 4 characters at most
config:                   # sections and fields of config.proto
  lora:
    region: EU_868
    ignore_mqtt: false
module_config:            # sections and fields of module_config.proto
  mqtt:
    enabled: true
    proxy_to_client_enabled: true
channels:                 # channel.proto: index, role, settings
  - index: 0
    role: PRIMARY
    settings:
      uplink_enabled: true
      downlink_enabled: true
      module_settings:
        position_precision: 13
```

Rules:

- Only the fields written in the profile are compared and written; every
  other setting keeps its value on the node. Nested messages
  (`map_report_settings`, `module_settings`) are written as nested
  mappings. Enum values are their protobuf names (`EU_868`, `PRIMARY`).
- Field names are checked against the protobufs: a misspelt field, an
  unknown enum value or a value of the wrong type stops the run before
  anything is written.
- The `security` section and channel keys (`psk`) are refused: the node
  keeps its own keys. Repeated and byte fields are not supported.
- `network.wifi_psk` must be 8 characters or more (the firmware's rule).

Applying: the settings that differ are written inside one settings
transaction (`begin_edit_settings` / `commit_edit_settings`), so the node
saves and reboots once, at the commit. The tool then reconnects and
compares again; the result is "the node matches the profile" or the list
of remaining differences.

## Files

| Path | Role |
|------|------|
| `provision.py` | The steps: ports, downloads, chip, flash, profile, compare, apply |
| `provision_cli.py` | Command line |
| `provision_gui.py` | Tkinter window |
| `node-profile.example.yaml` | Documented example profile |
| `~/.config/meshtastic/node-profile.yaml` | Default profile (not tracked) |
| `~/.config/meshtastic/backups/` | Settings exported before a flash (window) |
| `~/.cache/meshtastic-provision/` | Downloaded firmware images |
| `.venv/` | uv's environment (not tracked) |

Backups are the meshtastic CLI's `--export-config` YAML, which
`uvx meshtastic --port /dev/ttyUSB0 --configure <file>` restores.
