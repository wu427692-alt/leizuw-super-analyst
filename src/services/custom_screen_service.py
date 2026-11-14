# -*- coding: utf-8 -*-
"""Local composite screener for natural-language stock ideas.

Supports combinations such as "科技 + 超跌底部 + 放量企稳" by filtering the
local daily bars / fundamentals instead of relying on a fixed strategy.
"""

from __future__ import annotations

import math
import os
import re
import sqlite3
from datetime import date, timedelta

from src.config import runtime_data_directory
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

# 与其它服务共用同一个数据目录解析：Docker 里 DATABASE_PATH 显式指向
# /app/data，本机/桌面端未设置时落在 ./data，不再硬编码不可写的 /app。
DB_PATH = os.environ.get("DATABASE_PATH", str(runtime_data_directory() / "stock_analysis.db"))

THEME_INDUSTRIES: Dict[str, List[str]] = {
    "科技": ["半导体", "元器件", "软件服务", "通信设备", "互联网", "IT设备", "电信运营"],
    "硬科技": ["半导体", "元器件", "通信设备", "IT设备"],
    "半导体": ["半导体", "元器件"],
    "芯片": ["半导体", "元器件"],
    "软件": ["软件服务", "互联网"],
    "ai": ["软件服务", "互联网", "IT设备"],
    "人工智能": ["软件服务", "互联网", "IT设备"],
    "通信": ["通信设备", "电信运营"],
    "计算机": ["软件服务", "IT设备"],
    "电子": ["半导体", "元器件"],
    "互联网": ["互联网", "软件服务"],
}


CONCEPT_ALIASES: Dict[str, List[str]] = {
    "AI算力": ["算力", "数据中心", "服务器", "液冷", "CPO", "光模块", "光通信", "人工智能"],
    "光模块": ["光模块", "光通信", "CPO", "硅光", "光芯片"],
    "机器人": ["机器人", "人形", "减速器", "机器视觉", "工业母机"],
    "固态电池": ["固态电池", "固态电解质"],
}


def _num(value: Any, digits: int = 3) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return round(number, digits)


def _clean(text: Any) -> str:
    return str(text or "").strip()


