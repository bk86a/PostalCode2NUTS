"""Photon geocoding client — turns a free-form address into (lat, lon).

Talks to a self-hosted Photon instance over the URL supplied by config
(loopback in production, so address PII never leaves the host). Never raises:
any transport/HTTP/parse failure yields None so the /resolve cascade can fall
back to the postal result.
"""

from __future__ import annotations

import httpx


class PhotonClient:
    def __init__(self, base_url: str, client: httpx.Client, timeout: float = 5.0):
        self._base = base_url.rstrip("/")
        self._client = client
        self._timeout = timeout

    def geocode(
        self, street: str | None, city: str | None, postal_code: str | None
    ) -> tuple[float, float] | None:
        loc = f"{(postal_code or '').strip()} {(city or '').strip()}".strip()
        parts = [p for p in ((street or "").strip(), loc) if p]
        query = ", ".join(parts)
        if not query:
            return None
        try:
            resp = self._client.get(
                f"{self._base}/api",
                params={"q": query, "limit": 1},
                timeout=self._timeout,
            )
            resp.raise_for_status()
            data = resp.json()
        except (httpx.HTTPError, ValueError):
            return None
        feats = data.get("features") or []
        if not feats:
            return None
        coords = (feats[0].get("geometry") or {}).get("coordinates") or []
        if len(coords) < 2:
            return None
        return (float(coords[1]), float(coords[0]))  # (lat, lon) from [lon, lat]
