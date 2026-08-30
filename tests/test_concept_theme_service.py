from datetime import date, datetime, timedelta
from types import SimpleNamespace
import json
import math

from sqlalchemy import select

from src.services.concept_theme_service import (
    ConceptThemeService,
    _classify_unique_driver,
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
    MonitoringEventRecord,
    ResearchNote,
    StockDaily,
    UserAccount,
    UserWatchlistItem,
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
    assert theme_family("2026中报预增", "concept") == "业绩与财务特征"
    assert theme_family("QFII重仓", "concept") == "机构持仓与资金偏好"
    assert theme_family("长江三角", "concept") == "区域与地理"
    assert theme_family("地方国企", "theme") == "国资与所有制"
    assert theme_family("中际旭创", "industry") == "申万/市场行业体系"
    assert theme_cluster("通信设备Ⅲ(A股)", "申万/市场行业体系") == "通信设备"
    assert theme_cluster("通信设备Ⅳ(A股)", "申万/市场行业体系") == "通信设备"
    assert theme_cluster("NPO高速光模块", family) == "光通信产业链"


def test_unique_alpha_driver_classification_separates_reason_and_wording_direction() -> None:
    assert _classify_unique_driver("公司中标大额订单", "客户合作落地") == ("订单与客户", "positive")
    assert _classify_unique_driver("监管问询函", "项目存在终止风险") == ("风险与监管", "negative")
    assert _classify_unique_driver("发布新产品", "完成客户验证") == ("产品与技术", "neutral")


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
    assert theme_family("Kimi智能助手", "concept") == "AI算力与数字基础设施"
    assert theme_family("高带宽内存", "concept") == "AI算力与数字基础设施"
    assert theme_family("碳化硅", "concept") == "半导体与先进电子"
    assert theme_family("胎压监测", "concept") == "汽车与智能驾驶"
    assert theme_family("冰雪经济", "concept") == "消费与品牌"
    assert theme_family("PM2.5", "concept") == "生态环保"
    assert theme_family("国家大基金持股", "concept") == "机构持仓与资金偏好"
    assert theme_family("统一大市场", "concept") == "政策改革与区域"
    assert theme_family("RISC-V", "concept") == "AI算力与数字基础设施"
    assert theme_family("薄膜铌酸锂（TFLN）", "concept") == "AI算力与数字基础设施"
    assert theme_family("Chiplet", "concept") == "半导体与先进电子"
    assert theme_family("麦角硫因", "concept") == "医药健康"
    assert theme_family("科技重组预期", "concept") == "公司治理与资本事件"
    assert theme_family("人民币升值", "concept") == "宏观与跨境"
    assert theme_family("IC载板", "concept") == "半导体与先进电子"
    assert theme_family("800V快充", "concept") == "汽车与智能驾驶"
    assert theme_family("2026一季报扭亏", "style") == "业绩与财务特征"
    assert theme_family("城市更新", "concept") == "基础设施与交通物流"
    assert canonicalize_theme("CPO(共封装光学)") == "CPO/共封装光学"
    assert canonicalize_theme("光通信模块") == "光通信/光模块"
    assert canonicalize_theme("飞行汽车(eVTOL)") == "eVTOL/飞行汽车"


def test_theme_lifecycle_only_joins_real_semantic_cluster_intersections(tmp_path) -> None:
    service = _service(tmp_path)
    service.rotation = lambda **_kwargs: {  # type: ignore[method-assign]
        "items": [{
            "canonical_name": "ChatGPT", "family": "AI算力与数字基础设施",
            "cluster": "AI算力与数字基础设施", "market_date": "2026-08-28",
            "momentum_5d": 4.2, "pct_change": 1.1, "source_count": 3,
            "rotation_score": 78,
        }],
        "latest_date": "2026-08-28",
    }
    service.institution_theme_radar = lambda **_kwargs: {  # type: ignore[method-assign]
        "items": [
            {"canonical_name": "ChatGPT", "recent_7d": 12, "note_count": 20,
             "acceleration_pct": 80, "discovery_score": 82},
            {"canonical_name": "创新药", "recent_7d": 9, "note_count": 18,
             "acceleration_pct": 60, "discovery_score": 75},
        ],
        "as_of_at": "2026-08-31T06:00:00",
    }

    result = service.theme_lifecycle(days=30, limit=12)

    assert result["total"] == 1
    assert result["items"][0]["cluster"] == "ChatGPT"
    assert result["items"][0]["stage"] == "共识扩张"
    assert result["items"][0]["market_themes"] == ["ChatGPT"]
    assert result["items"][0]["corpus_themes"] == ["ChatGPT"]


def test_latest_market_date_never_uses_weekend_fact_row(tmp_path) -> None:
    service = _service(tmp_path)
    with service.db.session_scope() as session:
        session.add(StockDaily(code="000001", date=date(2026, 8, 21), close=10.0, data_source="test"))
        session.add(StockDaily(code="000001", date=date(2026, 8, 23), close=11.0, data_source="bad-weekend"))

    assert service.latest_market_date() == date(2026, 8, 21)


def test_rotation_history_backfill_uses_real_dates_without_rewinding_live_catalog(tmp_path) -> None:
    service = _service(tmp_path)
    trade_dates = [date(2026, 8, day) for day in (24, 25, 26, 27, 28)]
    now = utc_naive_now()
    with service.db.session_scope() as session:
        for trade_day in trade_dates:
            session.add(StockDaily(code="300308", date=trade_day, close=100.0, data_source="test"))
        session.add(ConceptThemeRecord(
            source="ths", source_code="885001.TI", name="CPO概念",
            canonical_name="CPO/共封装光学", theme_type="theme", level=3,
            market_date=trade_dates[-1], first_seen_at=now, last_seen_at=now, updated_at=now,
        ))

    def query(api_name, *, params):
        if api_name == "trade_cal":
            return {"rows": [{"cal_date": day.strftime("%Y%m%d")} for day in trade_dates]}
        trade_date = params["trade_date"]
        rows = {
            "moneyflow_cnt_ths": [{
                "trade_date": trade_date, "ts_code": "885001.TI", "name": "CPO概念",
                "pct_change": 1.2, "company_num": 18, "net_amount": 30,
            }],
            "dc_index": [{
                "trade_date": trade_date, "ts_code": "BK001.DC", "name": "光模块",
                "pct_change": 0.8, "up_num": 10, "down_num": 2, "idx_type": "概念板块",
            }],
            "dc_concept": [{
                "trade_date": trade_date, "theme_code": "000001.DC", "name": "光通信",
                "pct_change": "1.0", "sort": "25", "main_change": "1000",
            }],
        }[api_name]
        return {"rows": rows}

    service.gateway = SimpleNamespace(available=True, query=query)
    ConceptThemeService._market_date_value = trade_dates[-1]
    ConceptThemeService._market_date_checked_at = datetime.now()
    result = service.backfill_rotation_history(days=5, dates_per_run=2)
    assert result["dates"] == 2
    assert result["saved"] == 6
    with service.db.session_scope() as session:
        live = session.execute(select(ConceptThemeRecord).where(
            ConceptThemeRecord.source == "ths", ConceptThemeRecord.source_code == "885001.TI",
        )).scalar_one()
        snapshots = session.execute(select(ConceptThemeSnapshotRecord)).scalars().all()
        live_market_date = live.market_date
        snapshot_dates = {row.market_date for row in snapshots}
        snapshot_changes = [row.pct_change for row in snapshots]
    assert live_market_date == trade_dates[-1]
    assert snapshot_dates == {trade_dates[0], trade_dates[1]}
    assert all(value is not None for value in snapshot_changes)
    rotation = service.rotation(days=5, limit=8)
    assert rotation["available_dates"] == 2
    assert all(item["momentum_5d"] is None for item in rotation["items"])


def test_rotation_history_fallback_skips_weekend_fact_dates(tmp_path) -> None:
    service = _service(tmp_path)
    now = utc_naive_now()
    with service.db.session_scope() as session:
        for day in (21, 22, 23, 24):
            session.add(StockDaily(code="300308", date=date(2026, 8, day), close=100.0, data_source="test"))

    queried_dates = []

    def query(api_name, *, params):
        if api_name == "trade_cal":
            raise RuntimeError("calendar unavailable")
        queried_dates.append(params["trade_date"])
        return {"rows": []}

    service.gateway = SimpleNamespace(available=True, query=query)
    ConceptThemeService._market_date_value = date(2026, 8, 24)
    ConceptThemeService._market_date_checked_at = datetime.now()
    service.backfill_rotation_history(days=5, dates_per_run=5)
    assert "20260822" not in queried_dates
    assert "20260823" not in queried_dates
    assert set(queried_dates) == {"20260821", "20260824"}


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
    strict_consensus = service.overview(min_sources=2, page_size=12)
    membered = service.overview(readiness="membered", view="canonical", page_size=12)
    attributed = service.overview(readiness="attributed", view="canonical", page_size=12)

    assert filtered["total"] == 1
    assert filtered["items"][0]["cluster"] == "光通信产业链"
    assert filtered["items"][0]["canonical_source_count"] == 1
    assert filtered["items"][0]["canonical_node_count"] == 1
    assert filtered["summary"]["quality"]["catalog_date"] == "2026-08-28"
    assert filtered["summary"]["quality"]["fresh_catalogs"] == 1
    assert filtered["summary"]["quality"]["total_catalogs"] == 6
    assert recalled["total"] == 1
    assert recalled["items"][0]["canonical_name"] == "CPO/共封装光学"
    assert recalled["stock_matches"] == [{
        "ts_code": "300308.SZ", "name": "中际旭创", "theme_count": 1, "source_count": 1,
    }]
    assert strict_consensus["total"] == 0
    assert membered["total"] == 1
    assert membered["items"][0]["canonical_name"] == "CPO/共封装光学"
    assert attributed["total"] == 0
    with service.db.session_scope() as session:
        session.add_all([
            ConceptThemeRecord(
                source="dc_theme", source_code="000286.DC", name="CPO概念",
                canonical_name="CPO/共封装光学", theme_type="theme", level=3,
                market_date=date(2026, 8, 28), first_seen_at=now, last_seen_at=now, updated_at=now,
            ),
            ConceptThemeRecord(
                source="dc_board", source_code="BK1200.DC", name="CPO板块",
                canonical_name="CPO/共封装光学", theme_type="theme", level=3,
                market_date=date(2026, 8, 28), first_seen_at=now, last_seen_at=now, updated_at=now,
            ),
        ])
    canonical_view = service.overview(query="CPO", view="canonical", page_size=12)
    source_view = service.overview(query="CPO", view="source", page_size=12)
    assert canonical_view["total"] == 1
    # Two Eastmoney catalogs remain separate audit nodes, but count as one
    # independent provider together with Tonghuashun for consensus.
    assert canonical_view["items"][0]["canonical_source_count"] == 2
    assert canonical_view["items"][0]["canonical_node_count"] == 3
    assert canonical_view["items"][0]["constituent_count"] == 1
    assert source_view["total"] == 3


def test_two_catalogs_from_same_provider_do_not_create_false_consensus(tmp_path) -> None:
    service = _service(tmp_path)
    now = utc_naive_now()
    with service.db.session_scope() as session:
        board = ConceptThemeRecord(
            source="dc_board", source_code="BK1200.DC", name="光模块板块",
            canonical_name="光通信/光模块", theme_type="theme", level=3,
            market_date=date(2026, 8, 28), first_seen_at=now, last_seen_at=now, updated_at=now,
        )
        theme = ConceptThemeRecord(
            source="dc_theme", source_code="000287.DC", name="光模块概念",
            canonical_name="光通信/光模块", theme_type="theme", level=3,
            market_date=date(2026, 8, 28), first_seen_at=now, last_seen_at=now, updated_at=now,
        )
        session.add_all([board, theme]); session.flush()
        for source_node in (board, theme):
            session.add(ConceptMembershipRecord(
                theme_id=source_node.id, source=source_node.source,
                ts_code="300308.SZ", stock_name="中际旭创", active=True,
                first_seen_at=now, last_seen_at=now, updated_at=now,
            ))
        session.add(ConceptExposureRecord(
            ts_code="300308.SZ", stock_name="中际旭创", canonical_name="光通信/光模块",
            as_of_date=date(2026, 8, 28), horizon_days=60, source_count=2,
            consensus_score=100, relevance_score=50, market_score=50, specificity_score=50,
            weight_score=68, components_json=json.dumps({"consensus": 100}), calculated_at=now,
        ))

    audited = service.overview(query="光模块", view="canonical", page_size=12)
    strict = service.overview(query="光模块", view="canonical", min_sources=2, page_size=12)
    assert audited["items"][0]["canonical_node_count"] == 2
    assert audited["items"][0]["canonical_source_count"] == 1
    assert strict["total"] == 0
    repaired = service.reconcile_exposure_provider_counts()
    assert repaired == {"themes": 1, "exposures": 1}
    with service.db.session_scope() as session:
        exposure = session.query(ConceptExposureRecord).one()
        components = json.loads(exposure.components_json)
        assert exposure.source_count == 1
        assert exposure.consensus_score < 50
        assert exposure.weight_score < 50
        assert components["catalog_count"] == 2
        assert components["provider_count"] == 1


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
        theme_id = theme.id
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
            summary="中际旭创受益于CPO和高速光模块需求。", sentiment="bullish", importance_score=82,
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
    assert exposure["components"]["window_alpha_formula"].startswith("窗口Alpha=(1+回归日截距)")
    assert exposure["components"]["beta_ci_low"] < exposure["beta"] < exposure["components"]["beta_ci_high"]
    assert abs(exposure["components"]["beta_t_stat"]) >= 1
    assert exposure["components"]["local_corpus_evidence_count"] == 1
    assert exposure["components"]["local_corpus_score"] > 0
    assert exposure["evidence"][-1]["kind"] == "institution_corpus"
    detail = service.theme_detail(theme_id, refresh_if_empty=False, horizon_days=60)
    assert detail["institution_corpus"]["total"] == 1
    assert detail["institution_corpus"]["bullish"] == 1
    assert detail["institution_corpus"]["score"] > 0

    with service.db.session_scope() as session:
        legacy = session.execute(select(ConceptExposureRecord).where(
            ConceptExposureRecord.ts_code == "300308.SZ",
            ConceptExposureRecord.canonical_name == "CPO/共封装光学",
        )).scalar_one()
        legacy.components_json = "{}"
        legacy.residual_return = 999.0
    repair = service.reconcile_window_alpha_compounding(limit=10)
    assert repair["updated"] >= 1
    repaired = service.stock_lens("300308.SZ", refresh_if_empty=False, horizon_days=60)["themes"][0]
    expected = round(
        ((1 + float(repaired["alpha_annualized"]) / 252 / 100) ** int(repaired["observations"]) - 1) * 100,
        2,
    )
    assert repaired["residual_return"] == expected
    assert repaired["components"]["window_alpha_compounding_version"] == 1


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


def test_rotation_uses_cross_source_daily_median_and_rejects_partial_5d_window(tmp_path) -> None:
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
    assert item["momentum_5d"] is None
    assert item["source_count"] == 2
    assert item["history_days"] == 2


def test_rotation_compounds_five_daily_returns_instead_of_adding_percentages(tmp_path) -> None:
    service = _service(tmp_path)
    now = utc_naive_now()
    with service.db.session_scope() as session:
        session.add(StockDaily(code="000001", date=date(2026, 8, 28), close=10.0, data_source="test"))
        for offset in range(5):
            session.add(ConceptThemeSnapshotRecord(
                source="ths", source_code=f"885001-{offset}", canonical_name="CPO/共封装光学",
                theme_type="concept", market_date=date(2026, 8, 24) + timedelta(days=offset),
                pct_change=1.0, heat_score=70.0, captured_at=now,
            ))
    item = service.rotation(days=5, limit=8)["items"][0]
    assert item["momentum_5d"] == round(((1.01 ** 5) - 1) * 100, 3)
    assert "复利累计" in service.rotation(days=5, limit=8)["method"]
    history = service._canonical_snapshot_history("CPO/共封装光学", days=5)
    assert history["available_dates"] == 5
    assert history["cumulative_return"] == round(((1.01 ** 5) - 1) * 100, 3)
    assert history["points"][-1]["source_count"] == 1


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
                specificity_score=65, beta=1.2, residual_return=6.0, observations=20, confidence="medium",
                source_count=1, calculated_at=now,
            ),
            ConceptExposureRecord(
                ts_code="300308.SZ", stock_name="中际旭创", canonical_name="CPO/共封装光学",
                as_of_date=date(2026, 8, 27), horizon_days=60, weight_score=79,
                specificity_score=66, beta=0.8, residual_return=10.0, observations=52, confidence="medium",
                source_count=1, calculated_at=now,
            ),
        ])

    lens_20 = service.stock_lens("300308.SZ", refresh_if_empty=False, horizon_days=20)
    lens_60 = service.stock_lens("300308.SZ", refresh_if_empty=False, horizon_days=60)

    assert lens_20["as_of_date"] == "2026-08-28"
    assert lens_20["themes"][0]["beta"] == 1.2
    assert lens_60["as_of_date"] == "2026-08-27"
    assert lens_60["themes"][0]["beta"] == 0.8
    assert [point["horizon_days"] for point in lens_60["themes"][0]["horizon_profile"]] == [20, 60]
    assert lens_60["themes"][0]["beta_stability"] == "shifting"
    assert lens_60["summary"]["persistent_alpha_count"] == 1


