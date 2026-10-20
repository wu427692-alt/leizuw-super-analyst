import type { MonitorEvent } from '../../types/investmentMonitor';
import { isResearchEventType } from '../../utils/stockResearchRelation';

export type SourceRecordField = { label: string; value: string };
export type SourceRecordGroup = {
  title: string;
  fields: SourceRecordField[];
  note?: string;
  table?: {
    columns: Array<{ key: string; label: string }>;
    rows: Array<Record<string, string>>;
    /** The reader paginates presentation; all saved, whitelisted rows remain available. */
    pageSize: number;
    /** Saved rows containing known fields, not an upstream total. */
    total: number;
  };
};

type Row = Record<string, unknown>;
type Definition = readonly [label: string, ...keys: string[]];
const SOURCE_UNITS = '数值保留数据源原值，金额与数量单位未统一换算。';
const FINANCIAL_UNITS = '金额单位：元；数值保留本条数据源记录的原值。';
const SAVED_ROWS = '仅展示本条资料已保存的记录，不代表全部历史。';
const PERIOD: Definition[] = [['报告期', 'endDate'], ['披露日', 'annDate']];
// Keep selection evidence separate: f_ann_date is not a replacement for the
// saved ann_date, and source flags/types must not be translated into certainty.
const FINANCIAL_PERIOD: Definition[] = [
  ...PERIOD, ['实际公告日', 'fAnnDate'], ['报表类型代码', 'reportType'], ['更新标记（源值）', 'updateFlag'],
];
const TRADE_DATE: Definition[] = [['交易日期', 'tradeDate']];
const STOCK: Definition[] = [['股票代码', 'tsCode'], ['公司名称', 'name']];

function object(value: unknown): Row {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as Row : {};
}

function scalar(value: unknown): string | null {
  if (typeof value === 'number') return Number.isFinite(value) ? String(value) : null;
  if (typeof value !== 'string') return null;
  const text = value.trim();
  if (!text || /^(?:nan|[+-]?infinity|null|undefined)$/i.test(text)) return null;
  // Metadata is bounded plain text, not a second full-body/raw-payload renderer.
  return text.length > 1200 ? `${text.slice(0, 1200)}…` : text;
}

function valueAt(row: Row, keys: readonly string[]): string | null {
  for (const key of keys) {
    if (!Object.prototype.hasOwnProperty.call(row, key)) continue;
    const value = scalar(row[key]);
    if (value !== null) return value;
    // These two source fields explicitly allow a list of names/categories.
    if (['categoryNames', 'fundVisitors'].includes(key) && Array.isArray(row[key])) {
      const list = (row[key] as unknown[]).slice(0, 12).map(scalar).filter((item): item is string => item !== null);
      if (list.length) return `${list.join('、')}${(row[key] as unknown[]).length > 12 ? '…' : ''}`;
    }
  }
  return null;
}

function fields(row: Row, definitions: readonly Definition[]): SourceRecordField[] {
  return definitions.flatMap(([label, ...keys]) => {
    const value = valueAt(row, keys);
    return value === null ? [] : [{ label, value }];
  });
}

function finiteNumericText(value: string): boolean {
  return /^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:e[+-]?\d+)?$/i.test(value) && Number.isFinite(Number(value));
}

function unitFields(row: Row, definitions: readonly Definition[], unit: string, multiplier = 1): SourceRecordField[] {
  return fields(row, definitions).flatMap(field => {
    // Parse the whole saved scalar, never partial strings or missing values as zero.
    if (!finiteNumericText(field.value)) return [];
    const converted = Number(field.value) * multiplier;
    if (!Number.isFinite(converted)) return [];
    return [{ label: field.label, value: `${multiplier === 1 ? field.value : String(converted)} ${unit}` }];
  });
}

