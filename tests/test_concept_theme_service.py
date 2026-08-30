from datetime import date, datetime, timedelta
from types import SimpleNamespace
import json
import math

from src.services.concept_theme_service import (
    ConceptThemeService,
    canonicalize_theme,
    theme_cluster,
    theme_family,
)
from src.storage import (
    ConceptExposureRecord,
    ConceptMembershipRecord,
    ConceptThemeRecord,
    ConceptThemeSnapshotRecord,
    DatabaseManager,
    EssayAnalysisRecord,
    MarketIndexBar,
    ResearchNote,
    StockDaily,
    utc_naive_now,
)


def _service(tmp_path) -> ConceptThemeService:
    DatabaseManager.reset_instance()
    ConceptThemeService._market_date_value = None
    ConceptThemeService._market_date_checked_at = None
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


def test_family_rules_do_not_confuse_consumer_electronics_words_with_semiconductors() -> None:
    assert theme_family("电子竞技", "concept") == "消费与品牌"
    assert theme_family("电子商务", "concept") == "消费与品牌"
    assert theme_family("电子烟", "concept") == "消费与品牌"
    assert theme_family("电子身份证", "concept") == "AI算力与数字基础设施"
    assert theme_family("汽车电子", "concept") == "半导体与先进电子"
    assert theme_family("飞行汽车", "concept") == "低空经济与商业航天"
    assert theme_family("云计算", "concept") == "AI算力与数字基础设施"
    assert theme_family("OLED", "concept") == "半导体与先进电子"
    assert theme_family("农业种植", "concept") == "农业与食品"
    assert theme_family("CAR-T", "concept") == "医药健康"
    assert theme_family("可控核聚变", "concept") == "新能源与电力系统"
    assert theme_family("统一大市场", "concept") == "政策改革与区域"
    assert canonicalize_theme("CPO(共封装光学)") == "CPO/共封装光学"
    assert canonicalize_theme("光通信模块") == "光通信/光模块"
    assert canonicalize_theme("飞行汽车(eVTOL)") == "eVTOL/飞行汽车"


