from src.services.industry_research_service import IndustryResearchService


def test_pe_observations_drop_every_speculative_explanation() -> None:
    prose = (
        "2026年8月20日PE(TTM)为171.11倍 [valuation:603306.SH:20260820]，"
        "2026年8月31日为246.92倍 [valuation:603306.SH:20260831]。"
        "该张力的可能解释方向包括并表重估、光通信预期前置定价、"
        "TTM盈利分母被动抬升及筹码博弈，但均待核验。"
    )

    normalized = IndustryResearchService._enforce_neutral_pe_disclosure(prose)

    assert "171.11倍" in normalized
    assert "246.92倍" in normalized
    assert "差异待核验" in normalized
    for forbidden in (
        "可能解释方向", "并表重估", "前置定价", "分母被动抬升", "筹码博弈",
    ):
        assert forbidden not in normalized
    assert IndustryResearchService._production_accounting_policy_failures(normalized) == []


def test_pe_cleanup_preserves_cited_values_when_explanation_shares_the_sentence() -> None:
    prose = (
        "2026年8月20日PE(TTM)为171.11倍 [valuation:603306.SH:20260820]，"
        "2026年8月31日为246.92倍 [valuation:603306.SH:20260831]，"
        "该差异或受并购预期影响。"
    )

    normalized = IndustryResearchService._enforce_neutral_pe_disclosure(prose)

    assert "171.11倍" in normalized
    assert "246.92倍" in normalized
    assert "valuation:603306.SH:20260820" in normalized
    assert "valuation:603306.SH:20260831" in normalized
    assert "并购预期" not in normalized
    assert "差异待核验" in normalized


def test_pe_cleanup_blocks_growth_curve_valuation_story_but_not_non_pe_base_effect() -> None:
    pe = (
        "PE(TTM)为246.92倍 [valuation:603306.SH:20260831]。"
        "第二增长曲线支撑更高估值。"
    )
    non_pe = "2025年营收下滑主要源于高基数退出 [filing:example]。"

    assert IndustryResearchService._production_accounting_policy_failures(pe)
    assert IndustryResearchService._production_accounting_policy_failures(non_pe) == []


def test_pe_policy_still_rejects_explanation_when_neutral_boundary_is_appended() -> None:
    prose = (
        "2026年8月20日PE(TTM)为171.11倍，2026年8月31日为246.92倍。"
        "可能解释方向包括重组前置定价和TTM盈利分母被动抬升。"
        "各日期PE(TTM)仅作中性列示，差异待核验。"
    )

    failures = IndustryResearchService._production_accounting_policy_failures(prose)

    assert "PE(TTM)跨日期观察不得保留任何候选因果解释" in failures


def test_production_pe_story_is_removed_not_merely_hedged() -> None:
    prose = (
        "2026年8月31日PE(TTM)为246.92倍 [valuation:603306.SH:20260831]。"
        "在此信息缺口下，市场246.92倍PE(TTM)的定价逻辑存在多种互斥解释："
        "若交易完成，并表摊薄可能消化估值；市场可能前置定价光通信业务；"
        "筹码结构也可能驱动价格，但均待核验。"
    )

    normalized = IndustryResearchService._enforce_neutral_pe_disclosure(prose)

    assert "246.92倍" in normalized
    assert "valuation:603306.SH:20260831" in normalized
    for forbidden in ("互斥解释", "并表摊薄", "前置定价", "筹码结构"):
        assert forbidden not in normalized
    assert IndustryResearchService._production_accounting_policy_failures(normalized) == []


def test_directional_pe_comparison_is_rebuilt_as_neutral_dated_atoms() -> None:
    prose = (
        "PE(TTM)从2026年8月20日的171.11倍 [valuation:603306.SH:20260820]，"
        "跃升至2026年8月31日的246.92倍 [valuation:603306.SH:20260831]。"
    )

    normalized = IndustryResearchService._enforce_neutral_pe_disclosure(prose)

    assert "跃升" not in normalized
    assert "2026年8月20日PE(TTM)为171.11倍" in normalized
    assert "2026年8月31日PE(TTM)为246.92倍" in normalized
    assert "差异待核验" in normalized


def test_pe_figure_denominator_explanation_is_removed() -> None:
    prose = (
        "2026年8月31日PE(TTM)为246.92倍 [valuation:603306.SH:20260831]，"
        "需配合盈利分母明细解释变动原因。"
    )

    normalized = IndustryResearchService._enforce_neutral_pe_disclosure(prose)

    assert "盈利分母" not in normalized
    assert "246.92倍" in normalized
    assert "差异待核验" in normalized


def test_pe_candidate_explanations_are_removed_across_paragraphs_and_lists() -> None:
    prose = (
        "2026年8月20日PE(TTM)为171.11倍 [valuation:603306.SH:20260820]，"
        "2026年8月31日为246.92倍 [valuation:603306.SH:20260831]。\n\n"
        "- 可能解释包括并表重估。\n"
        "- 市场可能前置定价光通信业务。\n"
        "- 筹码博弈可能驱动估值。"
    )

    normalized = IndustryResearchService._enforce_neutral_pe_disclosure(prose)

    assert "171.11倍" in normalized
    assert "246.92倍" in normalized
    for forbidden in ("可能解释", "并表重估", "前置定价", "筹码博弈"):
        assert forbidden not in normalized


def test_audio_only_vietnam_profit_attribution_is_replaced_with_scope_boundary() -> None:
    prose = (
        "利润下滑是否源于越南产线爬坡仍需核验 "
        "[audio:audio-analysis-20260831T230444Z-645f4a099c]。"
    )

    normalized = IndustryResearchService._enforce_production_accounting_boundaries(
        prose,
        {
            "audio:audio-analysis-20260831T230444Z-645f4a099c",
            "filing:1225505930",
        },
        _huamao_governing_facts(),
    )

    assert "利润下滑是否源于" not in normalized
    assert "不能据此归因华懋科技合并收入、利润、毛利率或同比变化" in normalized
    assert "audio:" not in normalized
    assert IndustryResearchService._production_accounting_policy_failures(normalized) == []


def test_vietnam_attribution_question_fails_even_without_a_number() -> None:
    prose = "利润下滑是否源于越南工厂爬坡仍需核验。"

    failures = IndustryResearchService._production_accounting_policy_failures(prose)

    assert "越南爬坡不得作为上市公司合并业绩的归因候选" in failures


def test_exact_vietnam_subsidiary_filing_fact_is_preserved_and_isolated() -> None:
    prose = (
        "越南新生产基地爬坡及折旧导致越南子公司利润减少978.92万元，"
        "同比下降156.30% [filing:1225505930]。"
    )

    normalized = IndustryResearchService._enforce_production_accounting_boundaries(
        prose,
        {"filing:1225505930"},
        _huamao_governing_facts(),
    )

    assert "越南子公司利润同比减少978.92万元、下降156.30%" in normalized
    assert "不构成华懋科技合并归母净利润的完整归因" in normalized
    assert IndustryResearchService._production_accounting_policy_failures(normalized) == []


def test_wrong_direction_vietnam_numbers_are_not_rewritten_as_a_legal_decline() -> None:
    prose = (
        "2026H1，越南新生产基地爬坡导致越南子公司利润增加978.92万元，"
        "同比增长156.30% [filing:1225505930]。"
    )

    normalized = IndustryResearchService._enforce_production_accounting_boundaries(
        prose,
        {"filing:1225505930"},
        _huamao_governing_facts(),
    )

    assert "利润同比减少978.92万元" not in normalized
    assert "不能据此归因华懋科技合并收入" in normalized


def test_direct_filing_vietnam_qualitative_explanation_is_kept_with_boundary() -> None:
    prose = (
        "2026H1，越南新生产基地爬坡及折旧使盈利空间收窄 "
        "[filing:1225505930]。"
    )

    normalized = IndustryResearchService._enforce_production_accounting_boundaries(
        prose,
        {"filing:1225505930"},
        _huamao_governing_facts(),
    )

    assert "盈利空间有所收窄" in normalized
    assert "未给出各因素对合并归母净利润的量化贡献" in normalized
    assert IndustryResearchService._production_accounting_policy_failures(normalized) == []


def test_generic_vietnam_question_is_upgraded_to_direct_issuer_filing_fact() -> None:
    prose = "越南新生产基地爬坡仅作为待核验经营事项，不能归因合并业绩。"

    normalized = IndustryResearchService._enforce_production_accounting_boundaries(
        prose,
        {"filing:1225505930"},
        _huamao_governing_facts(),
    )

    assert "仅作为待核验" not in normalized
    assert "2025年4月20日投产" in normalized
    assert "2026H1" in normalized
    assert "盈利空间有所收窄" in normalized
    assert "filing:1225505930" in normalized


