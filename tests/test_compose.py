"""The docker-compose file builds this plugin from its own repository. Farmhand's Plugins page shows a copy of it
(frontend/src/plugins/compose/stream.yml in Farmhand's repository): change both together."""
from __future__ import annotations

from pathlib import Path

import yaml

PLUGIN = Path(__file__).resolve().parent.parent


def test_compose_file_is_valid_and_builds_the_plugin_from_its_own_repository():
    data = yaml.safe_load((PLUGIN / "docker-compose.yml").read_text(encoding="utf-8"))
    stream = data["services"]["stream"]
    assert stream["build"] == "https://github.com/The-Original-Reth/farmhand-youtube-plugin.git#main"
    assert stream["ports"] == ["8080:8080"]
    assert stream["env_file"] == [{"path": "stream.env", "required": False}]
    assert "stream-data:/data" in stream["volumes"]
    assert "stream-data" in data["volumes"]
