# -*- coding: utf-8 -*-
"""Local, human-filterable broker research-report library."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from hashlib import sha256
from io import BytesIO
import json
import logging
import threading
from typing import Any, Dict, Iterable, List, Optional

from openpyxl import Workbook
from openpyxl.styles import Font
from sqlalchemy import and_, asc, case, desc, func, or_, select, update
from sqlalchemy.exc import IntegrityError

from src.services.financial_data_service import FinancialDataService
from src.storage import (
    DatabaseManager,
    ResearchReportRecord,
    ResearchReportSyncState,
    utc_naive_now,
)

logger = logging.getLogger(__name__)
_SOURCE_KEY = "tushare.research_report"
_FIELDS = [
    "trade_date", "abstr", "title", "report_type", "author", "name",
    "ts_code", "inst_csname", "ind_name", "url",
]
_DEEP_TERMS = ("深度", "专题", "全景", "产业链", "白皮书", "年度策略", "投资策略")
_QUICK_TERMS = ("日报", "周报", "早报", "晨报", "快评", "点评")
_ACTIVE_SYNC_STATUSES = ("queued", "running")
_SYNC_LEASE = timedelta(minutes=15)
_SYNC_HEARTBEAT_SECONDS = 30


class ResearchReportLibraryError(RuntimeError):
    """Safe error exposed by the report-library API."""


class _ReportSyncLeaseLost(RuntimeError):
    """Another process owns this source; the old collector must stop writing."""


class ResearchReportLibraryService:
    """Backfill Tushare report metadata once, then search SQLite only."""

    _instance: Optional["ResearchReportLibraryService"] = None
    _instance_lock = threading.Lock()

    def __init__(
        self,
        *,
        db_manager: Optional[DatabaseManager] = None,
        financial: Optional[FinancialDataService] = None,
    ) -> None:
        self.db = db_manager or DatabaseManager.get_instance()
        self.financial = financial or FinancialDataService()
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="report-library")
        self._run_lock = threading.RLock()
        self._future = None
        # Construction is also used by read-only HTTP routes in other
        # processes. It must not relabel a live collector as interrupted.

    @classmethod
    def get_instance(cls) -> "ResearchReportLibraryService":
        with cls._instance_lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        with cls._instance_lock:
            if cls._instance is not None:
                cls._instance._executor.shutdown(wait=False, cancel_futures=True)
            cls._instance = None

    def ensure_background_sync(self) -> Dict[str, Any]:
        """Start the two-year backfill or a short incremental refresh when due."""
        status = self.status()
        today = date.today()
        if status["status"] in (*_ACTIVE_SYNC_STATUSES, "failed"):
            heartbeat = self._parse_datetime(status.get("updated_at") or status.get("started_at"))
            if status["status"] in _ACTIVE_SYNC_STATUSES and heartbeat and utc_naive_now() - heartbeat < _SYNC_LEASE:
                return status
            # Only the background entry point may reclaim an expired lease.
            # Preserve an interrupted request's scope instead of inventing a
            # second full-history request or silently discarding its range.
            return self.start_sync(
                start_date=self._parse_date(status.get("start_date")) or today - timedelta(days=14),
                end_date=self._parse_date(status.get("end_date")) or today,
                only_if_due=True,
            )
        earliest = self._parse_date(status.get("earliest_trade_date"))
        completed = self._parse_datetime(status.get("completed_at"))
        due = completed is None or utc_naive_now() - completed >= timedelta(hours=6)
        target_start = today - timedelta(days=730)
        # The exact boundary can be a weekend/holiday with no published report.
        needs_two_years = not status["total"] or earliest is None or earliest > target_start + timedelta(days=14)
        if needs_two_years:
            return self.start_sync(start_date=today - timedelta(days=730), end_date=today, only_if_due=True)
        # Having a report dated today does not mean later publications were
        # ingested. Reconcile the short overlap window whenever the cadence is due.
        if due:
            return self.start_sync(start_date=today - timedelta(days=14), end_date=today, only_if_due=True)
        return status

    def start_sync(
        self,
        *,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
        force: bool = False,
        only_if_due: bool = False,
    ) -> Dict[str, Any]:
        with self._run_lock:
            if self._future is not None and not self._future.done():
                return self.status()
            end = end_date or date.today()
            start = start_date or (end - timedelta(days=730))
            if start > end:
                raise ResearchReportLibraryError("研报同步开始日期不能晚于结束日期")
            windows = self._date_windows(start, end)
            claim = self._claim_sync(
                only_if_due=only_if_due and not force,
                status="queued",
                progress=0,
                message="研报链接同步已进入后台队列",
                start_date=start,
                end_date=end,
                total_windows=len(windows),
                completed_windows=0,
                cursor_offset=0,
                scanned_rows=0,
                saved_rows=0,
                last_error=None,
                completed_at=None,
            )
            if claim is None:
                return self.status()
            try:
                self._future = self._executor.submit(self._run_sync, start, end, windows, claim_started_at=claim)
            except RuntimeError as exc:
                self._write_state(_claim_started_at=claim, status="failed", message="研报同步后台执行器暂时不可用", completed_at=utc_naive_now())
                raise ResearchReportLibraryError("无法启动研报同步任务") from exc
            return self.status()

    def _claim_sync(self, *, only_if_due: bool, **changes: Any) -> Optional[datetime]:
        """Atomically claim the existing source row across web/worker processes.

        started_at is the claim identity; updated_at is its renewable lease.
        Conditional updates prevent a stale reader from replacing a fresh
        claimant or heartbeat. No schema change or host-specific PID is needed.
        """
        now = utc_naive_now()
        with self.db.get_session() as session:
            state = session.get(ResearchReportSyncState, _SOURCE_KEY)
            changes.update(started_at=now, updated_at=now)
            if state is None:
                session.add(ResearchReportSyncState(source_key=_SOURCE_KEY, **changes))
                try:
                    session.commit()
                    return now
                except IntegrityError:
                    session.rollback()
                    return None
            heartbeat = state.updated_at or state.started_at
            if state.status in _ACTIVE_SYNC_STATUSES and heartbeat and now - heartbeat < _SYNC_LEASE:
                return None  # Even force must not overlap a live writer.
            if only_if_due and state.status not in _ACTIVE_SYNC_STATUSES and state.completed_at and now - state.completed_at < timedelta(hours=6):
                return None
            # Resume only the same frozen date range. A later independent
            # request must never inherit completed windows from another scope.
            if (
                state.status in (*_ACTIVE_SYNC_STATUSES, "failed")
                and state.start_date is not None and state.end_date is not None
                and state.start_date == changes.get("start_date")
                and state.end_date == changes.get("end_date")
                and int(state.total_windows or 0) == int(changes.get("total_windows") or 0)
            ):
                for field in ("progress", "completed_windows", "cursor_offset", "scanned_rows", "saved_rows"):
                    changes[field] = int(getattr(state, field) or 0)
                changes["message"] = "从已保存的研报窗口与分页断点继续同步"
            claimed = session.execute(
                update(ResearchReportSyncState).where(
                    ResearchReportSyncState.source_key == _SOURCE_KEY,
                    ResearchReportSyncState.updated_at == state.updated_at,
                    ResearchReportSyncState.started_at == state.started_at,
                ).values(**changes).execution_options(synchronize_session=False)
            ).rowcount
            session.commit()
            return now if claimed else None

    def status(self) -> Dict[str, Any]:
        with self.db.get_session() as session:
            state = session.get(ResearchReportSyncState, _SOURCE_KEY)
            total, earliest, latest, pdf_count = session.execute(select(
                func.count(ResearchReportRecord.id),
                func.min(ResearchReportRecord.trade_date),
                func.max(ResearchReportRecord.trade_date),
                func.sum(case((ResearchReportRecord.pdf_url.is_not(None), 1), else_=0)),
            )).one()
        payload = {
            "status": state.status if state else "idle",
            "progress": int(state.progress or 0) if state else 0,
            "message": state.message if state else "等待首次同步",
            "start_date": self._date_text(state.start_date) if state else None,
            "end_date": self._date_text(state.end_date) if state else None,
            "total_windows": int(state.total_windows or 0) if state else 0,
            "completed_windows": int(state.completed_windows or 0) if state else 0,
            "cursor_offset": int(state.cursor_offset or 0) if state else 0,
            "scanned_rows": int(state.scanned_rows or 0) if state else 0,
            "saved_rows": int(state.saved_rows or 0) if state else 0,
            "last_error": state.last_error if state else None,
            "started_at": self._datetime_text(state.started_at) if state else None,
            "completed_at": self._datetime_text(state.completed_at) if state else None,
            "updated_at": self._datetime_text(state.updated_at) if state else None,
            "total": int(total or 0),
            "pdf_count": int(pdf_count or 0),
            "earliest_trade_date": self._date_text(earliest),
            "latest_trade_date": self._date_text(latest),
            "source": _SOURCE_KEY,
            "search_mode": "local_sqlite_only",
        }
        return payload

    def facets(self) -> Dict[str, Any]:
        return {
            "brokers": self._facet(ResearchReportRecord.broker, 200),
            "report_types": self._facet(ResearchReportRecord.report_type, 50),
            "industries": self._facet(ResearchReportRecord.industry, 300),
            "companies": self._facet(ResearchReportRecord.company_name, 500),
            "tags": self._tag_facets(),
        }

    def search(self, **filters: Any) -> Dict[str, Any]:
        page = max(1, int(filters.get("page") or 1))
        page_size = max(1, min(int(filters.get("page_size") or 30), 100))
        conditions = []
        self._append_text_conditions(conditions, ResearchReportRecord.title, filters.get("title_query"))
        self._append_text_conditions(conditions, ResearchReportRecord.abstract, filters.get("content_query"))
        for key, column in (
            ("broker", ResearchReportRecord.broker),
            ("report_type", ResearchReportRecord.report_type),
            ("industry", ResearchReportRecord.industry),
            ("ts_code", ResearchReportRecord.ts_code),
        ):
            value = str(filters.get(key) or "").strip()
            if value:
                conditions.append(column == value)
        for key, columns in (
            ("company", (ResearchReportRecord.company_name, ResearchReportRecord.ts_code)),
            ("author", (ResearchReportRecord.author,)),
        ):
            value = str(filters.get(key) or "").strip()
            if value:
                pattern = self._like(value)
                conditions.append(or_(*(column.like(pattern, escape="\\") for column in columns)))
        tag = str(filters.get("tag") or "").strip()
        if tag:
            conditions.append(ResearchReportRecord.tags_json.like(self._like(f'"{tag}"'), escape="\\"))
        start = self._parse_date(filters.get("start_date"))
        end = self._parse_date(filters.get("end_date"))
        if start:
            conditions.append(ResearchReportRecord.trade_date >= start)
        if end:
            conditions.append(ResearchReportRecord.trade_date <= end)
        if self._as_bool(filters.get("has_pdf"), default=True):
            conditions.extend((ResearchReportRecord.pdf_url.is_not(None), ResearchReportRecord.pdf_url != ""))
        where = and_(*conditions) if conditions else True
        order = asc(ResearchReportRecord.trade_date) if filters.get("sort") == "oldest" else desc(ResearchReportRecord.trade_date)
        with self.db.get_session() as session:
            total = session.execute(select(func.count(ResearchReportRecord.id)).where(where)).scalar() or 0
            rows = session.execute(
                select(ResearchReportRecord).where(where).order_by(order, desc(ResearchReportRecord.id))
                .offset((page - 1) * page_size).limit(page_size)
            ).scalars().all()
        return {
            "items": [self._serialize(row) for row in rows],
            "total": int(total),
            "page": page,
            "page_size": page_size,
            "source": "local_sqlite",
        }

    def export_selected(self, ids: Iterable[int]) -> bytes:
        selected_ids = list(dict.fromkeys(int(value) for value in ids))
        if not selected_ids:
            raise ResearchReportLibraryError("请至少选择一篇研报")
        if len(selected_ids) > 1000:
            raise ResearchReportLibraryError("一次最多导出1000篇研报，请分批选择")
        with self.db.get_session() as session:
            rows = session.execute(
                select(ResearchReportRecord).where(ResearchReportRecord.id.in_(selected_ids))
                .order_by(desc(ResearchReportRecord.trade_date), desc(ResearchReportRecord.id))
            ).scalars().all()
        missing_count = len(selected_ids) - len(rows)
        if missing_count:
            raise ResearchReportLibraryError(
                f"已选研报中有{missing_count}篇已不可用，未生成不完整的导出文件；请重新检索并移除不可用条目"
            )
        workbook = Workbook()
        sheet = workbook.active
        sheet.title = "已选研报"
        headers = ["日期", "标题", "摘要", "研报类型", "券商", "作者", "公司", "股票代码", "行业", "人工筛选标签", "PDF链接"]
        sheet.append(headers)
        for cell in sheet[1]:
            cell.font = Font(bold=True)
        for row in rows:
            values = self._serialize(row)
            sheet.append([
                values["trade_date"], values["title"], values["abstract"], values["report_type"],
                values["broker"], values["author"], values["company_name"], values["ts_code"],
                values["industry"], " / ".join(values["tags"]), values["pdf_url"],
            ])
            # Provider-authored text must remain text, not executable spreadsheet formulas.
            for cell in sheet[sheet.max_row]:
                if isinstance(cell.value, str):
                    cell.data_type = "s"
            if values["pdf_url"]:
                cell = sheet.cell(row=sheet.max_row, column=11)
                cell.hyperlink = values["pdf_url"]
                cell.style = "Hyperlink"
        sheet.freeze_panes = "A2"
        sheet.auto_filter.ref = sheet.dimensions
        for column, width in {"A": 13, "B": 55, "C": 70, "D": 14, "E": 16, "F": 20, "G": 18, "H": 14, "I": 16, "J": 30, "K": 50}.items():
            sheet.column_dimensions[column].width = width
        output = BytesIO()
        workbook.save(output)
        return output.getvalue()

    def _run_sync(
        self, start: date, end: date, windows: List[tuple[date, date]],
        *, claim_started_at: Optional[datetime] = None,
    ) -> None:
        scanned = 0
        saved = 0
        stop_heartbeat = threading.Event()
        heartbeat_thread = None

        def publish(**changes: Any) -> None:
            if not self._write_state(_claim_started_at=claim_started_at, **changes):
                raise _ReportSyncLeaseLost()

        def heartbeat() -> None:
            while not stop_heartbeat.wait(_SYNC_HEARTBEAT_SECONDS):
                try:
                    if not self._write_state(_claim_started_at=claim_started_at):
                        return
                except Exception as exc:  # retry transient SQLite contention, without resetting task state
                    logger.warning("Research report heartbeat failed: %s", type(exc).__name__)

        try:
            current = self.status()
            same_scope = (
                current.get("start_date") == start.isoformat()
                and current.get("end_date") == end.isoformat()
                and int(current.get("total_windows") or 0) == len(windows)
            )
            completed_windows = int(current.get("completed_windows") or 0) if same_scope else 0
            cursor_offset = int(current.get("cursor_offset") or 0) if same_scope else 0
            scanned = int(current.get("scanned_rows") or 0) if same_scope else 0
            saved = int(current.get("saved_rows") or 0) if same_scope else 0
            if not 0 <= completed_windows <= len(windows) or cursor_offset < 0 or cursor_offset >= 50_000:
                raise ResearchReportLibraryError("研报分页断点无效，保留数据等待检查")
            if claim_started_at is not None:
                heartbeat_thread = threading.Thread(target=heartbeat, name="report-library-heartbeat", daemon=True)
                heartbeat_thread.start()
            publish(status="running", progress=max(1, int(current.get("progress") or 0)) if same_scope else 1,
                    message="正在从 Tushare 分页同步研报元数据和 PDF 链接")
            for index, (window_start, window_end) in enumerate(windows[completed_windows:], start=completed_windows + 1):
                offset = cursor_offset if index == completed_windows + 1 else 0
                while True:
                    result = self.financial.query(
                        source="tushare",
                        resource="research_report",
                        params={
                            "start_date": window_start.strftime("%Y%m%d"),
                            "end_date": window_end.strftime("%Y%m%d"),
                            "limit": 1000,
                            "offset": offset,
                        },
                        fields=_FIELDS,
                    )
                    if not isinstance(result, dict) or result.get("error") or not isinstance(result.get("rows"), list):
                        raise ResearchReportLibraryError("研报接口没有返回有效分页，保留当前断点等待重试")
                    rows = result["rows"]
                    finished_window = len(rows) < 1000
                    next_offset = 0 if finished_window else offset + 1000
                    if next_offset >= 50_000:
                        raise ResearchReportLibraryError("单个日期窗口超过安全分页上限，请缩短同步窗口")
                    next_scanned = scanned + len(rows)
                    checkpoint = {
                        "completed_windows": index if finished_window else index - 1,
                        "cursor_offset": next_offset,
                        "scanned_rows": next_scanned,
                        "progress": min(99, max(1, round((index if finished_window else index - 1) / max(1, len(windows)) * 99))),
                        "message": f"研报链接同步 {index}/{len(windows)} · 已扫描 {next_scanned:,} 条 · 本窗偏移 {next_offset}",
                    }
                    # Data and its next cursor commit together. A crash before
                    # commit retries this page; a crash after commit resumes the
                    # next page, with no window completion inferred from counts.
                    changed = self._upsert(rows, _claim_started_at=claim_started_at,
                                           _checkpoint=checkpoint, _saved_before=saved)
                    scanned = next_scanned
                    saved += changed
                    if finished_window:
                        break
                    offset = next_offset
            publish(
                status="completed",
                progress=100,
                completed_windows=len(windows),
                cursor_offset=0,
                scanned_rows=scanned,
                saved_rows=saved,
                message=f"研报库同步完成：扫描 {scanned:,} 条，新增或更新 {saved:,} 条",
                completed_at=utc_naive_now(),
                last_error=None,
            )
        except _ReportSyncLeaseLost:
            logger.info("Research report sync stopped after its lease was replaced")
        except Exception as exc:  # noqa: BLE001 - failure must become durable status.
            logger.exception("Research report library sync failed")
            self._write_state(
                _claim_started_at=claim_started_at,
                status="failed",
                progress=min(99, int(self.status().get("progress") or 0)),
                scanned_rows=scanned,
                saved_rows=saved,
                message=f"研报链接同步失败：{type(exc).__name__}",
                last_error=str(exc)[:500],
                completed_at=utc_naive_now(),
            )
        finally:
            stop_heartbeat.set()
            if heartbeat_thread is not None and heartbeat_thread.ident is not None:
                heartbeat_thread.join(timeout=1)

    def _upsert(
        self, raw_rows: List[Dict[str, Any]], *, _claim_started_at: Optional[datetime] = None,
        _checkpoint: Optional[Dict[str, Any]] = None, _saved_before: int = 0,
    ) -> int:
        normalized = [self._normalize(row) for row in raw_rows]
        normalized = [row for row in normalized if row is not None]
        if not normalized and _checkpoint is None:
            return 0
        keys = [row["report_key"] for row in normalized]
        changed = 0
        with self.db.get_session() as session:
            if _claim_started_at is not None:
                # Fence the data write in the same SQLite transaction as the
                # ownership check, so a replaced task cannot insert old rows.
                owned = session.execute(update(ResearchReportSyncState).where(
                    ResearchReportSyncState.source_key == _SOURCE_KEY,
                    ResearchReportSyncState.started_at == _claim_started_at,
                    ResearchReportSyncState.status.in_(_ACTIVE_SYNC_STATUSES),
                ).values(updated_at=utc_naive_now())).rowcount
                if not owned:
                    raise _ReportSyncLeaseLost()
            existing = session.execute(
                select(ResearchReportRecord).where(ResearchReportRecord.report_key.in_(keys))
            ).scalars().all()
            by_key = {row.report_key: row for row in existing}
            for fields in normalized:
                record = by_key.get(fields["report_key"])
                if record is None:
                    record = ResearchReportRecord(**fields)
                    session.add(record)
                    by_key[fields["report_key"]] = record
                    changed += 1
                    continue
                if all(getattr(record, key) == value for key, value in fields.items() if key not in {"synced_at", "report_key"}):
                    continue
                for key, value in fields.items():
                    if key != "report_key":
                        setattr(record, key, value)
                changed += 1
            if _checkpoint is not None:
                statement = update(ResearchReportSyncState).where(ResearchReportSyncState.source_key == _SOURCE_KEY)
                if _claim_started_at is not None:
                    statement = statement.where(
                        ResearchReportSyncState.started_at == _claim_started_at,
                        ResearchReportSyncState.status.in_(_ACTIVE_SYNC_STATUSES),
                    )
                owned = session.execute(statement.values(
                    **_checkpoint, saved_rows=_saved_before + changed, updated_at=utc_naive_now(),
                )).rowcount
                if not owned:
                    raise _ReportSyncLeaseLost()
            session.commit()
        return changed

    @staticmethod
    def _normalize(row: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        title = str(row.get("title") or "").strip()
        trade_date = ResearchReportLibraryService._parse_date(row.get("trade_date"))
        if not title or trade_date is None:
            return None
        pdf_url = str(row.get("url") or "").strip()
        report_key = sha256(
            json.dumps([trade_date.isoformat(), title, pdf_url], ensure_ascii=False).encode("utf-8")
        ).hexdigest()
        report_type = str(row.get("report_type") or "").strip()
        broker = str(row.get("inst_csname") or "").strip()
        industry = str(row.get("ind_name") or "").strip()
        company = str(row.get("name") or "").strip()
        tags = [value for value in (report_type, industry, broker, company) if value]
        if any(term in title for term in _DEEP_TERMS):
            tags.append("深度研究")
        if any(term in title for term in _QUICK_TERMS):
            tags.append("快评跟踪")
        if pdf_url:
            tags.append("有PDF")
        return {
            "report_key": report_key,
            "trade_date": trade_date,
            "title": title[:800],
            "abstract": str(row.get("abstr") or "").strip(),
            "report_type": report_type[:80] or None,
            "author": str(row.get("author") or "").strip()[:300] or None,
            "company_name": company[:160] or None,
            "ts_code": str(row.get("ts_code") or "").strip()[:20] or None,
            "broker": broker[:160] or None,
            "industry": industry[:160] or None,
            "pdf_url": pdf_url or None,
            "tags_json": json.dumps(list(dict.fromkeys(tags)), ensure_ascii=False),
            "source": _SOURCE_KEY,
            "synced_at": utc_naive_now(),
        }

    def _write_state(self, *, _claim_started_at: Optional[datetime] = None, **changes: Any) -> bool:
        with self.db.get_session() as session:
            if _claim_started_at is not None:
                changed = session.execute(update(ResearchReportSyncState).where(
                    ResearchReportSyncState.source_key == _SOURCE_KEY,
                    ResearchReportSyncState.started_at == _claim_started_at,
                    ResearchReportSyncState.status.in_(_ACTIVE_SYNC_STATUSES),
                ).values(**{**changes, "updated_at": utc_naive_now()})).rowcount
                session.commit()
                return bool(changed)
            state = session.get(ResearchReportSyncState, _SOURCE_KEY)
            if state is None:
                state = ResearchReportSyncState(source_key=_SOURCE_KEY)
                session.add(state)
            for key, value in changes.items():
                if hasattr(state, key):
                    setattr(state, key, value)
            state.updated_at = utc_naive_now()
            session.commit()
            return True

    def _facet(self, column, limit: int) -> List[Dict[str, Any]]:
        with self.db.get_session() as session:
            rows = session.execute(
                select(column, func.count(ResearchReportRecord.id))
                .where(column.is_not(None), column != "")
                .group_by(column).order_by(desc(func.count(ResearchReportRecord.id)), asc(column)).limit(limit)
            ).all()
        return [{"value": str(value), "count": int(count)} for value, count in rows]

    def _tag_facets(self) -> List[Dict[str, Any]]:
        counts: Dict[str, int] = {}
        with self.db.get_session() as session:
            rows = session.execute(select(ResearchReportRecord.tags_json)).scalars().all()
        for raw in rows:
            try:
                values = json.loads(raw or "[]")
            except (TypeError, ValueError):
                values = []
            for value in values:
                counts[str(value)] = counts.get(str(value), 0) + 1
        preferred = {"深度研究", "快评跟踪", "有PDF", "个股研报", "行业研报", "策略研报"}
        items = [(key, value) for key, value in counts.items() if key in preferred]
        return [{"value": key, "count": value} for key, value in sorted(items, key=lambda item: (-item[1], item[0]))]

    @staticmethod
    def _append_text_conditions(conditions: List[Any], column, value: Any) -> None:
        for term in str(value or "").split():
            conditions.append(column.like(ResearchReportLibraryService._like(term), escape="\\"))

    @staticmethod
    def _like(value: str) -> str:
        escaped = str(value).replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        return f"%{escaped}%"

    @staticmethod
    def _date_windows(start: date, end: date) -> List[tuple[date, date]]:
        windows: List[tuple[date, date]] = []
        cursor = start
        while cursor <= end:
            window_end = min(end, cursor + timedelta(days=13))
            windows.append((cursor, window_end))
            cursor = window_end + timedelta(days=1)
        return windows

    @staticmethod
    def _parse_date(value: Any) -> Optional[date]:
        if isinstance(value, datetime):
            return value.date()
        if isinstance(value, date):
            return value
        raw = str(value or "").strip()
        if not raw:
            return None
        for fmt in ("%Y-%m-%d", "%Y%m%d"):
            try:
                return datetime.strptime(raw, fmt).date()
            except ValueError:
                continue
        return None

    @staticmethod
    def _parse_datetime(value: Any) -> Optional[datetime]:
        if isinstance(value, datetime):
            return value
        raw = str(value or "").strip()
        if not raw:
            return None
        try:
            return datetime.fromisoformat(raw.replace("Z", "+00:00")).replace(tzinfo=None)
        except ValueError:
            return None

    @staticmethod
    def _as_bool(value: Any, *, default: bool) -> bool:
        if value in (None, ""):
            return default
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() not in {"0", "false", "no", "off"}

    @staticmethod
    def _serialize(row: ResearchReportRecord) -> Dict[str, Any]:
        try:
            tags = json.loads(row.tags_json or "[]")
        except (TypeError, ValueError):
            tags = []
        return {
            "id": row.id,
            "trade_date": row.trade_date.isoformat() if row.trade_date else None,
            "title": row.title,
            "abstract": row.abstract or "",
            "report_type": row.report_type,
            "author": row.author,
            "company_name": row.company_name,
            "ts_code": row.ts_code,
            "broker": row.broker,
            "industry": row.industry,
            "pdf_url": row.pdf_url,
            "tags": tags if isinstance(tags, list) else [],
            "synced_at": ResearchReportLibraryService._datetime_text(row.synced_at),
        }

    @staticmethod
    def _date_text(value: Any) -> Optional[str]:
        return value.isoformat() if isinstance(value, date) else None

    @staticmethod
    def _datetime_text(value: Any) -> Optional[str]:
        return f"{value.isoformat()}Z" if isinstance(value, datetime) else None
