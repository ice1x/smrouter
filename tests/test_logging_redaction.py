import logging
from io import StringIO

from genti.logging_utils import configure_logging


def test_configure_logging_redacts_tokens():
    stream = StringIO()
    configure_logging("INFO", redactions=("123:ABC", "yt-key"), stream=stream)

    logger = logging.getLogger("redaction-test")
    logger.info("Tokens %s and %s", "123:ABC", "yt-key")

    output = stream.getvalue()
    assert "123:ABC" not in output
    assert "yt-key" not in output
    assert output.count("***REDACTED***") == 2