def test_multi_horizon_backfill_prioritizes_consensus_leaders_without_blocking(monkeypatch, tmp_path) -> None:
    service = _service(tmp_path)
    now = utc_naive_now()
    with service.db.session_scope() as session:
        session.add(StockDaily(code="000001", date=date(2026, 8, 28), close=10.0, data_source="test"))
        user = UserAccount(
            display_name="测试用户", normalized_name="测试用户", password_salt="salt",
            password_hash="hash", status="approved", created_at=now, updated_at=now,
        )
        session.add(user); session.flush()
        # Watchlists store both compact and suffixed forms in real migrated data.
        session.add(UserWatchlistItem(user_id=user.id, symbol="300308", created_at=now))
        session.add(ConceptExposureRecord(
            ts_code="300308.SZ", stock_name="中际旭创", canonical_name="CPO/共封装光学",
            as_of_date=date(2026, 8, 28), horizon_days=60, weight_score=88,
            source_count=3, confidence="high", observations=60, calculated_at=now,
        ))
        session.add(ConceptExposureRecord(
            ts_code="600371.SH", stock_name="万向德农", canonical_name="农业种植",
            as_of_date=date(2026, 8, 28), horizon_days=60, weight_score=99,
            source_count=4, confidence="high", observations=60, calculated_at=now,
        ))
    calls = []

    def fake_calculate(canonical_name, *, horizon_days, only_stock=None):
        calls.append((canonical_name, horizon_days, only_stock))
        return 1

    monkeypatch.setattr(service, "calculate_canonical_exposures", fake_calculate)
    result = service.backfill_multi_horizon_profiles(stock_limit=1, themes_per_stock=1)

    assert result == {"stocks": 1, "attempted": 2, "completed": 2, "failed": 0, "exposures": 2}
    assert calls == [
        ("CPO/共封装光学", 20, "300308.SZ"),
        ("CPO/共封装光学", 120, "300308.SZ"),
    ]


