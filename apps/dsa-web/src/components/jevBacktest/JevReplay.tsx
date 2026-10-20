import { useEffect, useState } from 'react';
import { Pause, Play, RotateCcw } from 'lucide-react';
import type { JevBacktestTask, JevEquityPoint, JevPriceBar, JevSignal, JevTrade } from '../../types/jevBacktest';

const n = (value?: number | null, digits = 2) => value == null || !Number.isFinite(value) ? '—' : value.toLocaleString('zh-CN', { minimumFractionDigits: digits, maximumFractionDigits: digits });
const pct = (value?: number | null) => value == null || !Number.isFinite(value) ? '—' : `${value > 0 ? '+' : ''}${n(value)}%`;
const time = (date: string) => date.replace('T', ' ').slice(0, 16);
const volumeText = (volume: number) => volume.toLocaleString('zh-CN', { maximumFractionDigits: 20 });
const signalText = (value: string) => ({
  ok: '已判断', uncertain: '置信度不足', unavailable: '模型不可用', not_called: '本根未调用',
  pending: '等待下一根K线', filled: '已成交', no_order: '无委托', no_next_bar: '无下一根行情',
  t_plus_one: 'T+1 待可卖', suspended: '停牌', zero_volume: '无成交量', limit_up: '涨停拦截',
  limit_down: '跌停拦截', slippage_beyond_price_limit: '滑点越过价格限制', insufficient_cash: '资金不足',
  no_position: '无持仓', already_in_position: '已有持仓', hold_or_target_already_met: '持有或已达目标',
  signal: '模型信号', max_holding_rule: '最长持仓退出', stop_loss_rule: '止损退出', take_profit_rule: '止盈退出',
  price_volume_only: '仅量价', model_unavailable_no_action: '模型不可用，无模型委托', decision_interval: '决策间隔',
} as Record<string, string>)[value] ?? value;
type LineRow = { date: string; equity: number; buyHold: number | null; ma: number | null };

function EquityChart({ rows }: { rows: LineRow[] }) {
  if (!rows.length) return <p className="jev-empty-inline">尚无权益数据。</p>;
  const values = rows.flatMap(row => [row.equity, row.buyHold, row.ma]).filter((value): value is number => value != null && Number.isFinite(value));
  const low = Math.min(...values); const high = Math.max(...values); const pad = Math.max((high - low) * 0.15, high * 0.0001, 1);
  const min = low - pad; const max = high + pad;
  const x = (index: number) => 76 + index / Math.max(rows.length - 1, 1) * 650;
  const y = (value: number) => 170 - (value - min) / (max - min) * 145;
  const series = [{ key: 'equity', label: 'Jev 权益', color: '#002FA7' }, { key: 'buyHold', label: '买入并持有', color: '#30343B' }, { key: 'ma', label: 'MA5 / MA20', color: '#9297A2' }] as const;
  return <div className="jev-chart"><svg viewBox="0 0 750 215" role="img" aria-label="截至当前回放时点的 Jev、买持和均线基准权益曲线，单位元">
    {[0, 1, 2, 3].map(index => { const value = min + (max - min) * index / 3; return <g key={index}><line x1="76" x2="726" y1={y(value)} y2={y(value)} className="jev-gridline" /><text x="68" y={y(value) + 4} textAnchor="end">{n(value, 0)}</text></g>; })}
    {series.map(series => {
      let connected = false;
      const path = rows.map((row, index) => { const value = row[series.key]; if (value == null) { connected = false; return ''; } const part = `${connected ? 'L' : 'M'}${x(index)},${y(value)}`; connected = true; return part; }).join(' ');
      return <g key={series.key}><path d={path} fill="none" stroke={series.color} strokeWidth={series.key === 'equity' ? 2.5 : 1.5} strokeDasharray={series.key === 'ma' ? '5 4' : undefined} />{rows.length === 1 && rows[0][series.key] != null ? <circle cx={x(0)} cy={y(rows[0][series.key]!)} r="3" fill={series.color} /> : null}</g>;
    })}
    <text x="76" y="202">{time(rows[0].date)}</text><text x="726" y="202" textAnchor="end">{time(rows.at(-1)!.date)}</text>
  </svg><div className="jev-chart-legend">{series.map(series => <span key={series.key}><i style={{ background: series.color }} />{series.label}</span>)}</div></div>;
}

