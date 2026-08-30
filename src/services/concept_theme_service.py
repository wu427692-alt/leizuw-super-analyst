# -*- coding: utf-8 -*-
"""Multi-source concept/theme graph and explainable stock exposure analytics."""

from __future__ import annotations

from collections import defaultdict
from contextlib import contextmanager
from datetime import date, datetime, timedelta
import json
import logging
import math
import re
import statistics
import threading
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np
from sqlalchemy import and_, desc, func, or_, select, update

from src.services.financial_data_service import (
    FinancialDataUpstreamError,
    TushareGatewayService,
)
from src.storage import (
    ConceptExposureRecord,
    ConceptMembershipRecord,
    ConceptMembershipSyncState,
    ConceptSyncRunRecord,
    ConceptThemeRecord,
    ConceptThemeSnapshotRecord,
    DatabaseManager,
    EssayAnalysisRecord,
    MarketIndexBar,
    MonitoringEventRecord,
    ResearchNote,
    StockDaily,
    UserWatchlistItem,
    utc_naive_now,
)

logger = logging.getLogger(__name__)


class ConceptThemeError(RuntimeError):
    """A recoverable concept data or analytics error."""


SOURCE_LABELS = {
    "ths": "同花顺概念/行业",
    "dc_board": "东方财富板块",
    "dc_theme": "东方财富题材库",
    "kpl": "开盘啦题材",
    "tdx": "通达信板块",
    "sw": "申万行业",
}

SOURCE_RELIABILITY = {
    "sw": 1.00,
    "dc_theme": 0.96,
    "kpl": 0.92,
    "dc_board": 0.86,
    "ths": 0.82,
    "tdx": 0.74,
}

THS_TYPE_MAP = {
    "N": ("concept", 3),
    "I": ("industry", 1),
    "R": ("region", 2),
    "S": ("feature", 3),
    "ST": ("style", 3),
    "TH": ("theme", 2),
    "BB": ("broad", 0),
}

FAMILY_RULES: Sequence[Tuple[str, Sequence[str]]] = (
    ("AI算力与数字基础设施", ("AI", "人工智能", "算力", "光模块", "光通信", "CPO", "服务器", "数据中心", "液冷", "PCB", "铜缆", "硅光", "存储", "数字身份", "电子身份证")),
    ("半导体与先进电子", ("半导体", "芯片", "集成电路", "光刻", "封测", "消费电子", "电子元件", "汽车电子", "军工电子", "电子布", "电子特气", "电子纸", "电子后视镜", "电子车牌", "电子信息", "被动元件", "先进封装")),
    ("先进制造与机器人", ("机器人", "自动化", "工业母机", "数控", "智能制造", "机械", "设备", "人形")),
    ("低空经济与商业航天", ("低空", "无人机", "eVTOL", "飞行汽车", "航空", "航天", "卫星", "军工", "大飞机")),
    ("汽车与智能驾驶", ("汽车", "车联网", "智能驾驶", "无人驾驶", "锂电", "充电桩", "一体化压铸")),
    ("新能源与电力系统", ("光伏", "风电", "储能", "电力", "电网", "核电", "氢能", "新能源", "电池")),
    ("医药健康", ("医药", "医疗", "创新药", "生物", "疫苗", "中药", "CXO", "器械")),
    ("消费与品牌", ("消费", "白酒", "食品", "旅游", "零售", "家电", "电商", "电子商务", "传媒", "游戏", "电子竞技", "电子游戏", "电子烟")),
    ("资源与周期", ("有色", "煤炭", "钢铁", "化工", "稀土", "黄金", "石油", "金属", "资源")),
    ("金融地产", ("银行", "证券", "保险", "金融", "地产", "房地产")),
    ("政策改革与区域", ("国企改革", "一带一路", "自贸", "新区", "区域", "振兴", "政策", "共同富裕")),
)

CLUSTER_RULES: Sequence[Tuple[str, Sequence[str]]] = (
    ("光通信产业链", ("CPO", "NPO", "光模块", "光通信", "光器件", "硅光", "光芯片", "光纤")),
    ("PCB/CCL产业链", ("PCB", "CCL", "覆铜板", "电子布", "玻纤布")),
    ("AI数据中心", ("算力", "数据中心", "服务器", "液冷", "超节点")),
    ("半导体设备材料", ("光刻", "晶圆", "半导体设备", "电子气体", "先进封装")),
    ("低空经济", ("低空", "EVTOL", "无人机", "空管")),
    ("商业航天", ("商业航天", "卫星", "火箭", "航天")),
    ("人形机器人", ("人形机器人", "减速器", "丝杠", "灵巧手")),
    ("智能驾驶", ("智能驾驶", "无人驾驶", "激光雷达", "车联网")),
    ("创新药", ("创新药", "ADC", "双抗", "减肥药", "小核酸")),
    ("新型电力系统", ("储能", "电网", "电力", "虚拟电厂", "特高压")),
)

CANONICAL_ALIASES = {
    "共封装光学": "CPO/共封装光学",
    "共封装光学CPO": "CPO/共封装光学",
    "CPO": "CPO/共封装光学",
    "CPO概念": "CPO/共封装光学",
    "CPO(共封装光学)": "CPO/共封装光学",
    "光模块": "光通信/光模块",
    "光通信": "光通信/光模块",
    "光通信模块": "光通信/光模块",
    "光通信设备": "光通信/光模块",
    "低空经济概念": "低空经济",
    "飞行汽车": "eVTOL/飞行汽车",
    "飞行汽车(eVTOL)": "eVTOL/飞行汽车",
    "eVTOL": "eVTOL/飞行汽车",
    "人形机器人概念": "人形机器人",
    "AI算力": "AI算力",
    "算力概念": "AI算力",
    "AIPC": "AI PC",
    "AI PC概念": "AI PC",
    "氢能源": "氢能",
    "钠电池": "钠离子电池",
    "核能核电": "核电",
    "核电核能": "核电",
    "HIT电池": "HJT电池",
}


def _json(value: Any, fallback: Any) -> Any:
    if value in (None, ""):
        return fallback
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(str(value))
    except (TypeError, ValueError, json.JSONDecodeError):
        return fallback


def _safe_float(value: Any) -> Optional[float]:
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (TypeError, ValueError):
        return None


def _safe_int(value: Any) -> Optional[int]:
    number = _safe_float(value)
    return int(number) if number is not None else None


def _parse_date(value: Any) -> Optional[date]:
    text = str(value or "").strip().replace("-", "")[:8]
    try:
        return datetime.strptime(text, "%Y%m%d").date() if text else None
    except ValueError:
        return None


def _ts_code(value: Any) -> str:
    text = str(value or "").strip().upper()
    if re.fullmatch(r"\d{6}\.(SH|SZ|BJ)", text):
        return text
    if re.fullmatch(r"\d{6}", text):
        suffix = "SH" if text.startswith(("5", "6", "9")) else "BJ" if text.startswith(("4", "8")) else "SZ"
        return f"{text}.{suffix}"
    return text


