import { useEffect, useState } from 'react';
import { Pause, Play } from 'lucide-react';
import { explainResearch } from './terminology';
import type { ConvertibleTask, ConvertibleTrade } from '../../types/jevConvertibleT';

const number = (value?: number | null, digits = 2) => value == null || !Number.isFinite(value) ? '—' : value.toLocaleString('zh-CN', { minimumFractionDigits: digits, maximumFractionDigits: digits });
const stamp = (value: string) => value.replace('T', ' ').slice(0, 26);
const finite = (value: unknown): value is number => typeof value === 'number' && Number.isFinite(value);

function Lines({ rows, keys, label, trades = [] }: { rows: Array<{ timestamp: string; eventIndex?: number; [key: string]: unknown }>; keys: Array<{ key: string; label: string; color: string }>; label: string; trades?: ConvertibleTrade[] }) {
  const markers = trades.map(trade => ({ trade, index: rows.findIndex(row => trade.eventIndex != null && row.eventIndex != null ? row.eventIndex === trade.eventIndex : row.timestamp === trade.timestamp) })).filter(marker => marker.index >= 0 && finite(marker.trade.price));
  const values = [...rows.flatMap(row => keys.map(key => row[key.key])).filter(finite), ...markers.map(marker => marker.trade.price)];
  if (!rows.length || !values.length) return <p className="jev-empty-inline">无可绘制记录。</p>;
  const low = values.reduce((low, value) => Math.min(low, value), Infinity); const high = values.reduce((high, value) => Math.max(high, value), -Infinity); const pad = Math.max((high - low) * .1, Math.abs(high) * .0001, .01);
  const x = (index: number) => 76 + index / Math.max(rows.length - 1, 1) * 650;
  const y = (value: number) => 195 - (value - low + pad) / (high - low + pad * 2) * 170;
  return <div className="jev-chart"><svg viewBox="0 0 750 238" role="img" aria-label={label}>
    {[0, 1, 2, 3].map(index => { const value = low - pad + (high - low + pad * 2) * index / 3; return <g key={index}><line x1="76" x2="726" y1={y(value)} y2={y(value)} className="jev-gridline" /><text x="68" y={y(value) + 4} textAnchor="end">{number(value, Math.abs(value) >= 1000 ? 0 : 2)}</text></g>; })}
    {keys.map(key => { let connected = false; const path = rows.map((row, index) => { const value = row[key.key]; if (!finite(value)) { connected = false; return ''; } const point = `${connected ? 'L' : 'M'}${x(index)},${y(value)}`; connected = true; return point; }).join(' '); return <g key={key.key}><path d={path} fill="none" stroke={key.color} strokeWidth="2" />{rows.length === 1 && finite(rows[0][key.key]) ? <circle cx={x(0)} cy={y(Number(rows[0][key.key]))} r="3" fill={key.color} /> : null}</g>; })}
    {markers.map(({ trade, index }, markerIndex) => <g key={markerIndex}><title>{stamp(trade.timestamp)} {trade.side} {number(trade.quantity, 0)} @ {number(trade.price, 4)}</title><circle cx={x(index)} cy={y(trade.price)} r="5" fill={trade.side === 'BUY' ? '#002FA7' : '#616773'} /><text x={x(index)} y={y(trade.price) + 3} textAnchor="middle" className="jev-trade-marker">{trade.side === 'BUY' ? 'B' : 'S'}</text></g>)}
    <text x="76" y="223">{stamp(rows[0].timestamp).slice(0, 19)}</text><text x="726" y="223" textAnchor="end">{stamp(rows.at(-1)!.timestamp).slice(0, 19)}</text>
  </svg><div className="jev-chart-legend">{keys.map(key => <span key={key.key}><i style={{ background: key.color }} />{key.label}</span>)}</div></div>;
}

