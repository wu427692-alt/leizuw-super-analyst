import json
from io import BytesIO
from unittest.mock import patch
from zipfile import ZipFile

from api.v1.endpoints import industry_research as endpoint


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
