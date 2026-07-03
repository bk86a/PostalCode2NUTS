"""Photon geocoding client — turns a free-form address into (lat, lon).

Talks to a self-hosted Photon instance over the URL supplied by config
(loopback in production, so address PII never leaves the host). Never raises:
any transport/HTTP/parse failure yields None so the /resolve cascade can fall
back to the postal result.

Query strategy: Photon's free-text ``q`` returns NOTHING when a street and a
postcode are combined in one string (e.g. "Museumpark 25, 3000 AE Rotterdam" →
0 results) even though "Museumpark 25, Rotterdam" resolves fine. So we try a
sequence of queries from most to least specific — street+city, then
postcode+city, then city — and never put the street and the postcode together.
The first query that returns a feature wins; the coarser fallbacks still land in
the correct NUTS-3 region.
"""

from __future__ import annotations

import httpx


class PhotonClient:
    def __init__(self, base_url: str, client: httpx.Client, timeout: float = 5.0):
        self._base = base_url.rstrip("/")
        self._client = client
        self._timeout = timeout

    @staticmethod
    def _candidates(street: str, city: str, postal_code: str) -> list[str]:
        """Ordered, de-duplicated query candidates — never street+postcode together."""
        out: list[str] = []
        if street and city:
            out.append(f"{street}, {city}")
        if postal_code and city:
            out.append(f"{postal_code} {city}")
        if city:
            out.append(city)
        if postal_code:
            out.append(postal_code)
        if street and not city and not postal_code:
            out.append(street)
        seen: set[str] = set()
        return [q for q in out if not (q in seen or seen.add(q))]

    def _query(self, q: str) -> tuple[float, float] | None:
        try:
            resp = self._client.get(
                f"{self._base}/api",
                params={"q": q, "limit": 1},
                timeout=self._timeout,
            )
            resp.raise_for_status()
            data = resp.json()
            feats = data.get("features") or []
            if not feats:
                return None
            coords = (feats[0].get("geometry") or {}).get("coordinates") or []
            if len(coords) < 2:
                return None
            # (lat, lon) from [lon, lat]; a non-Point feature nests coordinates,
            # so float() would raise TypeError — caught here to honour "never raises".
            return (float(coords[1]), float(coords[0]))
        except (httpx.HTTPError, ValueError, TypeError, IndexError):
            return None

    def geocode(
        self, street: str | None, city: str | None, postal_code: str | None
    ) -> tuple[float, float] | None:
        candidates = self._candidates(
            (street or "").strip(), (city or "").strip(), (postal_code or "").strip()
        )
        for q in candidates:
            coord = self._query(q)
            if coord is not None:
                return coord
        return None
