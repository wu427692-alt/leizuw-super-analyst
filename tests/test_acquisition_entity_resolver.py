from unittest.mock import Mock, patch

import pytest

from src.services.acquisition_entity_resolver import resolve_acquisition_entities


INDEX = [
    ["603306.SH", "603306", "华懋科技", "huamaokeji", "hmkj", ["华懋", "华懋(厦门)新材料科技股份有限公司"], "CN", "stock", True, 100],
    ["300476.SZ", "300476", "胜宏科技", "shenghongkeji", "shkj", ["胜宏"], "CN", "stock", True, 100],
    ["688828.SH", "688828", "国仪公司", "guoyigongsi", "gygs", ["国仪量子", "国仪量子技术(合肥)股份有限公司"], "CN", "stock", True, 100],
    ["920976.BJ", "920976", "北交样本科技", "beijiaoyangbenkeji", "bjybekj", [], "CN", "stock", True, 100],
    ["600000.SH", "600000", "浦发银行", "pufayinhang", "pfyh", [], "CN", "stock", True, 100],
]


def resolve(names=None, codes=None, text="", **kwargs):
    return resolve_acquisition_entities(company_names=names or [], symbols=codes or [],
                                        request_text=text, records=kwargs.pop("records", INDEX), **kwargs)


@pytest.mark.parametrize("query", ["华懋科技", "华懋", "华懋公司", "华懋(厦门)新材料科技股份有限公司", "华懋（厦门）新材料科技股份有限公司"])
def test_names_aliases_and_company_full_names(query):
    result = resolve([query])
    assert result["symbols"] == ["603306.SH"]
    assert result["company_names"] == ["华懋科技"]
    assert result["has_unresolved"] is False


def test_uniquely_matching_company_prefix_is_verified_not_model_guess():
    result = resolve(["国仪公司"])
    assert result["symbols"] == ["688828.SH"]
    assert result["entities"][0]["source"] == "stock_index"


def test_multiple_companies_are_all_resolved():
    result = resolve(["华懋", "胜宏", "国仪"])
    assert result["symbols"] == ["300476.SZ", "603306.SH", "688828.SH"]
    assert not result["has_unresolved"]


def test_shorter_nested_alias_does_not_create_duplicate_company():
    result = resolve(text="获取华懋科技和胜宏科技的所有公告")
    assert [row["query"] for row in result["entities"]] == ["华懋科技", "胜宏科技"]
    assert result["symbols"] == ["300476.SZ", "603306.SH"]


@pytest.mark.parametrize("code", ["603306", "603306.SH", "603306.SS", "603306.sh"])
def test_explicit_codes_are_validated_against_index(code):
    result = resolve(codes=[code])
    assert result["symbols"] == ["603306.SH"]
    assert not result["has_unresolved"]


def test_request_text_code_next_to_chinese_is_not_lost():
    result = resolve(text="请获取股票920976的公告")
    assert result["symbols"] == ["920976.BJ"]


def test_incorrect_exchange_is_not_silently_normalized():
    result = resolve(codes=["920976.SH"])
    assert not result["symbols"]
    assert result["entities"][0]["status"] == "unresolved"


def test_explicit_name_code_conflict_blocks_both():
    result = resolve(["华懋科技"], ["300476.SZ"])
    assert not result["symbols"]
    assert result["has_unresolved"]
    assert {row["status"] for row in result["entities"]} == {"conflict"}
    assert {row["reason"] for row in result["entities"]} == {"company_symbol_mismatch"}


def test_multiple_named_companies_cannot_hide_an_unrelated_explicit_code():
    result = resolve(["华懋科技", "胜宏科技"], ["600000.SH"])
    assert result["has_unresolved"]
    assert result["entities"][-1]["status"] == "conflict"
    assert "600000.SH" not in result["symbols"]


def test_unknown_and_unlisted_names_do_not_become_market_wide():
    result = resolve(["完全不存在的公司"])
    assert result["symbols"] == []
    assert result["entities"][0]["reason"] == "no_verified_security"
    assert result["has_unresolved"]
    assert "market_wide" not in result


def test_local_ambiguity_does_not_rank_by_popularity_or_exchange():
    rows = [*INDEX,
            ["600001.SH", "600001", "新星设备", "", "", [], "CN", "stock", True, 9999],
            ["920001.BJ", "920001", "新星技术", "", "", [], "CN", "stock", True, 1]]
    result = resolve_acquisition_entities(company_names=["新星"], symbols=[], request_text="",
                                          records=rows)
    assert not result["symbols"]
    assert result["entities"][0]["status"] == "ambiguous"
    assert len(result["entities"][0]["candidates"]) == 2


def test_caller_can_confirm_an_existing_ambiguous_candidate():
    rows = [*INDEX,
            ["600001.SH", "600001", "新星设备", "", "", [], "CN", "stock", True, 9999],
            ["920001.BJ", "920001", "新星技术", "", "", [], "CN", "stock", True, 1]]
    result = resolve_acquisition_entities(company_names=["新星"], symbols=[], request_text="",
                                          selections={"新星": "920001.BJ"}, records=rows)
    assert result["symbols"] == ["920001.BJ"]
    assert not result["has_unresolved"]


