"""Windows Docker API-server wiring — #39598 items 1-2.

Static guards so the Windows compose path cannot silently regress:
.env.example must document the API_SERVER_* vars, and
docker-compose.windows.yml must publish 8642 (bridge, not host mode).
"""

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]


def _read(name):
    return (REPO_ROOT / name).read_text(encoding="utf-8")


class TestApiServerDocumented:
    def test_env_example_lists_api_server_vars(self):
        body = _read(".env.example")
        for var in ("API_SERVER_KEY", "API_SERVER_HOST", "API_SERVER_PORT"):
            assert var in body, f".env.example never mentions {var} (#39598 item 1)"

    def test_base_compose_mentions_api_server_port(self):
        body = _read("docker-compose.yml")
        assert "API_SERVER_PORT" in body, "base compose hides API_SERVER_PORT (#39598 item 1)"


class TestWindowsComposeApiPort:
    def _gateway(self):
        doc = yaml.safe_load(_read("docker-compose.windows.yml"))
        return doc["services"]["gateway"]

    def test_windows_gateway_publishes_8642(self):
        gw = self._gateway()
        ports = [str(p) for p in (gw.get("ports") or [])]
        assert any("8642" in p for p in ports), (
            f"windows gateway publishes no 8642 mapping, got {ports} (#39598 item 2)"
        )

    def test_windows_gateway_not_host_network_mode(self):
        gw = self._gateway()
        assert gw.get("network_mode", "bridge") != "host", (
            "network_mode: host discards ports: on Docker Desktop Windows (#39598 item 2)"
        )
