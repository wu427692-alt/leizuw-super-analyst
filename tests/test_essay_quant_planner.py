import json

from src.services.essay_quant_planner import EssayQuantNaturalLanguagePlanner


class _Response:
    def __init__(self, payload):
        self.payload = payload


class FakeAnalyzer:
    configured = True
    model = "deepseek-test"

    def _post_with_retry(self, payload):
        assert "任意代码" in payload["messages"][0]["content"]
        return {"choices": [{"message": {"content": json.dumps({
            "title": "中信电子组20日事件研究",
            "hypothesis": "高重要度首次提及存在正超额收益",
            "source_query": "中信电子组",
            "signal_direction": "bullish",
            "lookback_days": 365,
            "holding_periods": [20],
            "transaction_cost_bps": 20,
            "raw_note_policy": "exclude",
            "dedupe_window_days": 5,
            "validation_method": "walk_forward",
            "assumptions": ["次日开盘可成交"],
        }, ensure_ascii=False)}}]}

    @staticmethod
    def _extract_content(payload):
        return payload["choices"][0]["message"]["content"]

    @staticmethod
    def _parse_json(content):
        return json.loads(content)


class FakeService:
    from src.services.essay_quant_service import EssayQuantService
    _normalize_rule = staticmethod(EssayQuantService._normalize_rule)


def test_natural_language_plan_is_bounded_and_code_is_valid_template():
    planner = EssayQuantNaturalLanguagePlanner(analyzer=FakeAnalyzer(), service=FakeService())
    result = planner.plan("研究中信电子组首次提及股票持有20日的超额收益")
    assert result["rule"]["transaction_cost_bps"] == 20
    assert result["rule"]["raw_note_policy"] == "exclude"
    assert result["safety"]["confirmation_required"] is True
    assert "exec(" not in result["code"]
    compile(result["code"], "<quant-plan>", "exec")


def test_natural_language_plan_removes_generic_corpus_source_query():
    class GenericCorpusAnalyzer(FakeAnalyzer):
        def _post_with_retry(self, payload):
            return {"choices": [{"message": {"content": json.dumps({
                "title": "全量小作文事件研究",
                "source_query": "小作文",
                "signal_direction": "bullish",
                "lookback_days": 60,
                "holding_periods": [5],
            }, ensure_ascii=False)}}]}

    planner = EssayQuantNaturalLanguagePlanner(analyzer=GenericCorpusAnalyzer(), service=FakeService())
    result = planner.plan("回测最近60天全部看多小作文持有5个交易日")
    assert result["rule"]["source_query"] == ""