def test_vietnam_operating_asset_fact_is_not_misclassified_as_profit_attribution() -> None:
    prose = "越南生产基地投入导致在建工程增加 [filing:1225505930]。"

    normalized = IndustryResearchService._enforce_production_accounting_boundaries(
        prose,
        {"filing:1225505930"},
        [],
    )

    assert normalized == prose
    assert IndustryResearchService._production_accounting_policy_failures(normalized) == []


def test_forbidden_main_business_phrase_is_removed_even_when_negated() -> None:
    prose = "现有证据尚不足以将调整后盈利归因于汽车主业内生增长乏力。"

    normalized = IndustryResearchService._enforce_production_accounting_boundaries(
        prose,
        {"filing:1225505930"},
        _huamao_governing_facts(),
    )

    assert "汽车主业内生增长乏力" not in normalized
    assert "不能据此归因于任何单一业务板块" in normalized
    assert IndustryResearchService._production_accounting_policy_failures(normalized) == []


def test_production_adjusted_profit_business_causality_is_replaced_before_other_guards() -> None:
    prose = (
        "汽车安全件主业2026年上半年收入同比下降4.63%，归母净利润同比"
        "下降86.47%，股份支付费用是表观恶化主因；扣除该影响后归母净利润"
        "1.26亿元，同比下降12.15%，显示调整后盈利仍承压 "
        "[filing:1225505930]。"
    )

    normalized = IndustryResearchService._enforce_production_accounting_boundaries(
        prose,
        {"filing:1225505930"},
        _huamao_governing_facts(),
    )

    assert "表观恶化主因" not in normalized
    assert "调整后盈利仍承压" not in normalized
    assert "股份支付费用1.20亿元" in normalized
    assert "扣除股份支付影响后的归母净利润同比下降12.15%" in normalized
    assert IndustryResearchService._production_accounting_policy_failures(normalized) == []


def test_production_adjusted_profit_cannot_prove_main_business_pressure() -> None:
    prose = (
        "华懋科技2026H1法定归母净利润同比下降82.95% "
        "[filing:1225505930]，即便扣除股份支付费用后仍同比下降12.15% "
        "[filing:1225505930]，表明公司主营业务面临切实压力。"
    )

    normalized = IndustryResearchService._enforce_production_accounting_boundaries(
        prose,
        {"filing:1225505930"},
        _huamao_governing_facts(),
    )

    assert "主营业务面临切实压力" not in normalized
    assert "股份支付费用1.20亿元" in normalized
    assert "不能据此归因于任何单一业务板块" in normalized
    assert IndustryResearchService._production_accounting_policy_failures(normalized) == []


def test_semicolon_guard_preserves_independent_filing_fact_before_adjusted_profit() -> None:
    governing = [{
        "metric": "扣除股份支付影响后的归母净利润同比",
        "value": -12.15,
        "supporting_evidence_ids": ["filing:1225505930"],
    }, {
        "metric": "股份支付费用",
        "display_value": "1.20亿元",
        "paired_display_value": "1.26亿元",
        "supporting_evidence_ids": ["filing:1225505930"],
    }]
    prose = (
        "2026年半年报披露公司主营业务产品销售价格承压；"
        "2026H1扣除股份支付影响后的归母净利润1.26亿元，同比下降12.15% "
        "[filing:1225505930]。"
    )

    normalized = IndustryResearchService._enforce_production_accounting_boundaries(
        prose,
        {"filing:1225505930"},
        governing,
    )

    assert "主营业务产品销售价格承压" in normalized
    assert "扣除股份支付影响后的归母净利润同比下降12.15%" in normalized


def _huamao_governing_facts() -> list[dict]:
    return [{
        "metric": "股份支付费用",
        "display_value": "1.20亿元",
        "paired_display_value": "1.26亿元",
        "supporting_evidence_ids": ["filing:1225505930"],
    }, {
        "metric": "扣除股份支付影响后的归母净利润同比",
        "value": -12.15,
        "supporting_evidence_ids": ["filing:1225505930"],
    }, {
        "metric": "归属于上市公司股东的净资产",
        "period": "2026H1",
        "display_value": "38.17亿元",
        "required_sentence": (
            "华懋科技2026年6月30日归属于上市公司股东的净资产"
            "38.17亿元 [filing:1225505930]"
        ),
        "supporting_evidence_ids": ["filing:1225505930"],
    }, {
        "metric": "总资产",
        "period": "2026H1",
        "display_value": "61.71亿元",
        "required_sentence": (
            "华懋科技2026年6月30日总资产61.71亿元 [filing:1225505930]"
        ),
        "supporting_evidence_ids": ["filing:1225505930"],
    }, {
        "metric": "总资产",
        "period": "2025FY",
        "display_value": "59.94亿元",
        "required_sentence": (
            "华懋科技2025年末总资产59.94亿元 [filing:1225505930]"
        ),
        "supporting_evidence_ids": ["filing:1225505930"],
    }]


def _huamao_governing_facts_with_transaction() -> list[dict]:
    return [
        *_huamao_governing_facts(),
        {
            "metric": "交易完成后股权状态",
            "period": "post_transaction_conditional",
            "condition": "仅在本次交易完成后成立",
            "supporting_evidence_ids": ["filing:1225532560"],
        },
        {
            "metric": "交易完成后合并范围状态",
            "period": "post_transaction_conditional",
            "condition": "仅在本次交易完成后成立",
            "supporting_evidence_ids": ["filing:1225532560"],
        },
    ]


def _rendered_chapter(summary: str, open_questions: list[str]) -> dict:
    return {
        "chapter_id": "decision_dashboard",
        "title": "决策仪表盘与下一步尽调",
        "summary": summary,
        "body_markdown": (
            "本章仅列示已绑定固定证据的事实，并把未决事项留在尽调问题中。 "
            "[filing:1225505930]"
        ),
        "open_questions": open_questions,
        "evidence_ids": ["filing:1225505930", "filing:1225532560"],
        "allowed_evidence_ids": ["filing:1225505930", "filing:1225532560"],
        "allowed_figure_ids": [],
    }


def test_storage_governs_summary_and_due_diligence_atoms_with_primary_filings() -> None:
    chapter = _rendered_chapter(
        "2026H1股份支付费用1.20亿元 [filing:1225505930]。",
        [
            (
                "华懋科技2026年6月30日总资产61.71亿元，归母净资产38.17亿元 "
                "[filing:1225505930]。"
            ),
            (
                "若本次交易完成，富创优越将成为全资子公司并纳入合并报表 "
                "[filing:1225532560]。"
            ),
        ],
    )

    stored = IndustryResearchService._sanitize_chapter_for_storage(
        chapter,
        governing_facts=_huamao_governing_facts_with_transaction(),
    )

    assert (
        "股份支付费用1.20亿元，扣除股份支付影响后的归母净利润1.26亿元 "
        "[filing:1225505930]"
    ) in stored["summary"]
    assert any(
        "华懋科技2026年6月30日总资产61.71亿元 [filing:1225505930]"
        in item
        and "华懋科技2026年6月30日归属于上市公司股东的净资产38.17亿元 "
        "[filing:1225505930]" in item
        for item in stored["open_questions"]
    )
    assert any(
        "若本次交易完成，富创优越将成为全资子公司并纳入合并报表 "
        "[filing:1225532560]" in item
        for item in stored["open_questions"]
    )
    for value in [stored["summary"], *stored["open_questions"]]:
        assert IndustryResearchService._production_accounting_policy_failures(value) == []


def test_storage_does_not_wash_wrong_subject_period_prediction_or_negation() -> None:
    cases = [
        "胜宏科技2026H1股份支付费用1.20亿元，调整后归母净利润1.26亿元 [filing:1225505930]。",
        "华懋科技2025H1股份支付费用1.20亿元，调整后归母净利润1.26亿元 [filing:1225505930]。",
        "预计华懋科技2026H1股份支付费用1.20亿元，调整后归母净利润1.26亿元 [filing:1225505930]。",
        "华懋科技2026H1股份支付费用1.20亿元，调整后归母净利润1.26亿元这一说法错误 [filing:1225505930]。",
        "胜宏科技2026H1总资产61.71亿元 [filing:1225505930]。",
        "华懋科技2025H1归母净资产38.17亿元 [filing:1225505930]。",
        "预计华懋科技2026H1总资产61.71亿元 [filing:1225505930]。",
    ]

    for prose in cases:
        stored = IndustryResearchService._sanitize_chapter_for_storage(
            _rendered_chapter(prose, [prose]),
            governing_facts=_huamao_governing_facts_with_transaction(),
        )
        rendered = "\n".join([stored["summary"], *stored["open_questions"]])
        assert "胜宏科技" not in rendered
        assert "2025H1" not in rendered
        assert "预计华懋科技" not in rendered
        assert "说法错误" not in rendered
        assert not (
            "1.20亿元" in rendered
            or "1.26亿元" in rendered
            or "61.71亿元" in rendered
            or "38.17亿元" in rendered
        )


