#!/bin/sh
# Install the logger on Raspberry Pi OS. Run from a checkout, as root.
# The Pi needs network access once, so pip can fetch zeroconf and dbus-next.
set -eu

if [ "$(id -u)" -ne 0 ]; then
  echo "Run this as root." >&2
  exit 1
fi

ROOT=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
VENV=/opt/mybolide/venv

if ! python3 -c "import venv, ensurepip" >/dev/null 2>&1; then
  echo "python3-venv is required. Install it with: apt install python3-venv" >&2
  exit 1
fi

if ! getent group dialout >/dev/null 2>&1; then
  echo "The dialout group is missing, so the service cannot open the serial port." >&2
  exit 1
fi

SHELL_PATH=/usr/sbin/nologin
if [ ! -x "$SHELL_PATH" ]; then
  SHELL_PATH=/bin/false
fi

if ! id mybolide >/dev/null 2>&1; then
  useradd --system --user-group --home-dir /var/lib/mybolide --shell "$SHELL_PATH" mybolide
fi
usermod -aG dialout mybolide
if ! getent group bluetooth >/dev/null 2>&1; then
  groupadd --system bluetooth
fi
usermod -aG bluetooth mybolide

install -d -o mybolide -g mybolide -m 0750 /var/lib/mybolide
install -d -o root -g mybolide -m 0750 /etc/mybolide
install -d -m 0755 /opt/mybolide

python3 -m venv "$VENV"
"$VENV/bin/pip" install "$ROOT"

install -m 0644 "$ROOT/deploy/99-ibus.rules" /etc/udev/rules.d/99-mybolide-ibus.rules
install -m 0644 "$ROOT/deploy/mybolide.service" /etc/systemd/system/mybolide.service
install -d -m 0755 /etc/dbus-1/system.d
install -m 0644 "$ROOT/deploy/mybolide-ble.conf" /etc/dbus-1/system.d/mybolide-ble.conf
systemctl reload dbus >/dev/null 2>&1 || true

if [ ! -x /usr/sbin/bluetoothd ] && [ ! -x /usr/libexec/bluetooth/bluetoothd ]; then
  echo "bluez is not installed, so the iPhone wake beacon will not advertise." >&2
  echo "Install it with: apt install bluez" >&2
fi

if [ ! -f /etc/mybolide/config.toml ]; then
  TOKEN=$(python3 -c "import secrets; print(secrets.token_urlsafe(32))")
  sed "s/^token = .*/token = \"${TOKEN}\"/" "$ROOT/config.example.toml" > /etc/mybolide/config.toml
  chown root:mybolide /etc/mybolide/config.toml
  chmod 0640 /etc/mybolide/config.toml
  echo "Wrote /etc/mybolide/config.toml with a new bearer token."
else
  echo "Kept the existing /etc/mybolide/config.toml."
fi

udevadm control --reload-rules
udevadm trigger --subsystem-match=tty || true
systemctl daemon-reload
systemctl enable mybolide.service
systemctl restart mybolide.service
echo "mybolide is enabled. The sync token is in /etc/mybolide/config.toml."