def test_market_consensus_leaders_require_cross_source_themes_and_one_snapshot(tmp_path) -> None:
    service = _service(tmp_path)
    now = utc_naive_now()
    with service.db.session_scope() as session:
        session.add_all([
            ConceptExposureRecord(
                ts_code="300308.SZ", stock_name="中际旭创", canonical_name="CPO/共封装光学",
                as_of_date=date(2026, 8, 28), horizon_days=60, weight_score=88,
                specificity_score=70, beta=1.35, residual_return=12.0, observations=60,
                confidence="high", source_count=4, calculated_at=now,
            ),
            ConceptExposureRecord(
                ts_code="300308.SZ", stock_name="中际旭创", canonical_name="液冷服务器",
                as_of_date=date(2026, 8, 28), horizon_days=60, weight_score=76,
                specificity_score=62, beta=.95, residual_return=8.0, observations=60,
                confidence="medium", source_count=2, calculated_at=now,
            ),
            ConceptExposureRecord(
                ts_code="300308.SZ", stock_name="中际旭创", canonical_name="光模块",
                as_of_date=date(2026, 8, 28), horizon_days=60, weight_score=72,
                specificity_score=58, beta=1.18, residual_return=6.0, observations=60,
                confidence="medium", source_count=3, calculated_at=now,
            ),
            ConceptExposureRecord(
                ts_code="300502.SZ", stock_name="新易盛", canonical_name="光模块单源线索",
                as_of_date=date(2026, 8, 28), horizon_days=60, weight_score=92,
                specificity_score=91, beta=1.6, residual_return=20.0, observations=60,
                confidence="high", source_count=1, calculated_at=now,
            ),
            ConceptExposureRecord(
                ts_code="300308.SZ", stock_name="中际旭创", canonical_name="旧快照",
                as_of_date=date(2026, 8, 27), horizon_days=60, weight_score=99,
                specificity_score=99, beta=3.0, residual_return=50.0, observations=60,
                confidence="high", source_count=6, calculated_at=now,
            ),
        ])

    radar = service.market_consensus_leaders(horizon_days=60, limit=8, mode="consensus")

    assert radar["as_of_date"] == "2026-08-28"
    assert radar["total_candidates"] == 1
    assert radar["items"][0]["ts_code"] == "300308.SZ"
    assert radar["items"][0]["consensus_theme_count"] == 3
    assert radar["items"][0]["independent_cluster_count"] == 2
    assert radar["items"][0]["theme_overlap_rate"] == 33.3
    assert [item["canonical_name"] for item in radar["items"][0]["primary_themes"]] == [
        "CPO/共封装光学", "液冷服务器",
    ]