def test_arbitrary_selection_is_rejected_even_when_real_other_company():
    result = resolve(["国仪"], selections={"国仪": "603306.SH"})
    assert not result["symbols"]
    assert result["entities"][0]["status"] == "conflict"


def test_unresolved_name_accepts_user_confirmed_verified_ticker():
    result = resolve(["用户的新公司简称"], selections={"用户的新公司简称": "688828.SH"})
    assert result["symbols"] == ["688828.SH"]
    assert result["entities"][0]["confirmed_by_user"] is True
    assert result["entities"][0]["source"] == "stock_index"


@pytest.mark.parametrize("code", ["688828.SZ", "999999.SH"])
def test_unresolved_name_cannot_confirm_wrong_or_missing_ticker(code):
    result = resolve(["用户的新公司简称"], selections={"用户的新公司简称": code})
    assert result["has_unresolved"]
    assert not result["symbols"]


def test_user_confirmed_name_does_not_override_conflicting_request_code():
    result = resolve(["用户的新公司简称"], text="股票603306.SH",
                     selections={"用户的新公司简称": "688828.SH"})
    assert result["has_unresolved"]
    assert not result["symbols"]
    assert {row["reason"] for row in result["entities"]} == {"company_symbol_mismatch"}


def test_matching_ticker_can_disambiguate_name():
    rows = [*INDEX,
            ["600001.SH", "600001", "新星设备", "", "", [], "CN", "stock", True, 9999],
            ["920001.BJ", "920001", "新星技术", "", "", [], "CN", "stock", True, 1]]
    result = resolve_acquisition_entities(company_names=["新星"], symbols=["920001.BJ"],
                                          request_text="", records=rows)
    assert result["symbols"] == ["920001.BJ"]
    assert not result["has_unresolved"]


def test_unknown_local_company_uses_injected_official_issuer_search():
    search = Mock(return_value=[{"code": "920001", "market": "BJ", "name": "新公司科技", "org_id": "gfbj1234"}])
    result = resolve(["新公司"], issuer_search=search)
    assert result["symbols"] == ["920001.BJ"]
    assert result["entities"][0]["org_id"] == "gfbj1234"
    assert result["entities"][0]["source"] == "cninfo"
    search.assert_called_once_with("新公司")


def test_official_full_company_query_can_return_only_exchange_short_name():
    search = Mock(return_value=[{"symbol": "688828.SH", "name": "国仪公司", "org_id": "9900063652"}])
    result = resolve(["国仪量子技术（合肥）股份有限公司"], issuer_search=search, records=[])
    assert not result["has_unresolved"]
    assert result["symbols"] == ["688828.SH"]
    assert result["entities"][0]["source"] == "cninfo"


def test_official_display_name_difference_does_not_rank_multiple_results():
    search = Mock(return_value=[{"symbol": "688828.SH", "name": "国仪公司"},
                                {"symbol": "600000.SH", "name": "浦发银行"}])
    result = resolve(["公司全名"], issuer_search=search)
    assert result["has_unresolved"]
    assert result["entities"][0]["status"] == "ambiguous"


def test_official_search_ambiguity_and_error_are_not_empty_success():
    search = Mock(return_value=[{"symbol": "920001.BJ", "name": "新公司科技"},
                                {"symbol": "600001.SH", "name": "新公司设备"}])
    result = resolve(["新公司"], issuer_search=search)
    assert result["entities"][0]["status"] == "ambiguous"
    failed = resolve(["新公司"], issuer_search=Mock(side_effect=TimeoutError("upstream timeout")))
    assert failed["entities"][0]["reason"] == "issuer_search_unavailable"
    assert failed["has_unresolved"]


def test_inactive_and_non_stock_records_are_ignored():
    rows = [
        ["920001.BJ", "920001", "新公司科技", "", "", [], "CN", "stock", False, 100],
        ["510300.SH", "510300", "新公司基金", "", "", [], "CN", "fund", True, 100],
    ]
    result = resolve_acquisition_entities(company_names=["新公司"], symbols=[], request_text="", records=rows)
    assert result["entities"][0]["status"] == "unresolved"


def test_existing_stock_loader_is_the_default_directory_source():
    with patch("src.services.acquisition_entity_resolver.get_stock_entity_index_records", return_value=INDEX) as loader:
        result = resolve_acquisition_entities(company_names=["国仪"], symbols=[], request_text="")
    assert result["symbols"] == ["688828.SH"]
    loader.assert_called_once_with()


def test_topic_only_plan_does_not_invent_company():
    result = resolve(text="获取低空经济主题研报")
    assert result == {"entities": [], "symbols": [], "company_names": [], "has_unresolved": False}