function PriceChart({ bars, equity, trades }: { bars: JevPriceBar[]; equity: JevEquityPoint[]; trades: JevTrade[] }) {
  // Limit the visible candle density without exposing bars after the replay cursor.
  const candles = bars.slice(-90);
  const points = candles.length ? candles : equity.slice(-90).map(row => ({ ...row, open: row.close, high: row.close, low: row.close }));
  if (!points.length) return <p className="jev-empty-inline">尚无价格数据。</p>;
  const visibleDates = new Set(points.map(row => row.date));
  const visibleTrades = trades.filter(trade => visibleDates.has(trade.date));
  const low = Math.min(...points.map(row => row.low), ...visibleTrades.map(trade => trade.price));
  const high = Math.max(...points.map(row => row.high), ...visibleTrades.map(trade => trade.price));
  const pad = Math.max((high - low) * 0.15, Math.abs(high) * 0.002, 0.01);
  const min = low - pad; const max = high + pad;
  const x = (index: number) => 58 + index / Math.max(points.length - 1, 1) * 668;
  const y = (price: number) => 200 - (price - min) / (max - min) * 178;
  const width = Math.min(9, 490 / points.length);
  const volumePoints = candles.filter(bar => Number.isFinite(bar.volume) && bar.volume >= 0);
  const maxVolume = volumePoints.length ? Math.max(...volumePoints.map(bar => bar.volume)) : 0;
  const volumeY = (volume: number) => 298 - volume / Math.max(maxVolume, 1) * 65;
  return <div className="jev-chart"><svg viewBox="0 0 750 335" role="img" aria-label={`截至当前时点的${candles.length ? '真实分钟K线' : '分钟收盘价'}、原始成交量及实际成交点`}>
    {[0, 1, 2, 3].map(index => { const value = min + (max - min) * index / 3; return <g key={index}><line x1="58" x2="726" y1={y(value)} y2={y(value)} className="jev-gridline" /><text x="48" y={y(value) + 4} textAnchor="end">{n(value)}</text></g>; })}
    {candles.length ? candles.map((bar, index) => <g key={bar.date}><title>{time(bar.date)} 开 {n(bar.open)} 高 {n(bar.high)} 低 {n(bar.low)} 收 {n(bar.close)}{Number.isFinite(bar.volume) && bar.volume >= 0 ? ` · 原始成交量 ${volumeText(bar.volume)}` : ' · 成交量未返回'}</title><line x1={x(index)} x2={x(index)} y1={y(bar.high)} y2={y(bar.low)} stroke={bar.close >= bar.open ? '#002FA7' : '#4E535C'} /><rect x={x(index) - width / 2} y={Math.min(y(bar.open), y(bar.close))} width={width} height={Math.max(1, Math.abs(y(bar.open) - y(bar.close)))} fill={bar.close >= bar.open ? '#FFFFFF' : '#4E535C'} stroke={bar.close >= bar.open ? '#002FA7' : '#4E535C'} /></g>) : <path d={points.map((row, index) => `${index ? 'L' : 'M'}${x(index)},${y(row.close)}`).join(' ')} stroke="#30343B" strokeWidth="1.7" fill="none" />}
    {visibleTrades.map((trade, index) => {
      const barIndex = points.findIndex(bar => bar.date === trade.date); if (barIndex < 0) return null;
      const cx = x(barIndex); const cy = y(trade.price);
      return <g key={`${trade.date}-${index}`}><title>{time(trade.date)} {trade.action} {trade.shares} 股 · 成交 {n(trade.price, 3)}</title><circle cx={cx} cy={cy} r="7" fill={trade.action === 'BUY' ? '#002FA7' : '#30343B'} stroke="#FFFFFF" strokeWidth="1.5" /><text x={cx} y={cy + 3} textAnchor="middle" className="jev-trade-marker">{trade.action === 'BUY' ? 'B' : 'S'}</text></g>;
    })}
    <text x="58" y="220">成交量（源原始单位）</text>
    {volumePoints.length ? <g aria-label="当前可见原始成交量">
      <line x1="58" x2="726" y1="233" y2="233" className="jev-gridline" />
      <line x1="58" x2="726" y1="298" y2="298" className="jev-gridline" />
      <text x="48" y="237" textAnchor="end"><title>当前可见最大成交量 {volumeText(maxVolume)}</title>{maxVolume.toLocaleString('zh-CN', { notation: 'compact', maximumFractionDigits: 1 })}</text>
      <text x="48" y="302" textAnchor="end">0</text>
      {candles.map((bar, index) => Number.isFinite(bar.volume) && bar.volume >= 0 ? <g key={bar.date}><title>{time(bar.date)} 原始成交量 {volumeText(bar.volume)}</title><rect x={x(index) - width / 2} y={volumeY(bar.volume)} width={width} height={298 - volumeY(bar.volume)} fill={bar.close >= bar.open ? '#002FA7' : '#4E535C'} opacity="0.7" /></g> : null)}
    </g> : <text x="58" y="263">成交量未返回</text>}
    <text x="58" y="323">{time(points[0].date)}</text><text x="726" y="323" textAnchor="end">{time(points.at(-1)!.date)}</text>
  </svg><div className="jev-chart-legend"><span>{candles.length ? '蓝色空心：收 ≥ 开 · 灰色实心：收 < 开' : '行情仅返回收盘价，未绘制蜡烛'}</span><span>B / S：实际买入 / 卖出</span><span>最近 {points.length} 根K线</span></div></div>;
}

