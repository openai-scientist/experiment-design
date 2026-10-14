"""``exd-api``: serve the engine over HTTP."""

import os

import uvicorn

from experiment_design.api.app import create_app
from experiment_design.api.observability import install_logging
from experiment_design.api.settings import ApiSettings


def main() -> None:
    settings = ApiSettings.from_env()
    install_logging(settings.service_key.get_secret_value())
    uvicorn.run(create_app(settings), host="0.0.0.0", port=int(os.environ.get("EXD_PORT", "8000")))


if __name__ == "__main__":
    main()
