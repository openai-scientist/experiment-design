"""The one retry the read-only metadata clients share: public APIs drop or throttle a request now
and then, and one failed lookup must not leave a real resource unverified."""

import time

import httpx

#: Seconds before the single retry (tests set it to 0).
RETRY_DELAY = 2.0


def get(client: httpx.Client, path: str, params: dict | None = None) -> httpx.Response:
    """GET ``path``; a network error, 429 or 5xx is retried once. A second network error raises."""
    try:
        response = client.get(path, params=params)
    except httpx.HTTPError:
        time.sleep(RETRY_DELAY)
        return client.get(path, params=params)
    if response.status_code == 429 or response.status_code >= 500:
        time.sleep(RETRY_DELAY)
        return client.get(path, params=params)
    return response
