from datetime import date, datetime, timedelta
from types import SimpleNamespace
import math

from src.services.concept_theme_service import (
    ConceptThemeService,
    canonicalize_theme,
    theme_cluster,
    theme_family,
)
from src.storage import (
    ConceptMembershipRecord,
    ConceptThemeRecord,
    DatabaseManager,
    MarketIndexBar,
    StockDaily,
    utc_naive_now,
)


def _service(tmp_path) -> ConceptThemeService:
    DatabaseManager.reset_instance()
    db = DatabaseManager(db_url=f"sqlite:///{tmp_path / 'concept-themes.db'}")
    return ConceptThemeService(db=db, gateway=SimpleNamespace(available=False))


def teardown_function() -> None:
    DatabaseManager.reset_instance()


def test_theme_hierarchy_merges_market_synonyms_without_losing_subtheme() -> None:
    assert canonicalize_theme("CPO概念") == "CPO/共封装光学"
    family = theme_family("CPO/共封装光学", "theme")
    assert family == "AI算力与数字基础设施"
    assert theme_cluster("CPO/共封装光学", family) == "光通信产业链"
    assert theme_cluster("NPO高速光模块", family) == "光通信产业链"


def test_overview_filters_family_before_pagination_and_can_recall_stock(tmp_path) -> None:
    service = _service(tmp_path)
    now = utc_naive_now()
    with service.db.session_scope() as session:
        ai_theme = ConceptThemeRecord(
            source="ths", source_code="885001.TI", name="CPO概念",
            canonical_name="CPO/共封装光学", theme_type="theme", level=3,
            market_date=date(2026, 8, 28), first_seen_at=now, last_seen_at=now, updated_at=now,
        )
        other = ConceptThemeRecord(
            source="ths", source_code="885002.TI", name="白酒",
            canonical_name="白酒", theme_type="theme", level=3,
            market_date=date(2026, 8, 28), first_seen_at=now, last_seen_at=now, updated_at=now,
        )
        session.add_all([ai_theme, other]); session.flush()
        session.add(ConceptMembershipRecord(
            theme_id=ai_theme.id, source="ths", ts_code="300308.SZ", stock_name="中际旭创",
            active=True, first_seen_at=now, last_seen_at=now, updated_at=now,
        ))

    filtered = service.overview(family="AI算力与数字基础设施", page_size=12)
    recalled = service.overview(query="中际旭创", page_size=12)

    assert filtered["total"] == 1
    assert filtered["items"][0]["cluster"] == "光通信产业链"
    assert recalled["total"] == 1
    assert recalled["items"][0]["canonical_name"] == "CPO/共封装光学"


def test_beta_uses_leave_one_out_theme_return_and_market_control(tmp_path) -> None:
    service = _service(tmp_path)
    now = utc_naive_now()
    with service.db.session_scope() as session:
        theme = ConceptThemeRecord(
            source="ths", source_code="885001.TI", name="CPO概念",
            canonical_name="CPO/共封装光学", theme_type="theme", level=3,
            constituent_count=4, heat_score=80, market_date=date(2026, 8, 28),
            first_seen_at=now, last_seen_at=now, updated_at=now,
        )
        session.add(theme); session.flush()
        for code in ("300308.SZ", "300001.SZ", "300002.SZ", "300003.SZ"):
            session.add(ConceptMembershipRecord(
                theme_id=theme.id, source="ths", ts_code=code, stock_name=code,
                active=True, first_seen_at=now, last_seen_at=now, updated_at=now,
            ))

        prices = {code: 100.0 for code in ("300308", "300001", "300002", "300003")}
        market_price = 1000.0
        day = date(2026, 5, 1)
        sessions = []
        while len(sessions) < 72:
            if day.weekday() < 5:
                sessions.append(day)
            day += timedelta(days=1)
        for index, trade_day in enumerate(sessions):
            market_return = 0.0015 * math.sin(index / 4.0)
            theme_return = 0.004 * math.cos(index / 3.0) + 0.001 * math.sin(index / 7.0)
            market_price *= 1 + market_return
            session.add(MarketIndexBar(
                symbol="000300.SH", timestamp=datetime.combine(trade_day, datetime.min.time()),
                frequency="1D", close=market_price, data_source="test",
            ))
            stock_returns = {
                "300308": 0.0004 + 1.45 * theme_return + 0.55 * market_return,
                "300001": theme_return + 0.08 * math.sin(index),
                "300002": theme_return + 0.06 * math.cos(index * 1.3),
                "300003": theme_return - 0.05 * math.sin(index * 0.7),
            }
            for code, stock_return in stock_returns.items():
                prices[code] *= 1 + stock_return
                session.add(StockDaily(code=code, date=trade_day, close=prices[code], data_source="test"))

    assert service.calculate_canonical_exposures("CPO/共封装光学", horizon_days=60) == 4
    lens = service.stock_lens("300308.SZ", refresh_if_empty=False, horizon_days=60)
    exposure = lens["themes"][0]
    assert exposure["observations"] >= 50
    assert exposure["beta"] is not None
    assert exposure["beta_interpretation"]
    assert exposure["components"]["regression"].startswith("个股日收益")
