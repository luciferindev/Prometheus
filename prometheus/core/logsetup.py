"""Structured JSON-lines logging with credential redaction."""
from __future__ import annotations

import json
import logging
import logging.handlers
import re
import time

from . import paths

# Anthropic keys, bearer tokens, and JWT-shaped Microsoft/Minecraft tokens.
_SECRET = re.compile(r"(sk-ant-[A-Za-z0-9_\-]{6,}|Bearer\s+[A-Za-z0-9._\-]{12,}|eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9._\-]{10,})")


def redact(text: str) -> str:
    return _SECRET.sub("[redacted]", text)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(record.created)) + f".{int(record.msecs):03d}",
            "level": record.levelname,
            "source": record.name,
            "msg": redact(record.getMessage()),
        }
        if record.exc_info:
            entry["exc"] = redact(self.formatException(record.exc_info))
        return json.dumps(entry, ensure_ascii=False)


def setup() -> logging.Logger:
    root = logging.getLogger("prometheus")
    if root.handlers:
        return root
    root.setLevel(logging.INFO)
    root.propagate = False
    directory = paths.ensure_private_dir(paths.log_dir())
    handler = logging.handlers.RotatingFileHandler(
        directory / "prometheus.jsonl", maxBytes=2_000_000, backupCount=3, encoding="utf-8")
    handler.setFormatter(JsonFormatter())
    root.addHandler(handler)
    return root