def test_storage_does_not_turn_wrong_units_or_false_transaction_status_canonical() -> None:
    cases = [
        "华懋科技2026H1股份支付费用1.20万元 [filing:1225505930]。",
        "华懋科技2026H1总资产61.71万元 [filing:1225505930]。",
        "富创优越已经成为全资子公司并纳入合并报表。",
        "如果交易不完成，富创优越将成为全资子公司并纳入合并报表 [filing:1225532560]。",
        "富创优越是否已经纳入合并报表？",
    ]

    for prose in cases:
        stored = IndustryResearchService._sanitize_chapter_for_storage(
            _rendered_chapter(prose, [prose]),
            governing_facts=_huamao_governing_facts_with_transaction(),
        )
        rendered = "\n".join([stored["summary"], *stored["open_questions"]])
        assert "1.20万元" not in rendered
        assert "61.71万元" not in rendered
        assert "已经成为全资子公司" not in rendered
        assert "如果交易不完成" not in rendered
        assert "是否已经纳入合并报表" not in rendered


def test_report_quality_audits_due_diligence_questions_not_only_summary() -> None:
    chapter = {
        "chapter_id": "decision_dashboard",
        "title": "决策仪表盘",
        "summary": "本章只列示已核验事实。",
        "body_markdown": "本章正文引用固定证据。 [filing:1225505930]。",
        "open_questions": [
            "富创优越已经成为全资子公司并纳入合并报表。",
        ],
        "allowed_evidence_ids": ["filing:1225505930", "filing:1225532560"],
        "allowed_figure_ids": [],
        "citation_validation": {},
        "model": "test-model",
    }
    snapshot = {
        "evidence": [
            {"evidence_id": "filing:1225505930", "evidence_level": "factual", "kind": "filing_text"},
            {"evidence_id": "filing:1225532560", "evidence_level": "factual", "kind": "filing_text"},
        ],
        "data_quality": {"status": "ready", "overall_score": 100, "critical_gaps": []},
        "source_plan": [],
    }

    result = IndustryResearchService._verify_report_quality(
        snapshot,
        [chapter],
        chapter["body_markdown"],
        editorial_review={
            "status": "completed",
            "release_recommendation": "ready",
            "unsupported_claims": [],
            "numeric_conflicts": [],
            "contradictions": [],
        },
    )

    failures = result["metrics"]["auxiliary_policy_failures"]
    assert any(item.startswith("open_question:decision_dashboard:1:") for item in failures)
    assert any("条件必须在同一句明示" in item for item in failures)


def test_h1_total_assets_requires_the_half_year_filing_not_financial_snapshot() -> None:
    prose = (
        "华懋科技2026年6月30日总资产61.71亿元 "
        "[financial:603306.SH:20260630]。"
    )

    failures = IndustryResearchService._production_accounting_policy_failures(prose)

    assert any(
        "2026H1总资产61.71亿元必须在同一事实原子引用对应H1一级证据" in item
        for item in failures
    )


def test_mixed_balance_sentence_is_split_into_governed_neutral_atoms() -> None:
    prose = (
        "2026年6月30日公司总资产61.71亿元，归母净资产38.17亿元，"
        "2025年末总资产59.94亿元 [filing:1225505930]，这是正面证据。"
    )

    normalized = IndustryResearchService._enforce_production_accounting_boundaries(
        prose,
        {"filing:1225505930"},
        _huamao_governing_facts(),
    )

    assert "2026年6月30日总资产61.71亿元" in normalized
    assert "2026年6月30日归属于上市公司股东的净资产38.17亿元" in normalized
    assert "2025年末总资产59.94亿元" in normalized
    assert "正面证据" not in normalized
    assert "不构成跨期增长或正面评价" in normalized


def test_revenue_profit_gap_is_not_turned_into_cost_attribution() -> None:
    prose = "营收微降与利润大降的错位，表明成本端或费用端压力。"

    normalized = IndustryResearchService._enforce_production_accounting_boundaries(
        prose,
        {"filing:1225505930"},
        _huamao_governing_facts(),
    )

    assert "表明成本端" not in normalized
    assert "不足以据此归因具体成本、费用、减值或产品结构因素" in normalized


def test_transaction_documents_do_not_become_compliance_opinion() -> None:
    prose = "四次修订加上沃克森评估报告，说明程序合规性具备基础。"

    normalized = IndustryResearchService._enforce_production_accounting_boundaries(
        prose,
        {"filing:1225532560"},
        [{
            "metric": "交易完成后股权状态",
            "supporting_evidence_ids": ["filing:1225532560"],
        }, {
            "metric": "交易完成后合并范围状态",
            "supporting_evidence_ids": ["filing:1225532560"],
        }],
    )

    assert "程序合规性具备基础" not in normalized
    assert "不构成监管合规结论" in normalized
    assert "filing:1225532560" in normalized


def test_adjusted_profit_is_not_renamed_underlying_earning_power() -> None:
    prose = (
        "2026H1法定归母净利润0.23亿元与调整后归母净利润1.26亿元的差异，"
        "说明underlying盈利能力面临压力 [filing:1225505930]。"
    )

    normalized = IndustryResearchService._enforce_production_accounting_boundaries(
        prose,
        {"filing:1225505930"},
        _huamao_governing_facts(),
    )

    assert "underlying" not in normalized
    assert "股份支付费用1.20亿元" in normalized
    assert "不能据此归因于任何单一业务板块" in normalized


def test_share_payment_question_drops_numeric_thresholds() -> None:
    prose = "若2026全年股份支付费用显著高于1.20亿元，需要跟踪利润影响。"

    normalized = IndustryResearchService._enforce_production_accounting_boundaries(
        prose,
        {"filing:1225505930"},
        _huamao_governing_facts(),
    )

    assert "1.20亿元" not in normalized
    assert "完整会计调节及同比口径" in normalized


def test_completed_audio_projection_is_not_reported_missing() -> None:
    prose = "录音线索安全投影尚未生成，不能使用。"

    normalized = IndustryResearchService._enforce_production_accounting_boundaries(
        prose,
        {"audio:audio-analysis-20260831T235616Z-b34c511f4d"},
        [],
    )

    assert "安全投影尚未生成" not in normalized
    assert "仅纳入已完成安全投影" in normalized


def test_production_adjusted_yoy_atom_uses_one_comma_bound_citation() -> None:
    prose = (
        "2026H1扣除股份支付影响后的归母净利润同比下降12.15%；"
        "该口径不能归因汽车主业 [filing:1225505930]。"
    )

    normalized = IndustryResearchService._enforce_production_accounting_boundaries(
        prose,
        {"filing:1225505930"},
        _huamao_governing_facts(),
    )

    atom = (
        "2026H1，扣除股份支付影响后的归母净利润同比下降12.15%，"
        "该调整后合并口径不能据此归因于任何单一业务板块 "
        "[filing:1225505930]"
    )
    assert atom in normalized
    assert "12.15%；" not in normalized
    assert IndustryResearchService._production_accounting_policy_failures(normalized) == []


def test_every_legacy_audio_projection_missing_phrase_becomes_completed_semantics() -> None:
    production_variants = (
        "本次快照中的机构纪要安全投影未生成，不能进入报告。",
        "录音线索安全投影尚未生成，不能使用。",
        "公司录音未处理，因此没有可用材料。",
        "音频尚未处理，待后续再分析。",
    )

    for prose in production_variants:
        normalized = IndustryResearchService._enforce_production_accounting_boundaries(
            prose,
            {"audio:audio-analysis-20260831T235616Z-b34c511f4d"},
            [],
        )
        assert "仅纳入已完成安全投影" in normalized
        assert "一级证据确认事实与待核验定性主题分层使用" in normalized
        assert "未生成" not in normalized
        assert "未处理" not in normalized
        auxiliary = IndustryResearchService._sanitize_governed_chapter_auxiliary_text(
            prose,
            {"audio:audio-analysis-20260831T235616Z-b34c511f4d"},
            [],
        )
        assert "仅纳入已完成安全投影" in auxiliary
        assert "未生成" not in auxiliary
        assert "未处理" not in auxiliary