export function JevReplay({ task }: { task: JevBacktestTask }) {
  const live = task.status === 'running' || task.status === 'queued';
  const result = task.result; const data = live ? task.preview : (result ?? task.preview);
  const [cursor, setCursor] = useState(1); const [playing, setPlaying] = useState(false); const [speed, setSpeed] = useState(5);
  const points = data?.equity ?? []; const count = live ? points.length : Math.min(cursor, points.length);
  const visibleEquity = points.slice(0, count); const current = visibleEquity.at(-1);
  const cutoff = current?.date;
  const trades = (data?.trades ?? []).filter(trade => cutoff && trade.date <= cutoff);
  const signals = (data?.signals ?? []).filter(signal => cutoff && signal.date <= cutoff).map(signal => (signal.fillDate && cutoff && signal.fillDate > cutoff) || (signal.date === cutoff && signal.executionAction !== 'HOLD' && !['no_order', 'no_next_bar'].includes(signal.fillStatus))
    ? { ...signal, fillDate: undefined, fillStatus: 'pending', executionReason: '等待后续K线执行' }
    : signal);
  const marketBars = (data?.marketData?.bars ?? []).filter(bar => cutoff && bar.date <= cutoff);
  const initial = data?.summary.initialCash ?? task.config.initialCash;
  const profit = current ? current.equity - initial : null;
  const realized = current?.realizedPnl ?? trades.reduce((sum, trade) => sum + (trade.pnl ?? 0), 0);
  const unrealized = current?.unrealizedPnl ?? (profit == null ? null : profit - realized);
  const lastSignal = signals.at(-1);
  const buyHold = new Map(result?.baselines?.buyHold?.equity.map(row => [row.date, row.equity]));
  const ma = new Map(result?.baselines?.ma520?.equity.map(row => [row.date, row.equity]));
  const rows = visibleEquity.map(row => ({ date: row.date, equity: row.equity, buyHold: buyHold.get(row.date) ?? null, ma: ma.get(row.date) ?? null }));
  const closed = trades.filter(trade => trade.pnl != null);
  const totalCost = trades.reduce((sum, trade) => sum + trade.commission + trade.sellTax + trade.slippageCost, 0);
  const returnOf = (equity?: number | null) => equity == null ? null : (equity / initial - 1) * 100;

  useEffect(() => {
    if (!playing || live || cursor >= points.length) return;
    const timer = setInterval(() => setCursor(value => Math.min(value + 1, points.length)), 1000 / speed);
    return () => clearInterval(timer);
  }, [playing, live, cursor, points.length, speed]);

  if (!data || !current) return <section className="jev-panel"><p className="jev-empty-inline">等待首个已处理行情。</p></section>;

  const maxDrawdown = -Math.min(...visibleEquity.map(row => row.drawdownPct));
  const metrics = [['Jev 收益', pct(returnOf(current.equity))], ['买入并持有', pct(returnOf(buyHold.get(current.date)))], ['MA5 / MA20', pct(returnOf(ma.get(current.date)))], ['最大回撤（至此）', `${n(maxDrawdown)}%`], ['已完成平仓胜率', closed.length ? `${n(closed.filter(trade => (trade.pnl ?? 0) > 0).length / closed.length * 100)}%` : '无平仓交易'], ['已发生成本（元）', n(totalCost)]];

  return <div className="jev-results">
    <section className="jev-panel jev-replay" aria-labelledby="jev-replay-heading">
      <div className="jev-section-heading"><div><h2 id="jev-replay-heading">{task.config.symbol} · {live ? '实时模拟' : result ? '分钟行情回放' : '已处理行情回放'}</h2><p>{task.config.startDate} 至 {task.config.endDate} · {task.config.timeframe} · {task.config.settlementMode === 'research_t0' ? '研究 T+0（不代表 A 股可执行）' : 'A 股 T+1'}</p></div><span className="jev-replay-clock">{time(current.date)}</span></div>
      {!live && !result ? <p className="jev-partial-note">任务已停止。以下仅回放停止前已处理的行情，不是完整区间回测结果。</p> : null}
      <div className="jev-playback-controls">
        <button className="jev-primary" disabled={live || points.length < 2} onClick={() => { if (cursor >= points.length) { setCursor(1); setPlaying(true); } else setPlaying(value => !value); }}>{playing && cursor < points.length ? <Pause size={16} /> : <Play size={16} />}{live ? '跟随任务' : playing && cursor < points.length ? '暂停回放' : '播放回放'}</button>
        <button className="jev-secondary" disabled={live} aria-label="回到回放起点" onClick={() => { setCursor(1); setPlaying(false); }}><RotateCcw size={16} /></button>
        <label className="jev-speed">速度<select aria-label="回放速度" value={speed} disabled={live} onChange={event => setSpeed(Number(event.target.value))}>{[1, 5, 20, 60].map(value => <option key={value} value={value}>{value}×</option>)}</select></label>
        <label className="jev-timeline"><span>已处理 {count} / {points.length} 根K线</span><input type="range" aria-label="回放进度" min="1" max={Math.max(points.length, 1)} value={count} disabled={live} onChange={event => { setCursor(Number(event.target.value)); setPlaying(false); }} /></label>
      </div>
      <div className="jev-replay-grid">
        <div className="jev-replay-chart"><PriceChart bars={marketBars} equity={visibleEquity} trades={trades} /><p className="jev-chart-note">{live ? '仅显示服务器已处理的行情。' : '播放和拖动进度在本地完成，不调用模型。1× 每秒推进一根K线。'}</p></div>
        <aside className="jev-account" aria-label="回放时点账户">
          <span>累计利润（元）</span><strong className="jev-profit" data-testid="jev-current-profit">{n(profit)}</strong>
          <dl><div><dt>账户权益</dt><dd>{n(current.equity)}</dd></div><div><dt>可用现金</dt><dd>{n(current.cash)}</dd></div><div><dt>持仓数量</dt><dd>{n(current.positionShares, 0)} 股</dd></div><div><dt>持仓市值</dt><dd>{n(current.equity - current.cash)}</dd></div><div><dt>已实现盈亏</dt><dd>{n(realized)}</dd></div><div><dt>持仓浮盈</dt><dd>{n(unrealized)}</dd></div></dl>
          <div className="jev-current-signal"><span>最新已知信号</span>{lastSignal ? <><strong>{lastSignal.action} <small>{lastSignal.confidence == null ? '置信度不可用' : `${n(lastSignal.confidence * 100, 1)}%`}</small></strong><p>{signalText(lastSignal.executionReason || lastSignal.status)}</p>{lastSignal.fillStatus === 't_plus_one' ? <p>T+1 待可卖</p> : null}</> : <p>等待信号</p>}</div>
        </aside>
      </div>
      <div className="jev-metrics">{metrics.map(([label, value]) => <div key={label}><span>{label}</span><strong>{value}</strong></div>)}</div>
      <details className="jev-equity-detail" open><summary>权益曲线与基准（截至当前时点）</summary><EquityChart rows={rows} /></details>
    </section>
    <section className="jev-panel">
      <div className="jev-section-heading"><div><h2>成交账本</h2><p>仅显示回放时点前的实际成交 · 金额单位：元</p></div><span>{trades.length} 笔</span></div>
      {trades.length ? <div className="jev-table-scroll" tabIndex={0} role="region" aria-label="当前时点成交明细，可横向滚动"><table><thead><tr><th>成交 / 信号时间</th><th>方向</th><th>数量（股）</th><th>成交价</th><th>佣金 / 税</th><th>滑点</th><th>余额</th><th>平仓盈亏</th><th>原因</th></tr></thead><tbody>{trades.map((trade, index) => <tr key={`${trade.date}-${index}`}><td>{time(trade.date)}<small>{time(trade.signalDate)}</small></td><td><span className={`jev-action jev-action-${trade.action}`}>{trade.action}</span></td><td>{n(trade.shares, 0)}</td><td>{n(trade.price, 3)}</td><td>{n(trade.commission)} / {n(trade.sellTax)}</td><td>{n(trade.slippageCost)}</td><td>{n(trade.cashAfter)}</td><td>{n(trade.pnl)}</td><td className="jev-reason">{signalText(trade.reason || '—')}</td></tr>)}</tbody></table></div> : <p className="jev-empty-inline">当前回放时点尚无成交。</p>}
    </section>
    <SignalLedger signals={signals} />
    {result ? <section className="jev-panel jev-methodology"><h2>数据与模拟口径</h2>{result.marketData?.source ? <p>行情来源：{result.marketData.source} · 周期 {result.marketData.frequency}</p> : null}{result.dataQuality.warnings?.length ? <ul className="jev-warning-list">{result.dataQuality.warnings.map((warning, index) => <li key={index}>{warning}</li>)}</ul> : null}<ul>{result.methodology.map((line, index) => <li key={index}>{line}</li>)}</ul><details><summary>查看本次回测参数</summary><dl className="jev-config-summary"><div><dt>分钟数据源</dt><dd>{task.config.dataSource === 'sina' ? '新浪公开分钟' : 'Tushare 分钟'}</dd></div><div><dt>输入窗口 / 决策间隔</dt><dd>{task.config.lookbackBars} / {task.config.decisionInterval} 根K线</dd></div><div><dt>置信阈值 / 目标仓位</dt><dd>{n(task.config.confidenceThreshold * 100, 0)}% / {n(task.config.positionPct * 100, 0)}%</dd></div><div><dt>最长持仓</dt><dd>{task.config.maxHoldingBars} 根K线</dd></div><div><dt>佣金 / 最低佣金</dt><dd>{task.config.commissionBps} bp / {task.config.minCommission} 元</dd></div><div><dt>卖出税 / 滑点</dt><dd>{task.config.sellTaxBps} bp / {task.config.slippageBps} bp</dd></div></dl></details></section> : null}
  </div>;
}