export function ConvertibleTResultView({ task }: { task: ConvertibleTask }) {
  const live = task.status === 'running' || task.status === 'queued';
  const data = task.result ?? task.preview;
  const [cursor, setCursor] = useState(1); const [playing, setPlaying] = useState(false); const [speed, setSpeed] = useState(5);
  const points = data?.equity ?? []; const count = live ? points.length : Math.min(cursor, points.length);
  const equity = points.slice(0, count); const current = equity.at(-1); const cutoff = current?.timestamp;
  const visible = (row: { timestamp: string; eventIndex?: number }) => {
    if (!cutoff) return false;
    if (row.eventIndex != null && current?.eventIndex != null) return row.eventIndex <= current.eventIndex;
    // Legacy traces without sequence metadata reveal timestamp groups only after
    // their final equity point, keeping equal-time future events private.
    return row.timestamp < cutoff || (row.timestamp === cutoff && points[count]?.timestamp !== cutoff);
  };
  const events = (data?.events ?? []).filter(visible); const event = events.at(-1);
  const trades = (data?.trades ?? []).filter(visible);
  const signals = (data?.signals ?? []).filter(visible);
  const recentEvents = events.slice(-120); const volumes = recentEvents.filter(event => finite(event.volume) && event.volume >= 0);
  const maxVolume = volumes.length ? Math.max(...volumes.map(event => event.volume!)) : 0;
  const currency = task.config.currency ?? (task.config.symbol === '113677' ? 'CNY' : '币种未提供');
  const rawUnit = task.config.quantityUnit; const quantityUnit = rawUnit === 'shares' ? '股' : rawUnit === 'bonds' ? '张' : rawUnit ?? (task.config.symbol === '113677' ? '张' : '单位未提供');
  const volumeUnit = rawUnit === 'shares' || rawUnit === '股' ? '股' : rawUnit === 'bonds' || rawUnit === '张' ? '张' : '来源原始单位';
  useEffect(() => {
    if (live || !playing || cursor >= points.length) return;
    const timer = setInterval(() => setCursor(value => Math.min(value + 1, points.length)), 1000 / speed);
    return () => clearInterval(timer);
  }, [live, playing, cursor, points.length, speed]);
  if (!data || !current) return null;
  const metrics = [['相对底仓持有的增量收益', current.incrementalTPnl], ['账户总收益', current.totalPnl], ['账户权益', current.equity], ['底仓持有基准权益', current.holdBaselineEquity]] as const;
  return <div className="cbt-results">
    <section className="jev-panel"><div className="jev-section-heading"><div><h2>{task.config.symbol} · {live ? '已处理事件' : '历史事件回放'}</h2><p>金额统一以 {currency} 计价；事件粒度 {task.config.eventGranularity}，模型间隔 {task.config.decisionIntervalSeconds} 秒。</p></div><span>{stamp(current.timestamp)}</span></div>
      {!task.result ? <p className="jev-partial-note">仅展示已处理的部分历史数据，不代表完整区间结果。</p> : null}
      <div className="jev-playback-controls"><button className="jev-primary" disabled={live || points.length < 2} onClick={() => { if (cursor >= points.length) { setCursor(1); setPlaying(true); } else setPlaying(value => !value); }}>{playing && cursor < points.length ? <Pause size={15} /> : <Play size={15} />}{live ? '跟随任务' : playing && cursor < points.length ? '暂停事件回放' : '播放事件回放'}</button><label className="jev-speed">回放速度<select aria-label="事件回放速度" disabled={live} value={speed} onChange={event => setSpeed(Number(event.target.value))}>{[1, 5, 20, 60].map(speed => <option key={speed} value={speed}>{speed} 事件/秒</option>)}</select></label><label className="jev-timeline"><span>已知事件 {count} / {points.length}</span><input type="range" aria-label="秒级研究回放进度" disabled={live} min="1" max={Math.max(points.length, 1)} value={count} onChange={event => { setCursor(Number(event.target.value)); setPlaying(false); }} /></label></div>
      <div className="cbt-result-metrics">{metrics.map(([label, value]) => <div key={label}><span>{label}（{currency}）</span><strong>{number(value)}</strong></div>)}</div><p className="cbt-metric-note">增量收益是相对底仓持有基准的权益差额，包括尚未平仓部分的影响，不等同于已实现利润。</p>
      <Lines rows={equity} keys={[{ key: 'equity', label: '策略权益', color: '#002FA7' }, { key: 'holdBaselineEquity', label: '底仓持有基准', color: '#9297A2' }]} label="截至当前事件的策略权益与底仓持有基准" />
      <div className="cbt-account-grid"><div><span>可用现金（{currency}）</span><strong>{number(current.cash)}</strong></div><div><span>库存（{quantityUnit}）</span><strong>{number(current.positionQuantity, 0)}</strong></div><div><span>当前价格（{currency}）</span><strong>{number(current.price, 4)}</strong></div>{current.realizedPnl != null ? <div><span>已实现盈亏（{currency}）</span><strong>{number(current.realizedPnl)}</strong></div> : null}{current.unrealizedPnl != null ? <div><span>持仓浮盈（{currency}）</span><strong>{number(current.unrealizedPnl)}</strong></div> : null}</div>
    </section>
    <section className="jev-panel"><div className="jev-section-heading"><div><h2>事件价格、成交量与参考因子</h2><p>仅显示当前游标前的已知记录；未返回的因子保持空缺。</p></div></div><Lines rows={recentEvents} trades={trades} keys={[{ key: 'price', label: '标的事件价格', color: '#002FA7' }]} label="当前已知真实事件价格" />{volumes.length ? <div className="jev-chart"><svg viewBox="0 0 750 115" role="img" aria-label="当前已知真实事件成交量"><text x="76" y="15">成交量（{volumeUnit}）</text><line x1="76" x2="726" y1="90" y2="90" className="jev-gridline" />{recentEvents.map((event, index) => { if (!finite(event.volume) || event.volume < 0) return null; const height = event.volume / Math.max(maxVolume, 1) * 60; return <g key={`${event.timestamp}-${index}`}><title>{stamp(event.timestamp)} 成交量 {number(event.volume, 4)}（{volumeUnit}）</title><rect x={76 + index / Math.max(recentEvents.length - 1, 1) * 650 - 2} y={90 - height} width="4" height={height} fill="#002FA7" opacity=".65" /></g>; })}</svg></div> : <p className="jev-empty-inline">成交量未返回。</p>}<div className="cbt-account-grid"><div><span>正股价格</span><strong>{number(event?.stockPrice, 4)}</strong></div><div><span>板块参考值</span><strong>{number(event?.sectorValue, 4)}</strong></div><div><span>大盘参考值</span><strong>{number(event?.marketValue, 4)}</strong></div><div><span>最优买价 / 卖价</span><strong>{number(finite(event?.bidPrice) ? event.bidPrice : null, 4)} / {number(finite(event?.askPrice) ? event.askPrice : null, 4)}</strong></div></div></section>
    <section className="jev-panel"><div className="jev-section-heading"><div><h2>库存与成交账本</h2><p>截至当前游标，共 {trades.length} 笔成交；金额单位 {currency}。</p></div></div>{trades.length ? <div className="jev-table-scroll" tabIndex={0} role="region" aria-label="做T成交账本"><table><thead><tr><th>成交时间</th><th>方向</th><th>数量（{quantityUnit}）</th><th>成交价</th><th>费用</th><th>现金余额</th><th>剩余库存</th><th>原因</th></tr></thead><tbody>{trades.map((trade, index) => <tr key={`${trade.timestamp}-${index}`}><td>{stamp(trade.timestamp)}</td><td>{trade.side}</td><td>{number(trade.quantity, 0)}</td><td>{number(trade.price, 4)}</td><td>{number(trade.fee)}</td><td>{number(trade.cashAfter)}</td><td>{number(trade.positionAfter, 0)}</td><td className="jev-reason">{trade.reason || '—'}</td></tr>)}</tbody></table></div> : <p className="jev-empty-inline">当前游标前尚无成交。</p>}</section>
    <section className="jev-panel"><div className="jev-section-heading"><h2>模型判断</h2><span>{signals.length} 次</span></div>{signals.length ? <div className="jev-table-scroll"><table><thead><tr><th>时点</th><th>动作</th><th>置信度</th><th>说明</th></tr></thead><tbody>{signals.map((signal, index) => <tr key={`${signal.timestamp}-${index}`}><td>{stamp(signal.timestamp)}</td><td>{signal.action}</td><td>{signal.confidence == null ? '—' : `${number(signal.confidence * 100, 1)}%`}</td><td className="jev-reason">{signal.reason || '—'}</td></tr>)}</tbody></table></div> : <p className="jev-empty-inline">当前游标前尚无模型判断。</p>}</section>
    {(data.dataQuality?.warnings.length || data.methodology?.length) ? <section className="jev-panel jev-methodology"><h2>数据与执行口径</h2>{data.dataQuality?.warnings.length ? <ul className="jev-warning-list">{data.dataQuality.warnings.map((warning, index) => <li key={index}>{explainResearch(warning)}</li>)}</ul> : null}{data.methodology?.length ? <ul>{data.methodology.map((line, index) => <li key={index}>{explainResearch(line)}</li>)}</ul> : null}</section> : null}
  </div>;
}