def test_canonical_vietnam_boundary_is_idempotent_and_unique_across_chapters() -> None:
    production_sentence = (
        "利润下滑是否源于越南新生产基地爬坡及折旧仍需核验，"
        "但目前没有分部量化数据。"
    )
    first = IndustryResearchService._enforce_production_accounting_boundaries(
        production_sentence,
        {"filing:1225505930"},
        _huamao_governing_facts(),
    )
    second = IndustryResearchService._enforce_production_accounting_boundaries(
        first,
        {"filing:1225505930"},
        _huamao_governing_facts(),
    )
    assert first == second

    filler = "本章只解释证据边界，不增加未经核验的公司事实。" * 95
    chapters = [
        {
            "chapter_id": chapter_id,
            "title": chapter_id,
            "summary": first,
            "body_markdown": f"{filler}\n\n{first}",
            "open_questions": [first],
            "allowed_evidence_ids": ["filing:1225505930"],
            "allowed_figure_ids": [],
            "citation_validation": {"revision_attempted": True},
        }
        for chapter_id in ("business_model", "financials", "events_risks")
    ]
    finalized = IndustryResearchService._deduplicate_canonical_safety_across_chapters(
        chapters
    )
    rendered = "\n".join(
        str(value)
        for chapter in finalized
        for value in (
            chapter.get("body_markdown"), chapter.get("summary"),
            *(chapter.get("open_questions") or []),
        )
    )
    assert rendered.count(first) == 1


def test_goodwill_ppa_boundary_variants_are_one_semantic_atom_across_chapters() -> None:
    variants = (
        "最终商誉须待交易完成并完成PPA，依据购买日可辨认净资产公允价值确认，目前无法确定金额。",
        "当前不能由交易对价确认商誉，购买价分摊PPA需结合可辨认净资产公允价值后确认。",
        "商誉金额尚无法判断，需待PPA完成并核验可辨认净资产公允价值。",
    )
    seen: set[str] = set()
    rendered = "\n".join(
        IndustryResearchService._deduplicate_canonical_safety_text(value, seen)
        for value in variants
    )

    assert rendered.count("本次交易的最终商誉金额不能由交易对价或持股比例直接计算") == 1
    assert "当前不能由交易对价确认商誉" not in rendered
    assert "商誉金额尚无法判断" not in rendered


def test_auxiliary_adjusted_profit_is_paired_or_naturalized_before_storage() -> None:
    cited = _rendered_chapter(
        "2026H1调整后归母净利润1.26亿元 [filing:1225505930]。",
        ["调整后归母净利润1.26亿元如何理解？"],
    )
    stored = IndustryResearchService._sanitize_chapter_for_storage(
        cited,
        governing_facts=_huamao_governing_facts(),
    )
    rendered = "\n".join([stored["summary"], *stored["open_questions"]])

    assert (
        "股份支付费用1.20亿元，扣除股份支付影响后的归母净利润1.26亿元 "
        "[filing:1225505930]"
    ) in rendered
    for item in [stored["summary"], *stored["open_questions"]]:
        if "1.26亿元" in item:
            assert "股份支付费用1.20亿元" in item
            assert "[filing:1225505930]" in item
            assert "汽车主业" not in item
            assert "光通信业务" not in item
        assert IndustryResearchService._production_accounting_policy_failures(item) == []


def test_final_storage_naturalizes_uncited_auxiliary_numbers_and_revalidates() -> None:
    filler = "固定快照只用于说明研究边界，正文不增加任何未经核验的公司事实。" * 82
    chapter = {
        "chapter_id": "decision_dashboard",
        "title": "决策仪表盘",
        "summary": "预计全年利润达到8亿元，汽车主业贡献6亿元。",
        "body_markdown": f"{filler} [filing:1225505930]。",
        "open_questions": [
            "管理层提出2027年收入达到30亿元，后续如何验证？",
            "未来客户数量是否超过20家？",
        ],
        "allowed_evidence_ids": ["filing:1225505930"],
        "allowed_figure_ids": [],
        "validation_failures": ["旧轮次遗留失败"],
        "citation_validation": {
            "revision_attempted": True,
            "revision_accepted": False,
        },
    }

    finalized = IndustryResearchService._deduplicate_canonical_safety_across_chapters(
        [chapter]
    )[0]

    assert "8亿元" not in finalized["summary"]
    assert "6亿元" not in finalized["summary"]
    assert all("30亿元" not in item and "20家" not in item for item in finalized["open_questions"])
    assert finalized["validation_failures"] == []
    assert finalized["citation_validation"]["final_storage_revalidated"] is True
    assert finalized["citation_validation"]["storage_validation_acceptable"] is True
    assert finalized["citation_validation"]["revision_accepted"] is True
    for value in [finalized["summary"], *finalized["open_questions"]]:
        citations = IndustryResearchService._citation_audit(
            value, {"filing:1225505930"},
        )
        assert citations["uncited_numeric_excerpts"] == []
        assert IndustryResearchService._production_accounting_policy_failures(value) == []


def test_eight_chapter_final_storage_is_revalidated_with_one_vietnam_and_ppa_boundary() -> None:
    vietnam = IndustryResearchService._enforce_production_accounting_boundaries(
        "越南新生产基地爬坡可能导致集团利润下滑，具体影响待核验。",
        {"filing:1225505930"},
        _huamao_governing_facts(),
    )
    ppa_variant = (
        "最终商誉须待交易完成并完成PPA，依据购买日可辨认净资产公允价值确认，"
        "目前无法确定金额。"
    )
    filler = "固定证据边界已经明确，章节只保留经过逐句核验的研究结论。" * 88
    chapter_ids = (
        "company_scope", "industry_position", "business_model", "financials",
        "expectations_valuation", "events_risks", "market_validation",
        "decision_dashboard",
    )
    chapters = [{
        "chapter_id": chapter_id,
        "title": chapter_id,
        "summary": "本章结论以固定快照和逐句核验为准。",
        "body_markdown": f"{filler}\n\n{vietnam}\n\n{ppa_variant}",
        "open_questions": [],
        "allowed_evidence_ids": ["filing:1225505930"],
        "allowed_figure_ids": [],
        "validation_failures": ["旧轮次失败"],
        "citation_validation": {
            "revision_attempted": True,
            "revision_accepted": False,
        },
    } for chapter_id in chapter_ids]

    finalized = IndustryResearchService._deduplicate_canonical_safety_across_chapters(
        chapters
    )
    rendered = "\n".join(str(item.get("body_markdown") or "") for item in finalized)

    assert len(finalized) == 8
    assert all(
        item["citation_validation"]["final_storage_revalidated"]
        and item["citation_validation"]["storage_validation_acceptable"]
        and item["citation_validation"]["revision_accepted"]
        and not item["validation_failures"]
        for item in finalized
    )
    assert rendered.count(vietnam) == 1
    assert rendered.count(
        "本次交易的最终商誉金额不能由交易对价或持股比例直接计算"
    ) == 1


def test_future_evidence_request_years_are_not_fact_number_claims() -> None:
    prose = (
        "仍需补充以下一级证据：2024年与2025年年度报告、"
        "2026H1财务明细，以及2026年订单与产能利用率访谈。"
    )

    assert IndustryResearchService._is_nonassertive_research_process_paragraph(prose)


def test_future_evidence_request_with_measured_value_still_fails_closed() -> None:
    prose = "仍需补充以下一级证据：2026H1营业收入10亿元的法定明细。"

    assert not IndustryResearchService._is_nonassertive_research_process_paragraph(prose)


def test_huamao_guard_never_rewrites_an_explicit_foreign_company() -> None:
    prose = "乙公司汽车主业内生增长乏力。"

    normalized = IndustryResearchService._enforce_production_accounting_boundaries(
        prose,
        {"filing:1225505930"},
        _huamao_governing_facts(),
    )

    assert normalized == prose
    assert "华懋科技" not in normalized


def test_balance_guard_rejects_wrong_period_delta_and_disallowed_source() -> None:
    cases = [
        (
            "华懋科技2025年末总资产61.71亿元 [filing:1225505930]。",
            {"filing:1225505930"},
        ),
        (
            "华懋科技2026H1总资产同比增加61.71亿元 [filing:1225505930]。",
            {"filing:1225505930"},
        ),
        (
            "华懋科技2026H1总资产61.71亿元 "
            "[financial:603306.SH:20260630]。",
            {"filing:1225505930"},
        ),
    ]

    for prose, allowed in cases:
        normalized = IndustryResearchService._enforce_production_accounting_boundaries(
            prose,
            allowed,
            _huamao_governing_facts(),
        )
        assert normalized == prose


def test_profit_guard_never_repairs_wrong_period() -> None:
    prose = (
        "华懋科技2025H1归母净利润2328.57万元，调整后归母净利润1.26亿元 "
        "[filing:1225505930]。"
    )

    normalized = IndustryResearchService._enforce_production_accounting_boundaries(
        prose,
        {"filing:1225505930"},
        _huamao_governing_facts(),
    )

    assert normalized == prose
    assert "2026H1" not in normalized


