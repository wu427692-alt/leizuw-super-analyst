from src.services.research_decision_service import ResearchDecisionService


class FakeMonitor:
    def source_bi(self, *, days):
        assert days == 30
        return {
            "summary": {"enabled": 3, "stored_event_count": 42, "fresh": 2, "monitoring_live": 3, "monitoring_delayed": 0, "stale": 1},
            "sources": [{"source_key": "cninfo.announcements", "name": "巨潮公告"}],
        }

    def super_watchlist(self, *, days, symbols):
        assert days == 365
        assert symbols == ["600519.SH"]
        return {"stocks": [{
            "symbol": "600519.SH", "name": "贵州茅台",
            "market": {"price": 1300, "change_pct": 2.5, "updated_at": "2026-08-22T10:00:00"},
            "evidence": {"event_count": 12, "factual_count": 10, "unverified_count": 2, "source_count": 3, "original_link_coverage": 80},
            "coverage": [
                {"name": "公告", "available": True, "freshness_status": "fresh"},
                {"name": "研报", "available": True, "freshness_status": "stale"},
            ],
            "timeline": [{"id": 1, "title": "公司公告", "event_at": "2026-08-22T09:00:00", "sentiment": "neutral", "metrics": {"_evidence": {"evidence_level": "factual"}}}],
            "consensus": {"broker_report_count": 2, "essay_expectation_count": 1, "method": "test"},
            "signals": [{"kind": "risk", "title": "需求待核验"}],
        }]}


def test_research_center_exposes_three_iterations_and_transparent_readiness():
    result = ResearchDecisionService(monitor=FakeMonitor()).overview(symbols=["600519.SH"])

    assert [item["version"] for item in result["iterations"]] == ["V1", "V2", "V3"]
    assert result["system"]["stored_event_count"] == 42
    assert len(result["functions"]) >= 10
    packet = result["decision_packets"][0]
    assert packet["symbol"] == "600519.SH"
    assert packet["readiness_score"] == 70
    assert packet["state"] == "需要补证"
    assert any(item["task"] == "更新研报" for item in packet["verification_tasks"])
    assert "不预测涨跌" in packet["disclaimer"]