const REPURCHASE: Definition[] = [
  ...PERIOD, ['回购进度', 'proc'], ['回购金额', 'amount'], ['回购数量', 'vol'],
];
const HOLDER_TRADE: Definition[] = [
  ['披露日', 'annDate'], ['股东', 'holderName'], ['增减持方向（源代码）', 'inDe'], ['变动数量', 'changeVol'],
];
const UNLOCK: Definition[] = [
  ['披露日', 'annDate'], ['解禁日期', 'floatDate'], ['股东', 'holderName'], ['解禁数量', 'floatShare'],
];
const FORECAST: Definition[] = [
  ...PERIOD, ['预告类型', 'type'], ['业绩摘要', 'summary'], ['变动原因', 'changeReason'],
];

// Field contracts come from InvestmentMonitorService's event builders. The API
// camel-cases keys; enum/source values remain unchanged. Never recurse through
// arbitrary metrics, result, _evidence, raw, or configuration objects.
export function sourceRecordGroups(event: Pick<MonitorEvent, 'eventType' | 'metrics'>): SourceRecordGroup[] {
  const metrics = object(event.metrics);
  const groups: SourceRecordGroup[] = [];
  const add = (title: string, value: unknown, definitions: readonly Definition[], note?: string, periodRequired = false) => {
    const row = object(value);
    const entries = fields(row, definitions);
    if (!entries.length) return;
    // Each statement owns its period. Neither the event timestamp nor another
    // nested statement is evidence for an absent report period.
    if (periodRequired && !entries.some(field => field.label === '报告期')) {
      entries.unshift({ label: '报告期', value: '未注明' });
    }
    groups.push({ title, fields: entries, ...(note ? { note } : {}) });
  };
  const table = (title: string, value: unknown, definitions: readonly Definition[], note = SAVED_ROWS, pageSize = 20, numericKeys: readonly string[] = []) => {
    if (!Array.isArray(value)) return;
    const rows: Array<Record<string, string>> = [];
    const used = new Set<string>();
    let total = 0;
    for (const item of value) {
      const row = object(item);
      const projected: Record<string, string> = {};
      for (const [, ...keys] of definitions) {
        const text = valueAt(row, keys);
        if (text !== null && (!numericKeys.includes(keys[0]) || finiteNumericText(text))) {
          projected[keys[0]] = text; used.add(keys[0]);
        }
      }
      if (!Object.keys(projected).length) continue;
      total += 1;
      rows.push(projected);
    }
    if (!total) return;
    const columns = definitions.filter(([, key]) => used.has(key)).map(([label, key]) => ({ key, label }));
    groups.push({ title, fields: [], note, table: {
      columns, total, pageSize, rows: rows.map(row => Object.fromEntries(columns.map(({ key }) => [key, row[key] ?? '—']))),
    } });
  };

  if (event.eventType === 'company_announcement') {
    add('公告资料', metrics, [
      ['披露公司', 'name'], ['股票代码', 'code'], ['公告分类', 'categoryNames'],
      ['文件类型', 'fileType'], ['文件大小（KB）', 'sizeKb'],
    ]);
  } else if (isResearchEventType(event.eventType)) {
    add('研究资料', metrics, [
      ['机构', 'instCsname', 'orgName', 'receOrg', 'broker'], ['研究主体', 'name'],
      ['主体代码', 'reportSymbol', 'tsCode'], ['研究类型', 'reportType'],
      ['作者', 'author', 'authorName'], ['行业', 'indName'], ['评级', 'rating'],
      ['报告日期', 'reportDate', 'tradeDate', 'survDate'], ['金股月份', 'month'],
      ['目标价下限（元）', 'targetPriceMin'], ['目标价上限（元）', 'targetPriceMax'],
      ['调研方式', 'receMode'], ['接待地点', 'recePlace'],
      // stk_surv visitors may be institutions or people, never report authors.
      ...(event.eventType === 'institution_survey' ? [['调研参与方', 'fundVisitors'] as Definition] : []),
    ]);
    if (event.eventType === 'institution_forecast') {
      table('研报预测记录', metrics.forecasts, [
        ['预测期', 'quarter'], ['EPS（元）', 'eps'], ['PE（倍）', 'pe'],
        ['净利润（万元）', 'np'], ['营业收入（万元）', 'opRt'], ['ROE（源值）', 'roe'],
      ], '净利润和营业收入保留源单位万元；ROE 按源值展示，不推断百分比缩放。按原始预测期展示；预测并非已实现业绩。仅包含本条研报已保存的记录。', 40, ['eps', 'pe', 'np', 'opRt', 'roe']);
    }
  } else if (event.eventType === 'market_snapshot') {
    const entries = [
      ...unitFields(metrics, [
        ['市盈率（PE）', 'valuationPe'], ['滚动市盈率（PE TTM）', 'valuationPeTtm'],
        ['市净率（PB）', 'valuationPb'], ['市销率（PS）', 'valuationPs'], ['滚动市销率（PS TTM）', 'valuationPsTtm'],
      ], '倍'),
      ...unitFields(metrics, [['总市值', 'valuationTotalMv'], ['流通市值', 'valuationCircMv']], '元', 10000),
      ...unitFields(metrics, [['换手率', 'valuationTurnoverRate'], ['自由流通股换手率', 'valuationTurnoverRateF']], '%'),
      ...unitFields(metrics, [['量比', 'valuationVolumeRatio']], '倍'),
    ];
    // daily_basic's saved valuation date is independent of the daily quote and
    // event timestamp. A missing valuation date must stay explicitly unknown.
    const date = valueAt(metrics, ['valuationTradeDate']);
    if (entries.length || date !== null) {
      groups.push({
        title: '估值记录', fields: [{ label: '估值日期', value: date ?? '未注明' }, ...entries],
        note: '市值已由源单位万元换算为元；估值日期仅取本条记录保存的估值交易日期，不使用行情日期。',
      });
    }
  } else if (event.eventType === 'fundamental_snapshot') {
    add('财务指标', metrics.indicator, [
      ...FINANCIAL_PERIOD, ['单季营业收入同比（%）', 'qSalesYoy'], ['累计营业收入同比（%）', 'orYoy'],
      ['累计营业总收入同比（%）', 'trYoy'], ['单季归母净利润同比（%）', 'qNetprofitYoy'],
      ['累计归母净利润同比（%）', 'netprofitYoy'], ['净资产收益率（%）', 'roe'],
      ['加权净资产收益率（%）', 'roeWaa'], ['销售毛利率（%）', 'grossprofitMargin'],
      ['销售净利率（%）', 'netprofitMargin'], ['资产负债率（%）', 'debtToAssets'], ['流动比率（倍）', 'currentRatio'],
    ], undefined, true);
    add('利润表', metrics.income, [...FINANCIAL_PERIOD, ['营业收入', 'revenue'], ['归母净利润', 'nIncomeAttrP'], ['净利润', 'nIncome']], FINANCIAL_UNITS, true);
    add('资产负债表', metrics.balanceSheet, [...FINANCIAL_PERIOD, ['资产总额', 'totalAssets'], ['负债总额', 'totalLiab']], FINANCIAL_UNITS, true);
    add('现金流量表', metrics.cashflow, [...FINANCIAL_PERIOD, ['经营活动现金流量净额', 'nCashflowAct']], FINANCIAL_UNITS, true);
    add('业绩预告', metrics.forecast, FORECAST, undefined, true);
  } else if (event.eventType === 'capital_chip_snapshot') {
    add('筹码表现', metrics.chipPerformance, [
      ...TRADE_DATE, ['加权平均成本', 'weightAvg'], ['获利比例（源值）', 'winnerRate'],
      ['50% 成本分位', 'cost50Pct'], ['85% 成本分位', 'cost85Pct'],
    ], SOURCE_UNITS);
    table('筹码分布', metrics.chipDistribution, [...TRADE_DATE, ['价格', 'price'], ['分布比例（源值）', 'percent']], `${SAVED_ROWS}${SOURCE_UNITS}`);
    add('个股融资融券', metrics.margin, [...TRADE_DATE, ['融资余额', 'rzye'], ['融资买入额', 'rzmre'], ['融券余额', 'rqye']], SOURCE_UNITS);
    add('全市场融资融券', metrics.marketMargin, [
      ...TRADE_DATE, ['全市场融资余额', 'financingBalance'], ['全市场融券余额', 'securitiesLendingBalance'],
      ['全市场两融余额', 'marginBalance'],
    ], SOURCE_UNITS);
    add('个股资金流', metrics.moneyflow, [...TRADE_DATE, ['资金净流入', 'netMfAmount']], SOURCE_UNITS);
    add('最近披露北向持股', metrics.northbound, [...TRADE_DATE, ['持股比例（源值）', 'ratio']]);
  } else if (event.eventType === 'technical_factor') {
    add('技术指标', metrics, [
      ...TRADE_DATE, ['收盘价', 'close'], ['前复权收盘价', 'closeQfq'],
      ['RSI 6', 'rsi6'], ['RSI 6（不复权）', 'rsiBfq6'], ['MACD', 'macd'], ['MACD（不复权）', 'macdBfq'],
      ['KDJ K', 'kdjK'], ['KDJ D', 'kdjD'], ['KDJ K（不复权）', 'kdjBfqK'], ['KDJ D（不复权）', 'kdjBfqD'],
    ], '指标按已保存口径展示，不混合复权类型，不代表买卖建议。');
    add('九转序列', metrics.nineTurn, [...TRADE_DATE, ['上序列计数', 'upCount'], ['下序列计数', 'downCount']]);
  } else if (event.eventType === 'ownership_snapshot') {
    add('股权质押', metrics.pledge, [...PERIOD, ['质押比例（%）', 'pledgeRatio']], undefined, true);
    table('限售解禁', metrics.shareFloat, UNLOCK, `${SAVED_ROWS}${SOURCE_UNITS}`);
    table('股东增减持', metrics.holderTrades, HOLDER_TRADE, `${SAVED_ROWS}${SOURCE_UNITS}`);
    table('股份回购', metrics.repurchases, REPURCHASE, `${SAVED_ROWS}${SOURCE_UNITS}`);
  } else if (event.eventType === 'company_profile') {
    add('公司资料', metrics, [
      ['公司全称', 'comName'], ['股票代码', 'tsCode'], ['成立日期', 'setupDate'],
      ['主营业务', 'mainBusiness'], ['注册资本（万元）', 'regCapital'], ['员工人数', 'employees'], ['办公地', 'office'],
    ]);
  } else if (event.eventType === 'executive_roster') {
    add('管理层记录', metrics, [['在任记录数', 'activeCount']]);
    table('管理层与任职', metrics.records, [
      ['姓名', 'name'], ['职务', 'title', 'lev'], ['披露日', 'annDate'], ['任职开始', 'beginDate'], ['任职结束', 'endDate'],
    ]);
  } else if (['top_shareholders', 'top_float_shareholders'].includes(event.eventType)) {
    add('股东资料', metrics, [['报告期', 'period']]);
    table(event.eventType === 'top_shareholders' ? '十大股东' : '十大流通股东', metrics.holders, [
      ['股东', 'holderName'], ['持股数量', 'holdAmount'], ['持股比例（源值）', 'holdRatio'], ...PERIOD,
    ], `${SAVED_ROWS}${SOURCE_UNITS}`);
  } else if (event.eventType === 'executive_rewards') {
    table('董监高披露记录', metrics.records, [['姓名', 'name'], ...PERIOD]);
  } else if (event.eventType === 'holder_number') {
    add('股东户数', metrics, [...STOCK, ...PERIOD, ['股东户数', 'holderNum']]);
  } else if (event.eventType === 'dividend') {
    add('分红方案', metrics, [
      ...PERIOD, ['分红进度', 'divProc'], ['每股税前现金分红', 'cashDivTax'],
      ['每股送股', 'stkBoRate'], ['每股转增', 'stkCoRate'], ['除权除息日', 'exDate'],
    ], SOURCE_UNITS);
  } else if (event.eventType === 'audit_opinion') {
    add('审计意见', metrics, [...PERIOD, ['审计意见', 'auditResult'], ['审计机构', 'auditAgency'], ['签字会计师', 'auditSign'], ['审计费用', 'auditFees']], SOURCE_UNITS);
  } else if (event.eventType === 'earnings_forecast') {
    add('业绩预告', metrics, FORECAST, undefined, true);
  } else if (event.eventType === 'earnings_express') {
    add('业绩快报', metrics, [...PERIOD, ['营业收入', 'revenue'], ['净利润', 'nIncome'], ['摊薄净资产收益率（%）', 'dilutedRoe']], SOURCE_UNITS, true);
  } else if (event.eventType === 'repurchase') {
    add('股份回购', metrics, REPURCHASE, SOURCE_UNITS);
  } else if (event.eventType === 'holder_trade') {
    add('股东增减持', metrics, HOLDER_TRADE, SOURCE_UNITS);
  } else if (event.eventType === 'share_unlock') {
    add('限售解禁', metrics, UNLOCK, SOURCE_UNITS);
  } else if (event.eventType === 'dragon_tiger') {
    add('龙虎榜交易披露', metrics, [
      ...STOCK, ...TRADE_DATE, ['上榜原因', 'reason'], ['收盘价', 'close'], ['涨跌幅（%）', 'pctChange'],
      ['换手率（%）', 'turnoverRate'], ['龙虎榜买入（元）', 'lBuy'], ['龙虎榜卖出（元）', 'lSell'],
      ['龙虎榜净额（元）', 'netAmount'], ['龙虎榜成交占比（%）', 'amountRate'],
    ]);
  } else if (event.eventType === 'institution_seat') {
    add('龙虎榜席位', metrics, [
      ...STOCK, ...TRADE_DATE, ['披露席位', 'exalter'], ['买卖类型（源代码）', 'side'], ['上榜原因', 'reason'],
      ['买入（元）', 'buy'], ['卖出（元）', 'sell'], ['净买入（元）', 'netBuy'],
    ]);
  } else if (event.eventType === 'block_trade') {
    add('大宗交易', metrics, [...STOCK, ...TRADE_DATE, ['成交价', 'price'], ['成交量', 'vol'], ['成交金额', 'amount'], ['买方', 'buyer'], ['卖方', 'seller']], SOURCE_UNITS);
  } else if (event.eventType === 'price_limit') {
    add('涨跌停记录', metrics, [...STOCK, ...TRADE_DATE, ['涨跌停类型（源代码）', 'limit'], ['收盘价', 'close'], ['涨跌幅（%）', 'pctChg'], ['封单金额', 'fdAmount'], ['开板次数', 'openTimes']], SOURCE_UNITS);
  } else if (event.eventType === 'suspension') {
    add('停复牌记录', metrics, [...STOCK, ...TRADE_DATE, ['停牌时间', 'suspendTime', 'suspendDate'], ['复牌时间', 'resumeTime', 'resumeDate'], ['原因', 'suspendReason']]);
  } else if (['enterprise_registration', 'enterprise_risk', 'enterprise_credit', 'enterprise_ipr', 'enterprise_history'].includes(event.eventType)) {
    add('企业查询记录', metrics, [['查询公司', 'queryCompany']]);
    if (event.eventType === 'enterprise_risk') {
      const result = object(metrics.result);
      const counts = Object.fromEntries(['detailRisks', 'relationRiskNotes'].flatMap(key => Array.isArray(result[key]) ? [[key, result[key].length]] : []));
      add('本次返回风险记录', counts, [['自身风险返回记录数', 'detailRisks'], ['关联风险返回记录数', 'relationRiskNotes']], '数量仅指本条资料保存的返回记录，不代表全部风险；未获取不等于无风险。');
    }
  }
  return groups;
}
