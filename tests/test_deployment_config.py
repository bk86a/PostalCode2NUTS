"""Guards on the shipped container configuration.

These assert on Dockerfile text rather than behaviour: the settings they cover
are uvicorn command-line flags, so nothing inside the app can observe them, and
a regression here is silent — the service keeps working while a control is off.
"""

import re
from pathlib import Path

DOCKERFILE = Path(__file__).resolve().parents[1] / "Dockerfile"


def _cmd_line() -> str:
    for line in DOCKERFILE.read_text().splitlines():
        if line.startswith("CMD "):
            return line
    raise AssertionError("no CMD line in Dockerfile")


class TestForwardedAllowIps:
    def test_does_not_trust_every_peer(self):
        """--forwarded-allow-ips '*' lets any client set X-Forwarded-For, and
        uvicorn takes its leftmost entry — a per-request rate-limit bucket and a
        forged client IP in the access log."""
        cmd = _cmd_line()
        assert not re.search(r"--forwarded-allow-ips\s+['\"]?\*", cmd)

    def test_defaults_to_loopback_and_is_operator_overridable(self):
        cmd = _cmd_line()
        assert "PC2NUTS_FORWARDED_ALLOW_IPS:-127.0.0.1" in cmd

    def test_proxy_headers_still_enabled(self):
        assert "--proxy-headers" in _cmd_line()

    def test_access_log_still_disabled(self):
        """uvicorn's access log carries /resolve street/city query params."""
        assert "--no-access-log" in _cmd_line()
