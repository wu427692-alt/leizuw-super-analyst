import { describe, expect, it } from 'vitest';
import { toCamelCase } from '../../../api/utils';
import type { MonitorEvent } from '../../../types/investmentMonitor';
import { sourceRecordGroups, type SourceRecordGroup } from '../sourceRecordFields';

function read(eventType: string, metrics: Record<string, unknown>) {
  // Exercise the same deep key conversion as GET /events/{id}, not a separate
  // hand-camel-cased fixture that could hide underscored source-key mistakes.
  return sourceRecordGroups(toCamelCase<Pick<MonitorEvent, 'eventType' | 'metrics'>>({ event_type: eventType, metrics }));
}

function values(group: SourceRecordGroup | undefined) {
  expect(group).toBeDefined();
  return Object.fromEntries(group!.fields.map(field => [field.label, field.value]));
}

describe('sourceRecordGroups: saved source fields only', () => {
  it('preserves announcement metadata, zero file size and source category lists', () => {
    const groups = read('company_announcement', {
      name: '样例公司', code: '600000', category_names: ['半年报', '更正公告'], file_type: 'PDF', size_kb: 0,
      disclosure_date: '2026-06-30', raw: { secret: 'never expose' }, config: 'never expose',
    });
    expect(values(groups[0])).toEqual({ 披露公司: '样例公司', 股票代码: '600000', 公告分类: '半年报、更正公告', 文件类型: 'PDF', '文件大小（KB）': '0' });
    expect(JSON.stringify(groups)).not.toContain('never expose');
  });

  it('keeps research identity, source dates and existing target-price units', () => {
    const groups = read('research_report_pdf', {
      inst_csname: '', org_name: '样例证券', name: '其他公司', report_symbol: '300684.SZ', ts_code: '300308.SZ',
      report_type: '行业报告', author: '研究员', ind_name: '光通信', rating: '原始评级', trade_date: '20260910',
      target_price_min: 0, target_price_max: 21, abstr: '正文由抽屉单独展示',
    });
    expect(values(groups[0])).toMatchObject({ 机构: '样例证券', 研究主体: '其他公司', 主体代码: '300684.SZ', 报告日期: '20260910', '目标价下限（元）': '0', '目标价上限（元）': '21' });
    expect(JSON.stringify(groups)).not.toContain('正文由抽屉单独展示');
    // The helper must not infer relevance from either source code or broad symbols.
    expect(JSON.stringify(groups)).not.toContain('本公司研究');
  });

  it('recognizes survey and monthly broker source contracts', () => {
    expect(values(read('institution_survey', { rece_org: '调研机构', fund_visitors: ['甲', '乙'], surv_date: '20260102', rece_mode: '电话会', rece_place: '线上' })[0]))
      .toMatchObject({ 机构: '调研机构', 调研参与方: '甲、乙', 报告日期: '20260102', 调研方式: '电话会', 接待地点: '线上' });
    expect(values(read('broker_recommendation', { broker: '某券商', month: '202609', ts_code: '600000.SH' })[0]))
      .toMatchObject({ 机构: '某券商', 金股月份: '202609', 主体代码: '600000.SH' });
  });

  it('retains report_rc author_name and never substitutes survey visitors for an author', () => {
    expect(values(read('institution_forecast', { author_name: '研报作者甲', fund_visitors: '不是作者' })[0]))
      .toEqual({ 作者: '研报作者甲' });
    expect(values(read('research_report_pdf', { author: '旧字段作者', author_name: '另一字段' })[0]))
      .toEqual({ 作者: '旧字段作者' });
    expect(values(read('institution_forecast', { author: '', author_name: '研报作者甲' })[0]))
      .toEqual({ 作者: '研报作者甲' });
    const survey = values(read('institution_survey', { fund_visitors: '测试基金' })[0]);
    expect(survey).toEqual({ 调研参与方: '测试基金' });
    expect(survey).not.toHaveProperty('作者');
  });

  it('retains all forecasts with 40-row presentation pages and does not lose zero EPS or PE', () => {
    const forecasts = Array.from({ length: 45 }, (_, index) => ({ quarter: `20${index}`, eps: index, pe: index, api_key: 'hidden' }));
    const table = read('institution_forecast', { forecasts })[0];
    expect(table.table?.total).toBe(45);
    expect(table.table?.pageSize).toBe(40);
    expect(table.table?.rows).toHaveLength(45);
    expect(table.table?.rows[0]).toEqual({ quarter: '200', eps: '0', pe: '0' });
    expect(table.table?.rows.at(-1)).toEqual({ quarter: '2044', eps: '44', pe: '44' });
    expect(table.note).toContain('预测并非已实现业绩');
    expect(JSON.stringify(table)).not.toContain('hidden');
  });

  it('keeps report_rc profit and revenue in source ten-thousand yuan and ROE unscaled', () => {
    const group = read('institution_forecast', { forecasts: [
      { quarter: '2027Q4', eps: 0, pe: -2, np: 0, op_rt: '125000.25', roe: 0.125 },
      { quarter: '2028Q4', eps: -0.5, pe: 0, np: '-123.45', op_rt: 0, roe: '-4.2' },
      { quarter: '2029Q4', np: 12.5, op_rt: -10, roe: 0 },
    ] })[0];
    expect(group.table?.columns).toEqual([
      { key: 'quarter', label: '预测期' }, { key: 'eps', label: 'EPS（元）' }, { key: 'pe', label: 'PE（倍）' },
      { key: 'np', label: '净利润（万元）' }, { key: 'opRt', label: '营业收入（万元）' }, { key: 'roe', label: 'ROE（源值）' },
    ]);
    expect(group.table?.rows).toEqual([
      { quarter: '2027Q4', eps: '0', pe: '-2', np: '0', opRt: '125000.25', roe: '0.125' },
      { quarter: '2028Q4', eps: '-0.5', pe: '0', np: '-123.45', opRt: '0', roe: '-4.2' },
      { quarter: '2029Q4', eps: '—', pe: '—', np: '12.5', opRt: '-10', roe: '0' },
    ]);
    expect(group.note).toContain('保留源单位万元');
    expect(group.note).toContain('不推断百分比缩放');
    expect(group.note).toContain('预测并非已实现业绩');
    expect(JSON.stringify(group)).not.toMatch(/归母|扣非|单季/);
  });

  it.each([undefined, null, '', '  ', NaN, Infinity, -Infinity, 'NaN', '-Infinity', {}, [], false, '12万元', '0x10', '1e309'])(
    'leaves invalid forecast values %s absent, never as a fabricated zero', invalid => {
      const group = read('institution_forecast', { forecasts: [
        { quarter: '2027Q4', eps: 1, pe: 2, np: 3, op_rt: 4, roe: 5 },
        { quarter: '2028Q4', eps: invalid, pe: invalid, np: invalid, op_rt: invalid, roe: invalid },
      ] })[0];
      expect(group.table?.rows[1]).toEqual({ quarter: '2028Q4', eps: '—', pe: '—', np: '—', opRt: '—', roe: '—' });
      expect(read('institution_forecast', { forecasts: [{ np: invalid, op_rt: invalid, roe: invalid }] })).toEqual([]);
    },
  );

  it('keeps old EPS/PE-only reports compatible without fabricating newly supported columns', () => {
    const group = read('institution_forecast', { forecasts: [{ quarter: '2027', eps: 0, pe: -2 }] })[0];
    expect(group.table?.columns).toEqual([
      { key: 'quarter', label: '预测期' }, { key: 'eps', label: 'EPS（元）' }, { key: 'pe', label: 'PE（倍）' },
    ]);
    expect(group.table?.rows).toEqual([{ quarter: '2027', eps: '0', pe: '-2' }]);
    expect(read('institution_forecast', { forecasts: null })).toEqual([]);
    expect(read('institution_forecast', { forecasts: [] })).toEqual([]);
  });

  it('reads new forecast keys only from the saved row whitelist without borrowing or exposing payloads', () => {
    const group = read('institution_forecast', {
      np: 777, op_rt: 888, roe: 999,
      forecasts: [{ quarter: '2027', np: 0, op_rt: -2, roe: 3,
        n_income_attr_p: 777, revenue: 888, op_pr: 999,
        raw: { np: 'RAW_SENTINEL' }, config: 'CONFIG_SENTINEL', secret: 'SECRET_SENTINEL',
      }, { quarter: '2028', raw: { np: 777, op_rt: 888, roe: 999 } }],
    })[0];
    expect(group.table?.rows).toEqual([
      { quarter: '2027', np: '0', opRt: '-2', roe: '3' },
      { quarter: '2028', np: '—', opRt: '—', roe: '—' },
    ]);
    expect(JSON.stringify(group)).not.toMatch(/SENTINEL|777|888|999/);
    const row = Object.freeze(Object.assign(Object.create({ np: 777, opRt: 888, roe: 999 }), { quarter: '2027', eps: 0 }));
    const metrics = Object.freeze({ forecasts: Object.freeze([row]) });
    expect(sourceRecordGroups({ eventType: 'institution_forecast', metrics })[0].table?.rows)
      .toEqual([{ quarter: '2027', eps: '0' }]);
  });

  it('renders the actual saved valuation fields with their own date and explicit units', () => {
    const groups = read('market_snapshot', {
      trade_date: '20260911', valuation_trade_date: '20260910',
      valuation_pe: 12.3, valuation_pe_ttm: 13.4, valuation_pb: 2.1,
      valuation_ps: 3.2, valuation_ps_ttm: 4.3, valuation_total_mv: 123456.789,
      valuation_circ_mv: 98765.4321, valuation_turnover_rate: 2.4,
      valuation_turnover_rate_f: 3.5, valuation_volume_ratio: 1.2,
      pe: 999, pb: 999, total_mv: 999, close: 999,
      raw: { valuation_pe: 888 }, config: 'hidden', valuation_unknown: 'hidden',
    });
    expect(groups).toHaveLength(1);
    expect(groups[0].title).toBe('估值记录');
    expect(values(groups[0])).toEqual({
      估值日期: '20260910', '市盈率（PE）': '12.3 倍', '滚动市盈率（PE TTM）': '13.4 倍',
      '市净率（PB）': '2.1 倍', '市销率（PS）': '3.2 倍', '滚动市销率（PS TTM）': '4.3 倍',
      总市值: '1234567890 元', 流通市值: '987654321 元', 换手率: '2.4 %', 自由流通股换手率: '3.5 %', 量比: '1.2 倍',
    });
    expect(groups[0].note).toContain('万元换算为元');
    expect(groups[0].note).not.toContain('未统一换算');
    expect(JSON.stringify(groups)).not.toMatch(/20260911|999|888|hidden/);
  });

  it.each([undefined, null, '', '  '])('does not borrow a valuation date when the saved date is %s', valuationTradeDate => {
    const groups = read('market_snapshot', {
      valuation_trade_date: valuationTradeDate, valuation_pe_ttm: 0,
      trade_date: '20260911', event_at: '20260912', as_of: '20260913',
      quote: { trade_date: '20260914' }, daily_basic: { trade_date: '20260915' },
    });
    expect(values(groups[0])).toEqual({ 估值日期: '未注明', '滚动市盈率（PE TTM）': '0 倍' });
    expect(JSON.stringify(groups)).not.toMatch(/2026091[1-5]/);
  });

  it('preserves zero and negative valuation scalars without treating percentages as fractions', () => {
    const groups = read('market_snapshot', {
      valuation_pe: 0, valuation_pe_ttm: -12.5, valuation_pb: ' 0 ', valuation_ps: '-1.2',
      valuation_total_mv: 0, valuation_circ_mv: '-12.5', valuation_turnover_rate: 0,
      valuation_turnover_rate_f: '-0.2', valuation_volume_ratio: 0,
    });
    expect(values(groups[0])).toEqual({
      估值日期: '未注明', '市盈率（PE）': '0 倍', '滚动市盈率（PE TTM）': '-12.5 倍',
      '市净率（PB）': '0 倍', '市销率（PS）': '-1.2 倍', 总市值: '0 元', 流通市值: '-125000 元',
      换手率: '0 %', 自由流通股换手率: '-0.2 %', 量比: '0 倍',
    });
  });

  it.each([null, undefined, '', ' ', NaN, Infinity, -Infinity, 'NaN', 'Infinity', {}, [], false, '12元', '0x10'])(
    'does not manufacture valuation values from %s', invalid => {
      expect(read('market_snapshot', {
        valuation_pe: invalid, valuation_total_mv: invalid, valuation_turnover_rate: invalid,
        trade_date: '20260911', pe: 12, total_mv: 30,
      })).toEqual([]);
    },
  );

  it('omits an overflowing yuan conversion and never substitutes unprefixed quote fields', () => {
    expect(read('market_snapshot', {
      valuation_total_mv: Number.MAX_VALUE, valuation_circ_mv: '1e309',
      pe: 12, pe_ttm: 13, pb: 2, ps: 3, ps_ttm: 4, total_mv: 100,
      circ_mv: 50, turnover_rate: 2, volume_ratio: 1, trade_date: '20260911',
    })).toEqual([]);
  });

  it('does not read inherited or nested valuation fields or mutate the saved source', () => {
    const metrics = Object.assign(Object.create({ valuationTradeDate: '20260911', valuationPe: 999 }), {
      valuationPb: 0, raw: Object.freeze({ valuationPe: 888 }), config: 'hidden',
    });
    Object.freeze(metrics);
    const groups = sourceRecordGroups({ eventType: 'market_snapshot', metrics });
    expect(values(groups[0])).toEqual({ 估值日期: '未注明', '市净率（PB）': '0 倍' });
    expect(metrics.raw).toEqual({ valuationPe: 888 });
    expect(JSON.stringify(groups)).not.toMatch(/20260911|999|888|hidden/);
  });

  it('keeps every financial statement period independent and marks a missing period', () => {
    const groups = read('fundamental_snapshot', {
      end_date: '20991231',
      indicator: { end_date: '20260630', ann_date: '20260801', q_sales_yoy: 0, or_yoy: 18, tr_yoy: 20, q_netprofit_yoy: -3, netprofit_yoy: 4, roe: 0, roe_waa: 2, grossprofit_margin: 27.44, netprofit_margin: -4.2, current_ratio: 0 },
      income: { end_date: '20260331', revenue: 123456789, n_income_attr_p: 0, n_income: 12 },
      balance_sheet: { end_date: '20251231', total_assets: 1000, total_liab: 0 },
      cashflow: { n_cashflow_act: 0, ann_date: '20260802' },
      forecast: { end_date: '20260930', type: '预增', summary: '仅预测' },
    });
    expect(groups.map(group => group.title)).toEqual(['财务指标', '利润表', '资产负债表', '现金流量表', '业绩预告']);
    expect(values(groups[0])).toMatchObject({ 报告期: '20260630', '单季营业收入同比（%）': '0', '累计营业收入同比（%）': '18', '累计营业总收入同比（%）': '20', '净资产收益率（%）': '0', '加权净资产收益率（%）': '2', '销售净利率（%）': '-4.2', '流动比率（倍）': '0' });
    expect(values(groups[1])).toMatchObject({ 报告期: '20260331', 营业收入: '123456789', 归母净利润: '0', 净利润: '12' });
    expect(values(groups[2])).toMatchObject({ 报告期: '20251231', 负债总额: '0' });
    expect(values(groups[3])).toEqual({ 报告期: '未注明', 披露日: '20260802', 经营活动现金流量净额: '0' });
    expect(values(groups[4])).toMatchObject({ 报告期: '20260930', 预告类型: '预增' });
    for (const group of groups.slice(1, 4)) {
      expect(group.note).toContain('金额单位：元');
      expect(group.note).toContain('原值');
      expect(group.note).not.toContain('未统一换算');
    }
    expect(JSON.stringify(groups)).not.toContain('20991231');
  });

  it.each(['indicator', 'income', 'balance_sheet', 'cashflow'])('exposes %s revision evidence separately without guessing which raw flag is latest', section => {
    const groups = read('fundamental_snapshot', {
      [section]: { end_date: '20260630', ann_date: '20260825', f_ann_date: '20260820', report_type: '4', update_flag: 0 },
      f_ann_date: '20990101', update_flag: '1', private_value: 'MUST_NOT_RENDER',
    });
    expect(values(groups[0])).toEqual({
      报告期: '20260630', 披露日: '20260825', 实际公告日: '20260820', 报表类型代码: '4', '更新标记（源值）': '0',
    });
    expect(JSON.stringify(groups)).not.toMatch(/20990101|MUST_NOT_RENDER|最新/);
  });

  it.each([null, undefined, '', false, {}, []])('does not borrow missing financial revision fields for %s', missing => {
    const groups = read('fundamental_snapshot', {
      income: { end_date: '20260630', revenue: 0, f_ann_date: missing, report_type: missing, update_flag: missing },
      indicator: { end_date: '20260331', f_ann_date: '20260428', report_type: '1', update_flag: '1' },
    });
    expect(values(groups.find(group => group.title === '利润表'))).toEqual({ 报告期: '20260630', 营业收入: '0' });
  });

  it('keeps financial amounts in source yuan and adds only the actual indicator whitelist keys', () => {
    const groups = read('fundamental_snapshot', {
      indicator: { netprofit_margin: 0, current_ratio: -1.25, net_profit_margin: 888, liquidity_ratio: 999 },
      income: { revenue: '123.45', n_income_attr_p: -10 },
      balance_sheet: { total_assets: 1000, total_liab: -2 },
      cashflow: { n_cashflow_act: -123.45 },
    });
    expect(values(groups[0])).toEqual({ 报告期: '未注明', '销售净利率（%）': '0', '流动比率（倍）': '-1.25' });
    expect(values(groups[1])).toMatchObject({ 营业收入: '123.45', 归母净利润: '-10' });
    expect(values(groups[2])).toMatchObject({ 资产总额: '1000', 负债总额: '-2' });
    expect(values(groups[3])).toMatchObject({ 经营活动现金流量净额: '-123.45' });
    expect(JSON.stringify(groups)).not.toMatch(/888|999/);
    expect(read('capital_chip_snapshot', { moneyflow: { net_mf_amount: 123.45 } })[0].note).toContain('未统一换算');
  });

  it('never interprets express diluted ROE as income growth or net income as attributable profit', () => {
    const group = read('earnings_express', { end_date: '20260630', diluted_roe: 0, n_income: 120 })[0];
    expect(values(group)).toMatchObject({ '摊薄净资产收益率（%）': '0', 净利润: '120' });
    expect(JSON.stringify(group)).not.toMatch(/同比|归母/);
  });

  it('separates capital snapshot dates and market totals without inventing main-force inflow', () => {
    const groups = read('capital_chip_snapshot', {
      chip_performance: { trade_date: '20260910', weight_avg: 12.34, winner_rate: 83.43, cost_50pct: 0, cost_85pct: 12.85 },
      chip_distribution: [{ trade_date: '20260910', price: 12.34, percent: 0 }],
      margin: { trade_date: '20260909', rzye: 0, rzmre: 11 },
      market_margin: { trade_date: '20260908', financing_balance: 100, margin_balance: 200 },
      moneyflow: { trade_date: '20260907', net_mf_amount: -88.25 },
      northbound: { trade_date: '20240816', ratio: 0 },
    });
    expect(values(groups.find(group => group.title === '筹码表现'))).toMatchObject({ 交易日期: '20260910', '获利比例（源值）': '83.43', '50% 成本分位': '0', '85% 成本分位': '12.85' });
    expect(values(groups.find(group => group.title === '个股融资融券'))).toMatchObject({ 交易日期: '20260909', 融资余额: '0' });
    expect(values(groups.find(group => group.title === '全市场融资融券'))).toMatchObject({ 交易日期: '20260908', 全市场两融余额: '200' });
    expect(values(groups.find(group => group.title === '个股资金流'))).toEqual({ 交易日期: '20260907', 资金净流入: '-88.25' });
    expect(values(groups.find(group => group.title === '最近披露北向持股'))).toMatchObject({ 交易日期: '20240816', '持股比例（源值）': '0' });
    expect(groups.find(group => group.title === '筹码分布')?.table?.rows[0]).toMatchObject({ percent: '0' });
    expect(JSON.stringify(groups)).not.toContain('主力');
    expect(JSON.stringify(groups)).not.toContain('8343');
  });

  it('does not borrow capital data dates from unrelated subgroups', () => {
    const groups = read('capital_chip_snapshot', { chip_performance: { trade_date: '20260910' }, moneyflow: { net_mf_amount: 7 } });
    expect(values(groups.find(group => group.title === '个股资金流'))).toEqual({ 资金净流入: '7' });
  });

  it('displays source technical variants separately and nine-turn data uses its own date', () => {
    const groups = read('technical_factor', { trade_date: '20260910', close: 0, close_qfq: 12, rsi_6: 0, rsi_bfq_6: 1, macd: 0, kdj_bfq_k: 12, nine_turn: { trade_date: '20260909', up_count: 0, down_count: 3 } });
    expect(values(groups[0])).toMatchObject({ 收盘价: '0', 前复权收盘价: '12', 'RSI 6': '0', 'RSI 6（不复权）': '1', MACD: '0', 'KDJ K（不复权）': '12' });
    expect(values(groups[1])).toEqual({ 交易日期: '20260909', 上序列计数: '0', 下序列计数: '3' });
    expect(groups[0].note).toContain('不混合复权类型');
  });

  it('uses company-source units and does not erase zero employees or capital', () => {
    expect(values(read('company_profile', { com_name: '公司原名', ts_code: '600000.SH', setup_date: '19990101', reg_capital: 0, employees: 0, main_business: '主营原文', office: '办公地址' })[0]))
      .toEqual({ 公司全称: '公司原名', 股票代码: '600000.SH', 成立日期: '19990101', 主营业务: '主营原文', '注册资本（万元）': '0', 员工人数: '0', 办公地: '办公地址' });
  });

  it.each([
    ['holder_number', { end_date: '20260630', holder_num: 0 }, '股东户数', '0'],
    ['dividend', { end_date: '20260630', div_proc: '实施', cash_div_tax: 0 }, '每股税前现金分红', '0'],
    ['audit_opinion', { audit_result: '标准无保留', audit_fees: 0 }, '审计费用', '0'],
    ['earnings_forecast', { end_date: '20260630', type: '预增', change_reason: '原文原因' }, '变动原因', '原文原因'],
    ['repurchase', { ann_date: '20260910', proc: '完成', amount: 0 }, '回购金额', '0'],
    ['holder_trade', { holder_name: '甲', in_de: 'DE', change_vol: 0 }, '变动数量', '0'],
    ['share_unlock', { float_date: '20260910', float_share: 0 }, '解禁数量', '0'],
    ['block_trade', { trade_date: '20260910', price: 10, vol: 0, buyer: '甲', seller: '乙' }, '成交量', '0'],
    ['price_limit', { trade_date: '20260910', pct_chg: 0, open_times: 0 }, '开板次数', '0'],
    ['suspension', { suspend_date: '20260910', resume_date: '20260911', suspend_reason: '原文原因' }, '复牌时间', '20260911'],
  ])('whitelists the saved %s fields', (eventType, metrics, label, value) => {
    expect(values(read(eventType, metrics)[0])[label]).toBe(value);
  });

  it('shows disclosure-backed dragon-tiger and seat amounts without rescaling', () => {
    expect(values(read('dragon_tiger', { trade_date: '20260910', ts_code: '600000.SH', reason: '原上榜原因', l_buy: 0, l_sell: 100000, net_amount: -100000, amount_rate: 12.5 })[0]))
      .toMatchObject({ '龙虎榜买入（元）': '0', '龙虎榜卖出（元）': '100000', '龙虎榜净额（元）': '-100000', '龙虎榜成交占比（%）': '12.5' });
    expect(values(read('institution_seat', { exalter: '原始席位', buy: 0, sell: 20, net_buy: -20, side: '卖方' })[0]))
      .toMatchObject({ 披露席位: '原始席位', '买入（元）': '0', '卖出（元）': '20', '净买入（元）': '-20' });
  });

  it('keeps ownership action periods and every saved row available for pagination', () => {
    const groups = read('ownership_snapshot', {
      pledge: { end_date: '20260630', pledge_ratio: 0 },
      share_float: Array.from({ length: 30 }, (_, index) => ({ float_date: `202609${String(index + 1).padStart(2, '0')}`, float_share: index, secret: 'hidden' })),
      holder_trades: [{ ann_date: '20260801', holder_name: '甲', in_de: 'DE', change_vol: 0 }],
      repurchases: [{ ann_date: '20260101', end_date: '20251231', proc: '完成', amount: 0 }],
    });
    expect(groups.map(group => group.title)).toEqual(['股权质押', '限售解禁', '股东增减持', '股份回购']);
    expect(values(groups[0])).toMatchObject({ 报告期: '20260630', '质押比例（%）': '0' });
    expect(groups[1].table?.total).toBe(30);
    expect(groups[1].table?.pageSize).toBe(20);
    expect(groups[1].table?.rows).toHaveLength(30);
    expect(groups[1].table?.rows[0]).toEqual({ floatDate: '20260901', floatShare: '0' });
    expect(groups[1].table?.rows.at(-1)).toEqual({ floatDate: '20260930', floatShare: '29' });
    expect(groups[2].table?.rows[0]).toMatchObject({ annDate: '20260801', changeVol: '0' });
    expect(groups[3].table?.rows[0]).toMatchObject({ annDate: '20260101', endDate: '20251231' });
    expect(JSON.stringify(groups)).not.toContain('hidden');
  });

  it.each(['top_shareholders', 'top_float_shareholders'])('preserves %s per-row disclosure periods', eventType => {
    const groups = read(eventType, { period: '20260630', holders: [{ holder_name: '股东甲', hold_amount: 0, hold_ratio: 3, ann_date: '20260801', end_date: '20260630' }] });
    expect(values(groups[0])).toEqual({ 报告期: '20260630' });
    expect(groups[1].table?.rows[0]).toEqual({ holderName: '股东甲', holdAmount: '0', holdRatio: '3', endDate: '20260630', annDate: '20260801' });
  });

  it('retains all valid manager records with 20-row pages and rejects malformed or arbitrary row fields', () => {
    const records = Array.from({ length: 23 }, (_, index) => ({ name: `姓名${index}`, title: '董事', end_date: '', api_key: 'hidden' }));
    const groups = read('executive_roster', { active_count: 0, records: [...records, null, 3, {}, { config: 'hidden' }] });
    expect(values(groups[0])).toEqual({ 在任记录数: '0' });
    expect(groups[1].table?.total).toBe(23);
    expect(groups[1].table?.pageSize).toBe(20);
    expect(groups[1].table?.rows).toHaveLength(23);
    expect(groups[1].table?.rows.at(-1)).toEqual({ name: '姓名22', title: '董事' });
    expect(groups[1].table?.columns).toEqual([{ key: 'name', label: '姓名' }, { key: 'title', label: '职务' }]);
    expect(JSON.stringify(groups)).not.toContain('hidden');
  });

  it('does not invent units/columns for the executive reward payload', () => {
    const groups = read('executive_rewards', { records: [{ name: '甲', end_date: '20251231', unknown_amount: 30 }] });
    expect(groups[0].table?.rows[0]).toEqual({ name: '甲', endDate: '20251231' });
    expect(JSON.stringify(groups)).not.toContain('30');
  });

  it('keeps enterprise result closed except known returned-risk array counts', () => {
    const groups = read('enterprise_risk', { query_company: '查询原公司', result: { detailRisks: [], relationRiskNotes: [{ private: 'hidden' }], token: 'hidden', company: { config: 'hidden' } } });
    expect(values(groups[0])).toEqual({ 查询公司: '查询原公司' });
    expect(values(groups[1])).toEqual({ 自身风险返回记录数: '0', 关联风险返回记录数: '1' });
    expect(groups[1].note).toContain('未获取不等于无风险');
    expect(JSON.stringify(groups)).not.toContain('hidden');
    expect(read('enterprise_risk', { result: {} })).toEqual([]);
  });

  it.each(['enterprise_registration', 'enterprise_credit', 'enterprise_ipr', 'enterprise_history'])('only shows the known company field for %s', eventType => {
    expect(read(eventType, { query_company: '甲', result: { arbitrary: 'hidden' } })).toEqual([{ title: '企业查询记录', fields: [{ label: '查询公司', value: '甲' }] }]);
  });

  it('handles missing, malformed, non-finite and unknown fields without fabricated zero', () => {
    expect(read('fundamental_snapshot', { indicator: { roe: NaN }, income: null, cashflow: ['unexpected'], balance_sheet: { total_assets: Infinity } })).toEqual([]);
    expect(read('research_report_pdf', { target_price_min: 'NaN', target_price_max: '-Infinity', name: {}, author: false })).toEqual([]);
    expect(read('unknown_future_type', { name: 'do not dump', records: [{ secret: 'do not dump' }] })).toEqual([]);
    expect(sourceRecordGroups({ eventType: 'company_profile', metrics: null as unknown as Record<string, unknown> })).toEqual([]);
  });

  it('does not use prototype or arbitrary nested fields and does not mutate source objects', () => {
    const metrics = Object.assign(Object.create({ regCapital: 123, comName: 'prototype' }), { employees: 0, raw: { secret: 'hidden' } });
    Object.freeze(metrics.raw); Object.freeze(metrics);
    expect(sourceRecordGroups({ eventType: 'company_profile', metrics })).toEqual([{ title: '公司资料', fields: [{ label: '员工人数', value: '0' }] }]);
    expect(metrics.raw).toEqual({ secret: 'hidden' });
  });

  it('bounds long metadata without interpreting markup', () => {
    const group = read('company_profile', { com_name: '<img onerror=alert(1)>', main_business: '文'.repeat(2000) })[0];
    expect(values(group).公司全称).toBe('<img onerror=alert(1)>');
    expect(values(group).主营业务).toHaveLength(1201);
    expect(values(group).主营业务.endsWith('…')).toBe(true);
  });
});
