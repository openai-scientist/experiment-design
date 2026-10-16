"""Read-only Zenodo record client for resources whose DOI or URL is a Zenodo record."""

import httpx

from experiment_design.agents.benchmark import http


class ZenodoUnavailable(Exception):
    """Zenodo could not answer: network error, 429, 5xx or malformed body."""


class ZenodoClient:
    def __init__(
        self,
        transport: httpx.BaseTransport | None = None,
        base_url: str = "https://zenodo.org",
        timeout: float = 20.0,
    ):
        self._client = httpx.Client(
            base_url=base_url,
            headers={"Accept": "application/json"},
            timeout=timeout,
            transport=transport,
            follow_redirects=True,
        )

    def __repr__(self) -> str:
        return f"ZenodoClient(base_url={str(self._client.base_url)!r})"

    def close(self) -> None:
        self._client.close()

    def record(self, record_id: str) -> dict | None:
        try:
            response = http.get(self._client, f"/api/records/{record_id}")
        except httpx.HTTPError as exc:
            raise ZenodoUnavailable(f"network error: {type(exc).__name__}") from exc
        if response.status_code == 404:
            return None
        if response.status_code != 200:
            raise ZenodoUnavailable(f"HTTP {response.status_code}")
        try:
            body = response.json()
        except ValueError as exc:
            raise ZenodoUnavailable("response is not JSON") from exc
        if not isinstance(body, dict):
            raise ZenodoUnavailable("unexpected record response")
        return body
