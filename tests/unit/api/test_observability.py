import io
import logging

from experiment_design.api.observability import SecretFilter, install_logging

SECRET = "sk-contract-secret-0000"


def test_logged_secret_is_masked_on_the_handler_output():
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    root = logging.getLogger()
    root.addHandler(handler)
    try:
        filt = install_logging()
        filt.add(SECRET)
        logging.getLogger("experiment_design").error("key=%s", SECRET)
        logging.getLogger("experiment_design").error("raw " + SECRET)
    finally:
        root.removeHandler(handler)
    out = stream.getvalue()
    assert "***" in out and SECRET not in out


def test_empty_secret_is_ignored():
    filt = SecretFilter()
    filt.add("")
    record = logging.LogRecord("t", logging.INFO, "f", 1, "plain text", None, None)
    assert filt.filter(record) and record.getMessage() == "plain text"