function SignalLedger({ signals }: { signals: JevSignal[] }) {
  return <section className="jev-panel"><div className="jev-section-heading"><div><h2>信号与执行状态</h2><p>置信度为模型输出；T+1 等限制可能使卖出信号等待执行。</p></div><span>{signals.length} 条</span></div>{signals.length ? <div className="jev-table-scroll" tabIndex={0} role="region" aria-label="当前时点信号明细，可横向滚动"><table><thead><tr><th>信号时间</th><th>模型动作</th><th>置信度</th><th>状态 / 标签</th><th>执行动作</th><th>成交状态</th><th>执行原因</th></tr></thead><tbody>{signals.map((signal, index) => <tr key={`${signal.date}-${index}`}><td>{time(signal.date)}</td><td><span className={`jev-action jev-action-${signal.action}`}>{signal.action}</span></td><td>{signal.confidence == null ? '—' : `${n(signal.confidence * 100, 1)}%`}</td><td><div className="jev-tags"><span>{signalText(signal.status)}</span>{signal.labels?.map(tag => <span key={tag}>{signalText(tag)}</span>)}</div></td><td>{signal.executionAction || '—'}</td><td>{signalText(signal.fillStatus || '—')}{signal.fillDate ? <small>{time(signal.fillDate)}</small> : null}</td><td className="jev-reason">{signalText(signal.executionReason || '—')}</td></tr>)}</tbody></table></div> : <p className="jev-empty-inline">当前回放时点尚无信号。</p>}</section>;
}