class CustomScreenService:
    def resolve_industries(self, theme: str, industries: Optional[List[str]] = None) -> List[str]:
        if industries:
            return [item for item in industries if item]
        text = _clean(theme).lower()
        for key, values in THEME_INDUSTRIES.items():
            if key.lower() in text:
                return list(values)
        # Fall back to exact industry names present in the local basic table.
        try:
            con = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
            rows = con.execute("SELECT DISTINCT industry FROM stock_basic WHERE industry IS NOT NULL").fetchall()
            con.close()
            exact = [row[0] for row in rows if text and text in str(row[0]).lower()]
            if exact:
                return exact
        except Exception:
            pass
        return list(THEME_INDUSTRIES["科技"])

    def _codes_for_industries(self, industries: List[str]) -> set[str]:
        if not industries:
            return set()
        con = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
        try:
            placeholders = ",".join("?" for _ in industries)
            rows = con.execute(
                f"SELECT DISTINCT code FROM stock_basic WHERE industry IN ({placeholders})",
                industries,
            ).fetchall()
        finally:
            con.close()
        return {str(row[0]).zfill(6) for row in rows if row[0]}

    def resolve_concepts(self, concepts: Optional[List[str]]) -> tuple[set[str], List[str]]:
        aliases: set[str] = set()
        for concept in concepts or []:
            text = _clean(concept)
            if not text:
                continue
            key = re.sub(r"\s+", "", text)
            matched = False
            for alias_key, values in CONCEPT_ALIASES.items():
                if re.sub(r"\s+", "", alias_key) == key:
                    aliases.update(values)
                    matched = True
                    break
            if not matched:
                aliases.add(text)
        if not aliases:
            return set(), []
        con = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
        try:
            clauses = []
            params: List[str] = []
            for alias in aliases:
                clauses.append("(name LIKE ? OR canonical_name LIKE ?)")
                params.extend([f"%{alias}%", f"%{alias}%"])
            themes = con.execute(
                f"SELECT id, name, canonical_name FROM concept_themes WHERE {' OR '.join(clauses)}",
                params,
            ).fetchall()
            if not themes:
                return set(), []
            theme_ids = [row[0] for row in themes]
            placeholders = ",".join("?" for _ in theme_ids)
            code_rows = con.execute(
                f"SELECT DISTINCT ts_code FROM concept_memberships "
                f"WHERE active = 1 AND theme_id IN ({placeholders})",
                theme_ids,
            ).fetchall()
            codes = {str(row[0]).split(".")[0].zfill(6) for row in code_rows if row[0]}
            names: List[str] = []
            for row in themes:
                name = _clean(row[2] or row[1])
                if name and name not in names:
                    names.append(name)
            return codes, names[:8]
        finally:
            con.close()

    def screen(
        self,
        theme: Optional[str] = "科技",
        industries: Optional[List[str]] = None,
        concepts: Optional[List[str]] = None,
        min_drawdown: float = 20.0,
        rsi_max: float = 45.0,
        max_distance_low: float = 25.0,
        min_amount: float = 1e8,
        min_market_cap: float = 3e9,
        require_stabilization: bool = True,
        max_change_pct: Optional[float] = 6.0,
        max_results: int = 10,
    ) -> Dict[str, Any]:
        concept_codes: set[str] = set()
        concept_themes: List[str] = []
        if concepts:
            concept_codes, concept_themes = self.resolve_concepts(concepts)
            if not concept_codes:
                return {"candidates": [], "error": f"没有找到概念成员：{concepts}"}
        if industries:
            industries = self.resolve_industries(theme or "科技", industries)
        elif not concepts:
            industries = self.resolve_industries(theme or "科技", None)
        else:
            industries = []

        industry_codes = self._codes_for_industries(industries) if industries else set()
        if concept_codes and industry_codes:
            combined = concept_codes & industry_codes
            if not combined:
                combined = concept_codes
        elif concept_codes:
            combined = concept_codes
        else:
            combined = industry_codes
        if not combined:
            return {"candidates": [], "error": "没有匹配的股票池"}

        con = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
        try:
            latest_row = con.execute("SELECT MAX(date) FROM stock_daily").fetchone()
            latest = latest_row[0] if latest_row else None
            if not latest:
                return {"candidates": [], "error": "本地行情为空"}
            start = (date.fromisoformat(latest) - timedelta(days=220)).isoformat()
            con.execute("CREATE TEMP TABLE _screen_codes (code TEXT PRIMARY KEY)")
            con.executemany(
                "INSERT OR IGNORE INTO _screen_codes(code) VALUES (?)",
                [(code,) for code in combined],
            )
            bars = pd.read_sql_query(
                """
                SELECT d.code, d.date, d.close, d.high, d.low, d.volume, d.amount, d.pct_chg
                FROM stock_daily d
                JOIN _screen_codes c ON c.code = d.code
                WHERE d.date >= ?
                ORDER BY d.code, d.date
                """,
                con,
                params=[start],
            )
            basics = pd.read_sql_query(
                """
                SELECT s.code, s.name, s.industry
                FROM stock_basic s
                JOIN _screen_codes c ON c.code = s.code
                """,
                con,
            )
            fundamentals = pd.read_sql_query(
                """
                SELECT code, pe, pe_ttm, pb, total_mv, circ_mv, turnover_rate, volume_ratio
                FROM stock_daily_basic
                WHERE date = ?
                """,
                con,
                params=[latest],
            )
        finally:
            con.close()

        if bars.empty:
            return {"candidates": [], "error": "没有符合行业的行情数据"}

        fundamentals = fundamentals.set_index("code")
        basic_map = basics.set_index("code").to_dict("index")
        candidates: List[Dict[str, Any]] = []

        for code, group in bars.groupby("code", sort=False):
            group = group.tail(140)
            if len(group) < 60:
                continue
            close_series = group["close"].astype(float)
            high_series = group["high"].astype(float)
            low_series = group["low"].astype(float)
            volume_series = group["volume"].astype(float)
            close = float(close_series.iloc[-1])
            if close <= 0:
                continue
            high_60 = float(high_series.tail(60).max())
            low_120 = float(low_series.tail(120).min())
            if high_60 <= 0 or low_120 <= 0:
                continue
            drawdown = (high_60 - close) / high_60 * 100.0
            distance_low = (close - low_120) / low_120 * 100.0
            ma5 = float(close_series.tail(5).mean())
            ma10 = float(close_series.tail(10).mean())
            ma20 = float(close_series.tail(20).mean())

            delta = close_series.diff()
            gain = delta.clip(lower=0).rolling(14).mean()
            loss = (-delta.clip(upper=0)).rolling(14).mean()
            rs = gain / loss.replace(0, np.nan)
            rsi = float((100 - 100 / (1 + rs)).iloc[-1]) if not math.isnan((100 - 100 / (1 + rs)).iloc[-1]) else 50.0

            pct_chg = float(group["pct_chg"].iloc[-1])
            amount = float(group["amount"].iloc[-1])
            vol_avg = float(volume_series.tail(20).mean())
            vol_ratio = float(volume_series.iloc[-1] / vol_avg) if vol_avg > 0 else 1.0
            ret5 = float(close / close_series.iloc[-6] - 1) * 100 if len(close_series) > 5 else 0.0
            ret20 = float(close / close_series.iloc[-21] - 1) * 100 if len(close_series) > 20 else 0.0

            info = basic_map.get(code, {})
            name = _clean(info.get("name") or code)
            industry = _clean(info.get("industry"))
            if "ST" in name.upper() or "退" in name:
                continue

            row = fundamentals.loc[code] if code in fundamentals.index else None
            pe = _num(row.get("pe_ttm") if row is not None and row.get("pe_ttm") is not None else (row.get("pe") if row is not None else None))
            pb = _num(row.get("pb") if row is not None else None)
            total_mv = _num(row.get("total_mv") if row is not None else None, 0)
            turnover = _num(row.get("turnover_rate") if row is not None else None)

            if drawdown < min_drawdown:
                continue
            if rsi > rsi_max:
                continue
            if distance_low > max_distance_low:
                continue
            if max_change_pct is not None and pct_chg > max_change_pct:
                continue
            if amount < min_amount:
                continue
            if total_mv is not None and total_mv < min_market_cap:
                continue
            stabilized = close >= ma5 or pct_chg >= 0
            if require_stabilization and not (stabilized and vol_ratio >= 0.8):
                continue

            oversold_score = min(drawdown / 40.0, 1.0) * 55 + max(0.0, (45.0 - rsi) / 45.0) * 45
            stab_score = (
                (40 if close >= ma5 else 0)
                + (30 if pct_chg >= 0 else 0)
                + min(vol_ratio / 1.5, 1.0) * 30
            )
            if pe is not None and 0 < pe <= 80:
                quality_score = (80 - pe) / 80 * 50
            else:
                quality_score = 25
            if pb is not None and 0 < pb <= 8:
                quality_score += (8 - pb) / 8 * 50
            else:
                quality_score += 25
            score = oversold_score * 0.45 + stab_score * 0.35 + quality_score * 0.20
            if distance_low <= 5:
                score += 3

            reasons = [
                f"较60日高点回撤 {drawdown:.1f}%",
                f"RSI(14) {rsi:.1f}",
                f"距120日低点 {distance_low:.1f}%",
                f"5日 {ret5:+.1f}% / 20日 {ret20:+.1f}%",
                "站上5日线" if close >= ma5 else "未站上5日线",
                f"量比 {vol_ratio:.2f}",
            ]
            candidates.append(
                {
                    "code": code,
                    "name": name,
                    "industry": industry,
                    "price": _num(close, 2),
                    "change_pct": _num(pct_chg, 2),
                    "drawdown_60": _num(drawdown, 1),
                    "rsi14": _num(rsi, 1),
                    "distance_low": _num(distance_low, 1),
                    "ret5": _num(ret5, 1),
                    "ret20": _num(ret20, 1),
                    "ma5": _num(ma5, 2),
                    "ma20": _num(ma20, 2),
                    "vol_ratio": _num(vol_ratio, 2),
                    "turnover_rate": turnover,
                    "amount": _num(amount, 0),
                    "pe": pe,
                    "pb": pb,
                    "total_mv": total_mv,
                    "score": _num(score, 2),
                    "reason": "；".join(reasons),
                }
            )

        candidates.sort(key=lambda item: item.get("score") or 0, reverse=True)
        top = candidates[: max(1, min(int(max_results), 30))]
        for index, item in enumerate(top, 1):
            item["rank"] = index
        return {
            "theme": theme or "",
            "industries": industries,
            "concepts": list(concepts or []),
            "conceptThemes": concept_themes,
            "data_as_of": latest,
            "universe_count": int(len(basic_map)),
            "after_filter_count": len(candidates),
            "conditions": {
                "minDrawdown": min_drawdown,
                "rsiMax": rsi_max,
                "maxDistanceLow": max_distance_low,
                "minAmount": min_amount,
                "minMarketCap": min_market_cap,
                "requireStabilization": require_stabilization,
                "maxChangePct": max_change_pct,
            },
            "candidates": top,
        }
