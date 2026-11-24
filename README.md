# MyBolide iBus logger

A listen-only service for a Raspberry Pi. It reads a Resler-style BMW iBus USB adapter, keeps every frame on disk, and decodes the instrument-cluster values an iPhone can graph later. The phone pulls the cache and acknowledges it. Until that ack, nothing is deleted, apart from a disk cap that protects the SD card.

The iPhone app is not in this repo. The contract it will use is [openapi.yaml](openapi.yaml).

## What is recorded

Every checksum-valid frame is stored. These cluster broadcasts also become graph series:

| Signal | Meaning |
| --- | --- |
| `speed_kmh` | Byte times 2. Raw `0xFF` is omitted. |
| `rpm` | Byte times 100. |
| `ambient_c`, `coolant_c` | Celsius. Coolant `0x7F` extends past 126 C. |
| `odometer_km` | Little-endian kilometres. |
| `ignition` | 0 off, 1 accessory, 2 run, 3 start. |
| `fuel_l` | Litres in the tank. The cluster sends 0 for empty and 255 for full; litres are that fraction of `tank_litres`. |

Buttons, lights, radio, and locks stay in the raw frame log. Boost, fuel trims, and other engine PIDs are not on this bus. Clusters built before about 2000 often leave fuel off the bus; those frames are still stored, without a `fuel_l` sample. Set `tank_litres` to the car's full-tank capacity.

The service never writes to the serial port. It opens the adapter read-only.

## Develop

Python 3.11 or newer:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
pytest
```

The tests use recorded frames and a virtual serial port. No car or adapter is required.

## Install on the Pi

From a checkout, as root, with network access for the one-time `pip install`:

```bash
sudo deploy/install.sh
```

The script creates the `mybolide` user, a virtualenv in `/opt/mybolide/venv`, a udev symlink at `/dev/ibus0` for FTDI adapters (`0403:6001` and `0403:6015`), and a systemd unit. The unit is enabled, so the logger starts on boot and restarts if it exits. It waits for the adapter and keeps serving the cache if the USB device is unplugged.

The bearer token is written to `/etc/mybolide/config.toml` the first time. Later installs keep that file. If `parity = "even"` captures nothing, the adapter is probably presenting 8N1; set `parity = "none"` and restart:

```bash
sudo systemctl restart mybolide
journalctl -u mybolide -f
```

## Sync

The API listens on port `8741` and is advertised as `_mybolide._tcp`. That port is clear of the HTTP and AirPlay ports a CarPlay stack usually wants.

```bash
curl -s -H "Authorization: Bearer TOKEN" \
  "http://192.168.4.1:8741/v1/sync/signals?after=0&limit=1000"
curl -s -H "Authorization: Bearer TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"signals_through": 10, "frames_through": 4}' \
  http://192.168.4.1:8741/v1/sync/ack
```

`mono_ms` stays ordered for one boot even when the Pi has no RTC. `wall_ms` is Unix time, and `clock_trusted` is true only after systemd-timesyncd has synchronized the clock. If `oldest_id` jumps ahead of the id you asked for, the disk cap dropped rows the phone had not collected yet.

## Background sync

The Pi advertises Bluetooth LE service `8f3c2a10-6b1e-4d55-9c0a-1e7b54a9d2f0`. iOS background scanning matches that UUID. A readable characteristic, `8f3c2a11-6b1e-4d55-9c0a-1e7b54a9d2f0`, returns a short JSON object:

```json
{"pending": true, "port": 8741}
```

`port` is the HTTP API. `pending` is true while any frame or signal row is still unacknowledged, so a woken app can leave an empty cache alone. The bearer token stays in the config file and in the phone. The advertisement and the characteristic omit it.

The first launch has to be opened by the owner. That is when the app stores the token and iOS shows the Bluetooth and Local Network prompts. After that, a Core Bluetooth state-restoration scan can relaunch the app when the phone hears the service. The relaunch happens only while Bluetooth and Background App Refresh stay on, and only if the owner has not swiped the app away. Low Power Mode makes the wake less reliable.

On wake, the app reads the characteristic. When `pending` is true and port `8741` is reachable, it pulls and acknowledges with a background `URLSession`. When the port is not reachable, it leaves the cache on the Pi. Wireless CarPlay often keeps the phone's Wi-Fi, so the phone can hear the beacon and still be unable to open the port until both devices share a network. A day of iBus frames goes over HTTP. The Bluetooth packet is only the doorbell.

Set `ble = false` if a later CarPlay stack needs the built-in controller to itself. A missing adapter is logged, and capture keeps running. The beacon needs BlueZ; `deploy/install.sh` adds the `mybolide` user to the `bluetooth` group, installs a D-Bus policy, and orders the unit after `bluetooth.target`.

## Sharing the Pi with CarPlay

The unit runs at low CPU and idle IO priority. It binds one high TCP port and opens only the FTDI iBus device. It leaves Wi-Fi and the display to CarPlay, and uses the built-in Bluetooth controller only for the wake-up beacon above. Give CarPlay a different USB port from the iBus adapter. If you add another FTDI device later, narrow `deploy/99-ibus.rules` with that adapter's serial number.

Commits land about once a second (`batch_ms`). A hard cut of 12 V can still corrupt an SD card; that is a power-supply question, not something this process can prevent.

On startup the logger reads the size of the filesystem that holds the cache, which on the Pi is the inserted SD card. The cap is at most half of that card, and it always leaves a quarter of the card free for the OS, CarPlay, and anything else installed there. On an 8 GiB or larger card that reserve is at least 2 GiB. Set `max_db_mb` to choose the cap yourself. A value larger than the free space is reduced so 64 MiB stays free.