def test_unique_alpha_evidence_excludes_sector_roundups_and_price_snapshots(tmp_path) -> None:
    service = _service(tmp_path)
    now = utc_naive_now()
    common = {
        "source_type": "essay", "event_type": "research", "perspective": "institution",
        "symbol_codes": "300308.SZ", "sentiment": "neutral", "importance_score": 80,
        "confidence_score": .8, "event_at": now,
    }
    with service.db.session_scope() as session:
        session.add_all([
            MonitoringEventRecord(
                source_key="zsxq.essay", source_name="知识星球小作文（待核验）", external_id="broad",
                title="AI算力产业链最新观点", summary="核心标的包括中际旭创等多家公司。", **common,
            ),
            MonitoringEventRecord(
                source_key="zsxq.essay", source_name="知识星球小作文（待核验）", external_id="company",
                title="中际旭创盈利预测上调", summary="预计高速光模块交付增长。", **common,
            ),
            MonitoringEventRecord(
                source_key="tushare.close", source_name="Tushare 收盘行情与估值", external_id="close",
                title="中际旭创 收盘 870.22（-7.72%）", summary="行情快照。", **common,
            ),
        ])

    evidence = service._unique_driver_evidence("300308.SZ", "中际旭创")
    assert [item["title"] for item in evidence] == ["中际旭创盈利预测上调"]
    assert evidence[0]["url"].startswith("/investment-monitor/feed?event=")