def test_latest_market_date_never_uses_weekend_fact_row(tmp_path) -> None:
    service = _service(tmp_path)
    with service.db.session_scope() as session:
        session.add(StockDaily(code="000001", date=date(2026, 8, 21), close=10.0, data_source="test"))
        session.add(StockDaily(code="000001", date=date(2026, 8, 23), close=11.0, data_source="bad-weekend"))

    assert service.latest_market_date() == date(2026, 8, 21)


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
    assert filtered["items"][0]["canonical_source_count"] == 1
    assert filtered["items"][0]["canonical_node_count"] == 1
    assert recalled["total"] == 1
    assert recalled["items"][0]["canonical_name"] == "CPO/共封装光学"
    assert recalled["stock_matches"] == [{
        "ts_code": "300308.SZ", "name": "中际旭创", "theme_count": 1, "source_count": 1,
    }]


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
        session.add(ResearchNote(
            topic_id="theme-evidence-1", group_id="g1", group_name="调研纪要",
            title="CPO产业链跟踪", content="明确讨论中际旭创的CPO业务与客户进展。",
            symbol_codes="300308.SZ", content_hash="theme-evidence-hash-1",
            created_at=datetime(2026, 8, 20, 10, 0),
        ))
        session.flush()
        session.add(EssayAnalysisRecord(
            topic_id="theme-evidence-1", status="completed", model="test-model",
            prompt_version="test-v1", input_hash="theme-analysis-hash-1",
            summary="中际旭创受益于CPO和高速光模块需求。", importance_score=82,
            confidence_score=0.86, themes_json=json.dumps(["CPO", "光模块"], ensure_ascii=False),
            stock_mentions_json=json.dumps([{
                "ts_code": "300308.SZ", "name": "中际旭创", "confidence": 0.9,
                "rationale": "CPO客户验证与高速光模块交付形成直接业务证据。",
            }], ensure_ascii=False),
            completed_at=datetime(2026, 8, 20, 10, 5), updated_at=datetime(2026, 8, 20, 10, 5),
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
    assert exposure["components"]["beta_ci_low"] < exposure["beta"] < exposure["components"]["beta_ci_high"]
    assert abs(exposure["components"]["beta_t_stat"]) >= 1
    assert exposure["components"]["local_corpus_evidence_count"] == 1
    assert exposure["components"]["local_corpus_score"] > 0
    assert exposure["evidence"][-1]["kind"] == "institution_corpus"


def test_complete_membership_refresh_deactivates_vanished_stock_without_deleting_history(tmp_path) -> None:
    service = _service(tmp_path)
    now = utc_naive_now()
    with service.db.session_scope() as session:
        theme = ConceptThemeRecord(
            source="ths", source_code="885001.TI", name="CPO概念",
            canonical_name="CPO/共封装光学", theme_type="theme", level=3,
            market_date=date(2026, 8, 28), first_seen_at=now, last_seen_at=now, updated_at=now,
        )
        session.add(theme); session.flush()
        theme_id = theme.id
        for code in ("300308.SZ", "300502.SZ"):
            session.add(ConceptMembershipRecord(
                theme_id=theme_id, source="ths", ts_code=code, stock_name=code,
                active=True, first_seen_at=now, last_seen_at=now, updated_at=now,
            ))

    snapshot = {"id": theme_id, "source": "ths", "market_date": "20260828"}
    service._upsert_memberships(
        snapshot, [{"con_code": "300308.SZ", "con_name": "中际旭创"}], replace_existing=True,
    )
    with service.db.session_scope() as session:
        rows = session.query(ConceptMembershipRecord).order_by(ConceptMembershipRecord.ts_code).all()
        assert [(row.ts_code, row.active) for row in rows] == [
            ("300308.SZ", True), ("300502.SZ", False),
        ]


def test_rotation_uses_cross_source_daily_median_and_keeps_history(tmp_path) -> None:
    service = _service(tmp_path)
    now = utc_naive_now()
    with service.db.session_scope() as session:
        session.add(StockDaily(code="000001", date=date(2026, 8, 28), close=10.0, data_source="test"))
        for market_date, values in (
            (date(2026, 8, 27), (("ths", 1.0, 70.0), ("dc_board", 3.0, 78.0))),
            (date(2026, 8, 28), (("ths", 2.0, 80.0), ("dc_board", 4.0, 88.0))),
        ):
            for source, change, heat in values:
                session.add(ConceptThemeSnapshotRecord(
                    source=source, source_code=f"{source}-{market_date}",
                    canonical_name="CPO/共封装光学", theme_type="concept", market_date=market_date,
                    pct_change=change, heat_score=heat, captured_at=now,
                ))

    rotation = service.rotation(days=5, limit=8)
    item = rotation["items"][0]
    assert item["canonical_name"] == "CPO/共封装光学"
    assert item["pct_change"] == 3.0
    assert item["momentum_5d"] == 5.0
    assert item["source_count"] == 2
    assert item["history_days"] == 2


def test_stock_lens_keeps_exposure_dates_isolated_by_horizon(tmp_path) -> None:
    service = _service(tmp_path)
    now = utc_naive_now()
    with service.db.session_scope() as session:
        theme = ConceptThemeRecord(
            source="ths", source_code="885001.TI", name="CPO概念",
            canonical_name="CPO/共封装光学", theme_type="theme", level=3,
            market_date=date(2026, 8, 28), first_seen_at=now, last_seen_at=now, updated_at=now,
        )
        session.add(theme); session.flush()
        session.add(ConceptMembershipRecord(
            theme_id=theme.id, source="ths", ts_code="300308.SZ", stock_name="中际旭创",
            active=True, first_seen_at=now, last_seen_at=now, updated_at=now,
        ))
        session.add_all([
            ConceptExposureRecord(
                ts_code="300308.SZ", stock_name="中际旭创", canonical_name="CPO/共封装光学",
                as_of_date=date(2026, 8, 28), horizon_days=20, weight_score=81,
                specificity_score=65, beta=1.2, observations=20, confidence="medium",
                source_count=1, calculated_at=now,
            ),
            ConceptExposureRecord(
                ts_code="300308.SZ", stock_name="中际旭创", canonical_name="CPO/共封装光学",
                as_of_date=date(2026, 8, 27), horizon_days=60, weight_score=79,
                specificity_score=66, beta=0.8, observations=52, confidence="medium",
                source_count=1, calculated_at=now,
            ),
        ])

    lens_20 = service.stock_lens("300308.SZ", refresh_if_empty=False, horizon_days=20)
    lens_60 = service.stock_lens("300308.SZ", refresh_if_empty=False, horizon_days=60)

    assert lens_20["as_of_date"] == "2026-08-28"
    assert lens_20["themes"][0]["beta"] == 1.2
    assert lens_60["as_of_date"] == "2026-08-27"
    assert lens_60["themes"][0]["beta"] == 0.8
