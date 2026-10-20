"""Point-in-time public context, independently aged at every model dispatch."""
from copy import deepcopy
from datetime import datetime
import math

from src.services.jev_live_context_client import SCHEMA, NUMERIC_MAX_AGE_SECONDS, validate_live_context_state


INDEX_CODES = {"hs300": "sh000300", "zz1000": "sh000852"}


def parse_time(value):
    result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("aware source time required")
    return result


def missing_numeric(source="not_observed", status="missing"):
    return {"status": status, "source": source, "source_time": None, "available_at": None,
            "age_seconds": None, "values": {}}


def missing_news(status="missing"):
    return {"status": status, "source": "public_news_cache", "fetched_at": None, "items": []}


def refresh_numeric(item, at, name):
    result = deepcopy(item)
    try:
        source = parse_time(item["source_time"]) if item["source_time"] else None
        available = parse_time(item["available_at"]) if item["available_at"] else None
        if (source and source > at) or (available and available > at) or (source and available and source > available):
            return missing_numeric(item["source"], "error")
        result["age_seconds"] = (at - source).total_seconds() if source else None
        if result["status"] == "fresh" and (source is None or available is None or result["age_seconds"] > NUMERIC_MAX_AGE_SECONDS[name]):
            result["status"] = "stale"
        if result["status"] != "fresh":
            result["values"] = {}
        return result
    except (ValueError, TypeError, KeyError):
        return missing_numeric(status="error")


class LiveContextTracker:
    def __init__(self):
        self.histories = {key: [] for key in INDEX_CODES}
        self.numeric = {key: missing_numeric() for key in (*INDEX_CODES, "cpo")}
        self.news = missing_news()
        self.diagnostics = {"hs300": {"label": "沪深300", "source_kind": "index_snapshot", "reason": "等待真实报价"},
                            "zz1000": {"label": "中证1000", "source_kind": "index_snapshot", "reason": "等待真实报价"},
                            "cpo": {"label": "CPO成分股等权参考", "source_kind": "constituent_proxy", "reason": "等待真实成分股报价"},
                            "news": {"label": "公开实时快讯", "source_kind": "public_news", "reason": "等待新闻缓存核验"}}

    def observe_indices(self, batch, at):
        at = parse_time(at)
        for key, code in INDEX_CODES.items():
            quote = batch.get("quotes", {}).get(code)
            if not quote:
                self.numeric[key] = missing_numeric("sina.public_snapshot", "missing")
                continue
            try:
                stamp, available = parse_time(quote["source_time"]), parse_time(quote["received_at"])
                age = (at - stamp).total_seconds()
                price, previous_close = quote["last_price"], quote["pre_close"]
                if not (0 <= age <= 15 and stamp <= available <= at and quote["trading_status"] == "trading"
                        and all(isinstance(v, (float, int)) and not isinstance(v, bool) and math.isfinite(v) and v > 0
                                for v in (price, previous_close))):
                    self.numeric[key] = refresh_numeric({"status": "stale", "source": "sina.public_snapshot",
                        "source_time": quote["source_time"], "available_at": quote["received_at"], "age_seconds": None, "values": {}}, at, key)
                    continue
                rows = self.histories[key]
                if rows and stamp > rows[-1][0] and (stamp - rows[-1][0]).total_seconds() > 15:
                    rows.clear()
                if rows and stamp < rows[-1][0]:
                    self.numeric[key] = missing_numeric("sina.public_snapshot", "error")
                    continue
                if not rows or stamp > rows[-1][0]:
                    rows.append((stamp, price))
                self.histories[key] = rows = rows[-80:]
                values = {"return_since_prev_close": price / previous_close - 1}
                for seconds in (30, 60):
                    earlier = next((row for row in reversed(rows) if seconds <= (stamp-row[0]).total_seconds() <= seconds+5), None)
                    if earlier:
                        values[f"return_{seconds}s"] = price / earlier[1] - 1
                self.numeric[key] = {"status": "fresh", "source": "sina.public_snapshot",
                    "source_time": stamp.isoformat(), "available_at": available.isoformat(), "age_seconds": age, "values": values}
                self.diagnostics[key]["reason"] = "真实指数快照；30/60秒收益只在完整观测窗口后提供"
            except (KeyError, TypeError, ValueError, ZeroDivisionError):
                self.numeric[key] = missing_numeric("sina.public_snapshot", "error")

    def observe_external(self, snapshot):
        self.numeric["cpo"] = deepcopy(snapshot["context"]["cpo"])
        self.news = deepcopy(snapshot["context"]["news"])
        for key in ("cpo", "news"):
            values = snapshot.get("diagnostics", {}).get(key, {})
            self.diagnostics[key].update({name: str(values[name])[:500] for name in ("label", "reason", "source_kind") if name in values})

    def external_failed(self):
        self.numeric["cpo"] = missing_numeric(status="error")
        self.news = missing_news("error")
        for key in ("cpo", "news"):
            self.diagnostics[key]["reason"] = "数据源检查失败，等待下一次有界刷新"

    def view(self, decision_at, stock_return_30s=None):
        at = parse_time(decision_at)
        result = {key: refresh_numeric(value, at, key) for key, value in self.numeric.items()}
        for key in INDEX_CODES:
            component = result[key]
            if component["status"] == "fresh" and stock_return_30s is not None and "return_30s" in component["values"]:
                component["values"]["relative_return"] = stock_return_30s-component["values"]["return_30s"]
        news = deepcopy(self.news)
        try:
            fetched = parse_time(news["fetched_at"]) if news["fetched_at"] else None
            if fetched and fetched > at:
                news = missing_news("error")
            elif news["status"] == "fresh" and (not fetched or (at-fetched).total_seconds() > 180):
                news["status"], news["items"] = "stale", []
            elif news["status"] == "fresh":
                news["items"] = [item for item in news["items"] if 0 <= (at-parse_time(item["published_at"])).total_seconds() <= 21600
                                 and parse_time(item["published_at"]) <= parse_time(item["available_at"]) <= fetched]
        except (TypeError, ValueError, KeyError):
            news = missing_news("error")
        result["news"] = news
        return result

    def model_state(self, base_state, decision_at, target=None):
        state = {"schema": SCHEMA, "decision_at": decision_at, "base_state": base_state,
                 "context": self.view(decision_at, base_state["indicators"].get("return_30s"))}
        if target is not None:
            state["target"] = target
        return validate_live_context_state(state)
