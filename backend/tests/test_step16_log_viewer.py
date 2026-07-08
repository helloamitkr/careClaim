"""Step 16 — the /logs viewer's reading side: parsing serialized loguru
lines into the compact record shape, and tailing only the end of the file."""

import json

from carebridge.logging import parse_log_line, tail_records


def _serialized_line(message: str, level: str = "INFO", **extra) -> str:
    return json.dumps(
        {
            "text": f"{message}\n",
            "record": {
                "time": {"repr": "2026-07-08 10:15:30.123456+05:30"},
                "level": {"name": level},
                "message": message,
                "extra": extra,
            },
        }
    )


def test_parse_extracts_the_viewer_fields():
    line = _serialized_line(
        "referral.routed · case case-A", component="bus", case_id="case-A", duration_ms=28.0
    )
    record = parse_log_line(line)
    assert record == {
        "time": "2026-07-08 10:15:30.123",
        "level": "INFO",
        "component": "bus",
        "message": "referral.routed · case case-A",
        "extra": {"case_id": "case-A", "duration_ms": 28.0},
    }


def test_parse_passes_non_json_lines_through_instead_of_dropping():
    record = parse_log_line("some stray plain-text line")
    assert record["component"] == "raw"
    assert record["message"] == "some stray plain-text line"


def test_parse_skips_blank_lines():
    assert parse_log_line("   ") is None


def test_tail_returns_only_the_last_n_records(tmp_path):
    path = tmp_path / "carebridge_2026-07-08.log"
    path.write_text("\n".join(_serialized_line(f"line {i}") for i in range(500)) + "\n")

    records = tail_records(path, 10)
    assert len(records) == 10
    assert records[-1]["message"] == "line 499"
    assert records[0]["message"] == "line 490"


def test_tail_of_a_short_file_returns_everything(tmp_path):
    path = tmp_path / "carebridge_2026-07-08.log"
    path.write_text(_serialized_line("only line") + "\n")

    records = tail_records(path, 100)
    assert [r["message"] for r in records] == ["only line"]
