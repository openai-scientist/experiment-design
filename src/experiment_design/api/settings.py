"""Service settings. Only two process variables are read, never a dotenv file."""

import os
from importlib import metadata
from pathlib import Path

from pydantic import SecretStr

from experiment_design.config import ConfigError
from experiment_design.contracts import Record


def _engine_version() -> str:
    try:
        return metadata.version("experiment-design")
    except metadata.PackageNotFoundError:
        return "0+unknown"


class ApiSettings(Record):
    config_path: Path = Path("exd.yaml")
    service_key: SecretStr
    engine_version: str = _engine_version()

    @classmethod
    def from_env(cls) -> "ApiSettings":
        key = os.environ.get("EXD_SERVICE_KEY", "")
        if not key:
            raise ConfigError("missing environment variable EXD_SERVICE_KEY")
        return cls(config_path=Path(os.environ.get("EXD_CONFIG", "exd.yaml")), service_key=key)
