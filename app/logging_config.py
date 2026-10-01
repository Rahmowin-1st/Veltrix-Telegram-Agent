from __future__ import annotations

import logging

from app.security.redaction import redact_text


class SecretRedactionFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        # Render the original %-formatted message first so numeric/string
        # placeholders keep their native types, then redact the final text.
        # Mutating record.args to strings breaks messages that use %d.
        try:
            rendered = record.getMessage()
        except Exception:
            return True
        record.msg = redact_text(rendered)
        record.args = ()
        return True


def configure_logging(level: str = "INFO") -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s :: %(message)s",
    )

    # httpx request logs may include Telegram Bot API URLs, which contain
    # the bot token in the path. Keep them out of normal application logs.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)

    redaction = SecretRedactionFilter()
    root = logging.getLogger()
    root.addFilter(redaction)
    for handler in root.handlers:
        handler.addFilter(redaction)
