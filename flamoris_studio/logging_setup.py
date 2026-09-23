"""Structured operational diagnostics without prompt, token or binary payloads."""
import json
import logging
import os
from datetime import datetime, timezone


class JsonFormatter(logging.Formatter):
    def format(self, record):
        return json.dumps({"time": datetime.fromtimestamp(record.created, timezone.utc).isoformat(),
                           "level": record.levelname, "category": record.name,
                           "event": record.getMessage()}, ensure_ascii=False)


def configure_logging():
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    logger = logging.getLogger("flamoris.studio")
    logger.handlers = [handler]
    logger.propagate = False
    logger.setLevel(os.getenv("STUDIO_LOG_LEVEL", "INFO").upper())
    return logger
