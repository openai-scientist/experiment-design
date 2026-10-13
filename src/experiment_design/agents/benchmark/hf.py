"""Read-only HuggingFace metadata client. Absence (404) differs from unavailability."""

import httpx


class HFUnavailable(Exception):
    """The Hub could not answer: network error, 429, 5xx, auth failure or malformed body."""


class HFClient:
    def __init__(
        self,
        token: str | None = None,
        transport: httpx.BaseTransport | None = None,
        base_url: str = "https://huggingface.co",
        timeout: float = 20.0,
    ):
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        self._client = httpx.Client(
            base_url=base_url, headers=headers, timeout=timeout, transport=transport
        )

    def __repr__(self) -> str:
        return f"HFClient(base_url={str(self._client.base_url)!r})"

    def close(self) -> None:
        self._client.close()

    def search_datasets(self, query: str, limit: int = 10) -> list[dict]:
        body = self._get("/api/datasets", {"search": query, "limit": limit})
        if not isinstance(body, list) or not all(isinstance(item, dict) for item in body):
            raise HFUnavailable("unexpected search response")
        return body

    def dataset_info(self, repo_id: str) -> dict | None:
        return self._info(f"/api/datasets/{repo_id}")

    def model_info(self, repo_id: str) -> dict | None:
        return self._info(f"/api/models/{repo_id}")

    def _info(self, path: str) -> dict | None:
        body = self._get(path, None, missing_ok=True)
        if body is None:
            return None
        if not isinstance(body, dict):
            raise HFUnavailable("unexpected metadata response")
        return body

    def _get(self, path: str, params: dict | None, *, missing_ok: bool = False):
        try:
            response = self._client.get(path, params=params)
        except httpx.HTTPError as exc:
            raise HFUnavailable(f"network error: {type(exc).__name__}") from exc
        if response.status_code == 404 and missing_ok:
            return None
        if response.status_code != 200:
            raise HFUnavailable(f"HTTP {response.status_code}")
        try:
            return response.json()
        except ValueError as exc:
            raise HFUnavailable("response is not JSON") from exc
