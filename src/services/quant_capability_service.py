# -*- coding: utf-8 -*-
"""Runtime capability checks for auction data, essay provenance and execution.

The natural-language planner must not guess whether a data feed or broker is
available.  This service is the source of truth for the three capabilities and
keeps order submission owner-scoped and confirmation-gated.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
import hashlib
import json
import os
import secrets
from typing import Any, Dict, Iterable, Optional
from urllib import error as urllib_error
from urllib import request as urllib_request
from uuid import uuid4

from sqlalchemy import desc, func, select

from src.request_identity import current_owner_id
from src.services.financial_data_service import (
    FinancialDataUpstreamError,
    TushareGatewayService,
)
from src.storage import (
    DatabaseManager,
    EssayAnalysisRecord,
    QuantAuctionRecord,
    QuantExecutionOrderRecord,
    ResearchNote,
    StockDaily,
    StockTick,
    ZsxqSyncState,
    utc_naive_now,
)


class QuantCapabilityError(ValueError):
    pass


def _number(value: Any) -> Optional[float]:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _iso(value: Optional[datetime | date]) -> Optional[str]:
    return value.isoformat() + ("Z" if isinstance(value, datetime) else "") if value else None


def _canonical_code(value: Any) -> str:
    raw = str(value or "").strip().upper()
    if raw[:6].isdigit() and len(raw) >= 6:
        digits = raw[:6]
    else:
        raise QuantCapabilityError("股票代码必须是六位 A 股代码")
    suffix = raw[6:].lstrip(".")
    if suffix not in {"SH", "SZ", "BJ"}:
        suffix = "BJ" if digits.startswith(("4", "8")) else "SH" if digits.startswith(("6", "9")) else "SZ"
    return f"{digits}.{suffix}"


class QuantCapabilityService:
    """Authoritative runtime checks plus safe paper/live order routing."""

    def __init__(
        self,
        db: Optional[DatabaseManager] = None,
        tushare: Optional[TushareGatewayService] = None,
        owner_id: Optional[str] = None,
    ):
        self.db = db or DatabaseManager.get_instance()
        self.tushare = tushare or TushareGatewayService()
        self.owner_id = owner_id if owner_id is not None else current_owner_id()

    @property
    def broker_url(self) -> str:
        return str(os.getenv("BROKER_EXECUTION_BASE_URL") or "").strip().rstrip("/")

    @property
    def broker_key(self) -> str:
        return str(os.getenv("BROKER_EXECUTION_API_KEY") or "").strip()

    @property
    def broker_name(self) -> str:
        return str(os.getenv("BROKER_EXECUTION_PROVIDER") or "broker_gateway").strip()[:64]

    def capabilities(self) -> Dict[str, Any]:
        with self.db.get_session() as session:
            auction_count, auction_latest, live_latest = session.execute(
                select(
                    func.count(QuantAuctionRecord.id),
                    func.max(QuantAuctionRecord.trade_date),
                    func.max(QuantAuctionRecord.captured_at).filter(QuantAuctionRecord.is_realtime.is_(True)),
                )
            ).one()
            note_count, group_count, latest_note, provenance_count = session.execute(
                select(
                    func.count(ResearchNote.id),
                    func.count(func.distinct(ResearchNote.group_id)),
                    func.max(ResearchNote.synced_at),
                    func.count(ResearchNote.id).filter(
                        ResearchNote.group_id.is_not(None), ResearchNote.topic_id.is_not(None)
                    ),
                )
            ).one()
            analyzed_count = session.execute(
                select(func.count(EssayAnalysisRecord.id)).where(EssayAnalysisRecord.status == "completed")
            ).scalar_one()
            sync_rows = session.execute(select(ZsxqSyncState)).scalars().all()
            order_count = 0
            if self.owner_id:
                order_count = session.execute(
                    select(func.count(QuantExecutionOrderRecord.id)).where(
                        QuantExecutionOrderRecord.owner_id == self.owner_id
                    )
                ).scalar_one()

        healthy_groups = sum(row.last_status == "success" and bool(row.last_success_at) for row in sync_rows)
        known_groups = max(int(group_count or 0), len(sync_rows))
        live_configured = bool(self.broker_url and self.broker_key)
        auction_feed_configured = bool(str(os.getenv("QUANT_AUCTION_FEED_BASE_URL") or "").strip())
        provenance_ratio = round((int(provenance_count or 0) * 100 / int(note_count or 1)), 1)
        analysis_ratio = round((int(analyzed_count or 0) * 100 / int(note_count or 1)), 1)
        sync_ratio = round((healthy_groups * 100 / known_groups), 1) if known_groups else 0.0
        return {
            "generated_at": _iso(utc_naive_now()),
            "auction": {
                "supported": True,
                "historical_provider": "Tushare stk_auction_o",
                "realtime_provider": "licensed auction feed" if auction_feed_configured else "Tushare rt_min_daily 09:30 confirmed bar",
                "tushare_configured": bool(self.tushare.available),
                "realtime_feed_configured": auction_feed_configured,
                "stored_rows": int(auction_count or 0),
                "latest_trade_date": _iso(auction_latest),
                "latest_realtime_at": _iso(live_latest),
                "entry_modes": ["next_auction", "next_open"],
                "status": "ready" if auction_count else "ready_to_sync" if self.tushare.available else "needs_data_credential",
                "note": "历史回测优先使用真实开盘集合竞价成交价；盘中交易使用实时通道报价，缺失时明确标记回退。",
            },
            "essay_coverage": {
                "supported": True,
                "source": "知识星球 MCP 增量库",
                "note_count": int(note_count or 0),
                "group_count": known_groups,
                "healthy_group_count": healthy_groups,
                "sync_coverage_pct": sync_ratio,
                "provenance_coverage_pct": provenance_ratio,
                "analysis_coverage_pct": analysis_ratio,
                "latest_synced_at": _iso(latest_note),
                "status": "ready" if known_groups and healthy_groups == known_groups and provenance_ratio == 100 else "audited_with_gaps",
                "note": "每条语料保留星球、帖子、时间与原文证据；完整性按已授权星球逐组审计，缺口不会被伪装成完整。",
            },
            "execution": {
                "supported": True,
                "paper_available": True,
                "live_available": live_configured,
                "provider": self.broker_name if live_configured else None,
                "requires_account_binding": not live_configured,
                "requires_preview": True,
                "requires_confirmation": True,
                "owner_scoped": True,
                "order_count": int(order_count or 0),
                "status": "live_ready" if live_configured else "paper_ready",
                "note": "模拟盘可立即使用；实盘通过券商网关路由，必须绑定账户并对每笔订单二次确认。",
            },
        }

    def sync_auction(
        self,
        symbols: Iterable[str],
        *,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
    ) -> Dict[str, Any]:
        codes = list(dict.fromkeys(_canonical_code(item) for item in symbols))[:60]
        if not codes:
            raise QuantCapabilityError("至少需要一个股票代码")
        if not self.tushare.available:
            raise QuantCapabilityError("Tushare 尚未配置，无法同步集合竞价")
        end = end_date or date.today()
        start = start_date or (end - timedelta(days=730))
        saved = 0
        failures: list[Dict[str, str]] = []
        for code in codes:
            try:
                result = self.tushare.query(
                    "stk_auction_o",
                    params={
                        "ts_code": code,
                        "start_date": start.strftime("%Y%m%d"),
                        "end_date": end.strftime("%Y%m%d"),
                    },
                    fields="ts_code,trade_date,close,open,high,low,vol,amount,vwap",
                )
                saved += self._save_auction_rows(result.get("rows") or [], "tushare.stk_auction_o", False)
            except Exception as exc:  # preserve per-symbol progress and report gaps
                failures.append({"ts_code": code, "message": str(exc)[:180]})
        return {
            "symbols": codes,
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
            "saved_rows": saved,
            "failed": failures,
            "complete": not failures,
        }

    def _save_auction_rows(self, rows: Iterable[Dict[str, Any]], source: str, realtime: bool) -> int:
        saved = 0
        with self.db.get_session() as session:
            for item in rows:
                code = _canonical_code(item.get("ts_code") or item.get("code"))
                raw_date = str(item.get("trade_date") or item.get("time") or "")[:10].replace("-", "")
                if len(raw_date) != 8 or not raw_date.isdigit():
                    continue
                trade_date = datetime.strptime(raw_date, "%Y%m%d").date()
                row = session.execute(
                    select(QuantAuctionRecord).where(
                        QuantAuctionRecord.ts_code == code,
                        QuantAuctionRecord.trade_date == trade_date,
                        QuantAuctionRecord.source == source,
                    )
                ).scalar_one_or_none()
                if row is None:
                    row = QuantAuctionRecord(ts_code=code, trade_date=trade_date, source=source)
                    session.add(row)
                row.open = _number(item.get("open"))
                row.close = _number(item.get("close"))
                row.high = _number(item.get("high"))
                row.low = _number(item.get("low"))
                row.volume = _number(item.get("vol") if item.get("vol") is not None else item.get("volume"))
                row.amount = _number(item.get("amount"))
                row.vwap = _number(item.get("vwap"))
                row.is_realtime = bool(realtime)
                row.raw_json = json.dumps(item, ensure_ascii=False, default=str)
                row.captured_at = utc_naive_now()
                saved += 1
            session.commit()
        return saved

    def auction_price_map(self, symbols: Iterable[str], start: date, end: date) -> Dict[tuple[str, date], float]:
        codes = [_canonical_code(item) for item in symbols]
        if not codes:
            return {}
        with self.db.get_session() as session:
            rows = session.execute(
                select(QuantAuctionRecord).where(
                    QuantAuctionRecord.ts_code.in_(codes),
                    QuantAuctionRecord.trade_date >= start,
                    QuantAuctionRecord.trade_date <= end,
                ).order_by(QuantAuctionRecord.trade_date, desc(QuantAuctionRecord.is_realtime))
            ).scalars().all()
        result: Dict[tuple[str, date], float] = {}
        for row in rows:
            value = row.close or row.vwap or row.open
            if value not in (None, 0):
                result.setdefault((row.ts_code, row.trade_date), float(value))
        return result

    def preview_order(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        if not self.owner_id:
            raise QuantCapabilityError("请先登录后再创建交易指令")
        mode = str(payload.get("mode") or "paper").strip().lower()
        if mode not in {"paper", "live"}:
            raise QuantCapabilityError("交易模式只能是 paper 或 live")
        if mode == "live" and not (self.broker_url and self.broker_key):
            raise QuantCapabilityError("实盘交易网关尚未绑定；请先在管理员后台配置券商通道")
        code = _canonical_code(payload.get("ts_code"))
        side = str(payload.get("side") or "").strip().lower()
        if side not in {"buy", "sell"}:
            raise QuantCapabilityError("交易方向只能是 buy 或 sell")
        quantity = int(payload.get("quantity") or 0)
        if quantity <= 0 or quantity > 10_000_000 or quantity % 100 != 0:
            raise QuantCapabilityError("A股委托数量必须为正数且为100股的整数倍")
        order_type = str(payload.get("order_type") or "limit").strip().lower()
        if order_type not in {"limit", "market"}:
            raise QuantCapabilityError("委托类型只能是 limit 或 market")
        limit_price = _number(payload.get("limit_price"))
        reference = self._latest_price(code)
        if order_type == "limit" and (limit_price is None or limit_price <= 0):
            raise QuantCapabilityError("限价委托必须填写有效价格")
        if order_type == "market" and mode == "live":
            raise QuantCapabilityError("A股实盘默认禁用无保护市价单，请改用限价委托")
        price_for_check = limit_price or reference.get("price")
        if price_for_check in (None, 0):
            raise QuantCapabilityError("当前没有可用于风险校验的价格，请刷新行情后重试")
        notional = float(price_for_check) * quantity
        max_notional = float(os.getenv("QUANT_MAX_ORDER_NOTIONAL_CNY", "500000"))
        if notional > max_notional:
            raise QuantCapabilityError(f"单笔金额超过系统上限 {max_notional:.0f} 元")
        token = secrets.token_urlsafe(32)
        order_id = uuid4().hex
        expires = utc_naive_now() + timedelta(minutes=5)
        request_payload = {
            "run_id": int(payload["run_id"]) if payload.get("run_id") is not None else None,
            "mode": mode, "ts_code": code, "side": side, "order_type": order_type,
            "quantity": quantity, "limit_price": limit_price, "reference_price": reference.get("price"),
            "reference_at": reference.get("timestamp"), "estimated_notional": round(notional, 2),
        }
        with self.db.get_session() as session:
            session.add(QuantExecutionOrderRecord(
                order_id=order_id, owner_id=self.owner_id, run_id=request_payload["run_id"],
                mode=mode, provider="paper" if mode == "paper" else self.broker_name,
                ts_code=code, side=side, order_type=order_type, quantity=quantity,
                limit_price=limit_price, confirmation_hash=hashlib.sha256(token.encode()).hexdigest(),
                confirmation_expires_at=expires, request_json=json.dumps(request_payload, ensure_ascii=False),
            ))
            session.commit()
        return {
            "order_id": order_id, "status": "awaiting_confirmation", "confirmation_token": token,
            "confirmation_expires_at": _iso(expires), "order": request_payload,
            "risk_checks": ["登录用户隔离", "100股整数倍", "单笔金额上限", "限价保护", "5分钟二次确认"],
        }

    def submit_order(self, order_id: str, confirmation_token: str, *, confirmed: bool) -> Dict[str, Any]:
        if not confirmed:
            raise QuantCapabilityError("必须明确确认本次委托")
        if not self.owner_id:
            raise QuantCapabilityError("请先登录")
        with self.db.get_session() as session:
            row = session.execute(select(QuantExecutionOrderRecord).where(
                QuantExecutionOrderRecord.order_id == str(order_id),
                QuantExecutionOrderRecord.owner_id == self.owner_id,
            )).scalar_one_or_none()
            if row is None:
                raise QuantCapabilityError("订单不存在")
            if row.status != "awaiting_confirmation":
                raise QuantCapabilityError(f"订单当前状态为 {row.status}，不能重复提交")
            if row.confirmation_expires_at < utc_naive_now():
                row.status = "expired"
                session.commit()
                raise QuantCapabilityError("确认已过期，请重新预览订单")
            if not secrets.compare_digest(row.confirmation_hash, hashlib.sha256(confirmation_token.encode()).hexdigest()):
                raise QuantCapabilityError("订单确认令牌无效")
            payload = json.loads(row.request_json or "{}")
            if row.mode == "paper":
                response = {
                    "provider_order_id": f"paper-{row.order_id}", "status": "filled",
                    "filled_quantity": row.quantity,
                    "filled_price": row.limit_price or payload.get("reference_price"),
                    "submitted_at": _iso(utc_naive_now()),
                }
            else:
                response = self._submit_live(payload, row.order_id)
            row.status = str(response.get("status") or "submitted")[:24]
            row.response_json = json.dumps(response, ensure_ascii=False, default=str)
            row.submitted_at = utc_naive_now()
            session.commit()
            return {"order_id": row.order_id, "mode": row.mode, "provider": row.provider, **response}

    def list_orders(self, limit: int = 50) -> Dict[str, Any]:
        if not self.owner_id:
            return {"items": [], "total": 0}
        with self.db.get_session() as session:
            rows = session.execute(select(QuantExecutionOrderRecord).where(
                QuantExecutionOrderRecord.owner_id == self.owner_id,
            ).order_by(desc(QuantExecutionOrderRecord.id)).limit(max(1, min(limit, 100)))).scalars().all()
        return {"items": [{
            "order_id": row.order_id, "run_id": row.run_id, "mode": row.mode, "provider": row.provider,
            "ts_code": row.ts_code, "side": row.side, "order_type": row.order_type,
            "quantity": row.quantity, "limit_price": row.limit_price, "status": row.status,
            "created_at": _iso(row.created_at), "submitted_at": _iso(row.submitted_at),
            "response": json.loads(row.response_json or "{}"),
        } for row in rows], "total": len(rows)}

    def _latest_price(self, code: str) -> Dict[str, Any]:
        candidates = {code, code.split(".")[0]}
        with self.db.get_session() as session:
            tick = session.execute(select(StockTick).where(
                StockTick.code.in_(candidates), StockTick.price.is_not(None)
            ).order_by(desc(StockTick.timestamp)).limit(1)).scalar_one_or_none()
            if tick and tick.price:
                return {"price": float(tick.price), "timestamp": _iso(tick.timestamp), "source": tick.data_source}
            daily = session.execute(select(StockDaily).where(
                StockDaily.code == code.split(".")[0], StockDaily.close.is_not(None)
            ).order_by(desc(StockDaily.date)).limit(1)).scalar_one_or_none()
        return {"price": float(daily.close), "timestamp": _iso(daily.date), "source": daily.data_source} if daily else {}

    def _submit_live(self, payload: Dict[str, Any], client_order_id: str) -> Dict[str, Any]:
        if not (self.broker_url and self.broker_key):
            raise QuantCapabilityError("实盘交易网关未配置")
        body = json.dumps({**payload, "client_order_id": client_order_id}, ensure_ascii=False).encode("utf-8")
        request = urllib_request.Request(
            f"{self.broker_url}/orders", data=body, method="POST",
            headers={"Authorization": f"Bearer {self.broker_key}", "Content-Type": "application/json"},
        )
        try:
            with urllib_request.urlopen(request, timeout=10) as response:  # noqa: S310 - URL is operator-owned env config.
                data = json.loads(response.read().decode("utf-8"))
        except (urllib_error.URLError, TimeoutError, ValueError) as exc:
            raise FinancialDataUpstreamError("券商交易网关提交失败") from exc
        if not isinstance(data, dict) or not data.get("status"):
            raise FinancialDataUpstreamError("券商交易网关返回格式无效")
        return data
