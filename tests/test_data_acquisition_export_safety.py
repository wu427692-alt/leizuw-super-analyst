"""Exercise the actual multi-channel ZIP pipeline with hostile source text."""

from __future__ import annotations

import csv
from io import BytesIO, StringIO
import json
import re
from types import SimpleNamespace
import zipfile

from openpyxl import load_workbook
import pytest

from src.services.data_acquisition_service import DataAcquisitionError, DataAcquisitionService


FORMULA_TEXT = [
    "=1+1", "+SUM(1,2)", "-SUM(1,2)", "@SUM(1,2)",
    "   =1+1", "\t+SUM(1,2)", "\n-SUM(1,2)", "\r@SUM(1,2)",
    " \t\r\n=1+1", "\x00=1+1", "\v=1+1", "\f=1+1", "\x1f=1+1",
    "\ufeff=1+1", "\u200b=1+1", "\u0085=1+1", "\u3000=1+1",
]


@pytest.mark.parametrize("source_text", FORMULA_TEXT)
def test_multi_channel_zip_exports_source_text_without_executable_formulas(tmp_path, monkeypatch, source_text):
    monkeypatch.setattr(
        "src.services.data_acquisition_service.get_config",
        lambda: SimpleNamespace(database_path=str(tmp_path / "unused.db")),
    )

    def no_network(*args, **kwargs):
        raise AssertionError("Export tests must not call a provider or AI")

    monkeypatch.setattr("requests.sessions.Session.request", no_network)
    service = DataAcquisitionService(
        planner=SimpleNamespace(model="offline-export-test"), financial=object(),
        monitor=object(), cninfo=object(), output_root=tmp_path / "packages",
    )
    rows = [{
        source_text: source_text,
        "body": source_text,
        "integer": 42,
        "negative_integer": -7,
        "ratio": 0.125,
        "negative_float": -3.5,
        "zero": 0,
        "numeric_text": "-123",
        "ordinary": "正常原文",
        "boolean": True,
        "structured": {"original": source_text},
        "missing": None,
    }]

    def execute(task, *args, **kwargs):
        if task["source"] == "monitor":
            raise DataAcquisitionError(source_text)
        if task["source"] == "cninfo":
            return []
        return rows

    monkeypatch.setattr(service, "_execute_task", execute)
    plan = {"title": "离线安全导出验收", "scope": {"market_wide": True}, "tasks": [
        {"id": "market", "source": "tushare", "resource": "daily", "label": "行情"},
        {"id": "notes", "source": "zsxq", "resource": "research_notes", "label": "纪要"},
        {"id": "errors", "source": "monitor", "resource": "events", "label": "错误信息"},
        {"id": "empty", "source": "cninfo", "resource": "announcements", "label": "空结果"},
    ]}
    result = service.run("获取全市场各渠道数据", plan)
    assert result["status"] == "partial"
    assert result["summary"]["failed_count"] == 2  # upstream failure plus explicit no-data outcome
    assert next(item for item in result["datasets"] if item["source"] == "cninfo")["outcome"] == "no_data"

    # XML 1.0 cannot represent these characters. Only the spreadsheet's
    # displayed text may remove them; the JSON source remains byte-faithful.
    spreadsheet_text = re.sub(r"[\x00-\x08\x0b-\x0c\x0e-\x1f]", "", source_text)
    with zipfile.ZipFile(service.package_path(result["job_id"])) as archive:
        for dataset in result["datasets"]:
            source = dataset["source"]
            workbook = load_workbook(BytesIO(archive.read(f"{source}/{source}.xlsx")), data_only=False)
            sheet = workbook.active
            for row in sheet:
                for cell in row:
                    assert cell.data_type != "f", (source, cell.coordinate, cell.value)
                    if isinstance(cell.value, str):
                        assert cell.data_type == "s", (source, cell.coordinate, cell.value)
            if source == "monitor":
                assert sheet["B2"].value == spreadsheet_text
                continue
            if source == "cninfo":
                assert "未返回符合条件的数据" in sheet["B2"].value
                continue

            assert sheet["A1"].value == spreadsheet_text
            assert sheet["A2"].value == spreadsheet_text
            assert sheet["B2"].value == spreadsheet_text
            for coordinate, expected in (("C2", 42), ("D2", -7), ("E2", 0.125), ("F2", -3.5), ("G2", 0)):
                assert sheet[coordinate].data_type == "n"
                assert sheet[coordinate].value == expected
                assert type(sheet[coordinate].value) is type(expected)
            assert sheet["H2"].value == "-123"
            assert sheet["H2"].data_type == "s"
            assert sheet["I2"].value == "正常原文"
            assert sheet["J2"].value is True

            csv_name = next(name for name in dataset["files"] if name.endswith(".csv"))
            csv_rows = list(csv.reader(StringIO(archive.read(csv_name).decode("utf-8-sig"))))
            assert csv_rows[0][0] == "'" + source_text
            assert csv_rows[1][0] == "'" + source_text
            assert csv_rows[1][1] == "'" + source_text
            assert csv_rows[1][2:7] == ["42", "-7", "0.125", "-3.5", "0"]
            assert csv_rows[1][7] == "'-123"
            assert csv_rows[1][8] == "正常原文"
            assert json.loads(csv_rows[1][10]) == {"original": source_text}

            json_name = next(name for name in dataset["files"] if name.endswith(".json"))
            exported_json = json.loads(archive.read(json_name))
            assert exported_json == rows
            assert exported_json[0][source_text] == source_text


def test_csv_quoting_cannot_move_formula_text_into_a_new_cell(tmp_path):
    path = tmp_path / "quoted.csv"
    source_text = '=HYPERLINK("https://example.invalid/a,b","标题")\r\n@SUM(1,2)'
    DataAcquisitionService._write_csv(path, [{"content": source_text, "count": -2}])
    with path.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.reader(handle))
    assert rows == [["content", "count"], ["'" + source_text, "-2"]]

