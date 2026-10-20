import { useEffect, useRef, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { RefreshCw } from 'lucide-react';
import { jevLiveTApi } from '../api/jevLiveT';
import { getParsedApiError, toApiErrorMessage } from '../api/error';
import { useUserAccess } from '../contexts/UserAccessContext';
import type { LiveTContext, LiveTContextDiagnostic, LiveTContextStatus, LiveTRun, LiveTRunStatus, LiveTRunSummary, LiveTStatus } from '../types/jevLiveT';
import './JevBacktestPage.css';
import './JevLiveTPage.css';
import JevLiveTControl from './JevLiveTControl';

const STATUS: Record<LiveTRunStatus, string> = { starting: '准备中', running: '观测中', paused: '已暂停', completed: '已完成', stopped: '已终止', failed: '失败' };
const active = (run?: LiveTRun | null) => !!run && ['starting', 'running', 'paused'].includes(run.status);
const number = (value?: number | null, digits = 2) => value == null || !Number.isFinite(value) ? '—' : value.toLocaleString('zh-CN', { minimumFractionDigits: digits, maximumFractionDigits: digits });
const shanghaiTime = new Intl.DateTimeFormat('zh-CN', { timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit', hourCycle: 'h23' });
const stamp = (value?: string | null) => {
  if (!value || !/(?:Z|[+-]\d{2}:\d{2})$/i.test(value)) return '—';
  const timestamp = Date.parse(value);
  if (!Number.isFinite(timestamp)) return '—';
  const parts = Object.fromEntries(shanghaiTime.formatToParts(timestamp).map(part => [part.type, part.value]));
  return `${parts.year}-${parts.month}-${parts.day} ${parts.hour}:${parts.minute}:${parts.second}`;
};
const seconds = (value?: number | null) => value == null || !Number.isFinite(value) || value < 0 ? '—' : `${number(value / 1000, 2)} 秒`;
const denied = (failure: unknown) => [401, 403].includes(getParsedApiError(failure).status ?? 0);
const action = (value: string) => ({ BUY: '买入', SELL: '卖出', HOLD: '观望' } as Record<string, string>)[value] ?? value;
const stopReason = (value: string) => ({ duration_complete: '达到本轮时长上限', continuous_session_ended: '连续交易时段已结束', model_budget_reached: '达到模型费用上限', model_budget_reserved: '剩余预算不足以发起下一次判断', model_request_limit: '达到模型调用次数上限', quote_failures: '行情连续请求失败', context_not_ready: '本轮未取得全部有效上下文，未调用模型' } as Record<string, string>)[value] ?? value;
const contextStatus: Record<LiveTContextStatus, string> = { fresh: '时点核验通过', stale: '已滞后', missing: '缺少数据', error: '获取失败' };
const percentage = (value?: number | null) => value == null || !Number.isFinite(value) ? '—' : `${number(value * 100)}%`;
const diagnosticText = (value?: string | null) => { const text = value?.trim(); return text && !/^(none|null|undefined)$/i.test(text) ? text : null; };
const publicNewsUrl = (value?: string | null) => {
  if (!value) return null;
  try { const url = new URL(value); return url.protocol === 'https:' && !url.username && !url.password ? url.href : null; }
  catch { return null; }
};

function ContextSources({ context, diagnostics }: { context: LiveTContext; diagnostics?: Record<string, LiveTContextDiagnostic> | null }) {
  return <>
    <div className="live-t-context-grid">
      {(['hs300', 'zz1000', 'cpo'] as const).map(key => {
        const source = context[key];
        const label = diagnosticText(diagnostics?.[key]?.label) || ({ hs300: '沪深300', zz1000: '中证1000', cpo: 'CPO 成分股等权参考' })[key];
        const values = source?.values;
        return <article className="live-t-source" key={key} aria-label={label}>
          <div className="live-t-source-heading"><h3>{label}</h3><span data-status={source?.status}>{source ? contextStatus[source.status] : '未返回'}</span></div>
          <p className="live-t-caption">{diagnosticText(diagnostics?.[key]?.reason) || (key === 'cpo' ? '成分股等权参考，不是官方板块指数。' : '指数实时快照，以已收到的信息为准。')}</p>
          <dl className="live-t-source-values"><div><dt>较昨收</dt><dd>{percentage(values?.returnSincePrevClose)}</dd></div><div><dt>近30秒变化</dt><dd>{percentage(values?.return30S)}</dd></div>{key === 'cpo' ? <><div><dt>有效成分数</dt><dd>{number(values?.memberCount, 0)}</dd></div><div><dt>数据覆盖度</dt><dd>{percentage(values?.coverageRatio)}</dd></div><div><dt>上涨成分占比</dt><dd>{percentage(values?.breadthUp)}</dd></div></> : <div><dt>近60秒变化</dt><dd>{percentage(values?.return60S)}</dd></div>}</dl>
          <dl className="live-t-source-times"><div><dt>数据源</dt><dd>{source?.source || '—'}</dd></div><div><dt>来源时间</dt><dd>{stamp(source?.sourceTime)}</dd></div><div><dt>实际可用时间</dt><dd>{stamp(source?.availableAt)}</dd></div><div><dt>记录时数据年龄</dt><dd>{source?.ageSeconds == null ? '—' : `${number(source.ageSeconds, 1)} 秒`}</dd></div></dl>
        </article>;
      })}
    </div>
    <div className="live-t-news"><div className="live-t-source-heading"><h3>{diagnosticText(diagnostics?.news?.label) || '公开快讯'}</h3><span data-status={context.news?.status}>{context.news ? contextStatus[context.news.status] : '未返回'}</span></div>
      <p className="live-t-caption">数据源：{context.news?.source || '—'} · 获取时间：{stamp(context.news?.fetchedAt)}{diagnosticText(diagnostics?.news?.reason) ? ` · ${diagnosticText(diagnostics?.news?.reason)}` : ''}</p>
      {context.news?.items?.length ? <ol className="live-t-news-list" aria-label="公开快讯列表">{context.news.items.slice(0, 8).map((item, index) => {
        const href = publicNewsUrl(item.url);
        return <li key={`${item.publishedAt}-${index}`}><div className="live-t-news-title">{href ? <a href={href} target="_blank" rel="noopener noreferrer">{item.title}</a> : <span>{item.title}</span>}</div>{item.summary ? <p>{item.summary}</p> : null}<p className="live-t-caption">{item.source || '来源未返回'} · 发布时间 {stamp(item.publishedAt)} · 实际可用 {stamp(item.availableAt)}</p></li>;
      })}</ol> : <p className="jev-empty-inline">尚未返回可展示的公开快讯。</p>}
    </div>
  </>;
}

function RunContext({ run }: { run: LiveTRun }) {
  return <section className="jev-panel live-t-context" aria-labelledby="live-context-heading">
    <div className="jev-section-heading"><div><h2 id="live-context-heading">市场与公开快讯</h2><p>分开展示当前收集结果与实际提交模型的内容；状态均对应记录中的时间。</p></div></div>
    <div aria-label="当前已收集的信息"><h3 className="live-t-context-title">当前已收集的信息</h3>{run.context ? <ContextSources context={run.context} diagnostics={run.contextDiagnostics} /> : <p className="jev-empty-inline">本轮未启用或未返回扩展上下文。</p>}</div>
    <details className="live-t-model-context"><summary>最近实际提交模型的上下文{run.lastModelContext ? <span> · 提交时间 {stamp(run.lastModelContext.decisionAt)}</span> : null}</summary>
      {run.lastModelContext ? <div aria-label="最近实际提交模型的信息"><p className="live-t-caption">这是该次提交时保存的内容，不代表之后新到达的信息也已送给模型。</p><ContextSources context={run.lastModelContext.context} diagnostics={run.contextDiagnostics} /></div> : <p className="jev-empty-inline">未返回已提交模型的扩展上下文记录。</p>}
    </details>
  </section>;
}

function AccountChart({ run }: { run: LiveTRun }) {
  const rows = run.equity;
  const values = rows.flatMap(row => [row.equity, row.holdBaselineEquity]).filter(Number.isFinite);
  if (!rows.length || !values.length) return <p className="jev-empty-inline">尚无已处理行情形成的权益记录。</p>;
  const low = values.reduce((minimum, value) => Math.min(minimum, value), Infinity);
  const high = values.reduce((maximum, value) => Math.max(maximum, value), -Infinity);
  const padding = Math.max((high - low) * .12, 1);
  const firstTime = Date.parse(rows[0].timestamp); const lastTime = Date.parse(rows.at(-1)!.timestamp);
  const x = (index: number) => 90 + (Number.isFinite(firstTime) && lastTime > firstTime ? (Date.parse(rows[index].timestamp) - firstTime) / (lastTime - firstTime) : index / Math.max(rows.length - 1, 1)) * 650;
  const y = (value: number) => 195 - (value - low + padding) / (high - low + padding * 2) * 175;
  return <div className="jev-chart"><p className="live-t-caption">近期权益走势；累计盈亏以账户统计为准。</p><svg viewBox="0 0 760 240" role="img" aria-label="纸上账户权益与底仓持有基准">
    {[0, 1, 2, 3].map(index => { const value = low - padding + index / 3 * (high - low + padding * 2); return <g key={index}><line x1="90" x2="740" y1={y(value)} y2={y(value)} className="jev-gridline" /><text x="82" y={y(value) + 4} textAnchor="end">{number(value, 0)}</text></g>; })}
    {(['equity', 'holdBaselineEquity'] as const).map((key, series) => { let connected = false; const path = rows.map((row, index) => { const value = row[key]; if (!Number.isFinite(value)) { connected = false; return ''; } const point = `${connected ? 'L' : 'M'}${x(index)},${y(value)}`; connected = true; return point; }).join(' '); return <g key={key}><path d={path} fill="none" stroke={series === 0 ? '#002FA7' : '#9297A2'} strokeWidth="2" />{rows.length === 1 && Number.isFinite(rows[0][key]) ? <circle cx="90" cy={y(rows[0][key])} r="3" fill={series === 0 ? '#002FA7' : '#9297A2'} /> : null}</g>; })}
    <text x="90" y="225">{stamp(rows[0].timestamp).slice(0, 19)}</text><text x="740" y="225" textAnchor="end">{stamp(rows.at(-1)?.timestamp).slice(0, 19)}</text>
  </svg><div className="jev-chart-legend"><span><i style={{ background: '#002FA7' }} />纸上账户权益</span><span><i style={{ background: '#9297A2' }} />底仓持有基准</span></div></div>;
}

function IncrementalChart({ run }: { run: LiveTRun }) {
  const rows = run.equity;
  const values = rows.flatMap(row => row.incrementalPnl != null && Number.isFinite(row.incrementalPnl) ? [row.incrementalPnl] : []);
  if (!values.length) return <div className="live-t-incremental"><h3>做T相对持有增量收益</h3><p className="jev-empty-inline">尚无已返回的增量收益记录。</p></div>;
  const bound = Math.max(1, ...values.map(value => Math.abs(value))) * 1.15;
  const firstTime = Date.parse(rows[0].timestamp); const lastTime = Date.parse(rows.at(-1)!.timestamp);
  const x = (index: number) => 90 + (Number.isFinite(firstTime) && lastTime > firstTime ? (Date.parse(rows[index].timestamp) - firstTime) / (lastTime - firstTime) : index / Math.max(rows.length - 1, 1)) * 650;
  const y = (value: number) => 110 - value / bound * 85;
  const path = rows.map((row, index) => {
    if (row.incrementalPnl == null || !Number.isFinite(row.incrementalPnl)) return '';
    const previous = rows[index - 1]?.incrementalPnl;
    return `${previous != null && Number.isFinite(previous) ? 'L' : 'M'}${x(index)},${y(row.incrementalPnl)}`;
  }).join(' ');
  return <div className="live-t-incremental"><h3>做T相对持有增量收益 <span>（{run.config.currency}）</span></h3><p className="live-t-caption">以不做交易的初始底仓与现金为对照；零线上方优于持有，下方弱于持有，包含未闭合库存的市值影响。</p>
    <div className="jev-chart live-t-incremental-chart"><svg viewBox="0 0 760 245" role="img" aria-label="做T相对持有增量收益曲线">
      {[-bound, 0, bound].map(value => <g key={value}><line x1="90" x2="740" y1={y(value)} y2={y(value)} className={value === 0 ? 'live-t-zero-line' : 'jev-gridline'}>{value === 0 ? <title>零收益基线</title> : null}</line><text x="82" y={y(value) + 4} textAnchor="end">{number(value, 0)}</text></g>)}
      <path d={path} fill="none" stroke="#002FA7" strokeWidth="2.5" />
      {rows.map((row, index) => row.incrementalPnl == null || !Number.isFinite(row.incrementalPnl) ? null : <circle key={`${row.timestamp}-${index}`} cx={x(index)} cy={y(row.incrementalPnl)} r={rows.length === 1 ? 3 : 2} fill="#002FA7"><title>{stamp(row.timestamp)} · 增量收益 {number(row.incrementalPnl)} {run.config.currency}</title></circle>)}
      <text x="90" y="227">{stamp(rows[0].timestamp).slice(0, 19)}</text><text x="740" y="227" textAnchor="end">{stamp(rows.at(-1)?.timestamp).slice(0, 19)}</text>
    </svg><div className="jev-chart-legend"><span><i style={{ background: '#002FA7' }} />相对持有增量收益</span><span><i className="live-t-zero-key" />零收益基线</span></div></div>
  </div>;
}

function SizingRule({ run }: { run: LiveTRun }) {
  const { confidenceSizing, confidenceThreshold, lotSize, orderQuantity } = run.config;
  if (!confidenceSizing) return confidenceThreshold == null ? null : <p className="live-t-caption">历史执行置信度门槛：{percentage(confidenceThreshold)}</p>;
  const example = lotSize != null && orderQuantity != null && Number.isFinite(lotSize) && Number.isFinite(orderQuantity) && lotSize > 0 && orderQuantity >= lotSize
    ? Math.min(orderQuantity, Math.max(lotSize, Math.floor(orderQuantity * .5 / lotSize) * lotSize)) : null;
  return <div className="live-t-sizing"><strong>模型决定方向 · 置信度决定数量</strong><p className="live-t-caption">买卖请求最低 {number(lotSize, 0)} 股，最高 {number(orderQuantity, 0)} 股；按整手向下取整{example == null ? '。' : `，50% = ${number(example, 0)} 股。`}观望不产生委托；实际成交仍受可售库存、资金、流动性及时效约束。</p></div>;
}

function LiveRunView({ run }: { run: LiveTRun }) {
  const account = run.account; const quote = run.quote; const currency = run.config.currency;
  const metrics = [
    ['当前账户权益', account?.currentEquity], ['相对持有的增量收益', account?.incrementalPnl],
    ['已闭合模拟T净利润', account?.closedTNetPnl], ['累计税费', account?.totalFees],
  ] as const;
  return <>
    <section className="jev-panel live-t-status"><div className="jev-section-heading"><div><h2>{run.config.name || run.config.symbol} · {STATUS[run.status] ?? run.status}</h2><p>{run.message || '等待试跑状态更新'} · 记录 {run.runId}</p></div><span className={`jev-badge ${run.status === 'running' ? 'jev-badge-running' : ''}`}>{STATUS[run.status] ?? run.status}</span></div>
      <SizingRule run={run} />
      {run.stopReason ? <p className="jev-partial-note">{run.status === 'paused' ? '暂停原因' : '停止原因'}：{stopReason(run.stopReason)}</p> : null}
      <p className="live-t-caption">快照更新于 {stamp(run.updatedAt)}{run.stoppedAt ? ` · 结束于 ${stamp(run.stoppedAt)}` : ''}</p>
    </section>
    <section className="jev-panel" aria-labelledby="live-quote-heading"><div className="jev-section-heading"><div><h2 id="live-quote-heading">行情到达与时效</h2><p>轮询频率与交易所新数据频率分别展示；重复快照不等于新增成交。</p></div><span>{quote?.stale === true ? '行情已滞后' : quote?.stale === false ? '服务端时效检查通过' : '时效待核验'}</span></div>
      <div className="live-t-quote-grid"><div><span>快照价格（{currency}）</span><strong>{number(quote?.price, 3)}</strong></div><div><span>行情时间 market_ts</span><strong>{stamp(quote?.marketTs)}</strong></div><div><span>接收时间 received_at</span><strong>{stamp(quote?.receivedAt)}</strong></div><div><span>快照时行情年龄</span><strong>{seconds(quote?.ageMs)}</strong></div></div>
      <dl className="live-t-facts"><div><dt>行情来源</dt><dd>{quote?.source || '尚未返回'}</dd></div><div><dt>采集轮询间隔</dt><dd>{seconds(run.config.quotePollIntervalMs)}</dd></div><div><dt>行情实际更新间隔</dt><dd>{seconds(quote?.observedUpdateIntervalMs)}</dd></div><div><dt>重复快照次数</dt><dd>{number(quote?.duplicateCount, 0)}</dd></div><div><dt>快照成交量（{quote?.volumeUnit || '源原始单位'}）</dt><dd>{number(quote?.volume, 0)}</dd></div></dl>
      <p className="live-t-caption">每 1 秒轮询不代表每秒都有交易所新行情。当前数值只来自已到达的真实公开快照，延迟与撮合限制以本轮记录为准。</p>
    </section>
    <RunContext run={run} />
    <section className="jev-panel" aria-labelledby="live-account-heading"><div className="jev-section-heading"><div><h2 id="live-account-heading">纸上账户 · {currency}</h2><p>严格 T+1：只可卖出昨日可售库存；今日买入进入冻结库存。</p></div></div>
      <div className="live-t-metrics">{metrics.map(([label, value]) => <div key={label}><span>{label}（{currency}）</span><strong>{number(value)}</strong></div>)}</div>
      <IncrementalChart run={run} />
      <AccountChart run={run} />
      <dl className="live-t-account-grid"><div><dt>初始底仓现值（{currency}）</dt><dd>{number(run.config.initialBaseValue)}</dd></div><div><dt>初始现金（{currency}）</dt><dd>{number(run.config.initialCash)}</dd></div><div><dt>初始总权益（{currency}）</dt><dd>{number(account?.initialEquity)}</dd></div><div><dt>持有基准权益（{currency}）</dt><dd>{number(account?.holdBaselineEquity)}</dd></div><div><dt>账户总收益（{currency}）</dt><dd>{number(account?.totalPnl)}</dd></div><div><dt>可用现金（{currency}）</dt><dd>{number(account?.cash)}</dd></div><div><dt>初始股数</dt><dd>{number(account?.initialShares, 0)}</dd></div><div><dt>当前总库存（股）</dt><dd>{number(account?.totalShares, 0)}</dd></div><div><dt>昨日可售库存（股）</dt><dd>{number(account?.sellableYesterdayShares, 0)}</dd></div><div><dt>今日冻结库存（股）</dt><dd>{number(account?.todayFrozenShares, 0)}</dd></div><div><dt>库存偏离初始（股）</dt><dd>{number(account?.inventoryGapShares, 0)}</dd></div><div><dt>已闭合T每初始股降本（{currency}/股）</dt><dd>{number(account?.costReductionPerInitialShare, 4)}</dd></div></dl>
      <p className="live-t-caption">底仓金额是模拟底仓的初始现值，不是真实买入成本。库存偏离为当前股数减初始股数，负数表示待回补。每初始股降本由已闭合模拟T净利润计算，增量权益包含未闭合库存影响；未提供历史持仓成本，不据此编造成本价。</p>
    </section>
    <section className="jev-panel"><div className="jev-section-heading"><div><h2>模型调用与试跑边界</h2><p>本区展示纸上记录；刷新页面不会启动模型或交易。</p></div></div>
      <dl className="live-t-account-grid">
        <div><dt>已调用 / 本轮上限</dt><dd>{number(run.usage.requests, 0)} / {run.config.maxModelRequests === null ? '不限' : number(run.config.maxModelRequests, 0)}</dd></div>
        <div><dt>{run.usage.costIsEstimate ? '本记录未核验预算估值' : '本记录模型费用'} / 可用额度（美元）</dt><dd>${number(run.usage.costUsd, 4)} / ${number(run.config.maxCostUsd, 4)}</dd></div>
        <div><dt>目标判断检查间隔</dt><dd>{seconds(run.config.decisionIntervalSeconds == null ? null : run.config.decisionIntervalSeconds * 1000)}</dd></div>
        <div><dt>实际模型请求间隔（近期中位数）</dt><dd>{seconds(run.usage.observedDecisionIntervalMs)}</dd></div>
        <div><dt>模型 HTTP 响应（最近）</dt><dd>{seconds(run.usage.lastLatencyMs)}</dd></div>
        <div><dt>模型 HTTP 响应（中位数）</dt><dd>{seconds(run.usage.medianLatencyMs)}</dd></div>
        <div><dt>最近模型请求发出</dt><dd>{stamp(run.usage.lastModelRequestAt)}</dd></div>
        <div><dt>最近模型响应收到</dt><dd>{stamp(run.usage.lastModelResponseAt)}</dd></div>
        <div><dt>本轮最长时长</dt><dd>{run.config.durationSeconds === null ? '持续' : run.config.durationSeconds == null ? '—' : `${number(run.config.durationSeconds, 0)} 秒`}</dd></div>
      </dl>
      <p className="live-t-caption">目标检查间隔不代表每次都调用模型；实际请求还需等待新行情、有效上下文及前次请求或待成交委托结束。实际请求间隔统计近期相邻请求，暂停和重启空档不计入。</p>
    </section>
    <section className="jev-panel"><div className="jev-section-heading"><h2>模拟成交账本</h2><span>最近 {run.trades.length} 笔 · 没有券商下单</span></div>{run.trades.length ? <div className="jev-table-scroll" role="region" aria-label="实时纸上模拟成交" tabIndex={0}><table><thead><tr><th>模拟时间</th><th>对应行情时间</th><th>方向</th><th>股数</th><th>价格</th><th>税费合计</th><th>现金余额</th><th>昨日可售 / 今日冻结</th><th>说明</th></tr></thead><tbody>{run.trades.map((trade, index) => <tr key={`${trade.timestamp}-${index}`}><td>{stamp(trade.timestamp)}</td><td>{stamp(trade.marketTs)}</td><td>{action(trade.action)}</td><td>{number(trade.shares, 0)}</td><td>{number(trade.price, 3)}</td><td>{number(trade.fee)}</td><td>{number(trade.cashAfter)}</td><td>{number(trade.sellableAfter, 0)} / {number(trade.frozenAfter, 0)}</td><td className="jev-reason">{trade.reason || '—'}</td></tr>)}</tbody></table></div> : <p className="jev-empty-inline">本轮尚无模拟成交。</p>}</section>
    <section className="jev-panel"><div className="jev-section-heading"><h2>已到达行情上的模型判断</h2><span>最近 {run.signals.length} 次</span></div>{run.signals.length ? <div className="jev-table-scroll"><table><thead><tr><th>行情时间</th><th>模型返回时间</th><th>动作</th><th>置信度</th><th>请求股数</th><th>执行状态</th><th>说明</th></tr></thead><tbody>{run.signals.map((signal, index) => <tr key={`${signal.marketTs}-${index}`}><td>{stamp(signal.marketTs)}</td><td>{stamp(signal.receivedAt)}</td><td>{action(signal.action)}</td><td>{signal.confidence == null ? '—' : `${number(signal.confidence * 100, 1)}%`}</td><td>{number(signal.requestedQuantity, 0)}</td><td>{signal.executionStatus || '—'}</td><td className="jev-reason">{signal.reason || '—'}</td></tr>)}</tbody></table></div> : <p className="jev-empty-inline">本轮尚无模型判断。</p>}</section>
    {(run.warnings.length || run.methodology.length) ? <section className="jev-panel jev-methodology"><h2>数据与模拟口径</h2>{run.warnings.length ? <ul className="jev-warning-list">{run.warnings.map((warning, index) => <li key={index}>{warning}</li>)}</ul> : null}{run.methodology.length ? <ul>{run.methodology.map((line, index) => <li key={index}>{line}</li>)}</ul> : null}</section> : null}
  </>;
}

function LiveWorkbench() {
  const [params, setParams] = useSearchParams(); const requestedRun = params.get('run');
  const [status, setStatus] = useState<LiveTStatus | null>(null); const [detail, setDetail] = useState<LiveTRun | null>(null);
  const [runs, setRuns] = useState<LiveTRunSummary[]>([]); const [loading, setLoading] = useState(true);
  const [error, setError] = useState(''); const [accessLost, setAccessLost] = useState(false); const [retry, setRetry] = useState(0);
  const invalidated = useRef(false);
  const selected = requestedRun ? detail?.runId === requestedRun ? detail : null : status?.currentRun ?? null;
  useEffect(() => {
    if (accessLost) return;
    invalidated.current = false; const controller = new AbortController(); let timer: ReturnType<typeof setTimeout> | undefined;
    let lastKnownRun: LiveTRun | null = null; let failedReads = 0;
    const loseAccess = () => { invalidated.current = true; controller.abort(); setStatus(null); setDetail(null); setRuns([]); setError(''); setAccessLost(true); setLoading(false); };
    const read = async (initial: boolean) => {
      try {
        const values = await Promise.allSettled([
          jevLiveTApi.status(controller.signal),
          ...(initial ? [jevLiveTApi.runs(controller.signal)] : []),
          ...(requestedRun ? [jevLiveTApi.run(requestedRun, controller.signal)] : []),
        ].map(request => request.catch(failure => {
          if (!controller.signal.aborted && denied(failure)) loseAccess();
          throw failure;
        })));
        if (controller.signal.aborted || invalidated.current) return;
        if (values.some(value => value.status === 'rejected' && denied(value.reason))) { loseAccess(); return; }
        const statusRead = values[0]; let current = lastKnownRun; let interval = 2000;
        if (statusRead.status === 'fulfilled') {
          const data = statusRead.value as LiveTStatus; setStatus(data);
          if (!requestedRun) {
            current = data.currentRun ?? null;
            if (!initial && current) {
              const { runId, status, message, createdAt, updatedAt, config } = current;
              setRuns(previous => [{ runId, status, message, createdAt, updatedAt, config }, ...previous.filter(row => row.runId !== runId)].slice(0, 20));
            }
          }
          interval = Math.max(1000, Math.min(10000, data.pollIntervalMs ?? 2000));
        }
        if (initial && values[1].status === 'fulfilled') setRuns((values[1].value as { items: LiveTRunSummary[] }).items);
        if (requestedRun) {
          const read = values.at(-1)!;
          if (read.status === 'fulfilled') { const run = read.value as LiveTRun; if (run.runId !== requestedRun) throw new Error('返回的试跑编号与所选记录不一致。'); setDetail(run); current = run; }
          else if (getParsedApiError(read.reason).status === 404) { setDetail(null); current = null; }
        }
        const failures = values.filter(value => value.status === 'rejected');
        lastKnownRun = current; failedReads = failures.length ? failedReads + 1 : 0;
        setError(failures.map(value => toApiErrorMessage(value.reason)).join('；')); setLoading(false);
        if (!requestedRun) timer = setTimeout(() => void read(false), failedReads >= 3 ? 30000 : active(current) ? interval : 10000);
        else if (active(current) && failedReads < 3) timer = setTimeout(() => void read(false), interval);
      } catch (failure) { if (!controller.signal.aborted && !invalidated.current) { if (denied(failure)) loseAccess(); else { setError(toApiErrorMessage(failure)); setLoading(false); if (!requestedRun) timer = setTimeout(() => void read(false), 30000); } } }
    };
    void read(true);
    document.title = 'Jev 实时纸上做T - 乐子乌超级价值';
    return () => { invalidated.current = true; controller.abort(); if (timer) clearTimeout(timer); };
  }, [requestedRun, retry, accessLost]);
  const select = (runId?: string) => { setDetail(null); setLoading(true); setError(''); const next = new URLSearchParams(params); if (runId) next.set('run', runId); else next.delete('run'); setParams(next); if (requestedRun === (runId ?? null)) setRetry(value => value + 1); };
  if (accessLost) return <main className="jev-page"><h1>实时纸上做T</h1><div className="jev-alert" role="alert">访问权限已变化，当前账号的试跑数据已清除。</div></main>;
  return <main className="jev-page live-t-page"><header className="jev-page-heading"><div><h1>实时纸上做T</h1><p>华懋科技 603306 · 800 万元现值底仓 + 400 万元现金 · 严格 T+1</p><p>时间均为上海时间（UTC+8）</p></div><span className="jev-input-label">纸上账户 · 不连接券商</span></header>
    <JevLiveTControl selectedRunId={requestedRun} formatTime={stamp} onAccessLost={() => { invalidated.current = true; setStatus(null); setDetail(null); setRuns([]); setAccessLost(true); }} onChanged={state => select(state.runId ?? undefined)} onOpenRun={select} />
    <div className="live-t-toolbar"><div><strong>{requestedRun ? '历史试跑记录' : '当前授权试跑'}</strong><p>{status?.message || (loading ? '正在读取试跑状态…' : '暂无状态说明')}</p></div><div>{requestedRun ? <button className="jev-secondary" onClick={() => select()}>回到当前试跑</button> : null}<button className="jev-secondary" disabled={loading} onClick={() => { setLoading(true); setRetry(value => value + 1); }}><RefreshCw size={14} />{loading ? '正在读取' : '刷新状态'}</button></div></div>
    {error ? <div className="jev-alert" role="alert">状态读取失败：{error}</div> : null}
    {selected ? <LiveRunView run={selected} /> : <section className="jev-panel live-t-empty"><h2>{loading ? '正在等待真实试跑快照' : '尚无可查看的授权试跑'}</h2><p>试跑由后台按授权范围启动。没有记录时不展示模拟收益，也不会因访问本页产生模型调用。</p></section>}
    <section className="jev-panel"><div className="jev-section-heading"><div><h2>纸上试跑历史</h2><p>仅展示当前账号有权查看的记录。</p></div></div>{runs.length ? <div className="jev-table-scroll"><table><thead><tr><th>证券 / 记录</th><th>开始记录时间</th><th>最近更新</th><th>状态</th><th>查看</th></tr></thead><tbody>{runs.map(run => <tr key={run.runId}><td>{run.config.name || run.config.symbol}<small>{run.runId}</small></td><td>{stamp(run.createdAt)}</td><td>{stamp(run.updatedAt)}</td><td>{STATUS[run.status] ?? run.status}</td><td><button className="jev-text-button" onClick={() => select(run.runId)}>查看试跑 {run.runId.slice(0, 8)}</button></td></tr>)}</tbody></table></div> : <p className="jev-empty-inline">{loading ? '正在读取试跑历史…' : '暂无试跑记录。'}</p>}</section>
  </main>;
}

export default function JevLiveTPage() {
  const access = useUserAccess();
  const scope = JSON.stringify([access.user?.id, access.user?.status, access.authMethod, access.accessEnabled, access.loggedIn]);
  if (access.isLoading || !access.loggedIn) return <main className="jev-page"><p>正在确认纸上试跑访问权限。</p></main>;
  return <LiveWorkbench key={scope} />;
}
