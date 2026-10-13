"""Delivery of events, artifacts and telemetry to Platform, with bounded retries."""

import json
import threading
import time
from collections.abc import Callable, Sequence

import httpx

from experiment_design.api.contract_models import (
    CALLBACK_ARTIFACTS,
    CALLBACK_EVENTS,
    CALLBACK_TELEMETRY,
    EVENT_GAP,
    SERVICE_KEY_HEADER,
    STAGE_CLOSED,
    ArtifactMeta,
    EventBatch,
    TelemetryBatch,
    TelemetryRecord,
)
from experiment_design.persistence.envelope import EnvelopeLog

_BATCH = 100


class CallbackError(RuntimeError):
    pass


class StageClosed(CallbackError):
    """Platform closed this stage run: stop delivering and cancel local work."""


def _code(response: httpx.Response) -> str | None:
    try:
        body = response.json()
    except ValueError:
        return None
    return body.get("code") if isinstance(body, dict) else None


class PlatformClient:
    def __init__(
        self,
        callback_url: str,
        service_key: str,
        *,
        transport: httpx.BaseTransport | None = None,
        attempts: int = 4,
        base_delay: float = 0.5,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.base = callback_url.rstrip("/")
        self.attempts = attempts
        self.base_delay = base_delay
        self.sleep = sleep
        self._flush_lock = threading.Lock()
        self.client = httpx.Client(
            transport=transport, headers={SERVICE_KEY_HEADER: service_key}, timeout=30
        )

    def close(self) -> None:
        self.client.close()

    def __repr__(self) -> str:
        return f"PlatformClient({self.base!r})"

    def _post(self, path: str, **kwargs) -> httpx.Response:
        """Retry network errors, 429 and 5xx; return any other response to the caller."""
        failure = "no attempt made"
        for attempt in range(self.attempts):
            try:
                response = self.client.post(self.base + path, **kwargs)
            except httpx.HTTPError as exc:
                failure = type(exc).__name__
            else:
                if response.status_code != 429 and response.status_code < 500:
                    return response
                failure = f"HTTP {response.status_code}"
            if attempt < self.attempts - 1:
                self.sleep(self.base_delay * 2**attempt)
        raise CallbackError(f"Platform callback {path} failed: {failure}")

    @staticmethod
    def _reject(path: str, response: httpx.Response) -> CallbackError:
        code = _code(response)
        if response.status_code == 409 and code == STAGE_CLOSED:
            return StageClosed("Platform closed the stage run")
        return CallbackError(f"Platform rejected {path}: HTTP {response.status_code} {code or ''}")

    def flush_events(self, log: EnvelopeLog) -> int:
        """Deliver every event after the cursor, in order; returns how many were delivered."""
        delivered = 0
        with self._flush_lock:
            resends = 0
            while True:
                pending = log.since(log.delivered_seq)[:_BATCH]
                if not pending:
                    return delivered
                body = EventBatch(events=pending).model_dump(mode="json", by_alias=True)
                response = self._post(CALLBACK_EVENTS, json=body)
                if response.status_code == 200:
                    log.mark_delivered(pending[-1].source_seq)
                    delivered += len(pending)
                    continue
                gap = None
                if response.status_code == 409 and _code(response) == EVENT_GAP:
                    gap = response.json().get("delivered_seq")
                if isinstance(gap, int) and not isinstance(gap, bool) and gap >= 0:
                    resends += 1
                    if resends > self.attempts:
                        raise CallbackError("Platform keeps reporting an event gap")
                    log.reset_delivered(gap)  # resend from the next sequence; never cancel
                    continue
                raise self._reject(CALLBACK_EVENTS, response)

    def push_artifact(self, meta: ArtifactMeta, content: bytes) -> str:
        meta_json = json.dumps(meta.model_dump(mode="json", by_alias=True))
        response = self._post(
            CALLBACK_ARTIFACTS,
            files={
                "meta": (None, meta_json, "application/json"),
                "file": (meta.filename, content, meta.media_type),
            },
        )
        if response.status_code != 201:
            raise self._reject(CALLBACK_ARTIFACTS, response)
        try:
            return response.json()["artifact_id"]
        except (ValueError, KeyError, TypeError) as exc:
            raise CallbackError("Platform artifact response has no artifact_id") from exc

    def send_telemetry(self, records: Sequence[TelemetryRecord]) -> None:
        body = TelemetryBatch(records=list(records)).model_dump(mode="json", by_alias=True)
        response = self._post(CALLBACK_TELEMETRY, json=body)
        if response.status_code >= 300:
            raise self._reject(CALLBACK_TELEMETRY, response)
