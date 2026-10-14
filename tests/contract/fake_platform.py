"""A minimal Platform for engine tests: callback ingest, artifact hosting and fault injection.

It mirrors only what the vendored contract README states: service-key auth, contiguous
``source_seq`` with EVENT_GAP, STAGE_CLOSED, idempotent duplicate delivery, multipart
artifact upload and opaque artifact download URLs.
"""

import hashlib
import json
from dataclasses import dataclass, field
from email import message_from_bytes
from email.policy import default as default_policy

import httpx

from experiment_design.api.contract_models import (
    ArtifactMeta,
    ArtifactRef,
    EventBatch,
    EventEnvelope,
    TelemetryBatch,
    TelemetryRecord,
)

PREFIX = "/api/v1"
HOST = "http://platform.test"


@dataclass
class StoredArtifact:
    artifact_id: str
    meta: ArtifactMeta
    content: bytes


@dataclass
class FakePlatform:
    service_key: str = "svc-key-test"
    events: dict[str, list[EventEnvelope]] = field(default_factory=dict)
    artifacts: dict[str, list[StoredArtifact]] = field(default_factory=dict)
    telemetry: dict[str, list[TelemetryRecord]] = field(default_factory=dict)
    hosted: dict[str, bytes] = field(default_factory=dict)
    closed: set[str] = field(default_factory=set)
    fail_next: list[int] = field(default_factory=list)  # HTTP statuses for the next callbacks
    drop_event_acks: int = 0  # accept the next batches but answer 503, forcing a resend
    requests: list[httpx.Request] = field(default_factory=list)
    _counter: int = 0

    def callback_url(self, engine_key: str, stage_run_id: str) -> str:
        return f"{HOST}{PREFIX}/internal/engines/{engine_key}/runs/{stage_run_id}"

    def add_artifact(
        self, content: bytes, *, schema_id: str, version: int = 1, engine_key: str = "i2h"
    ) -> ArtifactRef:
        self._counter += 1
        artifact_id = f"in-{self._counter}"
        self.hosted[artifact_id] = content
        return ArtifactRef(
            artifact_id=artifact_id,
            schema_id=schema_id,
            version=version,
            sha256=hashlib.sha256(content).hexdigest(),
            download_url=f"{HOST}{PREFIX}/internal/engines/{engine_key}/artifacts/{artifact_id}",
            primary=True,
        )

    def delivered_seq(self, stage_run_id: str) -> int:
        events = self.events.get(stage_run_id, [])
        return events[-1].source_seq if events else 0

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    # --- request handling -----------------------------------------------------------------

    @staticmethod
    def _error(status: int, code: str, message: str = "error", **extra) -> httpx.Response:
        return httpx.Response(status, json={"code": code, "message": message, **extra})

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.headers.get("X-Service-Key") != self.service_key:
            return self._error(401, "SERVICE_KEY_INVALID")
        path = request.url.path
        marker = f"{PREFIX}/internal/engines/"
        if not path.startswith(marker):
            return self._error(404, "NOT_FOUND")
        parts = path[len(marker) :].split("/")
        if len(parts) == 3 and parts[1] == "artifacts" and request.method == "GET":
            data = self.hosted.get(parts[2])
            return (
                httpx.Response(200, content=data)
                if data is not None
                else self._error(404, "NOT_FOUND")
            )
        if len(parts) != 4 or parts[1] != "runs" or request.method != "POST":
            return self._error(404, "NOT_FOUND")
        stage_run_id, kind = parts[2], parts[3]
        if self.fail_next:
            return httpx.Response(self.fail_next.pop(0))
        if stage_run_id in self.closed:
            return self._error(409, "STAGE_CLOSED")
        if kind == "events":
            return self._events(stage_run_id, request)
        if kind == "artifacts":
            return self._artifact(stage_run_id, request)
        if kind == "telemetry":
            return self._telemetry(stage_run_id, request)
        return self._error(404, "NOT_FOUND")

    def _events(self, stage_run_id: str, request: httpx.Request) -> httpx.Response:
        try:
            batch = EventBatch.model_validate_json(request.content)
        except ValueError:
            return self._error(422, "VALIDATION_ERROR")
        stored = self.events.setdefault(stage_run_id, [])
        accepted = skipped = 0
        for event in batch.events:
            last = stored[-1].source_seq if stored else 0
            if event.source_seq <= last:
                skipped += 1
            elif event.source_seq == last + 1:
                stored.append(event)
                accepted += 1
            else:
                return self._error(409, "EVENT_GAP", delivered_seq=last)
        if self.drop_event_acks:
            self.drop_event_acks -= 1
            return httpx.Response(503)
        return httpx.Response(200, json={"accepted": accepted, "skipped": skipped})

    def _artifact(self, stage_run_id: str, request: httpx.Request) -> httpx.Response:
        header = b"Content-Type: " + request.headers["content-type"].encode() + b"\r\n\r\n"
        message = message_from_bytes(header + request.content, policy=default_policy)
        parts = {
            part.get_param("name", header="content-disposition"): part
            for part in message.iter_parts()
        }
        if "meta" not in parts or "file" not in parts:
            return self._error(422, "VALIDATION_ERROR")
        meta = ArtifactMeta.model_validate(json.loads(parts["meta"].get_content()))
        content = parts["file"].get_payload(decode=True)
        if hashlib.sha256(content).hexdigest() != meta.sha256:
            return self._error(422, "SHA256_MISMATCH")
        self._counter += 1
        artifact_id = f"out-{self._counter}"
        self.artifacts.setdefault(stage_run_id, []).append(
            StoredArtifact(artifact_id, meta, content)
        )
        return httpx.Response(201, json={"artifact_id": artifact_id})

    def _telemetry(self, stage_run_id: str, request: httpx.Request) -> httpx.Response:
        try:
            batch = TelemetryBatch.model_validate_json(request.content)
        except ValueError:
            return self._error(422, "VALIDATION_ERROR")
        self.telemetry.setdefault(stage_run_id, []).extend(batch.records)
        return httpx.Response(202, json={"accepted": len(batch.records)})