def test_directional_pe_with_trailing_citations_never_fabricates_pairing() -> None:
    prose = (
        "2026年8月20日PE(TTM)为171.11倍，2026年8月31日跃升至246.92倍 "
        "[valuation:603306.SH:20260820] [valuation:603306.SH:20260831]。"
    )

    normalized = IndustryResearchService._enforce_neutral_pe_disclosure(prose)

    assert "2026年8月20日PE(TTM)为246.92倍" not in normalized
    assert "跃升" not in normalized


def test_opex_and_capex_multiples_are_not_misread_as_pe() -> None:
    prose = "OPEX为2.0倍，CAPEX为3.0倍 [filing:example]。"

    assert IndustryResearchService._pe_observation_values(prose) == set()
    assert IndustryResearchService._enforce_neutral_pe_disclosure(prose) == prose


def test_non_pe_high_base_sentence_survives_later_pe_context() -> None:
    prose = (
        "2026年8月20日PE(TTM)为171.11倍 [valuation:603306.SH:20260820]，"
        "2026年8月31日为246.92倍 [valuation:603306.SH:20260831]。\n\n"
        "2025年营收下滑主要源于高基数退出 [filing:example]。"
    )

    normalized = IndustryResearchService._enforce_neutral_pe_disclosure(prose)

    assert "营收下滑主要源于高基数退出" in normalized


def test_research_request_with_bare_capacity_measure_is_not_exempt() -> None:
    prose = "仍需补充以下一级证据：公司2026年完成收购，新增产能10万吨。"

    assert not IndustryResearchService._is_nonassertive_research_process_paragraph(prose)


def test_huamao_guard_does_not_rewrite_arbitrary_peer_names_or_peer_sources() -> None:
    cases = [
        "海利得越南工厂爬坡导致集团净利润下降 [filing:peer]。",
        "海利得汽车主业内生增长乏力。",
        "贵州茅台2026H1归母净利润2328.57万元 [filing:1225505930]。",
    ]
    allowed = {"filing:1225505930", "filing:peer"}

    for prose in cases:
        normalized = IndustryResearchService._enforce_production_accounting_boundaries(
            prose,
            allowed,
            _huamao_governing_facts(),
        )
        assert normalized == prose


def test_profit_and_share_guards_never_wash_prediction_correction_or_fake_citation() -> None:
    cases = [
        "预计2026H1归母净利润2328.57万元 [filing:1225505930]。",
        "2026H1归母净利润2328.57万元这一说法错误 [filing:1225505930]。",
        (
            "2026H1归母净利润2328.57万元 "
            "[filing:1225505930] [fake:1]。"
        ),
        (
            "预计2026H1股份支付费用1.20亿元，调整后归母净利润1.26亿元 "
            "[filing:1225505930]。"
        ),
    ]

    for prose in cases:
        normalized = IndustryResearchService._enforce_production_accounting_boundaries(
            prose,
            {"filing:1225505930"},
            _huamao_governing_facts(),
        )
        assert normalized == prose


def test_generic_business_explanation_survives_elsewhere_in_a_pe_chapter() -> None:
    prose = (
        "2026年8月20日PE(TTM)为171.11倍 [valuation:603306.SH:20260820]，"
        "2026年8月31日为246.92倍 [valuation:603306.SH:20260831]。\n\n"
        "营收下降的可能解释方向包括需求放缓 [filing:example]。\n\n"
        "毛利率下降，原因可能是原材料上涨 [filing:example]。"
    )

    normalized = IndustryResearchService._enforce_neutral_pe_disclosure(prose)

    assert "营收下降的可能解释方向包括需求放缓" in normalized
    assert "毛利率下降，原因可能是原材料上涨" in normalized


def test_pe_price_or_earnings_driver_question_is_removed() -> None:
    prose = (
        "2026年8月20日PE(TTM)为171.11倍 [valuation:603306.SH:20260820]，"
        "2026年8月31日为246.92倍 [valuation:603306.SH:20260831]。"
        "无法仅凭PE判断驱动端来自价格还是盈利，后续需分解PE(TTM)变化的驱动端。"
    )

    normalized = IndustryResearchService._enforce_neutral_pe_disclosure(prose)

    assert "171.11倍" in normalized
    assert "246.92倍" in normalized
    assert "驱动端" not in normalized
    assert "价格还是盈利" not in normalized
    assert "差异待核验" in normalized


def test_q2_ytd_minus_q1_derivation_is_not_published_as_single_quarter_fact() -> None:
    prose = (
        "2026Q2单季营业收入约5.79亿元、净利润约0.11亿元，"
        "由H1累计减Q1推算，非直接披露 [filing:1225505930]。"
    )

    normalized = IndustryResearchService._enforce_production_accounting_boundaries(
        prose,
        {"filing:1225505930"},
        _huamao_governing_facts(),
    )

    assert "5.79" not in normalized
    assert "0.11" not in normalized
    assert "累计值相减推算" in normalized
    assert "法定单季口径直接支持" in normalized


def test_2025_profit_gap_never_backsolves_share_payment() -> None:
    prose = (
        "2025H1归母净利润与扣非归母净利润差额0.12亿元，"
        "据此反推为股份支付费用。"
    )

    normalized = IndustryResearchService._enforce_production_accounting_boundaries(
        prose,
        {"filing:1225505930"},
        _huamao_governing_facts(),
    )

    assert "0.12" not in normalized
    assert "股份支付费用1.20亿元，较上年同期增加1.12亿元" in normalized
    assert "扣除股份支付影响后的归母净利润1.26亿元" in normalized
    assert "filing:1225505930" in normalized


def test_cashflow_guess_is_replaced_by_direct_half_year_filing_explanation() -> None:
    prose = (
        "经营现金流改善可能源于股份支付等非现金费用，"
        "也可能反映营运资本变动。"
    )

    normalized = IndustryResearchService._enforce_production_accounting_boundaries(
        prose,
        {"filing:1225505930"},
        _huamao_governing_facts(),
    )

    assert "可能源于股份支付" not in normalized
    assert "营运资本变动" not in normalized
    assert "购买商品、接受劳务支付的现金减少" in normalized
    assert "filing:1225505930" in normalized


def test_uncited_strategic_cost_story_is_not_kept_as_a_2026_fact() -> None:
    prose = (
        "战略转型费用前置论：公司推进收购可能产生中介费用、研发投入或"
        "团队扩张成本，但2026年半年报未单独披露，该解释停留在推测层面。"
    )

    normalized = IndustryResearchService._enforce_production_accounting_boundaries(
        prose,
        {"filing:1225505930"},
        _huamao_governing_facts(),
    )

    assert "2026年" not in normalized
    assert "该解释不进入事实层" in normalized


def test_uncited_revenue_adjusted_profit_comparison_becomes_governed_h1_atom() -> None:
    prose = "2026年上半年收入微降而调整后利润双位数下滑，具体机制待核验。"

    normalized = IndustryResearchService._enforce_production_accounting_boundaries(
        prose,
        {"filing:1225505930"},
        _huamao_governing_facts(),
    )

    assert "收入微降" not in normalized
    assert "扣除股份支付影响后的归母净利润同比下降12.15%" in normalized
    assert "filing:1225505930" in normalized


def test_uncited_gross_margin_threshold_is_removed_not_relabeled_qualitative() -> None:
    prose = "2025年全年及上半年公司毛利率维持在30%以上，2026年下滑持续性待验证。"

    normalized = IndustryResearchService._enforce_production_accounting_boundaries(
        prose,
        {"filing:1225505930"},
        _huamao_governing_facts(),
    )

    assert "30%以上" not in normalized
    assert "法定同口径数据验证" in normalized


def test_v30_fresh_cashflow_conflict_is_replaced_by_available_h1_fact() -> None:
    prose = (
        "2026年第一季度，经营活动产生的现金流量净额为1.17亿元，"
        "较上年同期增长514.20% [filing:1225224760]。"
        "该增幅源于上年同期基数较低（0.19亿元）。"
        "半年度现金流数据未在提供的底稿中完整披露，"
        "现有证据不足以判断H1经营现金流趋势。"
    )

    normalized = IndustryResearchService._enforce_production_accounting_boundaries(
        prose,
        {"filing:1225224760", "filing:1225505930"},
        _huamao_governing_facts(),
    )

    assert "514.20%" not in normalized
    assert "0.19亿元" not in normalized
    assert "半年度现金流数据未" not in normalized
    assert "2026H1，经营活动产生的现金流量净额2.83亿元" in normalized
    assert "[filing:1225505930]" in normalized


