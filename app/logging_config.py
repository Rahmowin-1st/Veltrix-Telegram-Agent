from __future__ import annotations

import logging

from app.security.redaction import redact_text


class SecretRedactionFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = redact_text(record.msg)
        if record.args:
            record.args = tuple(redact_text(str(v)) for v in record.args)
        return True


def configure_logging(level: str = "INFO") -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s :: %(message)s",
    )
    root = logging.getLogger()
    root.addFilter(SecretRedactionFilter())
    for handler in root.handlers:
        handler.addFilter(SecretRedactionFilter())
