# -*- coding: utf-8 -*-
"""Model-planned, auditable multi-source financial-data acquisition packages."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone
import csv
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
from typing import Any, Dict, List, Optional
from urllib.parse import unquote, urlparse
import zipfile

from json_repair import repair_json
from openpyxl import Workbook
import requests

from src.config import get_config
from src.services.essay_analysis_service import DEFAULT_ESSAY_MODEL, EssayDailyReportService
from src.services.cninfo_announcement_service import CninfoAnnouncementService
from src.services.financial_data_service import FinancialDataService, FinancialDataValidationError
from src.services.investment_monitor_service import InvestmentMonitorService

logger = logging.getLogger(__name__)

MAX_TASKS = 12
MAX_ROWS_PER_DATASET = 2000
_RESOURCE_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")

# The planner sees the most useful productized endpoints. The executor still supports
# every syntactically valid Tushare api_name so the user's full Pro entitlement remains callable.
TUSHARE_CATALOG = {
    "daily": "A股日线行情", "daily_basic": "每日估值与换手", "adj_factor": "复权因子",
    "stk_factor": "技术因子", "moneyflow": "个股资金流", "moneyflow_hsgt": "沪深港通资金",
    "margin_detail": "融资融券明细", "hk_hold": "沪深港通持股", "cyq_perf": "筹码胜率与成本",
    "cyq_chips": "筹码分布", "income": "利润表", "balancesheet": "资产负债表",
    "cashflow": "现金流量表", "fina_indicator": "财务指标", "forecast": "业绩预告",
    "express": "业绩快报", "dividend": "分红送股", "fina_audit": "财务审计意见",
    "pledge_stat": "股权质押", "share_float": "限售股解禁", "stk_holdertrade": "股东增减持",
    "repurchase": "股份回购", "top10_holders": "十大股东", "top10_floatholders": "十大流通股东",
    "block_trade": "大宗交易", "top_list": "龙虎榜", "top_inst": "龙虎榜机构明细",
    "concept": "概念分类", "concept_detail": "概念成分", "index_daily": "指数日线",
    "index_weight": "指数成分权重", "ths_hot": "同花顺热榜", "dc_hot": "东方财富热榜",
    "research_report": "可下载券商研报（个股/行业/策略）", "report_rc": "卖方盈利预测与研报索引",
    "broker_recommend": "券商月度金股", "major_news": "长篇财经新闻",
    "news": "实时快讯", "cctv_news": "新闻联播", "anns_d": "公告数据",
    "trade_cal": "交易日历", "stock_basic": "上市公司基础资料", "namechange": "证券曾用名",
    "new_share": "新股上市", "bak_basic": "备用基础行情", "stk_limit": "涨跌停价格",
    "limit_list_d": "涨跌停榜单", "suspend_d": "停复牌", "disclosure_date": "财报披露计划",
    "moneyflow_cnt_ths": "同花顺概念资金流", "moneyflow_ind_ths": "同花顺行业资金流",
    "limit_list_ths": "同花顺涨停/炸板/跌停池", "margin": "全市场融资融券汇总",
    "stk_nineturn": "神奇九转", "stk_managers": "上市公司管理层名录",
}

TYC_COMMANDS = {
    "company_full": [],
    "registration": ["company", "registration-info"],
    "company_search": ["company", "companies"],
    "company_profile": ["company", "profile"],
    "company_financials": ["company", "financial-data"],
    "shareholders": ["company", "shareholder-info"],
    "actual_controller": ["company", "actual-controller"],
    "risk_overview": ["risk", "overview"],
    "operation_credit": ["operation", "credit-evaluation"],
    "ipr_score": ["intellectual_property", "ipr-score"],
    "historical_overview": ["history", "historical-overview"],
}

TYC_FULL_COMMANDS = {
    "registration": ["company", "registration-info"],
    "profile": ["company", "profile"],
    "listing": ["company", "listing-info"],
    "financials": ["company", "financial-data"],
    "annual_reports": ["company", "annual-reports"],
    "shareholders": ["company", "shareholder-info"],
    "actual_controller": ["company", "actual-controller"],
    "beneficial_owners": ["company", "beneficial-owners"],
    "external_investments": ["company", "external-investments"],
    "key_personnel": ["company", "key-personnel"],
    "relation_graph": ["company", "relation-graph"],
    "risk_overview": ["risk", "overview"],
    "judicial_cases": ["risk", "judicial-case"],
    "administrative_penalties": ["risk", "administrative-penalty"],
    "business_exceptions": ["risk", "business-exception"],
    "credit": ["operation", "credit-evaluation"],
    "news_sentiment": ["operation", "news-sentiment"],
    "bidding": ["operation", "bidding-info"],
    "suppliers_customers": ["operation", "suppliers-and-customers"],
    "products": ["operation", "products-info"],
    "ipr_score": ["intellectual_property", "ipr-score"],
    "patents": ["intellectual_property", "patent-info"],
    "trademarks": ["intellectual_property", "trademark-info"],
    "historical_overview": ["history", "historical-overview"],
}

_FILE_REQUEST_RE = re.compile(r"(?:下载|打包|附上|包含|提供).{0,8}(?:文件|附件|PDF|原文)|(?:文件|附件|PDF).{0,8}(?:下载|打包|一并)", re.I)
MAX_DOWNLOAD_FILES = 100
MAX_FILE_BYTES = 50 * 1024 * 1024
MAX_PACKAGE_FILE_BYTES = 500 * 1024 * 1024

PLANNER_SYSTEM = """你是财经数据产品的数据调度规划器。把用户需求转成严格 JSON，不回答投资建议。
只能使用 capability_catalog 中的数据源和资源。只获取用户明确要求的数据维度，不得因为目录中存在其他能力就一并添加；用户要求“只/仅/不要”时必须严格排除未要求渠道。多维综合请求再覆盖行情、财务、资金、公告、新闻、机构观点、知识星球和工商风险中真正相关的维度，避免重复。
股票代码必须使用 Tushare 格式，如 603306.SH、300476.SZ。日期参数使用 YYYYMMDD；本地库日期使用 YYYY-MM-DD。
公告必须使用 cninfo.announcements 现场调用巨潮接口重新检索，不得使用本地公告缓存。Tushare 数据必须按维度直接调用对应 api_name；研报 PDF 库使用 research_report，盈利预测使用 report_rc，新闻使用 news/major_news，不能用 monitor.events 代替。不要向新闻接口传 ts_code。
知识星球使用 created_from、created_to、query 或 symbol；天眼查的 company_name 可以是单个名称或名称数组。
必须输出全局 scope：symbols、company_names、keywords、start_date、end_date、market_wide。除非用户明确要求全市场，market_wide 必须为 false。
每个任务必须包含 source、resource、label、reason、params、fields。params 只能包含该渠道实际查询参数，不能用空参数拉全量。最多12个任务。
天眼查要求“全面/全景/尽调”时使用 tianyancha.company_full；它会现场覆盖工商、股权、实控人、财务、司法风险、经营、知识产权和历史信息。
若用户要求下载、文件、附件、PDF或原文文件，include_files 必须为 true；否则为 false。
输出结构：{"title":"...","objective":"...","scope":{"symbols":[],"company_names":[],"keywords":[],"start_date":"","end_date":"","market_wide":false},"tasks":[...],"include_files":false,"output_formats":["json","csv","xlsx","zip"],"caveats":[]}。
不得输出 Markdown 或 JSON 外文字。"""


class DataAcquisitionError(RuntimeError):
    """Safe error surfaced by the acquisition workbench."""


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    if hasattr(value, "item"):
        try:
            return _json_safe(value.item())
        except (TypeError, ValueError):
            pass
    return str(value)


class DataAcquisitionPlanner:
    def __init__(self, *, session: Optional[requests.Session] = None):
        config = get_config()
        keys = list(getattr(config, "deepseek_api_keys", None) or [])
        self.api_key = str((keys[0] if keys else "") or "").strip()
        self.base_url = str(os.getenv("DATA_ACQUISITION_LLM_BASE_URL") or "https://api.deepseek.com").rstrip("/")
        self.model = str(
            os.getenv("DATA_ACQUISITION_LLM_MODEL")
            or os.getenv("ESSAY_ANALYSIS_MODEL")
            or DEFAULT_ESSAY_MODEL
        ).strip()
        self.timeout = max(10, min(int(os.getenv("DATA_ACQUISITION_LLM_TIMEOUT_SEC", "120")), 300))
        self.session = session or requests.Session()

    def plan(self, request_text: str) -> Dict[str, Any]:
        if not self.api_key:
            raise DataAcquisitionError("DeepSeek API key is not configured")
        catalog = {
            "tushare": TUSHARE_CATALOG,
            "zsxq": {"research_notes": "知识星球 MCP 增量同步后的本地调研纪要（含图片和附件链接）"},
            "cninfo": {"announcements": "巨潮官网公告现场检索（可下载PDF）"},
            "monitor": {"events": "统一投资情报事件流（仅用于其他情报，不代替公告、新闻或研报官方接口）"},
            "tianyancha": {key: key for key in TYC_COMMANDS},
        }
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": PLANNER_SYSTEM},
                {"role": "user", "content": json.dumps({
                    "today": datetime.now(timezone(timedelta(hours=8))).date().isoformat(),
                    "request": request_text,
                    "capability_catalog": catalog,
                }, ensure_ascii=False)},
            ],
            "response_format": {"type": "json_object"},
            "thinking": {"type": "disabled"},
            "temperature": 0.1,
            "max_tokens": 6000,
            "stream": False,
        }
        try:
            response = self.session.post(
                f"{self.base_url}/chat/completions",
                headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
                json=payload, timeout=self.timeout,
            )
            response.raise_for_status()
            body = response.json()
            content = body["choices"][0]["message"]["content"]
            parsed = json.loads(repair_json(content))
        except Exception as exc:
            logger.warning("Data acquisition planning failed: %s", type(exc).__name__)
            raise DataAcquisitionError("大模型暂时无法生成取数计划，请稍后重试") from exc
        return self._normalize(parsed, request_text)

    def _normalize(self, raw: Dict[str, Any], request_text: str) -> Dict[str, Any]:
        tasks = []
        for index, item in enumerate(raw.get("tasks") or []):
            if not isinstance(item, dict):
                continue
            source = str(item.get("source") or "").strip().lower()
            resource = str(item.get("resource") or "").strip().lower()
            if not DataAcquisitionService.is_supported(source, resource):
                continue
            fields = item.get("fields") if isinstance(item.get("fields"), list) else []
            tasks.append({
                "id": f"task-{index + 1}", "source": source, "resource": resource,
                "label": str(item.get("label") or TUSHARE_CATALOG.get(resource) or resource)[:80],
                "reason": str(item.get("reason") or "")[:300],
                "params": item.get("params") if isinstance(item.get("params"), dict) else {},
                "fields": [str(value) for value in fields[:100]],
            })
            if len(tasks) >= MAX_TASKS:
                break
        if not tasks:
            raise DataAcquisitionError("大模型没有生成可执行的数据任务，请补充股票、日期或数据类型")
        scope = DataAcquisitionService.normalize_scope(raw.get("scope"), tasks, request_text)
        return {
            "title": str(raw.get("title") or "一站式财经数据包")[:120],
            "objective": str(raw.get("objective") or request_text)[:1000],
            "tasks": tasks,
            "scope": scope,
            "include_files": bool(raw.get("include_files")) or bool(_FILE_REQUEST_RE.search(request_text)),
            "output_formats": ["json", "csv", "xlsx", "zip"],
            "caveats": [str(value)[:300] for value in (raw.get("caveats") or [])[:10]],
            "model": self.model,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }


class DataAcquisitionService:
    def __init__(self, *, planner: Optional[DataAcquisitionPlanner] = None,
                 financial: Optional[FinancialDataService] = None,
                 monitor: Optional[InvestmentMonitorService] = None,
                 cninfo: Optional[CninfoAnnouncementService] = None,
                 output_root: Optional[Path] = None):
        self.planner = planner or DataAcquisitionPlanner()
        self.financial = financial or FinancialDataService()
        self.monitor = monitor or InvestmentMonitorService()
        self.cninfo = cninfo or CninfoAnnouncementService()
        config = get_config()
        default_root = Path(config.database_path).expanduser().resolve().parent / "data_acquisition"
        self.output_root = Path(output_root or os.getenv("DATA_ACQUISITION_OUTPUT_DIR") or default_root).resolve()
        self.output_root.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def is_supported(source: str, resource: str) -> bool:
        if source == "tushare":
            return bool(_RESOURCE_RE.fullmatch(resource))
        if source == "zsxq":
            return resource == "research_notes"
        if source == "monitor":
            return resource == "events"
        if source == "cninfo":
            return resource == "announcements"
        return source == "tianyancha" and resource in TYC_COMMANDS

    def capabilities(self) -> Dict[str, Any]:
        return {
            "sources": [
                {"key": "tushare", "name": "Tushare Pro", "mode": "live", "available": self.financial.tushare.available,
                 "resources": [{"key": key, "name": value} for key, value in TUSHARE_CATALOG.items()],
                 "scope": "支持已授权的全部 Tushare api_name"},
                {"key": "zsxq", "name": "知识星球", "mode": "MCP 增量同步 + SQLite", "available": True,
                 "resources": [{"key": "research_notes", "name": "调研纪要、图片与附件索引"}]},
                {"key": "cninfo", "name": "巨潮资讯", "mode": "官方接口现场检索", "available": True,
                 "resources": [{"key": "announcements", "name": "上市公司公告与PDF原文"}]},
                {"key": "monitor", "name": "统一情报", "mode": "SQLite", "available": True,
                 "resources": [{"key": "events", "name": "其他多渠道情报事件"}]},
                {"key": "tianyancha", "name": "天眼查", "mode": "官方 MCP CLI 现场查询", "available": shutil.which("tyc") is not None,
                 "resources": [{"key": key, "name": key.replace("_", " ")} for key in TYC_COMMANDS]},
            ],
            "planner": {"available": bool(self.planner.api_key), "model": self.planner.model,
                        "max_tasks": MAX_TASKS, "max_rows_per_dataset": MAX_ROWS_PER_DATASET},
        }

    def plan(self, request_text: str) -> Dict[str, Any]:
        return self.planner.plan(str(request_text or "").strip())

    def run(self, request_text: str, plan: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        normalized_plan = self._validate_plan(plan or self.plan(request_text), request_text)
        now = datetime.now(timezone.utc)
        digest = hashlib.sha256(f"{now.isoformat()}:{request_text}".encode()).hexdigest()[:10]
        job_id = f"{now.strftime('%Y%m%dT%H%M%SZ')}-{digest}"
        final_dir = self.output_root / job_id
        if final_dir.exists():
            raise DataAcquisitionError("数据任务编号冲突，请重试")
        temp_dir = Path(tempfile.mkdtemp(prefix=f".{job_id}-", dir=self.output_root))
        datasets: List[Dict[str, Any]] = []
        try:
            for task in normalized_plan["tasks"]:
                try:
                    rows = self._execute_task(task, normalized_plan["scope"])
                    datasets.append({"task": task, "status": "success", "rows": rows[:MAX_ROWS_PER_DATASET],
                                     "row_count": len(rows[:MAX_ROWS_PER_DATASET]), "error": None,
                                     "applied_scope": normalized_plan["scope"]})
                except Exception as exc:  # One source must not invalidate an otherwise useful package.
                    logger.warning("Acquisition task failed source=%s resource=%s error=%s",
                                   task["source"], task["resource"], type(exc).__name__)
                    datasets.append({"task": task, "status": "failed", "rows": [], "row_count": 0,
                                     "error": self._safe_error(exc), "applied_scope": normalized_plan["scope"]})
            manifest = self._write_artifacts(temp_dir, job_id, request_text, normalized_plan, datasets)
            temp_dir.rename(final_dir)
            return manifest
        except Exception:
            shutil.rmtree(temp_dir, ignore_errors=True)
            raise

    def list_jobs(self, limit: int = 20) -> Dict[str, Any]:
        jobs = []
        for path in sorted(self.output_root.glob("*/manifest.json"), reverse=True)[:max(1, min(limit, 100))]:
            try:
                jobs.append(json.loads(path.read_text(encoding="utf-8")))
            except (OSError, ValueError):
                continue
        return {"items": jobs, "total": len(jobs)}

    def get_job(self, job_id: str) -> Dict[str, Any]:
        path = self._job_path(job_id) / "manifest.json"
        if not path.is_file():
            raise DataAcquisitionError("数据包不存在")
        return json.loads(path.read_text(encoding="utf-8"))

    def package_path(self, job_id: str) -> Path:
        path = self._job_path(job_id) / f"{job_id}.zip"
        if not path.is_file():
            raise DataAcquisitionError("数据包不存在")
        return path

    def _job_path(self, job_id: str) -> Path:
        if not re.fullmatch(r"[A-Za-z0-9_-]{8,80}", job_id or ""):
            raise DataAcquisitionError("无效的数据包编号")
        path = (self.output_root / job_id).resolve()
        if self.output_root not in path.parents:
            raise DataAcquisitionError("无效的数据包路径")
        return path

    def _validate_plan(self, plan: Dict[str, Any], request_text: str) -> Dict[str, Any]:
        if not isinstance(plan, dict):
            raise DataAcquisitionError("取数计划必须是对象")
        tasks = []
        for index, raw in enumerate((plan.get("tasks") or [])[:MAX_TASKS]):
            if not isinstance(raw, dict):
                continue
            source = str(raw.get("source") or "").strip().lower()
            resource = str(raw.get("resource") or "").strip().lower()
            if not self.is_supported(source, resource):
                raise DataAcquisitionError(f"不支持的数据任务：{source}.{resource}")
            params = raw.get("params") if isinstance(raw.get("params"), dict) else {}
            fields = raw.get("fields") if isinstance(raw.get("fields"), list) else []
            tasks.append({"id": str(raw.get("id") or f"task-{index + 1}")[:50], "source": source,
                          "resource": resource, "label": str(raw.get("label") or resource)[:80],
                          "reason": str(raw.get("reason") or "")[:300], "params": _json_safe(params),
                          "fields": [str(value) for value in fields[:100]]})
        if not tasks:
            raise DataAcquisitionError("取数计划没有可执行任务")
        scope = self.normalize_scope(plan.get("scope"), tasks, request_text)
        return {"title": str(plan.get("title") or "一站式财经数据包")[:120],
                "objective": str(plan.get("objective") or request_text)[:1000], "tasks": tasks,
                "scope": scope,
                "include_files": bool(plan.get("include_files")) or bool(_FILE_REQUEST_RE.search(request_text)),
                "output_formats": ["json", "csv", "xlsx", "zip"],
                "caveats": [str(value)[:300] for value in (plan.get("caveats") or [])[:10]],
                "model": str(plan.get("model") or self.planner.model)[:100],
                "generated_at": str(plan.get("generated_at") or datetime.now(timezone.utc).isoformat())}

    @staticmethod
    def normalize_scope(raw_scope: Any, tasks: List[Dict[str, Any]], request_text: str) -> Dict[str, Any]:
        scope = raw_scope if isinstance(raw_scope, dict) else {}
        serialized = json.dumps([task.get("params") or {} for task in tasks], ensure_ascii=False)
        symbol_values = scope.get("symbols") if isinstance(scope.get("symbols"), list) else []
        symbols = {str(value).strip().upper() for value in symbol_values if str(value).strip()}
        for match in re.finditer(r"(?<!\d)(\d{6})(?:\.(SH|SS|SZ|BJ))?(?!\d)", f"{request_text}\n{serialized}", re.I):
            digits, suffix = match.group(1), (match.group(2) or "").upper().replace("SS", "SH")
            if not suffix:
                suffix = "BJ" if digits.startswith(("4", "8")) else "SH" if digits.startswith(("6", "9")) else "SZ"
            symbols.add(f"{digits}.{suffix}")
        names = set()
        keywords = set()
        for value in scope.get("company_names") if isinstance(scope.get("company_names"), list) else []:
            if str(value).strip():
                names.add(str(value).strip())
        for value in scope.get("keywords") if isinstance(scope.get("keywords"), list) else []:
            if str(value).strip():
                keywords.add(str(value).strip())
        for task in tasks:
            params = task.get("params") or {}
            for key in ("company_name", "company_names"):
                values = params.get(key, [])
                values = values if isinstance(values, list) else [values]
                names.update(str(value).strip() for value in values if str(value).strip())
            values = params.get("keywords", [])
            values = values if isinstance(values, list) else [values]
            keywords.update(str(value).strip() for value in values if str(value).strip())
        request_and_names = f"{request_text}\n{' '.join(names)}".lower()
        for watch_symbol, watch_name in EssayDailyReportService._daily_watchlist():
            if watch_name.lower() in request_and_names:
                names.add(watch_name)
                symbols.add(watch_symbol)
        start_date = str(scope.get("start_date") or "").strip()
        end_date = str(scope.get("end_date") or "").strip()
        if not start_date or not end_date:
            for task in tasks:
                params = task.get("params") or {}
                start_date = start_date or str(params.get("start_date") or params.get("created_from") or "").strip()
                end_date = end_date or str(params.get("end_date") or params.get("created_to") or "").strip()
        return {"symbols": sorted(symbols), "company_names": sorted(names), "keywords": sorted(keywords),
                "start_date": start_date, "end_date": end_date,
                "market_wide": bool(scope.get("market_wide", False))}

    def _execute_task(self, task: Dict[str, Any], scope: Dict[str, Any]) -> List[Dict[str, Any]]:
        source, resource, params = task["source"], task["resource"], dict(task["params"])
        if source == "tushare":
            return self._execute_tushare(task, scope)
        if source == "zsxq":
            created_from = params.pop("start_date", params.get("created_from", None))
            created_to = params.pop("end_date", params.get("created_to", None))
            keywords = params.pop("keywords", None)
            queries = keywords if isinstance(keywords, list) else [params.pop("query", "")]
            symbols = params.pop("symbols", None)
            if symbols:
                queries = symbols if isinstance(symbols, list) else [symbols]
            scoped_queries = list(scope.get("company_names") or []) + list(scope.get("symbols") or [])
            if scoped_queries:
                queries = list(dict.fromkeys([*queries, *scoped_queries]))
            rows_by_id: Dict[str, Dict[str, Any]] = {}
            for value in queries or [""]:
                current = {key: item for key, item in params.items() if key in {
                    "group_id", "digested", "page", "page_size",
                }}
                current["page_size"] = min(int(current.get("page_size") or 100), 100)
                if created_from:
                    current["created_from"] = created_from
                if created_to:
                    current["created_to"] = created_to
                text = str(value or "").strip()
                if text:
                    current["symbol" if re.fullmatch(r"\d{6}(?:\.(?:SH|SZ|BJ))?", text, re.I) else "query"] = text
                result = self.financial.query(source=source, resource=resource, params=current, fields=None)
                for row in result.get("rows") or []:
                    rows_by_id[str(row.get("topic_id") or len(rows_by_id))] = row
            return self._filter_rows([_json_safe(row) for row in rows_by_id.values()], source, resource, scope)
        if source == "cninfo":
            start = self._as_date(params.get("start_date") or scope.get("start_date"), default_days=30)
            end = self._as_date(params.get("end_date") or scope.get("end_date"), default_days=0)
            raw_categories = params.get("categories") or params.get("category") or []
            categories = raw_categories if isinstance(raw_categories, list) else [value for value in str(raw_categories).split(";") if value]
            raw_symbols = params.get("symbols") or params.get("ts_codes") or params.get("ts_code") or params.get("symbol") or []
            symbols = raw_symbols if isinstance(raw_symbols, list) else [value.strip() for value in str(raw_symbols).split(",") if value.strip()]
            symbols = symbols or list(scope.get("symbols") or [])
            if not symbols and not scope.get("market_wide"):
                raise DataAcquisitionError("巨潮现场检索缺少股票代码，已阻止全市场请求")
            rows = self.cninfo.fetch(
                start_date=start, end_date=end, symbols=symbols, categories=categories,
                keyword=str(params.get("keyword") or params.get("query") or ""),
                page_size=min(int(params.get("page_size") or 100), 100),
                max_pages=min(int(params.get("max_pages") or 20), 100),
            )
            return [_json_safe(row) for row in rows]
        if source == "monitor":
            base_params = dict(params)
            raw_symbol = base_params.pop("ts_code", None) or base_params.pop("stock_code", None)
            if raw_symbol and not base_params.get("symbol"):
                base_params["symbol"] = raw_symbol
            raw_keywords = base_params.pop("keywords", None)
            if raw_keywords and not base_params.get("query"):
                values = raw_keywords if isinstance(raw_keywords, list) else [raw_keywords]
                base_params["query"] = " ".join(str(value).strip() for value in values if str(value).strip())
            for key in ("start_date", "end_date"):
                value = str(base_params.get(key) or "")
                if re.fullmatch(r"\d{8}", value):
                    base_params[key] = f"{value[:4]}-{value[4:6]}-{value[6:]}"
            allowed = ({
                "days", "symbol", "perspective", "event_type", "source_key", "query",
                "min_importance", "channel", "evidence_level", "page", "page_size",
            })
            base_params = {key: value for key, value in base_params.items() if key in allowed}
            base_params["page"] = 1
            base_params["page_size"] = min(int(base_params.get("page_size") or 200), 200)
            selectors = list(scope.get("symbols") or []) or list(scope.get("company_names") or [])
            if not selectors and not scope.get("market_wide") and not base_params.get("query") and not base_params.get("symbol"):
                raise DataAcquisitionError("该公告/情报任务没有明确股票或关键词，已阻止全量获取")
            param_sets = []
            for selector in selectors:
                current = dict(base_params)
                if re.fullmatch(r"\d{6}(?:\.(?:SH|SZ|BJ))?", str(selector), re.I):
                    current["symbol"] = selector
                else:
                    current["query"] = selector
                param_sets.append(current)
            if not param_sets:
                param_sets = [base_params]
            rows_by_id: Dict[str, Dict[str, Any]] = {}
            for current in param_sets:
                result = self.monitor.list_events(**current)
                for row in result.get("items") or []:
                    rows_by_id[str(row.get("id") or row.get("external_id") or len(rows_by_id))] = row
            return self._filter_rows([_json_safe(row) for row in rows_by_id.values()], source, resource, scope)
        if source == "tianyancha":
            raw_keys = params.get("search_key") or params.get("searchKey") or params.get("company_name") or ""
            search_keys = raw_keys if isinstance(raw_keys, list) else [raw_keys]
            search_keys = [str(value).strip() for value in search_keys if str(value).strip()]
            search_keys = search_keys or list(scope.get("company_names") or [])
            if not search_keys or any(len(value) > 200 for value in search_keys):
                raise DataAcquisitionError("天眼查任务缺少 company_name/search_key")
            rows = []
            for search_key in search_keys[:20]:
                resolved = self._resolve_tyc_company(search_key)
                resolved_key = str(resolved.get("name") or resolved.get("creditCode") or search_key)
                rows.append({"query_company": search_key, "facet": "entity_resolution", "status": "success", "payload": resolved})
                commands = TYC_FULL_COMMANDS if resource == "company_full" else {resource: TYC_COMMANDS[resource]}
                with ThreadPoolExecutor(max_workers=min(6, len(commands))) as pool:
                    futures = {pool.submit(self._run_tyc, command, resolved_key): facet for facet, command in commands.items()}
                    for future in as_completed(futures):
                        facet = futures[future]
                        try:
                            payload = future.result()
                            rows.append({"query_company": search_key, "facet": facet, "status": "success", "payload": _json_safe(payload)})
                        except Exception as exc:
                            rows.append({"query_company": search_key, "facet": facet, "status": "failed", "error": self._safe_error(exc)})
            return rows
        raise DataAcquisitionError(f"不支持的数据源：{source}")

    def _execute_tushare(self, task: Dict[str, Any], scope: Dict[str, Any]) -> List[Dict[str, Any]]:
        resource = task["resource"]
        query_params = dict(task["params"])
        raw_codes = query_params.pop("ts_codes", query_params.get("ts_code", ""))
        codes = raw_codes if isinstance(raw_codes, list) else [value.strip() for value in str(raw_codes).split(",") if value.strip()]
        codes = codes or list(scope.get("symbols") or [])
        fields = list(task.get("fields") or [])
        if resource == "research_report":
            required = ["trade_date", "abstr", "title", "report_type", "author", "name", "ts_code", "inst_csname", "ind_name", "url"]
            fields = list(dict.fromkeys([*fields, *required]))
        if resource in {"news", "major_news"}:
            query_params.pop("ts_code", None)
            for key in ("keyword", "keywords", "query", "company_name", "company_names", "symbol", "symbols"):
                query_params.pop(key, None)
            if resource == "news":
                query_params.setdefault("src", "cls")
            else:
                query_params.setdefault("src", "新浪财经")
            for key, suffix in (("start_date", "00:00:00"), ("end_date", "23:59:59")):
                raw = str(query_params.get(key) or scope.get(key) or "")
                if re.fullmatch(r"\d{8}", raw):
                    raw = f"{raw[:4]}-{raw[4:6]}-{raw[6:]} {suffix}"
                elif re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw):
                    raw = f"{raw} {suffix}"
                if raw:
                    query_params[key] = raw
            codes = []
        elif resource == "research_report":
            query_params.setdefault("start_date", str(scope.get("start_date") or "").replace("-", ""))
            query_params.setdefault("end_date", str(scope.get("end_date") or "").replace("-", ""))
            query_params = {key: value for key, value in query_params.items() if value not in (None, "", [])}

        param_sets = []
        if codes and resource not in {"news", "major_news"}:
            for code in codes[:20]:
                current = dict(query_params)
                current["ts_code"] = code
                param_sets.append(current)
            if resource == "research_report" and scope.get("company_names"):
                # Industry/strategy reports often omit ts_code but mention the company in abstr.
                param_sets.append(dict(query_params))
        else:
            param_sets.append(query_params)

        page_size = 1000 if resource == "research_report" else 1500 if resource == "news" else 800 if resource == "major_news" else 0
        rows: List[Dict[str, Any]] = []
        for base in param_sets:
            if not page_size:
                result = self.financial.query(source="tushare", resource=resource, params=base, fields=fields or None)
                rows.extend(result.get("rows") or [])
                continue
            for offset in range(0, MAX_ROWS_PER_DATASET, page_size):
                current = {**base, "limit": page_size, "offset": offset}
                result = self.financial.query(source="tushare", resource=resource, params=current, fields=fields or None)
                page = result.get("rows") or []
                rows.extend(page)
                if len(page) < page_size:
                    break
        filtered = self._filter_rows([_json_safe(row) for row in rows], "tushare", resource, scope)
        unique: Dict[str, Dict[str, Any]] = {}
        for row in filtered:
            identity = json.dumps(
                [row.get("ts_code"), row.get("trade_date") or row.get("datetime"), row.get("title"), row.get("url")],
                ensure_ascii=False, default=str,
            )
            unique[identity] = row
        return list(unique.values())

    @staticmethod
    def _run_tyc(command: List[str], search_key: str) -> Any:
        completed = subprocess.run(["tyc", "--compact", *command, search_key], capture_output=True, text=True, timeout=90, check=False)
        if completed.returncode != 0:
            raise DataAcquisitionError("天眼查查询失败，请检查该维度权限或企业名称")
        try:
            return json.loads(completed.stdout)
        except ValueError as exc:
            raise DataAcquisitionError("天眼查返回格式异常") from exc

    def _resolve_tyc_company(self, search_key: str) -> Dict[str, Any]:
        payload = self._run_tyc(["company", "companies"], search_key)
        items = payload.get("items") if isinstance(payload, dict) else None
        if not isinstance(items, list) or not items:
            raise DataAcquisitionError(f"天眼查没有找到企业：{search_key}")
        candidates = [item for item in items if isinstance(item, dict)]
        # L0 search is relevance-ranked by Tianyancha; re-sorting abbreviated listed-company
        # names locally can select an unrelated exact-substring company.
        return _json_safe(candidates[0])

    @staticmethod
    def _as_date(value: Any, *, default_days: int) -> date:
        raw = str(value or "").strip()
        if not raw:
            return datetime.now(timezone(timedelta(hours=8))).date() - timedelta(days=default_days)
        try:
            return datetime.strptime(raw.replace("-", "")[:8], "%Y%m%d").date()
        except ValueError as exc:
            raise DataAcquisitionError(f"无效日期：{raw}") from exc

    def _download_files(self, directory: Path, task: Dict[str, Any], rows: List[Dict[str, Any]], stem: str) -> List[Dict[str, Any]]:
        candidates: List[Dict[str, str]] = []
        if task["source"] == "cninfo":
            candidates.extend({"url": str(row.get("pdf_url") or ""), "name": str(row.get("title") or row.get("announcement_id") or "公告") + ".pdf"} for row in rows)
        elif task["source"] == "tushare" and task["resource"] == "research_report":
            candidates.extend({"url": str(row.get("url") or ""), "name": str(row.get("title") or "研报") + ".pdf"} for row in rows)
        elif task["source"] == "zsxq":
            from src.services.zsxq_mcp_sync_service import ZsxqMcpSyncService
            resolver = ZsxqMcpSyncService()
            for row in rows:
                topic_id = str(row.get("topic_id") or "")
                for kind, id_key in (("files", "file_id"), ("images", "image_id")):
                    for asset in row.get(kind) or []:
                        asset_id = str(asset.get(id_key) or "")
                        if not topic_id or not asset_id:
                            continue
                        try:
                            url = resolver.resolve_media_url_sync(topic_id, kind, asset_id)
                            candidates.append({"url": url, "name": str(asset.get("name") or asset.get("filename") or f"{topic_id}_{asset_id}")})
                        except Exception as exc:
                            candidates.append({"url": "", "name": str(asset.get("name") or asset_id), "error": self._safe_error(exc)})
        elif task["source"] == "tianyancha":
            for row in rows:
                self._collect_pdf_urls(row.get("payload"), candidates)

        target_dir = directory / "attachments" / task["source"] / stem
        results: List[Dict[str, Any]] = []
        seen = set()
        total_bytes = 0
        for index, candidate in enumerate(candidates[:MAX_DOWNLOAD_FILES], start=1):
            url = str(candidate.get("url") or "").strip()
            name = str(candidate.get("name") or f"file_{index}")
            identity = url or f"error:{name}"
            if identity in seen:
                continue
            seen.add(identity)
            if candidate.get("error") or not url:
                results.append({"name": name, "status": "failed", "error": candidate.get("error") or "原始文件没有可下载地址"})
                continue
            try:
                parsed = urlparse(url)
                if parsed.scheme != "https":
                    raise DataAcquisitionError("只允许下载 HTTPS 原始文件")
                if task["source"] == "cninfo" and parsed.hostname != "static.cninfo.com.cn":
                    raise DataAcquisitionError("巨潮文件域名不受信任")
                target_dir.mkdir(parents=True, exist_ok=True)
                safe_name = self._safe_filename(Path(unquote(parsed.path)).name or name)
                if "." not in safe_name:
                    safe_name += ".pdf" if task["resource"] in {"announcements", "research_report"} else ".bin"
                target = target_dir / f"{index:03d}_{safe_name}"
                size = self._download_one(url, target, remaining=max(0, MAX_PACKAGE_FILE_BYTES - total_bytes))
                total_bytes += size
                results.append({"name": name, "status": "success", "path": str(target.relative_to(directory)), "size_bytes": size})
            except Exception as exc:
                results.append({"name": name, "status": "failed", "error": self._safe_error(exc)})
        if len(candidates) > MAX_DOWNLOAD_FILES:
            results.append({"name": "download_limit", "status": "failed", "error": f"原始文件超过单包上限 {MAX_DOWNLOAD_FILES} 个"})
        return results

    @staticmethod
    def _collect_pdf_urls(value: Any, candidates: List[Dict[str, str]]) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                if isinstance(item, str) and item.startswith("https://") and ("pdf" in key.lower() or ".pdf" in item.lower()):
                    candidates.append({"url": item, "name": Path(urlparse(item).path).name or "天眼查原始文件.pdf"})
                else:
                    DataAcquisitionService._collect_pdf_urls(item, candidates)
        elif isinstance(value, list):
            for item in value:
                DataAcquisitionService._collect_pdf_urls(item, candidates)

    @staticmethod
    def _download_one(url: str, target: Path, *, remaining: int) -> int:
        if remaining <= 0:
            raise DataAcquisitionError("原始文件达到单包 500MB 上限")
        limit = min(MAX_FILE_BYTES, remaining)
        temporary = target.with_suffix(target.suffix + ".part")
        try:
            with requests.get(url, headers={"User-Agent": "Mozilla/5.0", "Referer": "https://www.cninfo.com.cn/"}, timeout=(10, 90), stream=True) as response:
                response.raise_for_status()
                declared = int(response.headers.get("Content-Length") or 0)
                if declared > limit:
                    raise DataAcquisitionError("原始文件超过大小上限")
                size = 0
                with temporary.open("wb") as output:
                    for chunk in response.iter_content(64 * 1024):
                        if not chunk:
                            continue
                        size += len(chunk)
                        if size > limit:
                            raise DataAcquisitionError("原始文件超过大小上限")
                        output.write(chunk)
            temporary.replace(target)
            return size
        finally:
            temporary.unlink(missing_ok=True)

    def _write_artifacts(self, directory: Path, job_id: str, request_text: str,
                         plan: Dict[str, Any], datasets: List[Dict[str, Any]]) -> Dict[str, Any]:
        generated_at = datetime.now(timezone.utc).isoformat()
        dataset_manifest = []
        workbooks: Dict[str, Workbook] = {}
        for index, dataset in enumerate(datasets, start=1):
            task, rows = dataset["task"], dataset["rows"]
            stem = f"{index:02d}_{self._safe_filename(task['label'])}"
            channel_dir = directory / task["source"]
            channel_dir.mkdir(parents=True, exist_ok=True)
            json_name, csv_name = f"{task['source']}/{stem}.json", f"{task['source']}/{stem}.csv"
            (directory / json_name).write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
            self._write_csv(directory / csv_name, rows)
            workbook = workbooks.setdefault(task["source"], Workbook())
            if workbook.active.title == "Sheet" and len(workbook.sheetnames) == 1:
                workbook.remove(workbook.active)
            self._write_sheet(workbook, stem[:31], rows, dataset.get("error"))
            attachments = self._download_files(directory, task, rows, stem) if plan.get("include_files") else []
            dataset_manifest.append({"task_id": task["id"], "label": task["label"], "source": task["source"],
                                     "resource": task["resource"], "status": dataset["status"],
                                     "row_count": dataset["row_count"], "error": dataset["error"],
                                     "applied_scope": dataset["applied_scope"], "files": [json_name, csv_name],
                                     "attachments": attachments})
        for source, workbook in workbooks.items():
            workbook.save(directory / source / f"{source}.xlsx")
        success_count = sum(item["status"] == "success" for item in dataset_manifest)
        downloaded_count = sum(sum(file["status"] == "success" for file in item["attachments"]) for item in dataset_manifest)
        failed_download_count = sum(sum(file["status"] == "failed" for file in item["attachments"]) for item in dataset_manifest)
        manifest = {"job_id": job_id, "contract_version": "channel-scoped-v3-live",
                    "title": plan["title"], "request": request_text,
                    "status": "success" if success_count == len(datasets) else ("partial" if success_count else "failed"),
                    "generated_at": generated_at, "plan": plan, "datasets": dataset_manifest,
                    "summary": {"task_count": len(datasets), "success_count": success_count,
                                "failed_count": len(datasets) - success_count,
                                "row_count": sum(item["row_count"] for item in dataset_manifest),
                                "include_files": bool(plan.get("include_files")),
                                "downloaded_file_count": downloaded_count,
                                "failed_file_count": failed_download_count},
                    "download_url": f"/api/v1/data-acquisition/jobs/{job_id}/download",
                    "formats": ["json", "csv", "xlsx", "zip"]}
        (directory / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        readme = [f"# {plan['title']}", "", f"原始需求：{request_text}", f"生成时间：{generated_at}", "",
                  "数据按渠道分目录；每个数据集同时提供 JSON 与 CSV，每个渠道单独提供 XLSX；manifest.json 记录现场接口、范围、参数、状态和行数。",
                  f"原始文件下载：{'已启用' if plan.get('include_files') else '未请求'}；已下载 {downloaded_count} 个，失败 {failed_download_count} 个。",
                  "失败的数据源不会阻断其他任务，具体原因请查看 manifest.json。"]
        (directory / "README.md").write_text("\n".join(readme), encoding="utf-8")
        zip_name = f"{job_id}.zip"
        with zipfile.ZipFile(directory / zip_name, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for path in sorted(directory.rglob("*")):
                if path.is_file() and path.name != zip_name:
                    archive.write(path, path.relative_to(directory))
        return manifest

    @staticmethod
    def _filter_rows(rows: List[Dict[str, Any]], source: str, resource: str,
                     scope: Dict[str, Any]) -> List[Dict[str, Any]]:
        if scope.get("market_wide"):
            return rows
        symbols = {str(value).upper() for value in scope.get("symbols") or []}
        code_digits = {value.split(".")[0] for value in symbols}
        terms = [str(value).strip() for value in [*(scope.get("company_names") or []),
                                                  *(scope.get("keywords") or [])] if str(value).strip()]
        text_resources = {"news", "major_news", "research_report", "report_rc", "anns_d",
                          "research_notes", "events", "announcements"}
        filtered = []
        for row in rows:
            row_codes = set()
            for key in ("ts_code", "symbol", "stock_code", "code"):
                value = row.get(key)
                if value:
                    row_codes.add(str(value).upper())
            values = row.get("symbols")
            if isinstance(values, list):
                row_codes.update(str(value).upper() for value in values)
            if row_codes and symbols:
                if not any(value in symbols or value.split(".")[0] in code_digits for value in row_codes):
                    continue
                filtered.append(row)
                continue
            if resource in text_resources and (terms or code_digits):
                haystack = json.dumps(row, ensure_ascii=False, default=str)
                if not any(term in haystack for term in terms) and not any(code in haystack for code in code_digits):
                    continue
            filtered.append(row)
        return filtered

    @staticmethod
    def _write_csv(path: Path, rows: List[Dict[str, Any]]) -> None:
        columns = list(dict.fromkeys(key for row in rows for key in row.keys()))
        with path.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=columns or ["message"])
            writer.writeheader()
            for row in rows:
                writer.writerow({key: DataAcquisitionService._cell(row.get(key)) for key in columns})

    @staticmethod
    def _write_sheet(workbook: Workbook, title: str, rows: List[Dict[str, Any]], error: Optional[str]) -> None:
        sheet = workbook.create_sheet(title=title or "dataset")
        if not rows:
            sheet.append(["status", "message"])
            sheet.append(["failed" if error else "empty", error or "No rows returned"])
            return
        columns = list(dict.fromkeys(key for row in rows for key in row.keys()))
        sheet.append(columns)
        for row in rows:
            sheet.append([DataAcquisitionService._cell(row.get(key)) for key in columns])
        sheet.freeze_panes = "A2"
        sheet.auto_filter.ref = sheet.dimensions

    @staticmethod
    def _cell(value: Any) -> Any:
        if isinstance(value, (dict, list, tuple)):
            return json.dumps(value, ensure_ascii=False, separators=(",", ":"))[:32000]
        if value is None:
            return ""
        return str(value)[:32000]

    @staticmethod
    def _safe_filename(value: str) -> str:
        cleaned = re.sub(r"[^\w\u4e00-\u9fff.-]+", "_", value, flags=re.UNICODE).strip("._")
        return cleaned[:60] or "dataset"

    @staticmethod
    def _safe_error(exc: Exception) -> str:
        if isinstance(exc, (DataAcquisitionError, FinancialDataValidationError)):
            return str(exc)[:300]
        return f"{type(exc).__name__}: 数据源调用失败"[:300]
