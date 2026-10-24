"""JSON-lines logging (spec §3.2): one parseable object per record, extras and tracebacks kept."""

import json
import logging
import logging.config
import sys

import pytest

from argelander.logs import JsonFormatter, dict_config


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


def test_uvicorns_colour_copy_of_the_message_is_dropped() -> None:
    record = logging.makeLogRecord({"msg": "Uvicorn running", "color_message": "\x1b[1mUvicorn"})

    assert "color_message" not in _format(record)


def test_dict_config_sends_uvicorns_records_to_stdout_as_json(
    capsys: pytest.CaptureFixture[str],
) -> None:
    root = logging.getLogger()
    saved = root.handlers[:], root.level
    try:
        logging.config.dictConfig(dict_config())
        logging.getLogger("uvicorn.error").info("Uvicorn running")
    finally:
        root.handlers[:], root.level = saved

    entry = json.loads(capsys.readouterr().out)
    assert entry["logger"] == "uvicorn.error"
    assert entry["message"] == "Uvicorn running"