def test_cluster_detail_aggregates_theme_nodes_without_inventing_cluster_beta(tmp_path) -> None:
    service = _service(tmp_path)
    now = utc_naive_now()
    with service.db.session_scope() as session:
        themes = []
        for source, source_code, name, canonical in (
            ("ths", "885001.TI", "CPO概念", "CPO/共封装光学"),
            ("dc_theme", "000699.DC", "光纤", "光纤"),
        ):
            theme = ConceptThemeRecord(
                source=source, source_code=source_code, name=name, canonical_name=canonical,
                theme_type="theme", level=3, market_date=date(2026, 8, 28),
                first_seen_at=now, last_seen_at=now, updated_at=now,
            )
            session.add(theme); session.flush(); themes.append(theme)
            session.add(ConceptMembershipRecord(
                theme_id=theme.id, source=source, ts_code="300308.SZ", stock_name="中际旭创",
                active=True, first_seen_at=now, last_seen_at=now, updated_at=now,
            ))
        session.add(ConceptExposureRecord(
            ts_code="300308.SZ", stock_name="中际旭创", canonical_name="CPO/共封装光学",
            as_of_date=date(2026, 8, 28), horizon_days=60, weight_score=86,
            beta=1.3, residual_return=11.0, observations=60, confidence="high",
            source_count=2, calculated_at=now,
        ))

    result = service.cluster_detail("AI算力与数字基础设施", "光通信产业链", horizon_days=60)

    assert result["theme_nodes"] == 2
    assert result["total_stocks"] == 1
    assert result["items"][0]["theme_count"] == 2
    assert result["items"][0]["dominant_exposure"]["canonical_name"] == "CPO/共封装光学"
    assert "不冒充二级主题回归" in result["method"]