def test_v30_unpaired_adjusted_profit_is_restored_to_governing_pair() -> None:
    prose = (
        "法定归母净利润0.23亿元与调整后1.26亿元之间的差异，"
        "主要源于股份支付这一非现金费用。"
    )

    normalized = IndustryResearchService._enforce_production_accounting_boundaries(
        prose,
        {"filing:1225505930"},
        _huamao_governing_facts(),
    )

    assert "主要源于" not in normalized
    assert "股份支付费用1.20亿元" in normalized
    assert "扣除股份支付影响后的归母净利润1.26亿元" in normalized
    assert not IndustryResearchService._production_accounting_policy_failures(normalized)


def test_v30_missing_consolidation_fact_downgrades_to_supported_ownership_state() -> None:
    facts = [
        item for item in _huamao_governing_facts_with_transaction()
        if item.get("metric") != "交易完成后合并范围状态"
    ]
    prose = "仅在交易完成后，富创优越的财务数据方可纳入上市公司合并报表。"

    normalized = IndustryResearchService._enforce_production_accounting_boundaries(
        prose,
        {"filing:1225505930", "filing:1225532560"},
        facts,
    )

    assert "纳入合并报表" not in normalized
    assert (
        "若本次交易完成，富创优越将成为全资子公司 "
        "[filing:1225532560]"
    ) in normalized


def test_v30_editor_wording_finding_does_not_blacklist_supported_number() -> None:
    finding = {
        "type": "unsupported_claim",
        "claim": "2026年上半年营业收入同比微降1.53%",
        "reason": "微降为主观判断，应使用中性表述下降1.53%并保留同句引用。",
        "evidence_ids": ["filing:1225505930"],
    }

    assert IndustryResearchService._editorial_finding_number_keys(finding) == set()


def test_v30_question_only_interview_ledger_is_not_a_company_fact() -> None:
    prose = (
        "1. 2026H1股份支付费用的授予对象与剩余摊销计划？ "
        "2. 富创优越42.16%股权对应的权益法投资收益金额？ "
        "3. 若交易完成，管理层留任安排是否存在？"
    )

    assert IndustryResearchService._is_nonassertive_research_process_paragraph(prose)


def test_v30_valuation_pe_chart_supports_date_only_chart_description() -> None:
    prose = "图表【valuation_pe｜PE(TTM)变化】展示2026年8月20日至31日的估值路径。"

    assert IndustryResearchService._has_allowed_temporal_figure(
        prose, {"valuation_pe"},
    )


def test_v30_profit_cash_bridge_split_sentences_do_not_publish_unsupported_causality() -> None:
    prose = (
        "图表【profit_cash_bridge｜归母净利润与经营现金流】用于观察利润与现金流。"
        "2026H1净利润与经营现金流出现较大背离，现金流相对稳健，"
        "说明非现金项目压制了利润。"
    )

    normalized = IndustryResearchService._enforce_production_accounting_boundaries(
        prose,
        {"filing:1225505930"},
        _huamao_governing_facts(),
    )

    assert "说明非现金项目压制" not in normalized
    assert "主要系购买商品、接受劳务支付的现金减少" in normalized
    assert "[filing:1225505930]" in normalized


def test_v30_quality_gate_counts_body_only_length_and_pure_length_failure() -> None:
    chapter_ids = [
        "company_scope", "business_model", "industry_position",
        "technology_operations", "financials", "expectations_valuation",
        "events_risks", "decision_dashboard",
    ]
    body = "固定证据快照内的事实用于形成可复核研究结论。" * 100 + " [event:1]"
    chapters = [
        {
            "chapter_id": chapter_id,
            "title": chapter_id,
            "summary": "研究摘要。",
            "body_markdown": body,
            "evidence_ids": ["event:1"],
            "allowed_evidence_ids": ["event:1"],
            "allowed_figure_ids": [],
            "model": "kimi-for-coding",
            "validation_failures": (
                ["正文 2300 字，低于 2400 字"] if chapter_id == "business_model" else []
            ),
            "citation_validation": {},
        }
        for chapter_id in chapter_ids
    ]
    snapshot = {
        "evidence": [{
            "evidence_id": "event:1", "kind": "event",
            "evidence_level": "factual", "summary": "事实证据",
        }],
        "visualizations": [
            {
                "id": f"figure-{index}", "analytical_question": "验证什么？",
                "insight": "仅展示固定快照。", "unit": "条", "source": "event:1",
            }
            for index in range(5)
        ],
        "media_gallery": [],
        "data_quality": {"status": "ready", "overall_score": 100, "critical_gaps": []},
        "source_plan": [],
        "governing_statutory_facts": [],
    }
    editorial_review = {
        "status": "completed", "release_recommendation": "ready",
        "unsupported_claims": [], "numeric_conflicts": [], "contradictions": [],
        "revision_cycle": {"attempted": True, "failed_chapters": []},
        "final_text_review": {"performed": True},
    }
    assembled = "\n\n".join(item["body_markdown"] for item in chapters) + ("附录资料。" * 600)

    quality = IndustryResearchService._verify_report_quality(
        snapshot, chapters, assembled, editorial_review=editorial_review,
    )

    assert quality["metrics"]["narrative_chars"] >= 20_000
    assert quality["metrics"]["chapter_body_chars"] < 20_000
    assert any("八章研究正文合计" in item for item in quality["critical_failures"])
    assert any("1 个章节未通过" in item for item in quality["critical_failures"])


def _v30_final_editor_evidence() -> dict[str, dict[str, object]]:
    return {
        "financial:603306.SH:20251231": {
            "evidence_id": "financial:603306.SH:20251231",
            "kind": "financial_statement",
            "symbol": "603306.SH",
            "title": "华懋科技2025年年度财务快照",
            "summary": "华懋科技2025年末总资产5993670009.88元。",
            "period": "20251231",
        },
        "filing:1225224760": {
            "evidence_id": "filing:1225224760",
            "kind": "filing_text",
            "symbol": "603306.SH",
            "title": "华懋科技2026年第一季度报告",
            "summary": "华懋科技2026年第一季度归母净利润11,696,307.92元。",
            "document_text": "归属于上市公司股东的净利润11,696,307.92元。",
        },
        "financial:603306.SH:20260331": {
            "evidence_id": "financial:603306.SH:20260331",
            "kind": "financial_statement",
            "symbol": "603306.SH",
            "title": "华懋科技2026年第一季度财务快照",
            "summary": "华懋科技2026年第一季度归母净利润11696307.92元。",
            "period": "20260331",
        },
        "valuation:603306.SH:20260820": {
            "evidence_id": "valuation:603306.SH:20260820",
            "kind": "valuation_fact",
            "symbol": "603306.SH",
            "title": "华懋科技2026年8月20日估值快照",
            "summary": "华懋科技2026年8月20日PE(TTM)为171.11倍。",
        },
        "valuation:603306.SH:20260831": {
            "evidence_id": "valuation:603306.SH:20260831",
            "kind": "valuation_fact",
            "symbol": "603306.SH",
            "title": "华懋科技2026年8月31日估值快照",
            "summary": "华懋科技2026年8月31日PE(TTM)为246.92倍。",
        },
    }


def _v30_final_editor_subject() -> dict[str, str]:
    return {"name": "华懋科技", "symbol": "603306.SH"}


def _v30_year_end_assets_review() -> dict[str, object]:
    return {
        "release_recommendation": "limited",
        "unsupported_claims": [{
            "chapter": "company_scope",
            "entity": "华懋科技",
            "claim": "华懋科技2025年末总资产为59.94亿元。",
            "reason": "需要确认全部出现位置均逐句引用对应一级证据。",
            "evidence_ids": ["financial:603306.SH:20251231"],
        }],
        "numeric_conflicts": [],
        "contradictions": [],
    }


def test_v30_final_editor_year_end_assets_requires_every_occurrence_to_be_safe() -> None:
    evidence = _v30_final_editor_evidence()
    safe = (
        "华懋科技2025年末总资产为59.94亿元 "
        "[financial:603306.SH:20251231]。"
    )
    unsafe = (
        "华懋科技2026年上半年末总资产为59.94亿元 "
        "[financial:603306.SH:20251231]。"
    )

    failed = IndustryResearchService._reconcile_supported_editorial_findings(
        _v30_year_end_assets_review(),
        [
            {"chapter_id": "company_scope", "body_markdown": safe, "summary": ""},
            {"chapter_id": "decision_dashboard", "body_markdown": unsafe, "summary": ""},
        ],
        evidence,
        expected_subject=_v30_final_editor_subject(),
    )

    assert len(failed["unsupported_claims"]) == 1
    assert failed.get("resolved_supported_claims") in (None, [])

    passed = IndustryResearchService._reconcile_supported_editorial_findings(
        _v30_year_end_assets_review(),
        [
            {"chapter_id": "company_scope", "body_markdown": safe, "summary": ""},
            {"chapter_id": "decision_dashboard", "body_markdown": safe, "summary": ""},
        ],
        evidence,
        expected_subject=_v30_final_editor_subject(),
    )

    assert passed["unsupported_claims"] == []
    assert len(passed["resolved_supported_claims"][0]["supporting_sentences"]) == 2


