import json
from io import BytesIO
from unittest.mock import patch
from zipfile import ZipFile

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from api.v1.endpoints import industry_research as endpoint


def test_project_archive_forwards_page_search_and_status_parameters():
    app = FastAPI()
    app.include_router(endpoint.router)
    response_payload = {
        "items": [], "total": 19, "limit": 12, "offset": 24, "query": "光模块%", "status": "completed",
        "counts": {"all": 40, "running": 10, "completed": 19, "limited": 5, "failed": 6},
    }
    with patch.object(endpoint, "IndustryResearchService") as service_class, TestClient(app) as client:
        mocked = service_class.return_value.list_projects
        mocked.return_value = response_payload
        response = client.get("/projects", params={
            "limit": 12, "offset": 24, "query": "光模块%", "status": "completed",
        })
    assert response.status_code == 200
    assert response.json() == response_payload
    mocked.assert_called_once_with(12, offset=24, query="光模块%", status="completed")


def test_project_archive_keeps_existing_default_parameters():
    app = FastAPI()
    app.include_router(endpoint.router)
    with patch.object(endpoint, "IndustryResearchService") as service_class, TestClient(app) as client:
        mocked = service_class.return_value.list_projects
        mocked.return_value = {"items": [], "total": 0}
        response = client.get("/projects")
    assert response.status_code == 200
    mocked.assert_called_once_with(30, offset=0, query="", status="all")


@pytest.mark.parametrize("params", [
    {"limit": 0}, {"limit": 101}, {"offset": -1}, {"status": "unknown"}, {"query": "x" * 201},
])
def test_project_archive_validates_parameters_before_service(params):
    app = FastAPI()
    app.include_router(endpoint.router)
    with patch.object(endpoint, "IndustryResearchService") as service_class, TestClient(app) as client:
        response = client.get("/projects", params=params)
    assert response.status_code == 422
    service_class.assert_not_called()


def test_blueprint_forwards_company_research_type():
    with patch.object(endpoint, "IndustryResearchService") as service_class:
        mocked = service_class.return_value.blueprint
        mocked.return_value = {"research_type": "company"}
        result = endpoint.blueprint(topic="中际旭创", lookback_days=730, research_type="company")

    assert result["research_type"] == "company"
    mocked.assert_called_once_with("中际旭创", lookback_days=730, research_type="company")


def test_download_returns_markdown_and_json_with_snapshot():
    project = {
        "project_id": "p1", "topic": "中际旭创", "status": "completed",
        "report": {"long_form_report": "# 中际旭创深度研究报告"},
        "snapshot": {"source_hash": "abc", "evidence": [{"evidence_id": "financial:1"}]},
    }
    with patch.object(endpoint, "IndustryResearchService") as service_class:
        service_class.return_value.get_project.return_value = project
        markdown = endpoint.project_download("p1", format="markdown")
        payload = endpoint.project_download("p1", format="json")

    assert markdown.body.decode("utf-8").startswith("# 中际旭创")
    decoded = json.loads(payload.body.decode("utf-8"))
    assert decoded["snapshot"]["source_hash"] == "abc"
    assert decoded["report"]["long_form_report"].startswith("# 中际旭创")


def test_download_returns_publication_word_and_pdf_without_raw_evidence_ids():
    project = {
        "project_id": "p1", "topic": "中际旭创", "research_type": "company", "status": "completed",
        "completed_at": "2026-08-31T12:00:00+08:00",
        "report": {
            "one_sentence": "高速光模块需求与产品升级共同驱动增长。",
            "executive_summary": "公司处于光模块产业链核心环节 [report:1]。",
            "subject": {"symbol": "300308.SZ"},
            "chapters": [{
                "chapter_id": "business", "title": "业务与产业位置", "summary": "结论摘要",
                "body_markdown": "## 核心业务\n\n公司收入持续增长 [report:1]。\n\n| 指标 | 数值 |\n| --- | --- |\n| 收入 | 100亿元 |",
                "evidence_ids": ["report:1"], "open_questions": [], "char_count": 60,
            }],
            "visualizations": [{
                "id": "revenue", "type": "bar", "title": "收入趋势", "data": [{"期间": "2025", "收入": 100}],
                "x_key": "期间", "y_keys": ["收入"], "source": "公司公告",
            }],
        },
        "snapshot": {"subject": {"symbol": "300308.SZ"}, "evidence": [{
            "evidence_id": "report:1", "source": "测试证券", "title": "公司深度报告", "date": "2026-08-30",
            "url": "https://example.com/report.pdf",
        }]},
    }
    with patch.object(endpoint, "IndustryResearchService") as service_class:
        service_class.return_value.get_project.return_value = project
        word = endpoint.project_download("p1", format="docx")
        pdf = endpoint.project_download("p1", format="pdf")

    assert word.media_type.startswith("application/vnd.openxmlformats")
    with ZipFile(BytesIO(word.body)) as archive:
        document_xml = archive.read("word/document.xml").decode("utf-8")
    assert "中际旭创" in document_xml
    assert "report:1" not in document_xml
    assert pdf.media_type == "application/pdf"
    assert pdf.body.startswith(b"%PDF")
    assert len(pdf.body) > 2_000