def _normalize_name(value: Any) -> str:
    text = str(value or "").strip()
    text = re.sub(r"[（(]?(?:概念|板块|指数)[）)]?$", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s+", "", text)
    return text or str(value or "").strip()


def canonicalize_theme(name: Any) -> str:
    normalized = _normalize_name(name)
    key = re.sub(r"[/+·—_\-（）()\s]", "", normalized).upper()
    for alias, target in CANONICAL_ALIASES.items():
        alias_key = re.sub(r"[/+·—_\-（）()\s]", "", alias).upper()
        if key == alias_key:
            return target
    return normalized


def theme_family(name: str, theme_type: str) -> str:
    if theme_type == "industry":
        return "申万/市场行业体系"
    if theme_type == "region":
        return "区域与地理"
    if theme_type in {"style", "feature", "broad"}:
        return "市场风格与宽基"
    upper = name.upper()
    for family, keywords in FAMILY_RULES:
        if any(keyword.upper() in upper for keyword in keywords):
            return family
    return "其他市场题材"


def theme_cluster(name: str, family: str) -> str:
    upper = str(name or "").upper()
    for cluster, keywords in CLUSTER_RULES:
        if any(keyword.upper() in upper for keyword in keywords):
            return cluster
    return family


class ConceptThemeService:
    """Shared concept catalog, evidence weights, and return attribution service."""

    _market_date_lock = threading.Lock()
    _market_date_value: Optional[date] = None
    _market_date_checked_at: Optional[datetime] = None

    def __init__(self, *, gateway: Optional[TushareGatewayService] = None, db: Optional[DatabaseManager] = None):
        self.gateway = gateway or TushareGatewayService()
        self.db = db or DatabaseManager.get_instance()

    @contextmanager
    def _read_scope(self):
        """Read without a commit so returned ORM attributes are not expired."""
        session = self.db.get_session()
        try:
            yield session
        finally:
            session.close()

    def latest_market_date(self) -> date:
        now = datetime.now()
        cached_at = self.__class__._market_date_checked_at
        cached_value = self.__class__._market_date_value
        if cached_value is not None and cached_at is not None and (now - cached_at).total_seconds() < 1800:
            return cached_value
        with self.__class__._market_date_lock:
            cached_at = self.__class__._market_date_checked_at
            cached_value = self.__class__._market_date_value
            if cached_value is not None and cached_at is not None and (now - cached_at).total_seconds() < 1800:
                return cached_value
            # Constituents and close-to-close attribution use the latest completed
            # exchange session. A malformed weekend StockDaily row must never be
            # forwarded as a provider trade_date (some concept APIs then return 0 rows).
            cutoff = date.today() if now.hour >= 16 else date.today() - timedelta(days=1)
            resolved: Optional[date] = None
            if getattr(self.gateway, "available", False):
                try:
                    calendar = self.gateway.query("trade_cal", params={
                        "exchange": "SSE",
                        "start_date": (cutoff - timedelta(days=24)).strftime("%Y%m%d"),
                        "end_date": cutoff.strftime("%Y%m%d"),
                        "is_open": "1",
                    })["rows"]
                    candidates = [_parse_date(row.get("cal_date")) for row in calendar]
                    resolved = max((item for item in candidates if item and item <= cutoff), default=None)
                except FinancialDataUpstreamError:
                    logger.debug("concept market calendar unavailable; using local completed session")
            if resolved is None:
                with self._read_scope() as session:
                    candidates = session.execute(
                        select(StockDaily.date).where(StockDaily.date <= cutoff)
                        .distinct().order_by(desc(StockDaily.date)).limit(12)
                    ).scalars().all()
                resolved = next((item for item in candidates if item.weekday() < 5), None)
            if resolved is None:
                resolved = cutoff
                while resolved.weekday() >= 5:
                    resolved -= timedelta(days=1)
            self.__class__._market_date_value = resolved
            self.__class__._market_date_checked_at = now
            return resolved

    def sync_catalog(self, *, market_date: Optional[date] = None) -> Dict[str, Any]:
        if not self.gateway.available:
            raise ConceptThemeError("Tushare 未配置，无法更新题材目录")
        target = market_date or self.latest_market_date()
        run_id = self._start_run("catalog", target, "读取六套题材目录")
        source_stats: Dict[str, Any] = {}
        normalized: List[Dict[str, Any]] = []
        try:
            ths = self.gateway.query("ths_index", params={})["rows"]
            source_stats["ths"] = len(ths)
            for row in ths:
                kind, level = THS_TYPE_MAP.get(str(row.get("type") or "N"), ("concept", 3))
                normalized.append(self._theme_row(
                    source="ths", code=row.get("ts_code"), name=row.get("name"), theme_type=kind,
                    level=level, count=row.get("count"), market_date=target, payload=row,
                ))

            day_text = target.strftime("%Y%m%d")
            dc = self.gateway.query("dc_index", params={"trade_date": day_text})["rows"]
            source_stats["dc_board"] = len(dc)
            for row in dc:
                raw_type = str(row.get("idx_type") or "概念板块")
                kind = "industry" if "行业" in raw_type else "region" if "地域" in raw_type else "concept"
                normalized.append(self._theme_row(
                    source="dc_board", code=row.get("ts_code"), name=row.get("name"), theme_type=kind,
                    level=_safe_int(row.get("level")) or (1 if kind == "industry" else 3),
                    count=(_safe_int(row.get("up_num")) or 0) + (_safe_int(row.get("down_num")) or 0),
                    market_date=_parse_date(row.get("trade_date")) or target,
                    pct_change=row.get("pct_change"), payload=row,
                ))

            dc_theme = self.gateway.query("dc_concept", params={"trade_date": day_text})["rows"]
            source_stats["dc_theme"] = len(dc_theme)
            for row in dc_theme:
                rank = _safe_float(row.get("sort"))
                heat = max(0.0, min(100.0, 101.0 - math.sqrt(rank or 10_000)))
                normalized.append(self._theme_row(
                    source="dc_theme", code=row.get("theme_code"), name=row.get("name"), theme_type="theme",
                    level=3, market_date=_parse_date(row.get("trade_date")) or target,
                    heat=heat, pct_change=row.get("pct_change"), fund_flow=row.get("main_change"), payload=row,
                ))

            kpl = self.gateway.query("kpl_concept", params={"trade_date": day_text})["rows"]
            source_stats["kpl"] = len(kpl)
            for row in kpl:
                normalized.append(self._theme_row(
                    source="kpl", code=row.get("ts_code"), name=row.get("name"), theme_type="theme", level=3,
                    count=(_safe_int(row.get("z_t_num")) or 0) + (_safe_int(row.get("up_num")) or 0),
                    market_date=_parse_date(row.get("trade_date")) or target,
                    heat=min(100.0, (_safe_float(row.get("z_t_num")) or 0) * 8.0), payload=row,
                ))

            tdx = self.gateway.query("tdx_index", params={"trade_date": day_text})["rows"]
            source_stats["tdx"] = len(tdx)
            for row in tdx:
                raw_type = str(row.get("idx_type") or "概念板块")
                kind = "industry" if "行业" in raw_type else "region" if "地区" in raw_type else "concept"
                normalized.append(self._theme_row(
                    source="tdx", code=row.get("ts_code"), name=row.get("name"), theme_type=kind,
                    level=1 if kind == "industry" else 3, count=row.get("idx_count"),
                    market_date=_parse_date(row.get("trade_date")) or target, payload=row,
                ))

            sw_count = 0
            for level_text, level_number in (("L1", 1), ("L2", 2), ("L3", 3)):
                rows = self.gateway.query("index_classify", params={"level": level_text, "src": "SW2021"})["rows"]
                sw_count += len(rows)
                for row in rows:
                    normalized.append(self._theme_row(
                        source="sw", code=row.get("index_code"), name=row.get("industry_name"),
                        theme_type="industry", level=level_number, parent=row.get("parent_code"),
                        market_date=target, payload=row,
                    ))
            source_stats["sw"] = sw_count

            saved = self._upsert_themes(normalized)
            self._finish_run(run_id, status="completed", progress=100, stage="目录更新完成",
                             themes=len(normalized), source_stats=source_stats)
            return {"status": "completed", "market_date": target.isoformat(), "seen": len(normalized),
                    "saved": saved, "sources": source_stats}
        except Exception as exc:
            self._finish_run(run_id, status="failed", progress=100, stage="目录更新失败",
                             themes=len(normalized), source_stats=source_stats, error=exc)
            raise ConceptThemeError(f"题材目录更新失败：{str(exc)[:300]}") from exc

    def refresh_theme(self, theme_id: int, *, calculate: bool = True) -> Dict[str, Any]:
        with self._read_scope() as session:
            theme = session.get(ConceptThemeRecord, int(theme_id))
            if theme is None:
                raise ConceptThemeError("题材不存在")
            snapshot = self._theme_dict(theme)
        rows = self._fetch_theme_members(snapshot)
        # Most component endpoints cap a single response at 2,000 or more rows.
        # Only a non-empty response safely below that boundary can be treated as complete.
        replace_existing = 0 < len(rows) < 1900
        saved = self._upsert_memberships(snapshot, rows, replace_existing=replace_existing)
        exposures = self.calculate_canonical_exposures(snapshot["canonical_name"], horizon_days=60) if calculate else 0
        return {"theme_id": theme_id, "received": len(rows), "saved": saved, "exposures": exposures}

    def refresh_stock(self, ts_code: str, *, calculate: bool = True) -> Dict[str, Any]:
        code = _ts_code(ts_code)
        if not re.fullmatch(r"\d{6}\.(SH|SZ|BJ)", code):
            raise ConceptThemeError("股票代码格式不正确")
        target = self.latest_market_date()
        day_text = target.strftime("%Y%m%d")
        catalog = self._theme_lookup()
        fetched: List[Tuple[str, Dict[str, Any]]] = []
        source_stats: Dict[str, int] = {}
        calls = (
            ("ths", "ths_member", {"con_code": code}),
            ("dc_board", "dc_member", {"con_code": code, "trade_date": day_text}),
            ("dc_theme", "dc_concept_cons", {"ts_code": code, "trade_date": day_text}),
            ("kpl", "kpl_concept_cons", {"con_code": code, "trade_date": day_text}),
            ("tdx", "tdx_member", {"con_code": code, "trade_date": day_text}),
        )
        for source, api_name, params in calls:
            try:
                rows = self.gateway.query(api_name, params=params)["rows"]
                source_stats[source] = len(rows)
                fetched.extend((source, row) for row in rows)
            except FinancialDataUpstreamError as exc:
                logger.warning("concept stock membership source failed: %s %s", source, exc)
                source_stats[source] = -1
        try:
            sw = self.gateway.query("index_member_all", params={"ts_code": code, "is_new": "Y"})["rows"]
            source_stats["sw"] = len(sw) * 3
            for row in sw:
                for level in (1, 2, 3):
                    fetched.append(("sw", {
                        "ts_code": row.get(f"l{level}_code"), "con_code": code, "con_name": row.get("name"),
                        "industry_name": row.get(f"l{level}_name"), "trade_date": day_text,
                    }))
        except FinancialDataUpstreamError as exc:
            logger.warning("concept stock SW membership source failed: %s", exc)
            source_stats["sw"] = -1

        grouped: Dict[int, List[Dict[str, Any]]] = defaultdict(list)
        missing: List[Dict[str, Any]] = []
        for source, row in fetched:
            source_code = str(row.get("theme_code") or row.get("ts_code") or "")
            theme = catalog.get((source, source_code))
            if theme is None:
                name = row.get("name") if source in {"kpl", "dc_theme"} else row.get("industry_name")
                if not name:
                    continue
                missing.append(self._theme_row(
                    source=source, code=source_code, name=name,
                    theme_type="industry" if source == "sw" else "theme", level=3,
                    market_date=_parse_date(row.get("trade_date")) or target, payload=row,
                ))
        if missing:
            self._upsert_themes(missing)
            catalog = self._theme_lookup()
        for source, row in fetched:
            source_code = str(row.get("theme_code") or row.get("ts_code") or "")
            theme = catalog.get((source, source_code))
            if theme:
                grouped[int(theme["id"])].append(row)
        saved = 0
        canonicals: set[str] = set()
        for theme_id, rows in grouped.items():
            theme = next(value for value in catalog.values() if int(value["id"]) == theme_id)
            saved += self._upsert_memberships(theme, rows, force_stock=code)
            canonicals.add(str(theme["canonical_name"]))
        exposure_count = 0
        if calculate:
            for canonical in canonicals:
                exposure_count += self.calculate_canonical_exposures(canonical, horizon_days=60, only_stock=code)
        return {"ts_code": code, "memberships": saved, "exposures": exposure_count, "sources": source_stats}

    def overview(
        self, *, query: str = "", theme_type: str = "", source: str = "", family: str = "", cluster: str = "",
        sort_by: str = "heat", page: int = 1, page_size: int = 80,
    ) -> Dict[str, Any]:
        page = max(1, page)
        page_size = max(12, min(page_size, 200))
        stock_matches: List[Dict[str, Any]] = []
        with self._read_scope() as session:
            statement = select(ConceptThemeRecord)
            count_stmt = select(func.count(ConceptThemeRecord.id))
            filters = []
            if query.strip():
                term = f"%{query.strip()}%"
                stock_theme_ids = select(ConceptMembershipRecord.theme_id).where(or_(
                    ConceptMembershipRecord.ts_code.like(term),
                    ConceptMembershipRecord.stock_name.like(term),
                ))
                filters.append(or_(ConceptThemeRecord.name.like(term), ConceptThemeRecord.canonical_name.like(term),
                                   ConceptThemeRecord.source_code.like(term), ConceptThemeRecord.id.in_(stock_theme_ids)))
            if theme_type:
                filters.append(ConceptThemeRecord.theme_type == theme_type)
            if source:
                filters.append(ConceptThemeRecord.source == source)
            if filters:
                statement = statement.where(and_(*filters))
                count_stmt = count_stmt.where(and_(*filters))
            if sort_by == "name":
                statement = statement.order_by(ConceptThemeRecord.canonical_name.asc())
            elif sort_by == "size":
                statement = statement.order_by(desc(ConceptThemeRecord.constituent_count), ConceptThemeRecord.name.asc())
            elif sort_by == "change":
                statement = statement.order_by(desc(ConceptThemeRecord.pct_change), desc(ConceptThemeRecord.heat_score))
            else:
                statement = statement.order_by(desc(ConceptThemeRecord.heat_score), desc(ConceptThemeRecord.market_date), ConceptThemeRecord.name.asc())
            if family or cluster:
                # Family is a deterministic semantic classification rather than
                # a provider field. Filter before pagination so the total and
                # every page remain correct instead of filtering a random slice.
                candidate_rows = session.execute(statement).scalars().all()
                family_items = [self._theme_dict(row) for row in candidate_rows]
                if family:
                    family_items = [item for item in family_items if item["family"] == family]
                if cluster:
                    family_items = [item for item in family_items if item["cluster"] == cluster]
                total = len(family_items)
                items = family_items[(page - 1) * page_size:page * page_size]
                rows = []
            else:
                rows = session.execute(statement.offset((page - 1) * page_size).limit(page_size)).scalars().all()
                total = int(session.execute(count_stmt).scalar_one())
                items = [self._theme_dict(row) for row in rows]
            latest_run = session.execute(select(ConceptSyncRunRecord).order_by(desc(ConceptSyncRunRecord.id)).limit(1)).scalar_one_or_none()
            source_counts = dict(session.execute(
                select(ConceptThemeRecord.source, func.count(ConceptThemeRecord.id)).group_by(ConceptThemeRecord.source)
            ).all())
            type_counts = dict(session.execute(
                select(ConceptThemeRecord.theme_type, func.count(ConceptThemeRecord.id)).group_by(ConceptThemeRecord.theme_type)
            ).all())
            membership_count = int(session.execute(select(func.count(ConceptMembershipRecord.id)).where(ConceptMembershipRecord.active.is_(True))).scalar_one())
            membered_theme_count = int(session.execute(select(func.count(func.distinct(ConceptMembershipRecord.theme_id))).where(
                ConceptMembershipRecord.active.is_(True))).scalar_one())
            attempted_theme_count = int(session.execute(select(func.count(ConceptMembershipSyncState.id))).scalar_one())
            failed_theme_count = int(session.execute(select(func.count(ConceptMembershipSyncState.id)).where(
                ConceptMembershipSyncState.status == "failed",
            )).scalar_one())
            exposure_count = int(session.execute(select(func.count(ConceptExposureRecord.id))).scalar_one())
            latest_market_date = session.execute(select(func.max(ConceptThemeRecord.market_date))).scalar_one_or_none()
            if query.strip():
                stock_term = f"%{query.strip()}%"
                matched_stocks = session.execute(select(
                    ConceptMembershipRecord.ts_code,
                    func.max(ConceptMembershipRecord.stock_name),
                    func.count(func.distinct(ConceptThemeRecord.canonical_name)),
                    func.count(func.distinct(ConceptMembershipRecord.source)),
                ).join(
                    ConceptThemeRecord, ConceptMembershipRecord.theme_id == ConceptThemeRecord.id,
                ).where(
                    ConceptMembershipRecord.active.is_(True),
                    or_(
                        ConceptMembershipRecord.ts_code.like(stock_term),
                        ConceptMembershipRecord.stock_name.like(stock_term),
                    ),
                ).group_by(ConceptMembershipRecord.ts_code).order_by(
                    desc(func.count(func.distinct(ConceptMembershipRecord.source))),
                    desc(func.count(func.distinct(ConceptThemeRecord.canonical_name))),
                ).limit(8)).all()
                stock_matches = [{
                    "ts_code": code, "name": name or code,
                    "theme_count": int(theme_count or 0), "source_count": int(source_count or 0),
                } for code, name, theme_count, source_count in matched_stocks]
        family_counts: Dict[str, int] = defaultdict(int)
        cluster_families: Dict[str, Dict[str, int]] = defaultdict(lambda: defaultdict(int))
        with self._read_scope() as session:
            family_rows = session.execute(select(ConceptThemeRecord.name, ConceptThemeRecord.theme_type)).all()
        for name, kind in family_rows:
            canonical = canonicalize_theme(name)
            family_name = theme_family(canonical, kind)
            family_counts[family_name] += 1
            cluster_families[family_name][theme_cluster(canonical, family_name)] += 1
        return {
            "items": items, "total": total, "page": page, "page_size": page_size,
            "stock_matches": stock_matches,
            "summary": {
                "themes": sum(source_counts.values()), "memberships": membership_count, "exposures": exposure_count,
                "membered_themes": membered_theme_count,
                "attempted_themes": attempted_theme_count,
                "failed_themes": failed_theme_count,
                "scan_coverage_pct": round(attempted_theme_count / max(1, sum(source_counts.values())) * 100, 1),
                "membership_coverage_pct": round(membered_theme_count / max(1, sum(source_counts.values())) * 100, 1),
                "sources": source_counts, "types": type_counts, "families": dict(sorted(family_counts.items(), key=lambda x: -x[1])),
                "cluster_families": {family_name: dict(sorted(values.items(), key=lambda item: -item[1]))
                                     for family_name, values in cluster_families.items()},
                "market_date": latest_market_date.isoformat() if latest_market_date else "",
            },
            "sync": self._run_dict(latest_run) if latest_run else None,
            "methodology": self.methodology(),
        }

    def theme_detail(self, theme_id: int, *, refresh_if_empty: bool = True, horizon_days: int = 60) -> Dict[str, Any]:
        horizon = min((20, 60, 120), key=lambda item: abs(item - int(horizon_days)))
        current_market_date = self.latest_market_date()
        with self._read_scope() as session:
            theme = session.get(ConceptThemeRecord, int(theme_id))
            if theme is None:
                raise ConceptThemeError("题材不存在")
            canonical = theme.canonical_name
            raw_themes = session.execute(
                select(ConceptThemeRecord).where(ConceptThemeRecord.canonical_name == canonical)
                .order_by(desc(ConceptThemeRecord.heat_score))
            ).scalars().all()
            theme_ids = [row.id for row in raw_themes]
            member_counts = dict(session.execute(select(
                ConceptMembershipRecord.theme_id, func.count(ConceptMembershipRecord.id)
            ).where(
                ConceptMembershipRecord.theme_id.in_(theme_ids), ConceptMembershipRecord.active.is_(True)
            ).group_by(ConceptMembershipRecord.theme_id)).all()) if theme_ids else {}
            exposure_total = int(session.execute(select(func.count(ConceptExposureRecord.id)).where(
                ConceptExposureRecord.canonical_name == canonical,
                ConceptExposureRecord.horizon_days == horizon,
                ConceptExposureRecord.as_of_date == current_market_date,
            )).scalar_one())
        if refresh_if_empty:
            # Fill every source node belonging to the canonical theme. One
            # provider alone cannot form market consensus.
            refreshed_source = False
            for raw_theme in raw_themes[:8]:
                if int(member_counts.get(raw_theme.id, 0)) > 0:
                    continue
                try:
                    self.refresh_theme(raw_theme.id, calculate=False)
                    refreshed_source = True
                except Exception as exc:  # One unavailable source must not hide the usable sources.
                    logger.warning("concept source node refresh failed: %s %s", raw_theme.id, exc)
            if exposure_total == 0 or refreshed_source:
                self.calculate_canonical_exposures(canonical, horizon_days=horizon)
        with self._read_scope() as session:
            raw_themes = session.execute(
                select(ConceptThemeRecord).where(ConceptThemeRecord.canonical_name == canonical)
                .order_by(desc(ConceptThemeRecord.heat_score))
            ).scalars().all()
            theme_ids = [row.id for row in raw_themes]
            memberships = session.execute(
                select(ConceptMembershipRecord, ConceptThemeRecord)
                .join(ConceptThemeRecord, ConceptMembershipRecord.theme_id == ConceptThemeRecord.id)
                .where(ConceptMembershipRecord.theme_id.in_(theme_ids), ConceptMembershipRecord.active.is_(True))
            ).all()
            latest_exposure_date = session.execute(select(func.max(ConceptExposureRecord.as_of_date)).where(
                ConceptExposureRecord.canonical_name == canonical,
                ConceptExposureRecord.horizon_days == horizon,
            )).scalar_one_or_none()
            exposures = session.execute(
                select(ConceptExposureRecord).where(
                    ConceptExposureRecord.canonical_name == canonical,
                    ConceptExposureRecord.horizon_days == horizon,
                    ConceptExposureRecord.as_of_date == latest_exposure_date,
                )
                .order_by(desc(ConceptExposureRecord.weight_score)).limit(800)
            ).scalars().all()
        exposure_by_stock = {row.ts_code: row for row in exposures}
        stocks: Dict[str, Dict[str, Any]] = {}
        for membership, raw_theme in memberships:
            item = stocks.setdefault(membership.ts_code, {
                "ts_code": membership.ts_code, "name": membership.stock_name, "sources": [], "reasons": [],
                "source_count": 0, "weight_score": 0.0, "beta": None, "alpha_annualized": None,
                "r_squared": None, "confidence": "insufficient",
            })
            if raw_theme.source not in item["sources"]:
                item["sources"].append(raw_theme.source)
            if membership.reason and membership.reason not in item["reasons"]:
                item["reasons"].append(membership.reason)
        for code, item in stocks.items():
            item["source_count"] = len(item["sources"])
            exposure = exposure_by_stock.get(code)
            if exposure:
                item.update({
                    "weight_score": round(exposure.weight_score, 1), "beta": exposure.beta,
                    "alpha_annualized": exposure.alpha_annualized, "residual_return": exposure.residual_return,
                    "r_squared": exposure.r_squared, "confidence": exposure.confidence,
                    "components": _json(exposure.components_json, {}),
                    "beta_interpretation": self._beta_interpretation(exposure.beta, exposure.confidence),
                })
            item["reasons"] = item["reasons"][:3]
        ordered = sorted(stocks.values(), key=lambda value: (-value["weight_score"], -value["source_count"], value["name"]))
        consensus_distribution = {
            "strong": sum(1 for item in ordered if item["source_count"] >= 3),
            "confirmed": sum(1 for item in ordered if item["source_count"] == 2),
            "single_source": sum(1 for item in ordered if item["source_count"] == 1),
        }
        return {
            "theme": {**self._theme_dict(raw_themes[0]), "canonical_name": canonical} if raw_themes else {"id": theme_id},
            "source_nodes": [self._theme_dict(row) for row in raw_themes],
            "stocks": ordered,
            "total_stocks": len(ordered),
            "consensus_stocks": sum(1 for item in ordered if item["source_count"] >= 2),
            "consensus_distribution": consensus_distribution,
            "attribution_ready": sum(1 for item in ordered if item["beta"] is not None),
            "horizon_days": horizon,
            "methodology": self.methodology(),
        }

    def stock_lens(self, ts_code: str, *, refresh_if_empty: bool = True, horizon_days: int = 60) -> Dict[str, Any]:
        code = _ts_code(ts_code)
        horizon = min((20, 60, 120), key=lambda item: abs(item - int(horizon_days)))
        with self._read_scope() as session:
            count = int(session.execute(select(func.count(ConceptMembershipRecord.id)).where(
                ConceptMembershipRecord.ts_code == code, ConceptMembershipRecord.active.is_(True)
            )).scalar_one())
        if count == 0 and refresh_if_empty:
            self.refresh_stock(code, calculate=True)
        with self._read_scope() as session:
            rows = session.execute(
                select(ConceptMembershipRecord, ConceptThemeRecord)
                .join(ConceptThemeRecord, ConceptMembershipRecord.theme_id == ConceptThemeRecord.id)
                .where(ConceptMembershipRecord.ts_code == code, ConceptMembershipRecord.active.is_(True))
            ).all()
            latest_date = session.execute(select(func.max(ConceptExposureRecord.as_of_date)).where(
                ConceptExposureRecord.ts_code == code,
                ConceptExposureRecord.horizon_days == horizon,
            )).scalar_one_or_none()
            exposures = session.execute(select(ConceptExposureRecord).where(
                ConceptExposureRecord.ts_code == code,
                ConceptExposureRecord.as_of_date == latest_date if latest_date else ConceptExposureRecord.id < 0,
                ConceptExposureRecord.horizon_days == horizon,
            ).order_by(desc(ConceptExposureRecord.weight_score))).scalars().all()
        grouped: Dict[str, Dict[str, Any]] = {}
        stock_name = ""
        for membership, theme in rows:
            stock_name = stock_name or membership.stock_name
            family_name = theme_family(theme.canonical_name, theme.theme_type)
            item = grouped.setdefault(theme.canonical_name, {
                "canonical_name": theme.canonical_name, "family": family_name,
                "cluster": theme_cluster(theme.canonical_name, family_name),
                "theme_type": theme.theme_type, "sources": [], "reasons": [], "theme_ids": [],
            })
            if theme.source not in item["sources"]:
                item["sources"].append(theme.source)
            item["theme_ids"].append(theme.id)
            if membership.reason and membership.reason not in item["reasons"]:
                item["reasons"].append(membership.reason)
        exposure_map = {row.canonical_name: row for row in exposures}
        themes = []
        for canonical, item in grouped.items():
            exposure = exposure_map.get(canonical)
            values = {**item, "source_count": len(item["sources"]), "reasons": item["reasons"][:4]}
            if exposure:
                values.update(self._exposure_dict(exposure))
            else:
                values.update({"weight_score": 0.0, "beta": None, "alpha_annualized": None,
                               "r_squared": None, "confidence": "insufficient"})
            themes.append(values)
        themes.sort(key=lambda value: (-value["weight_score"], -value["source_count"]))
        consensus_themes = [item for item in themes if item["source_count"] >= 2]
        unique_themes = sorted([
            item for item in themes
            if item["source_count"] == 1
            and (item.get("specificity_score") or 0) >= 50
            and (item.get("weight_score") or 0) > 0
        ], key=lambda value: (
            -(value.get("specificity_score") or 0),
            -(value.get("residual_return") or -999),
        ))[:6]
        drivers = self._unique_driver_evidence(code)
        return {
            "ts_code": code, "name": stock_name, "as_of_date": latest_date.isoformat() if latest_date else None,
            "themes": themes, "primary_themes": consensus_themes[:5], "unique_themes": unique_themes,
            "unique_drivers": drivers,
            "horizon_days": horizon,
            "summary": {
                "theme_count": len(themes), "source_count": len({source for item in themes for source in item["sources"]}),
                "consensus_count": sum(1 for item in themes if item["source_count"] >= 2),
                "alpha_positive_count": sum(1 for item in themes if (item.get("alpha_annualized") or 0) > 0),
            },
            "methodology": self.methodology(),
        }

    def calculate_canonical_exposures(
        self, canonical_name: str, *, horizon_days: int = 60, only_stock: Optional[str] = None,
    ) -> int:
        horizon = max(20, min(int(horizon_days), 250))
        as_of = self.latest_market_date()
        start = as_of - timedelta(days=max(120, horizon * 3))
        with self._read_scope() as session:
            memberships = session.execute(
                select(ConceptMembershipRecord, ConceptThemeRecord)
                .join(ConceptThemeRecord, ConceptMembershipRecord.theme_id == ConceptThemeRecord.id)
                .where(ConceptThemeRecord.canonical_name == canonical_name,
                       ConceptMembershipRecord.active.is_(True))
            ).all()
        if not memberships:
            return 0
        by_stock: Dict[str, Dict[str, Any]] = {}
        for membership, theme in memberships:
            item = by_stock.setdefault(membership.ts_code, {"name": membership.stock_name, "sources": set(),
                                                              "reasons": [], "theme_counts": []})
            item["sources"].add(theme.source)
            if membership.reason:
                item["reasons"].append(membership.reason)
            item["theme_counts"].append(max(1, theme.constituent_count or 0))
        codes = sorted(by_stock)
        if not codes:
            return 0
        storage_codes = [code[:6] for code in codes]
        with self._read_scope() as session:
            prices = session.execute(select(StockDaily.code, StockDaily.date, StockDaily.close).where(
                StockDaily.code.in_(storage_codes), StockDaily.date >= start, StockDaily.date <= as_of,
                StockDaily.close.is_not(None), StockDaily.close > 0,
            )).all()
            market_rows = session.execute(select(MarketIndexBar.timestamp, MarketIndexBar.close).where(
                MarketIndexBar.symbol == "000300.SH", MarketIndexBar.frequency == "1D",
                MarketIndexBar.timestamp >= datetime.combine(start, datetime.min.time()),
                MarketIndexBar.timestamp <= datetime.combine(as_of, datetime.max.time()),
            ).order_by(MarketIndexBar.timestamp)).all()
            theme_breadths = dict(session.execute(select(
                ConceptMembershipRecord.ts_code,
                func.count(func.distinct(ConceptThemeRecord.canonical_name)),
            ).join(
                ConceptThemeRecord, ConceptMembershipRecord.theme_id == ConceptThemeRecord.id,
            ).where(
                ConceptMembershipRecord.ts_code.in_(codes), ConceptMembershipRecord.active.is_(True),
                ConceptThemeRecord.theme_type.in_(("theme", "concept")),
            ).group_by(ConceptMembershipRecord.ts_code)).all())
        price_map: Dict[str, Dict[date, float]] = defaultdict(dict)
        code_lookup = {code[:6]: code for code in codes}
        for code, day, close in prices:
            mapped = code_lookup.get(str(code)[:6])
            if mapped and close:
                price_map[mapped][day] = float(close)
        returns: Dict[str, Dict[date, float]] = {}
        for code, points in price_map.items():
            ordered = sorted(points.items())[-(horizon + 8):]
            returns[code] = {ordered[index][0]: ordered[index][1] / ordered[index - 1][1] - 1.0
                             for index in range(1, len(ordered)) if ordered[index - 1][1] > 0}
        market_points = [(row[0].date(), float(row[1])) for row in market_rows if row[1]]
        market_returns = {market_points[index][0]: market_points[index][1] / market_points[index - 1][1] - 1.0
                          for index in range(1, len(market_points)) if market_points[index - 1][1] > 0}
        days = sorted({day for values in returns.values() for day in values})[-horizon:]
        theme_daily: Dict[date, Tuple[float, int]] = {}
        for day in days:
            values = [series[day] for series in returns.values() if day in series and abs(series[day]) < 0.25]
            if len(values) >= 3:
                theme_daily[day] = (float(np.mean(values)), len(values))
        local_evidence = self._local_theme_evidence(canonical_name, by_stock)
        records: List[Dict[str, Any]] = []
        for code, meta in by_stock.items():
            if only_stock and code != _ts_code(only_stock):
                continue
            series = returns.get(code, {})
            y_values: List[float] = []
            x_values: List[List[float]] = []
            for day in days:
                if day not in series or day not in theme_daily or day not in market_returns:
                    continue
                stock_ret = series[day]
                mean_ret, count = theme_daily[day]
                leave_one_out = ((mean_ret * count) - stock_ret) / (count - 1) if count > 1 else mean_ret
                if max(abs(stock_ret), abs(leave_one_out), abs(market_returns[day])) >= 0.25:
                    continue
                y_values.append(stock_ret)
                x_values.append([1.0, leave_one_out, market_returns[day]])
            beta = market_beta = alpha = residual_return = r_squared = None
            beta_standard_error = beta_t_stat = beta_ci_low = beta_ci_high = None
            observations = len(y_values)
            if observations >= 20:
                x = np.asarray(x_values, dtype=float)
                y = np.asarray(y_values, dtype=float)
                coefficients, *_ = np.linalg.lstsq(x, y, rcond=None)
                fitted = x @ coefficients
                residuals = y - fitted
                total_ss = float(np.sum((y - np.mean(y)) ** 2))
                beta = round(float(coefficients[1]), 4)
                market_beta = round(float(coefficients[2]), 4)
                alpha = round(float(coefficients[0]) * 252 * 100, 2)
                # With an intercept, ordinary residuals sum to zero by design.
                # The useful window statistic is the intercept contribution
                # compounded across the actually observed research window.
                residual_return = round((math.exp(float(coefficients[0]) * observations) - 1.0) * 100, 2)
                r_squared = round(max(0.0, 1.0 - float(np.sum(residuals ** 2)) / total_ss), 4) if total_ss else 0.0
                degrees_of_freedom = observations - x.shape[1]
                if degrees_of_freedom > 0:
                    residual_variance = float(np.sum(residuals ** 2)) / degrees_of_freedom
                    covariance = residual_variance * np.linalg.pinv(x.T @ x)
                    raw_standard_error = math.sqrt(max(0.0, float(covariance[1, 1])))
                    if raw_standard_error > 0:
                        beta_standard_error = round(raw_standard_error, 4)
                        beta_t_stat = round(float(coefficients[1]) / raw_standard_error, 3)
                        beta_ci_low = round(float(coefficients[1]) - 1.96 * raw_standard_error, 4)
                        beta_ci_high = round(float(coefficients[1]) + 1.96 * raw_standard_error, 4)
            source_count = len(meta["sources"])
            reason_count = len(meta["reasons"])
            longest_reason = max((len(reason) for reason in meta["reasons"]), default=0)
            provider_reason_score = min(100.0, 25.0 + min(50.0, longest_reason * 0.25) + min(25.0, reason_count * 8.0))
            corpus = local_evidence.get(code, {"count": 0, "score": 0.0, "items": []})
            local_evidence_score = float(corpus.get("score") or 0.0)
            reason_quality = min(100.0, provider_reason_score + 0.30 * local_evidence_score)
            source_strength = sum(SOURCE_RELIABILITY.get(source, 0.7) for source in meta["sources"])
            consensus = min(100.0, 100.0 * (
                0.55 * min(1.0, source_strength / 2.5) +
                0.45 * min(1.0, source_count / 3.0)
            ))
            typical_size = statistics.median(meta["theme_counts"] or [500])
            theme_scarcity = max(24.0, 100.0 - math.log10(max(2.0, typical_size)) * 27.0)
            stock_theme_breadth = max(1, int(theme_breadths.get(code) or 1))
            stock_focus = max(20.0, 100.0 - math.log2(stock_theme_breadth) * 10.0)
            specificity = 0.65 * theme_scarcity + 0.35 * stock_focus
            market_score = self._canonical_market_score(canonical_name)
            weight = round(0.36 * consensus + 0.29 * reason_quality + 0.20 * market_score + 0.15 * specificity, 1)
            confidence = "high" if (
                observations >= 55 and source_count >= 2 and (r_squared or 0) >= 0.15 and abs(beta_t_stat or 0) >= 2.0
            ) else "medium" if (
                observations >= 35 and source_count >= 1 and (r_squared or 0) >= 0.05 and abs(beta_t_stat or 0) >= 1.0
            ) else "low" if observations >= 20 else "insufficient"
            components = {
                "consensus": round(consensus, 1), "relevance": round(reason_quality, 1),
                "market": round(market_score, 1), "specificity": round(specificity, 1),
                "formula": "36%来源共识 + 29%业务证据 + 20%市场热度 + 15%题材专属性",
                "regression": "个股日收益 = Alpha + Beta题材×剔除自身后的题材等权收益 + Beta市场×沪深300收益",
                "beta_standard_error": beta_standard_error,
                "beta_t_stat": beta_t_stat,
                "beta_ci_low": beta_ci_low,
                "beta_ci_high": beta_ci_high,
                "confidence_rule": "置信度同时约束样本数、来源数、R²与Beta t统计量",
                "theme_scarcity": round(theme_scarcity, 1),
                "stock_focus": round(stock_focus, 1),
                "stock_theme_breadth": stock_theme_breadth,
                "provider_reason_score": round(provider_reason_score, 1),
                "local_corpus_score": round(local_evidence_score, 1),
                "local_corpus_evidence_count": int(corpus.get("count") or 0),
                "local_corpus_window_days": 365,
            }
            evidence_items = [*meta["reasons"][:6], *list(corpus.get("items") or [])[:4]]
            records.append({
                "ts_code": code, "stock_name": meta["name"], "canonical_name": canonical_name,
                "as_of_date": as_of, "horizon_days": horizon, "weight_score": weight,
                "consensus_score": consensus, "relevance_score": reason_quality,
                "market_score": market_score, "specificity_score": specificity,
                "beta": beta, "market_beta": market_beta, "alpha_annualized": alpha,
                "residual_return": residual_return, "r_squared": r_squared, "observations": observations,
                "confidence": confidence, "source_count": source_count,
                "evidence_count": len(meta["reasons"]) + int(corpus.get("count") or 0),
                "components_json": json.dumps(components, ensure_ascii=False),
                "evidence_json": json.dumps(evidence_items, ensure_ascii=False),
                "unique_drivers_json": "[]", "calculated_at": utc_naive_now(),
            })
        if not records:
            return 0
        with self.db.session_scope() as session:
            for values in records:
                existing = session.execute(select(ConceptExposureRecord).where(
                    ConceptExposureRecord.ts_code == values["ts_code"],
                    ConceptExposureRecord.canonical_name == canonical_name,
                    ConceptExposureRecord.as_of_date == as_of,
                    ConceptExposureRecord.horizon_days == horizon,
                )).scalar_one_or_none()
                if existing is None:
                    session.add(ConceptExposureRecord(**values))
                else:
                    for key, value in values.items():
                        setattr(existing, key, value)
        return len(records)

    def _local_theme_evidence(
        self, canonical_name: str, by_stock: Dict[str, Dict[str, Any]],
    ) -> Dict[str, Dict[str, Any]]:
        """Map recent AI-structured institution notes to explicit stock/theme pairs.

        A note contributes only when its AI theme/tag fields mention the current
        canonical theme and its stock_mentions field explicitly identifies a
        member stock.  This deliberately avoids treating a generic industry
        paragraph as evidence for every constituent.
        """
        if not by_stock:
            return {}
        tokens = [item.strip() for item in re.split(r"[/、|]", canonical_name) if len(item.strip()) >= 2]
        tokens = list(dict.fromkeys([canonical_name, *tokens]))[:6]
        clauses = []
        for token in tokens:
            pattern = f"%{token}%"
            clauses.extend((
                EssayAnalysisRecord.themes_json.like(pattern),
                EssayAnalysisRecord.tags_json.like(pattern),
                EssayAnalysisRecord.industries_json.like(pattern),
            ))
        if not clauses:
            return {}
        cutoff = utc_naive_now() - timedelta(days=365)
        with self._read_scope() as session:
            rows = session.execute(select(
                EssayAnalysisRecord.topic_id,
                EssayAnalysisRecord.stock_mentions_json,
                EssayAnalysisRecord.confidence_score,
                EssayAnalysisRecord.importance_score,
                EssayAnalysisRecord.summary,
                ResearchNote.title,
            ).join(
                ResearchNote, ResearchNote.topic_id == EssayAnalysisRecord.topic_id,
            ).where(
                EssayAnalysisRecord.status == "completed",
                EssayAnalysisRecord.updated_at >= cutoff,
                or_(*clauses),
            ).order_by(
                desc(EssayAnalysisRecord.importance_score), desc(EssayAnalysisRecord.updated_at),
            ).limit(800)).all()
        code_lookup = {code[:6]: code for code in by_stock}
        name_lookup = {
            re.sub(r"\s+", "", str(meta.get("name") or "")): code
            for code, meta in by_stock.items() if meta.get("name")
        }
        mapped: Dict[str, Dict[str, Any]] = defaultdict(lambda: {
            "topic_ids": set(), "confidence": [], "importance": [], "items": [],
        })
        for topic_id, mentions_json, confidence, importance, summary, title in rows:
            mentions = _json(mentions_json, [])
            if not isinstance(mentions, list):
                continue
            for mention in mentions:
                if not isinstance(mention, dict):
                    continue
                raw_code = _ts_code(mention.get("ts_code") or mention.get("code") or "")
                code = raw_code if raw_code in by_stock else code_lookup.get(raw_code[:6])
                if not code:
                    code = name_lookup.get(re.sub(r"\s+", "", str(mention.get("name") or "")))
                if not code or topic_id in mapped[code]["topic_ids"]:
                    continue
                mapped[code]["topic_ids"].add(topic_id)
                mention_confidence = _safe_float(mention.get("confidence"))
                mapped[code]["confidence"].append(mention_confidence if mention_confidence is not None else (_safe_float(confidence) or 0.5))
                mapped[code]["importance"].append(_safe_float(importance) or 50.0)
                rationale = str(mention.get("rationale") or "").strip()
                excerpt = rationale or str(summary or "").strip()[:180]
                mapped[code]["items"].append({
                    "kind": "institution_corpus", "topic_id": topic_id,
                    "title": str(title or "机构语料题材证据")[:120],
                    "summary": excerpt[:240], "source": "机构段子 AI 结构化标签",
                })
        result: Dict[str, Dict[str, Any]] = {}
        for code, value in mapped.items():
            count = len(value["topic_ids"])
            average_confidence = statistics.mean(value["confidence"] or [0.5])
            average_importance = statistics.mean(value["importance"] or [50.0])
            score = min(100.0, count * 11.0 + average_confidence * 35.0 + average_importance * 0.22)
            result[code] = {
                "count": count, "score": round(score, 1),
                "items": value["items"][:8],
            }
        return result

    def sync_status(self) -> Dict[str, Any]:
        with self._read_scope() as session:
            latest = session.execute(select(ConceptSyncRunRecord).order_by(desc(ConceptSyncRunRecord.id)).limit(1)).scalar_one_or_none()
            counts = {
                "themes": int(session.execute(select(func.count(ConceptThemeRecord.id))).scalar_one()),
                "memberships": int(session.execute(select(func.count(ConceptMembershipRecord.id))).scalar_one()),
                "exposures": int(session.execute(select(func.count(ConceptExposureRecord.id))).scalar_one()),
                "stocks": int(session.execute(select(func.count(func.distinct(ConceptMembershipRecord.ts_code)))).scalar_one()),
                "membership_themes_attempted": int(session.execute(select(func.count(ConceptMembershipSyncState.id))).scalar_one()),
                "membership_themes_failed": int(session.execute(select(func.count(ConceptMembershipSyncState.id)).where(
                    ConceptMembershipSyncState.status == "failed",
                )).scalar_one()),
            }
        return {"available": self.gateway.available, "latest_run": self._run_dict(latest) if latest else None, **counts}

    def rotation(self, *, days: int = 20, limit: int = 24) -> Dict[str, Any]:
        """Aggregate daily source snapshots into a transparent theme rotation board."""
        window = max(5, min(int(days), 60))
        row_limit = max(8, min(int(limit), 60))
        cutoff = self.latest_market_date() - timedelta(days=window * 2 + 10)
        with self._read_scope() as session:
            rows = session.execute(select(ConceptThemeSnapshotRecord).where(
                ConceptThemeSnapshotRecord.market_date >= cutoff,
                ConceptThemeSnapshotRecord.theme_type.in_(("theme", "concept")),
            ).order_by(ConceptThemeSnapshotRecord.market_date)).scalars().all()
        grouped: Dict[str, Dict[date, List[ConceptThemeSnapshotRecord]]] = defaultdict(lambda: defaultdict(list))
        for row in rows:
            grouped[row.canonical_name][row.market_date].append(row)
        items: List[Dict[str, Any]] = []
        all_dates: set[date] = set()
        for canonical_name, by_date in grouped.items():
            points: List[Dict[str, Any]] = []
            for market_date, values in sorted(by_date.items())[-window:]:
                changes = [float(value.pct_change) for value in values if value.pct_change is not None]
                heats = [float(value.heat_score) for value in values if value.heat_score is not None]
                if not changes and not heats:
                    continue
                all_dates.add(market_date)
                points.append({
                    "date": market_date.isoformat(),
                    "pct_change": round(float(statistics.median(changes)), 3) if changes else None,
                    "heat_score": round(max(heats), 1) if heats else None,
                    "source_count": len({value.source for value in values}),
                })
            if not points:
                continue
            latest = points[-1]
            recent_changes = [point["pct_change"] for point in points[-5:] if point["pct_change"] is not None]
            latest_change = float(latest["pct_change"] or 0.0)
            momentum_5d = round(sum(recent_changes), 3) if recent_changes else None
            heat = float(latest["heat_score"] or 45.0)
            source_count = int(latest["source_count"] or 0)
            rotation_score = max(0.0, min(100.0,
                42.0 + max(-24.0, min(24.0, latest_change * 5.0))
                + min(18.0, source_count * 6.0) + (heat - 50.0) * 0.16
            ))
            family_name = theme_family(canonical_name, "theme")
            items.append({
                "canonical_name": canonical_name, "family": family_name,
                "cluster": theme_cluster(canonical_name, family_name),
                "market_date": latest["date"], "pct_change": latest["pct_change"],
                "momentum_5d": momentum_5d, "heat_score": latest["heat_score"],
                "source_count": source_count, "rotation_score": round(rotation_score, 1),
                "history_days": len(points), "points": points,
            })
        items.sort(key=lambda item: (-item["rotation_score"], -item["source_count"], item["canonical_name"]))
        return {
            "items": items[:row_limit], "total": len(items), "window_days": window,
            "available_dates": len(all_dates),
            "latest_date": max(all_dates).isoformat() if all_dates else None,
            "method": "同一规范题材按来源日涨跌中位数聚合；轮动分由当日涨跌、来源数和热度构成，不是收益预测。",
        }

    def backfill_current_snapshots(self) -> Dict[str, int]:
        """Seed daily history from the latest catalog after schema upgrades."""
        now = utc_naive_now()
        saved = 0
        with self.db.session_scope() as session:
            themes = session.execute(select(ConceptThemeRecord).where(
                ConceptThemeRecord.market_date.is_not(None),
            )).scalars().all()
            existing = {(row.source, row.source_code, row.market_date) for row in session.execute(
                select(ConceptThemeSnapshotRecord)
            ).scalars().all()}
            for row in themes:
                key = (row.source, row.source_code, row.market_date)
                if key in existing:
                    continue
                session.add(ConceptThemeSnapshotRecord(
                    source=row.source, source_code=row.source_code, canonical_name=row.canonical_name,
                    theme_type=row.theme_type, market_date=row.market_date,
                    constituent_count=row.constituent_count or 0, heat_score=row.heat_score,
                    pct_change=row.pct_change, fund_flow=row.fund_flow, captured_at=now,
                ))
                saved += 1
        return {"saved": saved}

    def normalize_catalog_names(self) -> Dict[str, int]:
        """Reapply the current ontology to stored source nodes without touching source facts."""
        changed = 0
        scanned = 0
        with self.db.session_scope() as session:
            rows = session.execute(select(ConceptThemeRecord)).scalars().all()
            for row in rows:
                scanned += 1
                canonical = canonicalize_theme(row.name)
                if row.canonical_name != canonical:
                    row.canonical_name = canonical
                    row.updated_at = utc_naive_now()
                    changed += 1
        return {"scanned": scanned, "changed": changed}

    @staticmethod
    def methodology() -> Dict[str, Any]:
        return {
            "version": "concept-consensus-v1.18",
            "principles": [
                "不同数据源的原始题材分别保留，规范名只用于聚合，不覆盖原始归属。",
                "题材权重是可解释的市场共识评分，不等于指数公司法定权重，也不是收益预测。",
                "业务证据只接受供应商入选理由，或本地机构语料中题材标签与股票明确提及的交叉命中。",
                "Beta 使用剔除个股自身后的题材组合，并同时控制沪深300，避免机械自相关。",
                "Beta 置信度同时检查样本数、来源数、R²及回归系数t统计量，并展示95%区间。",
                "Alpha 分为统计残差与可核验证据两层；证据只做归因线索，不声称因果。",
                "题材轮动保留每日来源快照后再聚合，不用当前值伪造历史，也不把轮动分解释为收益预测。",
            ],
            "weight_formula": {
                "source_consensus": 0.36, "business_evidence": 0.29,
                "market_attention": 0.20, "theme_specificity": 0.15,
            },
            "beta_formula": "r_stock = alpha + beta_theme × r_theme_leave_one_out + beta_market × r_CSI300 + epsilon",
            "windows": [20, 60, 120],
            "minimum_observations": 20,
            "sources": [{"key": key, "name": value, "reliability": SOURCE_RELIABILITY[key]}
                        for key, value in SOURCE_LABELS.items()],
            "license_note": "同花顺等第三方接口数据按其授权仅用于个人学习研究；不在此功能中转售原始数据。",
        }

    def _theme_row(self, *, source: str, code: Any, name: Any, theme_type: str, level: int,
                   market_date: date, payload: Dict[str, Any], count: Any = 0, parent: Any = None,
                   heat: Any = None, pct_change: Any = None, fund_flow: Any = None) -> Dict[str, Any]:
        label = str(name or code or "未知题材").strip()
        return {
            "source": source, "source_code": str(code or label).strip(), "name": label,
            "canonical_name": canonicalize_theme(label), "theme_type": theme_type, "level": int(level),
            "parent_code": str(parent).strip() if parent not in (None, "", "0") else None,
            "constituent_count": _safe_int(count) or 0, "market_date": market_date,
            "heat_score": _safe_float(heat), "pct_change": _safe_float(pct_change),
            "fund_flow": _safe_float(fund_flow), "source_payload_json": json.dumps(payload, ensure_ascii=False, default=str),
        }

    def _upsert_themes(self, rows: Iterable[Dict[str, Any]]) -> int:
        now = utc_naive_now()
        saved = 0
        prepared = [values for values in rows if values.get("source_code")]
        with self.db.session_scope() as session:
            theme_map = {(row.source, row.source_code): row for row in session.execute(
                select(ConceptThemeRecord)
            ).scalars().all()}
            dates = {values.get("market_date") for values in prepared if values.get("market_date")}
            snapshot_map = {(row.source, row.source_code, row.market_date): row for row in session.execute(
                select(ConceptThemeSnapshotRecord).where(ConceptThemeSnapshotRecord.market_date.in_(dates))
            ).scalars().all()} if dates else {}
            for values in prepared:
                theme_key = (values["source"], values["source_code"])
                existing = theme_map.get(theme_key)
                if existing is None:
                    existing = ConceptThemeRecord(**values, first_seen_at=now, last_seen_at=now, updated_at=now)
                    session.add(existing)
                    theme_map[theme_key] = existing
                else:
                    for key, value in values.items():
                        if value is not None or key not in {"heat_score", "pct_change", "fund_flow"}:
                            setattr(existing, key, value)
                    existing.last_seen_at = now
                    existing.updated_at = now
                market_date = values.get("market_date")
                if market_date:
                    snapshot_key = (values["source"], values["source_code"], market_date)
                    snapshot = snapshot_map.get(snapshot_key)
                    snapshot_values = {
                        "canonical_name": values["canonical_name"], "theme_type": values["theme_type"],
                        "constituent_count": values.get("constituent_count") or 0,
                        "heat_score": values.get("heat_score"), "pct_change": values.get("pct_change"),
                        "fund_flow": values.get("fund_flow"), "captured_at": now,
                    }
                    if snapshot is None:
                        snapshot = ConceptThemeSnapshotRecord(
                            source=values["source"], source_code=values["source_code"],
                            market_date=market_date, **snapshot_values,
                        )
                        session.add(snapshot)
                        snapshot_map[snapshot_key] = snapshot
                    else:
                        for key, value in snapshot_values.items():
                            if value is not None or key not in {"heat_score", "pct_change", "fund_flow"}:
                                setattr(snapshot, key, value)
                saved += 1
        return saved

    def _fetch_theme_members(self, theme: Dict[str, Any]) -> List[Dict[str, Any]]:
        code = theme["source_code"]
        day_text = (self.latest_market_date()).strftime("%Y%m%d")
        source = theme["source"]
        if source == "ths":
            return self.gateway.query("ths_member", params={"ts_code": code})["rows"]
        if source == "dc_board":
            return self.gateway.query("dc_member", params={"ts_code": code, "trade_date": day_text})["rows"]
        if source == "dc_theme":
            return self.gateway.query("dc_concept_cons", params={"theme_code": code, "trade_date": day_text})["rows"]
        if source == "kpl":
            return self.gateway.query("kpl_concept_cons", params={"ts_code": code, "trade_date": day_text})["rows"]
        if source == "tdx":
            return self.gateway.query("tdx_member", params={"ts_code": code, "trade_date": day_text})["rows"]
        if source == "sw":
            param = {f"l{theme['level']}_code": code, "is_new": "Y"}
            return self.gateway.query("index_member_all", params=param)["rows"]
        return []

    def _upsert_memberships(
        self, theme: Dict[str, Any], rows: Sequence[Dict[str, Any]],
        force_stock: Optional[str] = None, replace_existing: bool = False,
    ) -> int:
        now = utc_naive_now()
        market_date = theme.get("market_date")
        if isinstance(market_date, str):
            market_date = _parse_date(market_date)
        saved = 0
        received_codes: set[str] = set()
        with self.db.session_scope() as session:
            theme_row = session.get(ConceptThemeRecord, int(theme["id"]))
            for row in rows:
                code = _ts_code(force_stock or row.get("con_code") or row.get("ts_code"))
                if not re.fullmatch(r"\d{6}\.(SH|SZ|BJ)", code):
                    continue
                received_codes.add(code)
                name = str(row.get("con_name") or row.get("name") or "").strip()
                reason = str(row.get("reason") or row.get("desc") or "").strip() or None
                day = _parse_date(row.get("trade_date")) or market_date
                existing = session.execute(select(ConceptMembershipRecord).where(
                    ConceptMembershipRecord.theme_id == int(theme["id"]),
                    ConceptMembershipRecord.source == theme["source"],
                    ConceptMembershipRecord.ts_code == code,
                )).scalar_one_or_none()
                values = {
                    "stock_name": name, "reason": reason, "active": True,
                    "source_weight": SOURCE_RELIABILITY.get(theme["source"], 0.7),
                    "hot_rank": _safe_int(row.get("hot_num")), "market_date": day,
                    "last_seen_at": now, "updated_at": now,
                }
                if existing is None:
                    session.add(ConceptMembershipRecord(
                        theme_id=int(theme["id"]), source=theme["source"], ts_code=code,
                        first_seen_at=now, **values,
                    ))
                else:
                    for key, value in values.items():
                        setattr(existing, key, value)
                saved += 1
            if replace_existing and received_codes and force_stock is None:
                session.execute(update(ConceptMembershipRecord).where(
                    ConceptMembershipRecord.theme_id == int(theme["id"]),
                    ConceptMembershipRecord.source == theme["source"],
                    ConceptMembershipRecord.active.is_(True),
                    ~ConceptMembershipRecord.ts_code.in_(received_codes),
                ).values(active=False, updated_at=now))
            if theme_row is not None and saved:
                theme_row.constituent_count = saved if replace_existing else max(theme_row.constituent_count or 0, saved)
                theme_row.last_seen_at = now
        return saved

    def _theme_lookup(self) -> Dict[Tuple[str, str], Dict[str, Any]]:
        with self._read_scope() as session:
            rows = session.execute(select(ConceptThemeRecord)).scalars().all()
        return {(row.source, row.source_code): self._theme_dict(row) for row in rows}

    def _canonical_market_score(self, canonical_name: str) -> float:
        with self._read_scope() as session:
            rows = session.execute(select(ConceptThemeRecord).where(
                ConceptThemeRecord.canonical_name == canonical_name)).scalars().all()
        values = [row.heat_score for row in rows if row.heat_score is not None]
        changes = [abs(row.pct_change) for row in rows if row.pct_change is not None]
        heat = max(values, default=45.0)
        movement = min(100.0, max(changes, default=0.0) * 14.0)
        return max(20.0, min(100.0, heat * 0.72 + movement * 0.28))

    def _unique_driver_evidence(self, code: str) -> List[Dict[str, Any]]:
        cutoff = utc_naive_now() - timedelta(days=180)
        compact = code[:6]
        items: List[Dict[str, Any]] = []
        with self._read_scope() as session:
            events = session.execute(select(MonitoringEventRecord).where(
                MonitoringEventRecord.event_at >= cutoff,
                MonitoringEventRecord.symbol_codes.like(f"%{code}%"),
            ).order_by(desc(MonitoringEventRecord.importance_score), desc(MonitoringEventRecord.event_at)).limit(12)).scalars().all()
            notes = session.execute(select(ResearchNote).where(
                ResearchNote.created_at >= cutoff,
                or_(ResearchNote.symbol_codes.like(f"%{code}%"), ResearchNote.symbol_codes.like(f"%{compact}%")),
            ).order_by(desc(ResearchNote.created_at)).limit(8)).scalars().all()
            ai_notes = session.execute(
                select(ResearchNote, EssayAnalysisRecord)
                .join(EssayAnalysisRecord, EssayAnalysisRecord.topic_id == ResearchNote.topic_id)
                .where(
                    ResearchNote.created_at >= cutoff,
                    EssayAnalysisRecord.status == "completed",
                    or_(
                        EssayAnalysisRecord.stock_mentions_json.like(f"%{code}%"),
                        EssayAnalysisRecord.stock_mentions_json.like(f"%{compact}%"),
                        ResearchNote.symbol_codes.like(f"%{code}%"),
                    ),
                )
                .order_by(desc(EssayAnalysisRecord.importance_score), desc(ResearchNote.created_at)).limit(10)
            ).all()
        for event in events:
            items.append({
                "kind": "event", "title": event.title, "summary": event.summary,
                "source": event.source_name, "date": event.event_at.isoformat() if event.event_at else None,
                "importance": event.importance_score, "url": event.url,
            })
        ai_topic_ids = {note.topic_id for note, _analysis in ai_notes}
        for note in notes:
            if note.topic_id in ai_topic_ids:
                continue
            items.append({
                "kind": "institution_note", "title": note.title or (note.content or "")[:80],
                "summary": (note.content or "")[:240], "source": note.group_name or "知识星球",
                "date": note.created_at.isoformat() if note.created_at else None,
                "importance": 60, "url": f"/essay-radar/feed?topic={note.topic_id}",
            })
        for note, analysis in ai_notes:
            catalysts = _json(analysis.catalysts_json, [])
            risks = _json(analysis.risks_json, [])
            qualifiers = [str(value) for value in [*catalysts[:2], *risks[:2]] if value]
            items.append({
                "kind": "ai_institution_signal",
                "title": note.title or f"{code} 机构语料 AI 结构化线索",
                "summary": str(analysis.summary or "")[:240] + (("；" + "；".join(qualifiers)) if qualifiers else ""),
                "source": f"{analysis.model} · 机构段子研判",
                "date": note.created_at.isoformat() if note.created_at else None,
                "importance": analysis.importance_score or 55,
                "url": f"/essay-radar/feed?topic={note.topic_id}",
            })
        items.sort(key=lambda value: (
            value.get("importance") or 0,
            1 if value.get("kind") == "ai_institution_signal" else 0,
            value.get("date") or "",
        ), reverse=True)
        deduplicated: List[Dict[str, Any]] = []
        seen_links: set[str] = set()
        for item in items:
            key = str(item.get("url") or f"{item.get('kind')}:{item.get('title')}")
            if key in seen_links:
                continue
            seen_links.add(key)
            deduplicated.append(item)
        return deduplicated[:16]

    @staticmethod
    def _theme_dict(row: ConceptThemeRecord) -> Dict[str, Any]:
        family = theme_family(row.canonical_name, row.theme_type)
        return {
            "id": row.id, "source": row.source, "source_label": SOURCE_LABELS.get(row.source, row.source),
            "source_code": row.source_code, "name": row.name, "canonical_name": row.canonical_name,
            "theme_type": row.theme_type, "level": row.level, "parent_code": row.parent_code,
            "constituent_count": row.constituent_count, "market_date": row.market_date.isoformat() if row.market_date else None,
            "heat_score": row.heat_score, "pct_change": row.pct_change, "fund_flow": row.fund_flow,
            "family": family, "cluster": theme_cluster(row.canonical_name, family),
            "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        }

    @staticmethod
    def _exposure_dict(row: ConceptExposureRecord) -> Dict[str, Any]:
        return {
            "weight_score": round(row.weight_score, 1), "consensus_score": row.consensus_score,
            "relevance_score": row.relevance_score, "market_score": row.market_score,
            "specificity_score": row.specificity_score, "beta": row.beta, "market_beta": row.market_beta,
            "alpha_annualized": row.alpha_annualized, "residual_return": row.residual_return,
            "r_squared": row.r_squared, "observations": row.observations, "confidence": row.confidence,
            "source_count": row.source_count, "evidence_count": row.evidence_count,
            "beta_interpretation": ConceptThemeService._beta_interpretation(row.beta, row.confidence),
            "components": _json(row.components_json, {}), "evidence": _json(row.evidence_json, []),
        }

    @staticmethod
    def _beta_interpretation(beta: Optional[float], confidence: str) -> str:
        if beta is None:
            return "样本不足，暂不解释"
        prefix = "低置信：" if confidence in {"low", "insufficient"} else ""
        if beta >= 1.2:
            return f"{prefix}对题材波动高度敏感"
        if beta >= 0.8:
            return f"{prefix}与题材走势较同步"
        if beta >= 0.3:
            return f"{prefix}受题材影响但弹性较低"
        if beta <= -0.3:
            return f"{prefix}窗口内与题材反向，需核验公司独立事件"
        return f"{prefix}题材联动较弱，个股因素占比更高"

    def _start_run(self, run_type: str, market_date: date, stage: str) -> int:
        with self.db.session_scope() as session:
            row = ConceptSyncRunRecord(run_type=run_type, status="running", market_date=market_date,
                                       progress=2, stage=stage, started_at=utc_naive_now(), updated_at=utc_naive_now())
            session.add(row)
            session.flush()
            return int(row.id)

    def _finish_run(self, run_id: int, *, status: str, progress: int, stage: str, themes: int,
                    source_stats: Dict[str, Any], error: Optional[Exception] = None) -> None:
        with self.db.session_scope() as session:
            row = session.get(ConceptSyncRunRecord, run_id)
            if row:
                row.status = status
                row.progress = progress
                row.stage = stage
                row.themes_seen = themes
                row.source_stats_json = json.dumps(source_stats, ensure_ascii=False)
                row.error = f"{type(error).__name__}: {str(error)[:500]}" if error else None
                row.finished_at = utc_naive_now()
                row.updated_at = utc_naive_now()

    @staticmethod
    def _run_dict(row: ConceptSyncRunRecord) -> Dict[str, Any]:
        return {
            "id": row.id, "run_type": row.run_type, "status": row.status,
            "market_date": row.market_date.isoformat() if row.market_date else None,
            "progress": row.progress, "stage": row.stage, "themes_seen": row.themes_seen,
            "memberships_seen": row.memberships_seen, "exposures_seen": row.exposures_seen,
            "sources": _json(row.source_stats_json, {}), "error": row.error,
            "started_at": row.started_at.isoformat() if row.started_at else None,
            "finished_at": row.finished_at.isoformat() if row.finished_at else None,
        }
