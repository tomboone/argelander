"""JSON-lines logging (spec §3.2): one parseable object per record, extras and tracebacks kept."""

import json
import logging
import sys

from argelander.logs import JsonFormatter


def _format(record: logging.LogRecord) -> dict[str, object]:
    return json.loads(JsonFormatter().format(record))


def test_a_record_becomes_one_json_object_with_its_extra_fields() -> None:
    record = logging.makeLogRecord(
        {"name": "argelander.fetch.worker", "levelname": "WARNING", "levelno": 30}
    )
    record.msg, record.args = "%s failed for %s", ("tess", "V1 Dra")
    record.survey = "tess"  # as `extra={"survey": "tess"}` sets it

    entry = _format(record)

    assert entry["level"] == "WARNING"
    assert entry["logger"] == "argelander.fetch.worker"
    assert entry["message"] == "tess failed for V1 Dra"
    assert entry["survey"] == "tess"
    assert "msg" not in entry and "args" not in entry
    assert isinstance(entry["time"], str) and entry["time"].endswith("+00:00")


def test_a_traceback_is_kept_inside_the_one_line() -> None:
    try:
        raise ValueError("boom")
    except ValueError:
        record = logging.getLogger("t").makeRecord(
            "t", logging.ERROR, __file__, 1, "failed", (), exc_info=sys.exc_info()
        )

    line = JsonFormatter().format(record)

    assert "\n" not in line
    assert "ValueError: boom" in json.loads(line)["exception"]