def test_institution_radar_keeps_ai_candidates_separate_from_provider_consensus(tmp_path) -> None:
    service = _service(tmp_path)
    now = utc_naive_now()
    with service.db.session_scope() as session:
        session.add_all([
            ConceptThemeRecord(
                source="ths", source_code="885001.TI", name="CPO概念",
                canonical_name="CPO/共封装光学", theme_type="theme", level=3,
                market_date=date(2026, 8, 28), first_seen_at=now, last_seen_at=now, updated_at=now,
            ),
            ConceptThemeRecord(
                source="dc_theme", source_code="000699.DC", name="CPO概念",
                canonical_name="CPO/共封装光学", theme_type="theme", level=3,
                market_date=date(2026, 8, 28), first_seen_at=now, last_seen_at=now, updated_at=now,
            ),
        ])
        for index in range(2):
            topic_id = f"institution-radar-{index}"
            session.add(ResearchNote(
                topic_id=topic_id, group_id="g1", group_name="调研纪要",
                title=f"中际旭创新技术跟踪{index}", content="讨论CPO与自研新主题。",
                symbol_codes="300308.SZ", content_hash=f"institution-radar-content-{index}",
                created_at=now - timedelta(days=index + 1),
            ))
            session.flush()
            session.add(EssayAnalysisRecord(
                topic_id=topic_id, status="completed", model="test-model", prompt_version="test-v1",
                input_hash=f"institution-radar-analysis-{index}", summary="明确公司与新技术主题。",
                sentiment="bullish" if index == 0 else "neutral", importance_score=80,
                confidence_score=.9, themes_json=json.dumps(["CPO", "自研新主题"], ensure_ascii=False),
                stock_mentions_json=json.dumps([{
                    "ts_code": "300308.SZ", "name": "中际旭创", "confidence": .9,
                }], ensure_ascii=False), completed_at=now, updated_at=now,
            ))

    radar = service.institution_theme_radar(days=30, limit=16)
    by_name = {item["canonical_name"]: item for item in radar["items"]}

    assert by_name["CPO/共封装光学"]["status"] == "provider_consensus"
    assert by_name["CPO/共封装光学"]["provider_count"] == 2
    assert by_name["自研新主题"]["status"] == "corpus_candidate"
    assert by_name["自研新主题"]["provider_count"] == 0
    assert by_name["自研新主题"]["discovery_score"] < 100
    assert by_name["自研新主题"]["acceleration_pct"] is None
    assert by_name["自研新主题"]["stocks"][0] == {
        "ts_code": "300308.SZ", "name": "中际旭创", "mentions": 2,
    }
    assert "不自动计入市场共识" in radar["method"]


