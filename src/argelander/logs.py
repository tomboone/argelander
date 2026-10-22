"""Logging to stdout as JSON lines (spec §3.2), one object per record, for the services.

Each line holds the time, level, logger and message, any fields passed with `extra=`, and the
traceback when there is one. The long-running services call `configure_logging` once at
startup, except `web`, which hands uvicorn `dict_config`; library code only ever calls
`logging.getLogger(__name__)`.
"""

import json
import logging
import sys
from datetime import UTC, datetime

# Every attribute a bare LogRecord carries; whatever else a record has came from `extra=`.
# uvicorn passes `color_message`, its message again with ANSI colours, on its own records.
_STANDARD = frozenset(vars(logging.LogRecord("", 0, "", 0, "", None, None))) | {
    "message",
    "asctime",
    "color_message",
}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "time": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        entry.update((key, value) for key, value in vars(record).items() if key not in _STANDARD)
        if record.exc_info:
            entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(entry, default=str)


def dict_config(level: int = logging.INFO) -> dict[str, object]:
    """`configure_logging` as a `logging.config.dictConfig` dict, for a server that applies the
    config itself in every process it starts (uvicorn's `log_config`, under `--reload`)."""
    return {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {"json": {"()": JsonFormatter}},
        "handlers": {
            "stdout": {
                "class": "logging.StreamHandler",
                "formatter": "json",
                "stream": "ext://sys.stdout",
            }
        },
        # uvicorn's loggers keep their own handlers unless told otherwise; send them to root.
        "loggers": {
            name: {"handlers": [], "propagate": True}
            for name in ("uvicorn", "uvicorn.error", "uvicorn.access")
        },
        "root": {"level": level, "handlers": ["stdout"]},
    }


def configure_logging(level: int = logging.INFO) -> None:
    """Send every log record at `level` and above to stdout as JSON, replacing any handlers."""
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    logging.basicConfig(level=level, handlers=[handler], force=True)
