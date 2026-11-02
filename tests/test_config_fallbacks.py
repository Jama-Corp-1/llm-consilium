from pathlib import Path

import yaml

CONFIG = Path(__file__).resolve().parents[1] / "proxy" / "config.yaml"


def test_router_settings_have_retry_and_cooldown():
    data = yaml.safe_load(CONFIG.read_text())
    rs = data["router_settings"]
    assert rs["allowed_fails"] >= 1
    assert rs["cooldown_time"] >= 1
    assert "retry_policy" in rs

