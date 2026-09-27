# meshtastic-provision

Prepare a Meshtastic node plugged in over USB in one go: install the
firmware (optional), then apply a settings profile and verify it. A
companion to [meshtastic-desktop](../meshtastic-desktop_git), which expects
its node configured as the example profile describes (MQTT client proxy on
the public broker, region EU_868). Python, with a command line and a Qt
window; no interface of its own is needed on the node.

## Install

Only [uv](https://docs.astral.sh/uv/) is needed; it fetches Python 3.13
and the libraries `esptool`, `meshtastic`, `pyyaml`, `requests`, `pyserial`
and `PySide6-Essentials` (Qt, for the window; 80 MB) on the first run.

```sh
curl -LsSf https://astral.sh/uv/install.sh | sh
cd meshtastic-provision
uv run provision_cli.py --list-ports     # first run: creates .venv
```

The user must be allowed to open the serial port. The tools check it
before anything else; when it is missing they offer to install a udev
rule (`packaging/70-meshtastic-provision.rules`, through the system's
password prompt) that lets the user logged in at the desktop open USB
serial ports, effective at once. The `.deb` installs that rule itself. The
older way, `sudo usermod -aG dialout $USER` and a new login, still works
and is offered when the rule is not enough (no systemd-logind).

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

Left, the node and the actions; right, the settings form. Plug the node
in: the window sees the port, asks the node which board it is (a running
firmware reports its build target, e.g. `heltec-v3`, and its MAC address),
then reads the chip (family, MAC, flash size, in-package PSRAM). The board list is reduced to
the chip's family and the node's board is preselected. A blank board
answers nothing: choose it by hand. With several nodes plugged in, picking
a port in the list detects that one. Choose the firmware version and the
usage, check the settings, then:

- Flash & configure: after a confirmation, erases the flash, installs the firmware, waits
  for the node to boot, applies the settings and verifies them. With the
  backup box ticked, the node's current settings are first exported to
  `~/.config/meshtastic/backups/node-<date>.yaml` (this needs a working
  node; untick it for a blank or bricked board).
- Configure only: applies the settings to the node as it is.
- Check: reports the settings that differ, writes nothing.

The Usage list says what the node is for, after `doc/Usages.md`:
position tracking, off-grid messaging, emergency relay, community relay
and gateway, sensors and telemetry, range test sender or receiver, node
attached to a PC. Picking one writes its settings into the form (role,
position and screen intervals, MQTT, the public channel's uplink and
downlink, telemetry, Store & Forward, range test; see `usages.py`), and
above "Details", the window explains the usage, lists what to do to put
the node to work (power, placement, the other nodes needed), then the
settings it writes. Every usage sets the same settings, so switching replaces them
all. The form stays editable. Store & Forward (emergency relay) needs
PSRAM: on a chip known to have none, the window says so and the run is
refused.

The settings form shows the settings a user chooses; the others (role,
intervals, modules) come from the usage and are saved with the form. A
checkbox says whether a setting is applied (unticked, the node keeps its
own value), enum values come as a choice list, passwords are masked. The form is validated
and saved to `~/.config/meshtastic/node-profile.yaml` before each action
and when the window closes; that file is the command line's default
profile, so both tools share the same settings.

During an action the window shows its stages (Download, Erase, Write,
Restart, Settings, Check for a flash), each ticked when done or crossed
when it failed, an animated bar during waits of unknown length, and the
outcome in one line. Everything that can be edited is greyed meanwhile.
The technical log is folded under "Details"; it is also kept in
`~/.local/state/meshtastic-provision/provision.log`.

### Command line

```sh
uv run provision_cli.py --list-ports | --list-boards | --list-versions
uv run provision_cli.py --port /dev/ttyUSB0 --detect
uv run provision_cli.py --port /dev/ttyUSB0 --profile node-profile.yaml --check
uv run provision_cli.py --port /dev/ttyUSB0 --profile node-profile.yaml
uv run provision_cli.py --port /dev/ttyUSB0 --flash 2.7.26.54e0d8d \
    --profile node-profile.yaml [--board heltec-v3] [--backup before.yaml] [--yes]
```

`--profile` defaults to `~/.config/meshtastic/node-profile.yaml` when that
file exists. `--board` is taken from the running node when omitted; when
given, it must match what the node reports. A blank board reports nothing,
so `--board` is required then. `--flash` asks for a typed `yes` before
erasing unless `--yes` is given. Exit code 0 when the node matches the
profile at the end, 1 otherwise.

## What a flash does

Like the firmware's `bin/device-install.sh` and the web flasher:

1. Asks the node on the port which board it is (up to 12 s; a blank board
   does not answer) and, when asked, backs up its settings.
2. Reads the board's manifest for the version
   (`firmware-<version>/firmware-<board>-<version>.mt.json` on
   `meshtastic.github.io`): file names, MD5s and the partition table.
3. Downloads the factory image (bootloader, partition table, application),
   the OTA loader (partition `app1`) and the file system image (partition
   `spiffs`) into `~/.cache/meshtastic-provision/`, checking size and MD5
   on every use.
4. Reads the chip with esptool: its family must be the manifest's
   (`esp32s3` for a Heltec V3), its flash large enough for the partition
   table, and its MAC address the one the node reported, so the wrong
   board plugged in by mistake is refused. When no node answered, the
   identity cannot be checked: choose the board carefully.
5. Erases the whole flash, writes the three images at their offsets and
   verifies them, then restarts the chip.
6. Waits for the node to answer on the port (first boot: 15 to 30 s) and
   goes on with the profile.

The node comes back with default settings and a new private key: other
nodes must learn its public key again before direct messages reach it.
Unplugging during the write leaves the board without firmware; a second
Flash recovers it.

Only ESP32 boards are supported. nRF52 boards (UF2 through the bootloader
drive) are not, yet.

## Profile

The settings, as a YAML file: what the window's form saves, and what the
command line takes with `--profile`. `node-profile.example.yaml` is
documented field by field and matches the node meshtastic-desktop is
developed with; a new form starts from it without its Wi-Fi placeholders.
By hand: copy the example to `~/.config/meshtastic/node-profile.yaml`,
fill in the Wi-Fi and broker secrets, and keep that copy out of any
repository (`.gitignore` already excludes `node-profile.yaml` here).

```yaml
owner: my-node-01         # long name
owner_short: ND01         # 4 characters at most
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
- `position`: the node's fixed position, `latitude` and `longitude` in
  degrees (both or neither) and an optional `altitude` in metres, sent with
  the admin `set_fixed_position` message, which also turns
  `config.position.fixed_position` on. The window's "My position" button
  fills it from this computer's position: the Wi-Fi networks around looked
  up by BeaconDB (tens of metres where they are mapped), else the public IP
  address (the town only); the source and precision are shown.
- `usage`: the usage the settings were started from (a key of
  `usages.py`: `tracking`, `off-grid`, `emergency`, `community`, `sensor`,
  `range-sender`, `range-receiver`, `pc`). Nothing is written from it: the
  settings themselves are in the profile.
- `module_config.store_forward.enabled: true` needs PSRAM: the chip is read
  first (which restarts the node) and a chip whose eFuses say it has none
  is refused. A classic ESP32 does not record an external PSRAM chip
  (T-Beam), so it is let through.
- `config.device.tzdef` is the POSIX rule the node needs
  (`CET-1CEST,M3.5.0,M10.5.0/3`); the window shows and takes named zones
  (`Europe/Paris`) and converts with the system's time zone database.
- `app`: meshtastic-desktop's own settings on this computer
  (`~/.config/meshtastic/settings.json`), the booleans `auto_connect`,
  `mqtt_observer`, `mqtt_observer_all_regions`, `map_world_nodes`,
  `map_gateway_links` and `online_tiles`. They are written after the node, together with the
  node's port as the address the app connects to (`last_address`), keeping
  every other setting of the file. The app must not be running: it reads
  the file at start-up only and rewrites it when it saves.

Applying: the settings that differ are written inside one settings
transaction (`begin_edit_settings` / `commit_edit_settings`), so the node
saves and reboots once, at the commit. The tool then reconnects and
compares again; the result is "the node matches the profile" or the list
of remaining differences.

## Linux package

For people who should not need a terminal: a Debian package that installs
the window under `/opt/meshtastic-provision` with a menu entry ("Meshtastic
node provisioning") and an icon. It is built with PyInstaller on a machine
of the target architecture (x86_64 here, aarch64 on a Raspberry Pi), with
`dpkg-deb` and `fakeroot` installed:

```sh
packaging/build-linux.sh          # -> dist/meshtastic-provision_<version>_<arch>.deb
```

The user installs it by double-clicking the file (the software centre) or
with `sudo apt install ./meshtastic-provision_<version>_<arch>.deb`, then
opens it from the menu. Plugging a node in is enough: the window detects
it; the package's udev rule lets the user open the serial port without any
group or password. Nothing to type.

The package holds Python, Qt and the libraries (about 200 MB installed,
66 MB to download). `packaging/` has the PyInstaller spec, the menu entry
and the icon.

## Files

| Path | Role |
|------|------|
| `provision.py` | The steps: ports, downloads, chip, flash, profile, compare, apply |
| `provision_cli.py` | Command line |
| `provision_gui.py` | Qt window (PySide6) |
| `profile_form.py` | The settings form of the window |
| `usages.py` | The usages a node can be prepared for, as settings (the window's Usage list) |
| `timezones.py` | Named time zones (`Europe/Paris`) to and from the node's POSIX rule |
| `geolocate.py` | This computer's position for "My position" (BeaconDB, then the IP address) |
| `wifi.py` | The Wi-Fi networks this computer sees (NetworkManager), for the Nearby menu |
| `node-profile.example.yaml` | Documented example profile |
| `doc/node-setup.md` | The manual set-up of the first node, which the profile automates |
| `doc/Usages.md` | What Meshtastic is used for and how it answers each need (in French) |
| `~/.config/meshtastic/node-profile.yaml` | The settings: saved by the window, default profile of the command line |
| `~/.config/meshtastic/backups/` | Settings exported before a flash (window) |
| `~/.cache/meshtastic-provision/` | Downloaded firmware images |
| `packaging/` | PyInstaller spec, menu entry, icon and the `.deb` build script |
| `.venv/`, `build/`, `dist/` | uv's environment and the build outputs (not tracked) |

Backups are the meshtastic CLI's `--export-config` YAML, which
`uvx meshtastic --port /dev/ttyUSB0 --configure <file>` restores.

## License

MIT, see [LICENSE](LICENSE).
