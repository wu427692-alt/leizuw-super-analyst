"""采集保护机制的回归测试。

覆盖两件线上真出过问题的事：
1. 抓取窗口没取完时，游标不能被推到当下（否则中间那段数据永久丢失）；
2. 新鲜度看门狗要能识别停摆的源，又不能把「天然稀疏」的源误报成故障。
"""

from datetime import datetime, timedelta, timezone

import pytest

from src.services.investment_monitor_service import InvestmentMonitorService


def _service() -> InvestmentMonitorService:
    return InvestmentMonitorService.__new__(InvestmentMonitorService)


def _event(at: datetime) -> dict:
    return {"event_at": at}


def test_cursor_does_not_advance_when_nothing_was_fetched():
    """上游延迟导致本轮 0 条时，游标必须原地不动，下一轮还在原处重试。"""
    service = _service()
    previous = (datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=3)).replace(microsecond=0)
    source = {"last_success_at": previous.isoformat() + "Z"}

    cursor = service._cursor_after_fetch(source, [])

    assert cursor == previous


def test_cursor_advances_only_to_newest_fetched_event():
    """取到了数据，游标推到这批事件的最新时间，而不是「当前时间」。"""
    service = _service()
    previous = (datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=6)).replace(microsecond=0)
    newest = (datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=40)).replace(microsecond=0)
    source = {"last_success_at": previous.isoformat() + "Z"}

    cursor = service._cursor_after_fetch(source, [_event(newest)])

    assert cursor == newest
    assert cursor < datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=30)


def test_cursor_never_moves_backwards_for_upserted_sources():
    """日更类源会重复返回旧时间的事件，游标不能因此倒退。"""
    service = _service()
    previous = (datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=1)).replace(microsecond=0)
    stale_event = (datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=3)).replace(microsecond=0)
    source = {"last_success_at": previous.isoformat() + "Z"}

    cursor = service._cursor_after_fetch(source, [_event(stale_event)])

    assert cursor == previous


def test_cursor_clamps_events_dated_in_the_future():
    """时钟漂移或错误时间戳不能让游标跑到未来。"""
    service = _service()
    source = {"last_success_at": None}
    future = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=2)

    cursor = service._cursor_after_fetch(source, [_event(future)])

    assert cursor <= datetime.now(timezone.utc).replace(tzinfo=None)


def test_cursor_ignores_events_without_timestamp():
    service = _service()
    previous = (datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=2)).replace(microsecond=0)
    source = {"last_success_at": previous.isoformat() + "Z"}

    assert service._cursor_after_fetch(source, [{"event_at": None}]) == previous


@pytest.mark.parametrize(
    ("category", "cadence_seconds", "max_stale_minutes"),
    [
        ("news", 60, 90),          # 快讯必须小时级新鲜
        ("company", 60, 6 * 60),   # 公告可以隔夜
        ("ownership", 60, 26 * 60)  # 股权披露天然稀疏
    ],
)
def test_freshness_budget_follows_source_category(monkeypatch, category, cadence_seconds, max_stale_minutes):
    """容忍窗口按类别给：噪声大的告警等于没有告警。"""
    service = _service()
    now = datetime.now(timezone.utc).replace(tzinfo=None).replace(microsecond=0)

    class _Repo:
        def source_event_freshness(self):
            # 刚过 7 小时：news 应该判停摆，company/ownership 不应误报。
            return {"probe.source": {"latest_event_at": (now - timedelta(hours=7)).isoformat() + "Z"}}

        def list_sources(self):
            return [{
                "source_key": "probe.source", "name": "探针源", "enabled": True,
                "category": category, "poll_interval_seconds": cadence_seconds,
                "last_success_at": (now - timedelta(hours=7)).isoformat() + "Z",
            }]

        def update_source_status(self, *args, **kwargs):  # pragma: no cover - 只记录调用
            return None

    service.repo = _Repo()  # type: ignore[assignment]
    report = service.audit_source_freshness(stale_after_minutes=90)
    flagged = {item["source_key"] for item in report["stalled"]}

    if max_stale_minutes <= 7 * 60:
        assert "probe.source" in flagged
    else:
        assert "probe.source" not in flagged


def test_freshness_skips_backup_and_never_run_sources(monkeypatch):
    """备用源和从未成功过的源不应产生停摆告警。"""
    service = _service()
    now = datetime.now(timezone.utc).replace(tzinfo=None).replace(microsecond=0)
    old = (now - timedelta(days=30)).isoformat() + "Z"

    class _Repo:
        def source_event_freshness(self):
            return {
                "sse.announcements": {"latest_event_at": old},
                "never.ran": {"latest_event_at": None},
            }

        def list_sources(self):
            return [
                {"source_key": "sse.announcements", "name": "上交所官方公告（备用）", "enabled": True,
                 "category": "company", "poll_interval_seconds": 60, "last_success_at": old},
                {"source_key": "never.ran", "name": "从未运行", "enabled": True,
                 "category": "news", "poll_interval_seconds": 60, "last_success_at": None},
            ]

        def update_source_status(self, *args, **kwargs):  # pragma: no cover
            return None

    service.repo = _Repo()  # type: ignore[assignment]
    report = service.audit_source_freshness(stale_after_minutes=90)

    assert report["stalled"] == []
