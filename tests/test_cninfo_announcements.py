# -*- coding: utf-8 -*-
"""CNInfo query normalization and investment-monitor integration tests."""

from __future__ import annotations

from datetime import date, datetime
from io import BytesIO
import os
import zipfile

import pytest

from src.config import Config
from src.repositories.investment_monitor_repo import InvestmentMonitorRepository
from src.services.cninfo_announcement_service import CninfoAnnouncementError, CninfoAnnouncementService
from src.services.announcement_artifact_service import AnnouncementArtifactService
from src.services.investment_monitor_service import InvestmentMonitorService
from src.storage import DatabaseManager


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class FakePdfResponse:
    headers = {}

    def __init__(self, content):
        self.content = content

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def raise_for_status(self):
        return None

    def iter_content(self, chunk_size):
        del chunk_size
        yield self.content


class FakePdfSession:
    def __init__(self, content):
        self.content = content

    def get(self, *args, **kwargs):
        del args, kwargs
        return FakePdfResponse(self.content)


class FakeSession:
    def __init__(self):
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if "topSearch" in url:
            return FakeResponse([{"code": "600519", "orgId": "gssh0600519", "zwjc": "贵州茅台"}])
        page = int(kwargs["data"]["pageNum"])
        rows = [{
            "secCode": "600519", "secName": "贵州茅台", "orgId": "gssh0600519",
            "announcementId": "1222993920", "announcementTitle": "贵州茅台2024年年度报告",
            "announcementTime": 1743609600000,
            "adjunctUrl": "finalpage/2025-04-03/1222993920.PDF",
            "adjunctSize": 3542, "adjunctType": "PDF",
        }] if page == 1 else []
        return FakeResponse({"announcements": rows, "totalpages": 1})


def test_cninfo_resolves_org_id_and_normalizes_https_pdf():
    session = FakeSession()
    service = CninfoAnnouncementService(session=session, request_interval=0)
    rows = service.fetch(
        start_date=date(2025, 1, 1), end_date=date(2025, 12, 31), symbols=["600519.SH"],
        categories=["category_ndbg_szsh"],
    )
    assert len(rows) == 1
    assert rows[0]["category_names"] == ["年报"]
    assert rows[0]["pdf_url"] == "https://static.cninfo.com.cn/finalpage/2025-04-03/1222993920.PDF"
    query_payload = next(kwargs["data"] for url, kwargs in session.calls if "hisAnnouncement" in url)
    assert query_payload["stock"] == "600519,gssh0600519"


def test_cninfo_rejects_unknown_category():
    with pytest.raises(CninfoAnnouncementError, match="未知公告分类"):
        CninfoAnnouncementService(session=FakeSession()).fetch(
            start_date=date(2025, 1, 1), end_date=date(2025, 1, 2), categories=["unknown"],
        )


class FakeCninfo:
    @staticmethod
    def categories():
        return [{"code": "category_ndbg_szsh", "name": "年报"}]

    @staticmethod
    def fetch(**kwargs):
        del kwargs
        return [{
            "announcement_id": "ann-1", "code": "600519", "name": "贵州茅台",
            "title": "贵州茅台年度报告", "announcement_at": datetime(2026, 8, 19, 0, 0),
            "pdf_url": "https://static.cninfo.com.cn/finalpage/a.PDF",
            "category_names": ["年报"], "category_codes": ["category_ndbg_szsh"],
            "size_kb": 123, "file_type": "PDF", "org_id": "org", "raw": {"announcementId": "ann-1"},
        }]

    @staticmethod
    def fetch_recent(symbols, days=2):
        del symbols, days
        return []


def test_manual_announcement_sync_is_idempotent_and_queryable(tmp_path):
    previous = os.environ.get("DATABASE_PATH")
    os.environ["DATABASE_PATH"] = str(tmp_path / "announcements.db")
    Config.reset_instance(); DatabaseManager.reset_instance()
    try:
        repo = InvestmentMonitorRepository(DatabaseManager.get_instance())
        service = InvestmentMonitorService(repository=repo, cninfo=FakeCninfo())
        payload = {"start_date": "2026-08-18", "end_date": "2026-08-19", "categories": []}
        assert service.sync_announcements(payload)["created"] == 1
        assert service.sync_announcements(payload)["created"] == 0
        result = service.list_announcements(days=30, symbol="600519")
        assert result["total"] == 1
        assert result["items"][0]["perspective"] == "company"
        assert result["items"][0]["url"].endswith("a.PDF")
        exact = service.list_announcements(
            start_date="2026-08-19", end_date="2026-08-19", category="category_ndbg_szsh",
        )
        assert exact["total"] == 1
        assert service.export_announcements(start_date="2026-08-19", end_date="2026-08-19")[:2] == b"PK"
    finally:
        DatabaseManager.reset_instance(); Config.reset_instance()
        if previous is None:
            os.environ.pop("DATABASE_PATH", None)
        else:
            os.environ["DATABASE_PATH"] = previous


def test_package_uses_persisted_event_and_contains_pdf_txt_and_excel(tmp_path):
    from pypdf import PdfWriter

    buffer = BytesIO()
    writer = PdfWriter(); writer.add_blank_page(width=100, height=100); writer.write(buffer)
    artifacts = AnnouncementArtifactService(session=FakePdfSession(buffer.getvalue()))
    artifacts.root = tmp_path / "cache"; artifacts.root.mkdir()
    event = {
        "id": 7, "external_id": "ann-7", "title": "年度报告", "event_at": "2026-08-19T00:00:00Z",
        "symbols": ["600519.SH"], "actors": ["贵州茅台"], "tags": ["公司公告", "年报"],
        "url": "https://static.cninfo.com.cn/finalpage/a.PDF", "metrics": {"size_kb": 1},
    }
    result = artifacts.package([event], include_text=True)
    try:
        with zipfile.ZipFile(result["path"]) as archive:
            names = archive.namelist()
            assert "上市公司公告索引.xlsx" in names
            assert any(name.startswith("PDF/") for name in names)
            assert any(name.startswith("TXT/") for name in names)
            assert "manifest.json" in names
        assert result["downloaded"] == 1
    finally:
        result["path"].unlink(missing_ok=True)