def test_related_theme_affinity_uses_real_constituent_jaccard(tmp_path) -> None:
    service = _service(tmp_path)
    now = utc_naive_now()
    with service.db.session_scope() as session:
        themes = {}
        for index, canonical_name in enumerate(("CPO/共封装光学", "光通信/光模块", "液冷服务器")):
            theme = ConceptThemeRecord(
                source="ths", source_code=f"88510{index}.TI", name=canonical_name,
                canonical_name=canonical_name, theme_type="theme", level=3,
                market_date=date(2026, 8, 28), first_seen_at=now, last_seen_at=now, updated_at=now,
            )
            session.add(theme); session.flush(); themes[canonical_name] = theme
        memberships = {
            "CPO/共封装光学": [("300308.SZ", "中际旭创"), ("300502.SZ", "新易盛"), ("300394.SZ", "天孚通信")],
            "光通信/光模块": [("300308.SZ", "中际旭创"), ("300502.SZ", "新易盛"), ("600000.SH", "浦发银行")],
            "液冷服务器": [("300308.SZ", "中际旭创"), ("000001.SZ", "平安银行")],
        }
        for canonical_name, stocks in memberships.items():
            for code, name in stocks:
                session.add(ConceptMembershipRecord(
                    theme_id=themes[canonical_name].id, source="ths", ts_code=code, stock_name=name,
                    active=True, first_seen_at=now, last_seen_at=now, updated_at=now,
                ))

    affinity = service._related_theme_affinity(
        "CPO/共封装光学", ["300308.SZ", "300502.SZ", "300394.SZ"], limit=8,
    )

    assert affinity["target_total_stocks"] == 3
    assert len(affinity["items"]) == 1
    assert affinity["items"][0]["canonical_name"] == "光通信/光模块"
    assert affinity["items"][0]["shared_stocks"] == 2
    assert affinity["items"][0]["jaccard_pct"] == 50.0
    assert affinity["items"][0]["relation_type"] == "高度重叠"
    assert affinity["items"][0]["target_exclusive_stocks"] == 1