def _v30_year_end_equity_conflict() -> dict[str, object]:
    return {
        "type": "numeric_conflict",
        "entity": "华懋科技",
        "metric": "2025年末归母净资产",
        "values": ["34.30亿元（隐含推导值）"],
        "units": ["亿元"],
        "periods": ["2025年末"],
        "accounting_bases": ["statutory_attributable_equity"],
        "evidence_ids": [],
        "resolution": "未解决：该隐含推导值没有一级证据。",
    }


def test_v30_final_editor_only_clears_3430_after_value_and_derivative_disappear() -> None:
    base_review = {
        "release_recommendation": "limited",
        "unsupported_claims": [],
        "numeric_conflicts": [_v30_year_end_equity_conflict()],
        "contradictions": [],
    }
    clean = IndustryResearchService._reconcile_final_editorial_state(
        base_review,
        [{
            "chapter_id": "financials",
            "body_markdown": "华懋科技2026H1归母净资产为38.17亿元。",
            "summary": "",
        }],
        _v30_final_editor_evidence(),
        expected_subject=_v30_final_editor_subject(),
    )

    finding = clean["numeric_conflicts"][0]
    assert finding["program_verification"] == "absent_disputed_value_v30"
    assert IndustryResearchService._review_issue_resolved(finding)
    assert clean["release_recommendation"] == "ready"

    residue_cases = (
        "华懋科技2025年末归母净资产为34.30亿元。",
        "华懋科技归母净资产较2025年末增长11.28%。",
    )
    for residue in residue_cases:
        blocked = IndustryResearchService._reconcile_final_editorial_state(
            base_review,
            [{"chapter_id": "financials", "body_markdown": residue, "summary": ""}],
            _v30_final_editor_evidence(),
            expected_subject=_v30_final_editor_subject(),
        )
        assert not IndustryResearchService._review_issue_resolved(
            blocked["numeric_conflicts"][0]
        )
        assert blocked["release_recommendation"] == "limited"


def _v30_q1_profit_conflict() -> dict[str, object]:
    return {
        "type": "numeric_conflict",
        "entity": "华懋科技",
        "metric": "2026Q1归母净利润",
        "values": ["0.12亿元", "1169.63万元", "11,696,307.92元"],
        "units": ["亿元", "万元", "元"],
        "periods": ["2026Q1"],
        "accounting_bases": ["statutory"],
        "evidence_ids": [
            "filing:1225224760",
            "financial:603306.SH:20260331",
        ],
        "resolution": "精度差异待终稿统一。",
    }


def test_v30_final_editor_arbitrates_q1_profit_as_one_exact_statutory_fact() -> None:
    base_review = {
        "release_recommendation": "limited",
        "unsupported_claims": [],
        "numeric_conflicts": [_v30_q1_profit_conflict()],
        "contradictions": [],
    }
    canonical = (
        "华懋科技2026年第一季度归母净利润为11,696,307.92元 "
        "[filing:1225224760]。"
    )
    resolved = IndustryResearchService._reconcile_final_editorial_state(
        base_review,
        [{"chapter_id": "financials", "body_markdown": canonical, "summary": ""}],
        _v30_final_editor_evidence(),
        expected_subject=_v30_final_editor_subject(),
    )

    finding = resolved["numeric_conflicts"][0]
    assert finding["program_verification"] == "governing_same_fact_representation_v25"
    assert IndustryResearchService._review_issue_resolved(finding)
    assert resolved["release_recommendation"] == "ready"

    for noncanonical in (
        "华懋科技2026年第一季度归母净利润为0.12亿元 [filing:1225224760]。",
        "华懋科技2026年第一季度归母净利润为11,796,307.92元 [filing:1225224760]。",
    ):
        blocked = IndustryResearchService._reconcile_final_editorial_state(
            base_review,
            [{
                "chapter_id": "financials",
                "body_markdown": noncanonical,
                "summary": "",
            }],
            _v30_final_editor_evidence(),
            expected_subject=_v30_final_editor_subject(),
        )
        assert not IndustryResearchService._review_issue_resolved(
            blocked["numeric_conflicts"][0]
        )
        assert blocked["release_recommendation"] == "limited"

    bad_evidence = _v30_final_editor_evidence()
    bad_evidence["financial:603306.SH:20260331"] = {
        **bad_evidence["financial:603306.SH:20260331"],
        "summary": "华懋科技2026年第一季度归母净利润11696308.92元。",
    }
    blocked_source = IndustryResearchService._reconcile_final_editorial_state(
        base_review,
        [{"chapter_id": "financials", "body_markdown": canonical, "summary": ""}],
        bad_evidence,
        expected_subject=_v30_final_editor_subject(),
    )
    assert blocked_source["release_recommendation"] == "limited"


def _v30_pe_directionality_contradiction() -> dict[str, object]:
    return {
        "issue": "PE(TTM)从171.11倍波动至246.92倍仍带方向性，应改为中性列示。",
        "chapters": "expectations_valuation, decision_dashboard",
        "evidence_ids": [
            "valuation:603306.SH:20260820",
            "valuation:603306.SH:20260831",
        ],
        "resolution": "需删除波动至等方向措辞并保留差异待核验。",
    }


def test_v30_final_editor_resolves_contradiction_only_pe_after_neutral_verification() -> None:
    base_review = {
        "release_recommendation": "limited",
        "unsupported_claims": [],
        "numeric_conflicts": [],
        "contradictions": [_v30_pe_directionality_contradiction()],
    }
    neutral = (
        "华懋科技2026年8月20日PE(TTM)为171.11倍 "
        "[valuation:603306.SH:20260820]；"
        "华懋科技2026年8月31日PE(TTM)为246.92倍 "
        "[valuation:603306.SH:20260831]。"
        "两项PE(TTM)仅按日期中性列示，差异待核验；不得据此推导业务原因。"
    )
    resolved = IndustryResearchService._reconcile_final_editorial_state(
        base_review,
        [{
            "chapter_id": "expectations_valuation",
            "body_markdown": neutral,
            "summary": "",
        }],
        _v30_final_editor_evidence(),
        expected_subject=_v30_final_editor_subject(),
    )

    finding = resolved["contradictions"][0]
    assert finding["program_verification"] == "neutral_final_pe_text_v30"
    assert IndustryResearchService._review_issue_resolved(finding)
    assert resolved["release_recommendation"] == "ready"

    nonneutral_cases = (
        neutral.replace("仅按日期中性列示", "从171.11倍波动至246.92倍"),
        neutral.replace("差异待核验", "数据已经确认"),
        neutral.replace(
            "[valuation:603306.SH:20260831]",
            "[filing:1225224760]",
        ),
    )
    for nonneutral in nonneutral_cases:
        blocked = IndustryResearchService._reconcile_final_editorial_state(
            base_review,
            [{
                "chapter_id": "expectations_valuation",
                "body_markdown": nonneutral,
                "summary": "",
            }],
            _v30_final_editor_evidence(),
            expected_subject=_v30_final_editor_subject(),
        )
        assert not IndustryResearchService._review_issue_resolved(
            blocked["contradictions"][0]
        )
        assert blocked["release_recommendation"] == "limited"


