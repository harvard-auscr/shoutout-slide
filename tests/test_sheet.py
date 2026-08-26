"""Spreadsheet reader: column detection, ordering, cleaning, both file types."""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

import pytest
from openpyxl import Workbook

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from shoutout_gen.sheet import SheetFormatError, parse_timestamp, read_shoutouts  # noqa: E402


def _csv(tmp_path: Path, header: str, *rows: str) -> Path:
    p = tmp_path / "sheet.csv"
    # newline="" so an embedded "\r\n" inside a quoted cell is written verbatim
    # (Windows text mode would otherwise turn it into "\r\r\n").
    with p.open("w", encoding="utf-8-sig", newline="") as fh:
        fh.write(header + "\n" + "\n".join(rows) + "\n")
    return p


def test_google_form_csv_sorted_by_timestamp(tmp_path):
    p = _csv(
        tmp_path,
        "Timestamp,Shout-out!,Email Address",
        "9/17/2025 20:14:33,second,a@x",
        "9/17/2025 19:01:00,first,b@x",
        "9/17/2025 20:14:33,also second (stable),c@x",
        "9/17/2025 21:00:00,,d@x",
        '9/17/2025 22:00:00,"multi\r\nline",e@x',
    )
    out = read_shoutouts(p)
    assert [s.text for s in out] == ["first", "second", "also second (stable)", "multi\nline"]
    assert out[0].timestamp == datetime(2025, 9, 17, 19, 1)
    assert out[0].row == 3


def test_untimestamped_rows_go_last_in_sheet_order(tmp_path):
    p = _csv(tmp_path, "Timestamp,Message", "not a date,z", "2025-01-02 10:00,b", ",y", "2025-01-01 10:00,a")
    assert [s.text for s in read_shoutouts(p)] == ["a", "b", "z", "y"]


def test_columns_detected_by_content_when_headers_are_unhelpful(tmp_path):
    p = _csv(tmp_path, "A,B,C", "2025-01-01 10:00,x,a much longer shout-out here", "2025-01-01 11:00,y,another long one")
    out = read_shoutouts(p)
    assert [s.text for s in out] == ["a much longer shout-out here", "another long one"]
    assert out[0].timestamp is not None


def test_forced_columns_and_bad_names(tmp_path):
    p = _csv(tmp_path, "Timestamp,Note,Shoutout", "2025-01-01,keep me,not me")
    assert [s.text for s in read_shoutouts(p, text_column="Note")] == ["keep me"]
    with pytest.raises(SheetFormatError):
        read_shoutouts(p, text_column="Nope")
    with pytest.raises(SheetFormatError):
        read_shoutouts(p, time_column="Nope")


def test_xlsx_with_real_datetime_cells(tmp_path):
    p = tmp_path / "sheet.xlsx"
    wb = Workbook()
    ws = wb.active
    ws.append(["Timestamp", "Shout-out"])
    ws.append([datetime(2025, 9, 17, 20, 0), "later"])
    ws.append([datetime(2025, 9, 17, 19, 0), "earlier"])
    ws.append([None, None])
    wb.save(p)
    assert [s.text for s in read_shoutouts(p)] == ["earlier", "later"]


def test_unsupported_and_empty_files(tmp_path):
    with pytest.raises(SheetFormatError):
        read_shoutouts(tmp_path / "x.txt")
    empty = tmp_path / "e.csv"
    empty.write_text("", encoding="utf-8")
    with pytest.raises(SheetFormatError):
        read_shoutouts(empty)


@pytest.mark.parametrize(
    "value, expected",
    [
        ("9/17/2025 20:14:33", datetime(2025, 9, 17, 20, 14, 33)),
        ("2025-09-17T20:14:33", datetime(2025, 9, 17, 20, 14, 33)),
        ("2025-09-17", datetime(2025, 9, 17)),
        (datetime(2025, 1, 1), datetime(2025, 1, 1)),
        ("", None),
        (None, None),
        ("yesterday", None),
    ],
)
def test_parse_timestamp(value, expected):
    assert parse_timestamp(value) == expected
