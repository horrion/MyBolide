"""Runtime configuration loaded from a TOML file."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

PLACEHOLDER_TOKEN = "change-me"
SUPPORTED_BAUDS = frozenset({9600, 19200, 38400, 57600, 115200})


@dataclass(frozen=True, slots=True)
class Config:
    device: str = "/dev/ibus0"
    baud: int = 9600
    parity: str = "even"
    rtscts: bool = False
    gap_ms: int = 50
    bind_host: str = "0.0.0.0"
    bind_port: int = 8741
    token: str = ""
    db_path: str = "/var/lib/mybolide/bus.sqlite"
    max_db_mb: int | None = None
    tank_litres: float = 63.0
    batch_ms: int = 1000
    mdns: bool = True
    ble: bool = True

    @property
    def gap_s(self) -> float:
        return self.gap_ms / 1000


def load_config(path: Path) -> Config:
    try:
        with path.open("rb") as handle:
            raw = tomllib.load(handle)
    except FileNotFoundError as exc:
        raise ValueError(f"config file not found: {path}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(f"config file is not valid TOML: {path}") from exc

    if not isinstance(raw, dict):
        raise ValueError("config file must be a TOML table")

    parity = str(raw.get("parity", "even")).lower()
    if parity not in ("even", "none"):
        raise ValueError("parity must be 'even' or 'none'")

    baud = int(raw.get("baud", 9600))
    if baud not in SUPPORTED_BAUDS:
        raise ValueError(f"unsupported baud {baud}")

    gap_ms = int(raw.get("gap_ms", 50))
    if gap_ms <= 0:
        raise ValueError("gap_ms must be positive")

    batch_ms = int(raw.get("batch_ms", 1000))
    if batch_ms <= 0:
        raise ValueError("batch_ms must be positive")

    max_db_mb = _optional_mib(raw.get("max_db_mb", None), present="max_db_mb" in raw)
    tank_litres = float(raw.get("tank_litres", 63.0))
    if tank_litres <= 0:
        raise ValueError("tank_litres must be positive")

    host, port = _parse_bind(str(raw.get("bind", "0.0.0.0:8741")))
    token = str(raw.get("token", "")).strip()
    if not token or token == PLACEHOLDER_TOKEN:
        raise ValueError(
            "config token must be a secret other than 'change-me'"
        )

    mdns = raw.get("mdns", True)
    if not isinstance(mdns, bool):
        raise ValueError("mdns must be true or false")

    ble = raw.get("ble", True)
    if not isinstance(ble, bool):
        raise ValueError("ble must be true or false")

    rtscts = raw.get("rtscts", False)
    if not isinstance(rtscts, bool):
        raise ValueError("rtscts must be true or false")

    return Config(
        device=str(raw.get("device", "/dev/ibus0")),
        baud=baud,
        parity=parity,
        rtscts=rtscts,
        gap_ms=gap_ms,
        bind_host=host,
        bind_port=port,
        token=token,
        db_path=str(raw.get("db_path", "/var/lib/mybolide/bus.sqlite")),
        max_db_mb=max_db_mb,
        tank_litres=tank_litres,
        batch_ms=batch_ms,
        mdns=mdns,
        ble=ble,
    )


def _optional_mib(value: object, *, present: bool) -> int | None:
    if not present or value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("max_db_mb must be a whole number of megabytes")
    if value <= 0:
        raise ValueError("max_db_mb must be positive")
    return value


def _parse_bind(value: str) -> tuple[str, int]:
    if ":" not in value:
        raise ValueError("bind must look like '0.0.0.0:8741'")
    host, port_text = value.rsplit(":", 1)
    if not host:
        raise ValueError("bind is missing a host")
    if not port_text.isdigit():
        raise ValueError("bind port must be a number")
    port = int(port_text)
    if port < 1 or port > 65535:
        raise ValueError("bind port is out of range")
    return host, port