def _v31_wrapped_statutory_snapshot() -> dict[str, object]:
    """Minimal issuer-bound filing text shaped like exchange PDF extraction."""

    company = "华懋(厦门)新材料科技股份有限公司"
    return {
        "topic": "华懋科技",
        "subject": {
            "name": "华懋科技",
            "symbol": "603306.SH",
            "resolved": True,
        },
        "evidence": [
            {
                "evidence_id": "filing:1225505930",
                "kind": "filing_text",
                "symbol": "603306.SH",
                "company": company,
                "title": "华懋科技2026年半年度报告",
                "report_period": "20260630",
                "document_text": (
                    "单位：元 币种：人民币\n"
                    "本报告期末 上年度末 本报告期末比上年度末增减（%）\n"
                    "总资产 6,171,145,144.82 5,993,670,009.88 2.96\n"
                    "归属于上市公司股东的净资产 "
                    "3,817,464,934.50 3,429,966,675.77 11.30\n"
                    "单位：元 币种：人民币\n"
                    "本报告期 上年同期 本报告期比上年同期增减（%）\n"
                    "归属于上市公司股东的净利润\n"
                    "23,285,735.42\n"
                    "136,579,029.44\n"
                    "-82.95\n"
                ),
            },
            {
                "evidence_id": "filing:1225224760",
                "kind": "filing_text",
                "symbol": "603306.SH",
                "company": company,
                "title": "华懋科技2026年第一季度报告",
                "report_period": "20260331",
                "document_text": (
                    "单位：元 币种：人民币\n"
                    "本报告期 上年同期 本报告期比上年同期增减（%）\n"
                    "归属于上市公司股东的扣除非经常性损益的净利润\n"
                    "6,856,275.15\n"
                    "81,550,519.89\n"
                    "-91.59\n"
                ),
            },
            {
                "evidence_id": "filing:1224752345",
                "kind": "filing_text",
                "symbol": "603306.SH",
                "company": company,
                "title": "华懋科技2025年第三季度报告",
                "report_period": "20250930",
                "document_text": (
                    "单位：元 币种：人民币\n"
                    "本报告期末 上年度末\n"
                    "归属于上市公司股东的所有者权益\n"
                    "3,363,507,381.94\n"
                    "3,786,494,130.43\n"
                ),
            },
        ],
    }


def _v31_wrapped_statutory_facts() -> list[dict]:
    return IndustryResearchService._build_governing_statutory_facts(
        _v31_wrapped_statutory_snapshot()
    )


def _v31_wrapped_statutory_evidence() -> dict[str, dict[str, object]]:
    return {
        str(item["evidence_id"]): item
        for item in _v31_wrapped_statutory_snapshot()["evidence"]
    }


def test_v31_h1_statutory_profit_recognizes_pdf_wrapped_table_row() -> None:
    facts = _v31_wrapped_statutory_facts()

    fact = next(
        item for item in facts
        if item.get("period") == "2026H1"
        and item.get("metric") == "归属于上市公司股东的净利润"
        and item.get("metric_basis") == "statutory_gaap_attributable"
    )

    assert fact["value"] == 23_285_735.42
    assert fact["display_value"] == "2328.57万元"
    assert fact["supporting_evidence_ids"] == ["filing:1225505930"]


def test_v31_q3_equity_recognizes_pdf_wrapped_table_row() -> None:
    facts = _v31_wrapped_statutory_facts()

    fact = next(
        item for item in facts
        if item.get("period") == "2025Q3"
        and item.get("metric") == "归属于上市公司股东的净资产"
    )

    assert fact["value"] == 3_363_507_381.94
    assert fact["display_value"] == "33.64亿元"
    assert fact["supporting_evidence_ids"] == ["filing:1224752345"]


def test_v31_q1_deducted_profit_removes_factor_ten_value_and_keeps_exact_fact() -> None:
    prose = (
        "华懋科技2026Q1扣非归母净利润0.69亿元，同比下降91.59% "
        "[filing:1225224760]，说明主营业务恶化。"
    )

    normalized = IndustryResearchService._enforce_production_accounting_boundaries(
        prose,
        {"filing:1225224760"},
        _v31_wrapped_statutory_facts(),
    )

    assert "0.69亿元" not in normalized
    assert "6,856,275.15元" in normalized
    assert "上年同期81,550,519.89元" in normalized
    assert "同比下降91.59% [filing:1225224760]" in normalized
    assert "主营业务恶化" not in normalized


def test_v31_final_editor_resolves_fy_and_q3_equity_as_distinct_periods() -> None:
    review = {
        "release_recommendation": "limited",
        "unsupported_claims": [],
        "numeric_conflicts": [{
            "type": "numeric_conflict",
            "entity": "华懋科技",
            "metric": "归母净资产",
            "values": ["34.30亿元", "33.64亿元"],
            "units": ["亿元", "亿元"],
            "periods": ["2025年末", "2025Q3"],
            "accounting_bases": [
                "statutory_attributable_equity",
                "statutory_attributable_equity",
            ],
            "evidence_ids": [],
            "resolution": "未解决：两个归母净资产数值不同。",
        }],
        "contradictions": [],
    }
    body = (
        "华懋科技2025年末归母净资产34.30亿元 "
        "[filing:1225505930]。\n"
        "华懋科技2025Q3归母净资产33.64亿元 "
        "[filing:1224752345]。"
    )

    reconciled = IndustryResearchService._reconcile_final_editorial_state(
        review,
        [{"chapter_id": "financials", "body_markdown": body, "summary": ""}],
        _v31_wrapped_statutory_evidence(),
        expected_subject=_v30_final_editor_subject(),
        governing_facts=_v31_wrapped_statutory_facts(),
    )

    finding = reconciled["numeric_conflicts"][0]
    assert finding["program_verification"] == "governing_distinct_period_series_v31"
    assert IndustryResearchService._review_issue_resolved(finding)
    assert reconciled["release_recommendation"] == "ready"


def test_v31_final_editor_resolves_assets_magnitude_typo_only_after_599_is_absent() -> None:
    review = {
        "release_recommendation": "limited",
        "unsupported_claims": [],
        "numeric_conflicts": [{
            "type": "numeric_conflict",
            "entity": "华懋科技",
            "metric": "2025年末总资产",
            "values": ["59.94亿元", "5.99亿元"],
            "units": ["亿元", "亿元"],
            "periods": ["2025年末", "2025年末"],
            "accounting_bases": ["statutory_balance_sheet"] * 2,
            "evidence_ids": [],
            "resolution": "未解决：存在十倍量级差异。",
        }],
        "contradictions": [],
    }
    canonical = (
        "华懋科技2025年末总资产59.94亿元 [filing:1225505930]。"
    )
    common = {
        "evidence_by_id": _v31_wrapped_statutory_evidence(),
        "expected_subject": _v30_final_editor_subject(),
        "governing_facts": _v31_wrapped_statutory_facts(),
    }

    resolved = IndustryResearchService._reconcile_final_editorial_state(
        review,
        [{"chapter_id": "financials", "body_markdown": canonical, "summary": ""}],
        **common,
    )
    finding = resolved["numeric_conflicts"][0]
    assert finding["program_verification"] == "removed_magnitude_typo_v31"
    assert IndustryResearchService._review_issue_resolved(finding)
    assert resolved["release_recommendation"] == "ready"

    blocked = IndustryResearchService._reconcile_final_editorial_state(
        review,
        [{
            "chapter_id": "financials",
            "body_markdown": canonical + "另一处仍写作5.99亿元。",
            "summary": "",
        }],
        **common,
    )
    assert not IndustryResearchService._review_issue_resolved(
        blocked["numeric_conflicts"][0]
    )
    assert blocked["release_recommendation"] == "limited"


def test_v31_pe_finding_ignores_extra_filing_context_but_audits_each_numeric_atom() -> None:
    evidence = _v30_final_editor_evidence()
    finding = {
        **_v30_pe_directionality_contradiction(),
        "evidence_ids": [
            "valuation:603306.SH:20260820",
            "filing:1225224760",
            "valuation:603306.SH:20260831",
        ],
    }
    review = {
        "release_recommendation": "limited",
        "unsupported_claims": [],
        "numeric_conflicts": [],
        "contradictions": [finding],
    }
    neutral = (
        "华懋科技2026年8月20日PE(TTM)为171.11倍 "
        "[valuation:603306.SH:20260820]。\n"
        "华懋科技2026年8月31日PE(TTM)为246.92倍 "
        "[valuation:603306.SH:20260831]。\n"
        "两项PE(TTM)仅按日期中性列示，差异待核验；不得据此推导业务原因。"
    )

    resolved = IndustryResearchService._reconcile_final_editorial_state(
        review,
        [{
            "chapter_id": "expectations_valuation",
            "body_markdown": neutral,
            "summary": "",
        }],
        evidence,
        expected_subject=_v30_final_editor_subject(),
    )
    assert resolved["contradictions"][0]["program_verification"] == (
        "neutral_final_pe_text_v30"
    )
    assert IndustryResearchService._review_issue_resolved(
        resolved["contradictions"][0]
    )

    bad_atom = neutral.replace(
        "[valuation:603306.SH:20260831]",
        "[filing:1225224760]",
    )
    blocked = IndustryResearchService._reconcile_final_editorial_state(
        review,
        [{
            "chapter_id": "expectations_valuation",
            "body_markdown": bad_atom,
            "summary": "",
        }],
        evidence,
        expected_subject=_v30_final_editor_subject(),
    )
    assert not IndustryResearchService._review_issue_resolved(
        blocked["contradictions"][0]
    )
    assert blocked["release_recommendation"] == "limited"
