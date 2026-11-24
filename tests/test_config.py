from pathlib import Path

import pytest

from mybolide.config import load_config
from mybolide.service import main

ROOT = Path(__file__).resolve().parents[1]


def test_example_config_is_rejected_until_the_token_is_replaced(tmp_path):
    example = ROOT / "config.example.toml"
    with pytest.raises(ValueError, match="change-me"):
        load_config(example)
    text = example.read_text(encoding="utf-8").replace(
        'token = "change-me"', 'token = "real-token"'
    )
    path = tmp_path / "config.toml"
    path.write_text(text, encoding="utf-8")
    config = load_config(path)
    assert config.device == "/dev/ibus0"
    assert config.baud == 9600
    assert config.parity == "even"
    assert config.rtscts is False
    assert config.gap_ms == 50
    assert config.bind_host == "0.0.0.0"
    assert config.bind_port == 8741
    assert config.token == "real-token"
    assert config.max_db_mb is None
    assert config.tank_litres == 63.0
    assert config.batch_ms == 1000
    assert config.mdns is True
    assert config.ble is True


def test_main_exits_when_the_config_is_missing(tmp_path):
    assert main(["--config", str(tmp_path / "missing.toml")]) == 1


def test_max_db_mb_is_an_optional_override(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('token = "real-token"\nmax_db_mb = 512\n', encoding="utf-8")
    loaded = load_config(path)
    assert loaded.max_db_mb == 512
    assert loaded.ble is True
    path.write_text('token = "real-token"\nble = false\n', encoding="utf-8")
    assert load_config(path).ble is False
    path.write_text('token = "real-token"\nble = "yes"\n', encoding="utf-8")
    with pytest.raises(ValueError, match="ble"):
        load_config(path)
    path.write_text('token = "real-token"\nmax_db_mb = 0\n', encoding="utf-8")
    with pytest.raises(ValueError, match="max_db_mb"):
        load_config(path)
    path.write_text('token = "real-token"\ntank_litres = 70\n', encoding="utf-8")
    assert load_config(path).tank_litres == 70.0


def test_rejects_bad_parity_and_bind(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('token = "real-token"\nparity = "odd"\n', encoding="utf-8")
    with pytest.raises(ValueError, match="parity"):
        load_config(path)
    path.write_text('token = "real-token"\nbind = "8741"\n', encoding="utf-8")
    with pytest.raises(ValueError, match="bind"):
        load_config(path)
