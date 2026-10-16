"""Read-only GitHub metadata client for resources hosted on GitHub."""

import httpx

from experiment_design.agents.benchmark import http


class GitHubUnavailable(Exception):
    """GitHub could not answer: network error, 403/429, 5xx or malformed body."""


class GitHubClient:
    def __init__(
        self,
        token: str | None = None,
        transport: httpx.BaseTransport | None = None,
        base_url: str = "https://api.github.com",
        timeout: float = 20.0,
    ):
        headers = {"Accept": "application/vnd.github+json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        self._client = httpx.Client(
            base_url=base_url,
            headers=headers,
            timeout=timeout,
            transport=transport,
            follow_redirects=True,
        )

    def __repr__(self) -> str:
        return f"GitHubClient(base_url={str(self._client.base_url)!r})"

    def close(self) -> None:
        self._client.close()

    def repo(self, owner: str, name: str) -> dict | None:
        return self._get(f"/repos/{owner}/{name}")

    def latest_release(self, owner: str, name: str) -> dict | None:
        return self._get(f"/repos/{owner}/{name}/releases/latest")

    def commit(self, owner: str, name: str, ref: str) -> dict | None:
        return self._get(f"/repos/{owner}/{name}/commits/{ref}")

    def _get(self, path: str) -> dict | None:
        try:
            response = http.get(self._client, path)
        except httpx.HTTPError as exc:
            raise GitHubUnavailable(f"network error: {type(exc).__name__}") from exc
        if response.status_code == 404:
            return None
        if response.status_code != 200:
            raise GitHubUnavailable(f"HTTP {response.status_code}")
        try:
            body = response.json()
        except ValueError as exc:
            raise GitHubUnavailable("response is not JSON") from exc
        if not isinstance(body, dict):
            raise GitHubUnavailable("unexpected metadata response")
        return body