def test_watchlist_theme_map_isolates_codes_and_deduplicates_narratives(tmp_path) -> None:
    service = _service(tmp_path)
    now = utc_naive_now()
    with service.db.session_scope() as session:
        session.add_all([
            ConceptExposureRecord(
                ts_code="300308.SZ", stock_name="中际旭创", canonical_name="CPO/共封装光学",
                as_of_date=date(2026, 8, 28), horizon_days=60, weight_score=88,
                source_count=3, beta=1.3, residual_return=8, confidence="high", calculated_at=now,
            ),
            ConceptExposureRecord(
                ts_code="300308.SZ", stock_name="中际旭创", canonical_name="光通信/光模块",
                as_of_date=date(2026, 8, 28), horizon_days=60, weight_score=80,
                source_count=2, beta=1.1, residual_return=5, confidence="medium", calculated_at=now,
            ),
            ConceptExposureRecord(
                ts_code="300502.SZ", stock_name="新易盛", canonical_name="CPO/共封装光学",
                as_of_date=date(2026, 8, 27), horizon_days=60, weight_score=84,
                source_count=3, beta=1.2, residual_return=7, confidence="high", calculated_at=now,
            ),
            ConceptExposureRecord(
                ts_code="600000.SH", stock_name="非自选", canonical_name="银行",
                as_of_date=date(2026, 8, 28), horizon_days=60, weight_score=99,
                source_count=4, beta=.8, residual_return=2, confidence="high", calculated_at=now,
            ),
        ])

    result = service.watchlist_theme_map(["300308", "300502"], horizon_days=60)

    assert result["stock_count"] == 2
    assert {item["ts_code"] for item in result["stocks"]} == {"300308.SZ", "300502.SZ"}
    assert result["as_of_date"] == "2026-08-28"
    assert result["themes"][0]["cluster"] == "光通信产业链"
    assert result["themes"][0]["stock_count"] == 2
    assert result["stocks"][0]["independent_cluster_count"] == 1
    assert result["concentration"]["level"] == "高"
    assert result["concentration"]["top_cluster"] == "光通信产业链"
    assert result["concentration"]["top_coverage_pct"] == 100.0
    assert result["concentration"]["covered_stock_count"] == 2
    assert "不代表持仓市值权重" in result["concentration"]["interpretation"]


def test_membership_change_ledger_excludes_initial_baseline(tmp_path) -> None:
    service = _service(tmp_path)
    now = utc_naive_now()
    with service.db.session_scope() as session:
        old_theme = ConceptThemeRecord(
            source="ths", source_code="T-OLD", name="光模块", canonical_name="光通信/光模块",
            theme_type="concept", level=3, first_seen_at=now - timedelta(days=20),
            last_seen_at=now, updated_at=now,
        )
        new_theme = ConceptThemeRecord(
            source="kpl", source_code="T-NEW", name="机器人", canonical_name="机器人",
            theme_type="theme", level=3, first_seen_at=now - timedelta(days=1),
            last_seen_at=now, updated_at=now,
        )
        session.add_all([old_theme, new_theme])
        session.flush()
        session.add_all([
            ConceptMembershipRecord(
                theme_id=old_theme.id, source="ths", ts_code="300308.SZ", stock_name="中际旭创",
                active=True, first_seen_at=now - timedelta(days=1), last_seen_at=now,
                updated_at=now, market_date=date(2026, 8, 28),
            ),
            ConceptMembershipRecord(
                theme_id=old_theme.id, source="ths", ts_code="300502.SZ", stock_name="新易盛",
                active=False, first_seen_at=now - timedelta(days=10), last_seen_at=now - timedelta(days=1),
                updated_at=now - timedelta(hours=4), market_date=date(2026, 8, 28),
            ),
            ConceptMembershipRecord(
                theme_id=new_theme.id, source="kpl", ts_code="688017.SH", stock_name="绿的谐波",
                active=True, first_seen_at=now - timedelta(days=1), last_seen_at=now,
                updated_at=now, market_date=date(2026, 8, 28),
            ),
        ])

    result = service.membership_change_ledger(days=7, limit=12)

    assert {(item["state"], item["ts_code"]) for item in result["items"]} == {
        ("added", "300308.SZ"), ("removed", "300502.SZ"),
    }
    assert result["baseline_ignored"] == 1
    assert result["added"] == 1
    assert result["removed"] == 1
