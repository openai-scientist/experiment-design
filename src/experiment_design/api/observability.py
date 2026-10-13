"""Keep credentials out of log output."""

import logging
import threading

_MASK = "***"


class SecretFilter(logging.Filter):
    """Replaces every registered secret in a record's message and arguments."""

    def __init__(self) -> None:
        super().__init__()
        self._secrets: set[str] = set()
        self._lock = threading.Lock()

    def add(self, secret: str) -> None:
        if secret:
            with self._lock:
                self._secrets.add(secret)

    def _scrub(self, value):
        if isinstance(value, str):
            for secret in list(self._secrets):
                value = value.replace(secret, _MASK)
            return value
        if isinstance(value, tuple):
            return tuple(self._scrub(v) for v in value)
        if isinstance(value, dict):
            return {k: self._scrub(v) for k, v in value.items()}
        if isinstance(value, BaseException):
            return self._scrub(str(value))
        return value

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = self._scrub(record.getMessage()) if record.args else self._scrub(record.msg)
        record.args = None
        if record.exc_info:
            record.exc_text = self._scrub(logging.Formatter().formatException(record.exc_info))
            record.exc_info = None
        return True


SECRETS = SecretFilter()


def install_logging(*secrets: str) -> SecretFilter:
    """Attach the shared filter to the root handlers; later secrets go through ``SECRETS.add``."""
    for secret in secrets:
        SECRETS.add(secret)
    logging.basicConfig(level=logging.INFO)
    for handler in logging.getLogger().handlers:
        if SECRETS not in handler.filters:
            handler.addFilter(SECRETS)
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        for handler in logging.getLogger(name).handlers:
            if SECRETS not in handler.filters:
                handler.addFilter(SECRETS)
    return SECRETS
