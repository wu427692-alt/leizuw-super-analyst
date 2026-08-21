import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { ChevronDown, ChevronRight, Database, History, Landmark, RefreshCw, Search, TrendingUp } from 'lucide-react';
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import { investmentMonitorApi } from '../api/investmentMonitor';
import { AppPage, EmptyState } from '../components/common';
import { InvestmentMonitorNav } from '../components/investmentMonitor/InvestmentMonitorNav';
import type { DragonTigerDaily, DragonTigerHistory, DragonTigerRecord } from '../types/investmentMonitor';
import './DragonTigerPage.css';

const localDate = (daysAgo = 0) => {
  const value = new Date();
  value.setDate(value.getDate() - daysAgo);
  return value.toLocaleDateString('sv-SE');
};

const compactDate = (value: string) => value ? `${value.slice(0, 4)}-${value.slice(4, 6)}-${value.slice(6, 8)}` : '—';
const amount = (value?: number | null) => {
  const number = Number(value || 0);
  const absolute = Math.abs(number);
  if (absolute >= 1e8) return `${(number / 1e8).toFixed(2)}亿`;
  if (absolute >= 1e4) return `${(number / 1e4).toFixed(1)}万`;
  return number.toFixed(0);
};
const percent = (value?: number | null) => value == null ? '—' : `${value >= 0 ? '+' : ''}${Number(value).toFixed(2)}%`;

function SummaryCell({ label, value, note }: { label: string; value: string | number; note: string }) {
  return <div className="dragon-stat"><span>{label}</span><strong>{value}</strong><small>{note}</small></div>;
}

function SeatTable({ item }: { item: DragonTigerRecord }) {
  if (!item.seats?.length) return <div className="dragon-seat-empty">该交易日未返回对应营业部席位明细。</div>;
  return <div className="dragon-seat-panel">
    <div className="dragon-seat-head"><span>营业部 / 机构席位</span><span>方向</span><span>买入</span><span>卖出</span><span>净额</span></div>
    {item.seats.map((seat, index) => <div className="dragon-seat-row" key={`${seat.exalter}-${seat.side}-${index}`}>
      <span aria-label={seat.exalter}>{seat.exalter}</span><span>{seat.side === '0' ? '买入前五' : seat.side === '1' ? '卖出前五' : seat.side || '—'}</span>
      <span>{amount(seat.buy)}</span><span>{amount(seat.sell)}</span><span data-tone={(seat.netBuy ?? 0) >= 0 ? 'positive' : 'negative'}>{amount(seat.netBuy)}</span>
    </div>)}
  </div>;
}

function DailyRow({ item, open, onToggle }: { item: DragonTigerRecord; open: boolean; onToggle: () => void }) {
  return <div className="dragon-record">
    <button type="button" className="dragon-record-main" onClick={onToggle} aria-expanded={open}>
      <span className="dragon-expand">{open ? <ChevronDown /> : <ChevronRight />}</span>
      <span className="dragon-stock"><strong>{item.name}</strong><small>{item.tsCode}</small></span>
      <span><small>涨跌幅</small><strong>{percent(item.pctChange)}</strong></span>
      <span><small>成交额</small><strong>{amount(item.amount)}</strong></span>
      <span><small>榜单买入</small><strong>{amount(item.lBuy)}</strong></span>
      <span><small>榜单卖出</small><strong>{amount(item.lSell)}</strong></span>
      <span data-tone={(item.netAmount ?? 0) >= 0 ? 'positive' : 'negative'}><small>净额</small><strong>{amount(item.netAmount)}</strong></span>
      <span className="dragon-reason"><small>上榜原因</small><strong aria-label={item.reason}>{item.reason}</strong></span>
    </button>
    {open ? <SeatTable item={item} /> : null}
  </div>;
}

export default function DragonTigerPage() {
  const [mode, setMode] = useState<'daily' | 'history'>('daily');
  const [daily, setDaily] = useState<DragonTigerDaily | null>(null);
  const [history, setHistory] = useState<DragonTigerHistory | null>(null);
  const [tradeDate, setTradeDate] = useState('');
  const [startDate, setStartDate] = useState(localDate(30));
  const [endDate, setEndDate] = useState(localDate());
  const [query, setQuery] = useState('');
  const [symbol, setSymbol] = useState('');
  const [openKey, setOpenKey] = useState('');
  const [loading, setLoading] = useState(true);
  const [syncing, setSyncing] = useState(false);
  const [status, setStatus] = useState('正在读取 Tushare 龙虎榜缓存…');
  const didInitialLoad = useRef(false);

  const loadDaily = useCallback(async (date?: string, refresh = false) => {
    setLoading(true); setStatus(refresh ? '正在直接刷新 Tushare top_list / top_inst…' : '正在读取当日龙虎榜…');
    try {
      const value = await investmentMonitorApi.dragonTigerDaily(date, refresh);
      setDaily(value); setTradeDate(compactDate(value.tradeDate));
      setStatus(`${value.source.provider} · ${value.source.apis.join(' + ')} · ${value.source.updateNote}`);
    } catch (error) {
      setStatus(error instanceof Error ? error.message : '龙虎榜暂时不可用');
    } finally { setLoading(false); }
  }, []);

  const loadHistory = useCallback(async () => {
    setLoading(true); setStatus('正在检索本地龙虎榜历史库…');
    try {
      const value = await investmentMonitorApi.dragonTigerHistory({ startDate, endDate, symbol, query, pageSize: 100 });
      setHistory(value); setStatus(`本地 SQLite · 已缓存 ${value.cachedTradeDays} 个交易日 · 当前条件 ${value.total} 条`);
    } catch (error) {
      setStatus(error instanceof Error ? error.message : '历史龙虎榜暂时不可用');
    } finally { setLoading(false); }
  }, [endDate, query, startDate, symbol]);

  useEffect(() => {
    if (didInitialLoad.current) return;
    didInitialLoad.current = true;
    void Promise.all([loadDaily(), loadHistory()]);
  }, [loadDaily, loadHistory]);

  const syncHistory = async () => {
    setSyncing(true); setStatus('正在按交易日增量补齐，已入库日期不会重复新增…');
    try {
      const result = await investmentMonitorApi.syncDragonTiger(startDate, endDate);
      setStatus(`补齐完成：${result.tradeDays} 个交易日，接口返回 ${result.topListCount} 条，新增 ${result.created} 条`);
      await loadHistory();
    } catch (error) {
      setStatus(error instanceof Error ? error.message : '历史补齐失败');
    } finally { setSyncing(false); }
  };

  const filteredDaily = useMemo(() => {
    const term = query.trim().toLowerCase();
    if (!term) return daily?.items ?? [];
    return (daily?.items ?? []).filter(item => `${item.name} ${item.tsCode} ${item.reason}`.toLowerCase().includes(term));
  }, [daily?.items, query]);

  const summary = daily?.summary;
  return <AppPage className="dragon-page max-w-[1760px]">
    <div className="dragon-shell">
      <header className="dragon-header">
        <div><span>交易所公开信息 · 本地历史缓存</span><h1>龙虎榜与营业部席位</h1><p>每日明细、机构席位和历史净额分开读取；金额统一按 Tushare 原始“元”口径换算显示。</p></div>
        <div className="dragon-header-status"><i className={loading || syncing ? 'is-loading' : ''} /><span>{status}</span></div>
      </header>
      <InvestmentMonitorNav />

      <div className="dragon-toolbar">
        <div className="dragon-mode"><button className={mode === 'daily' ? 'is-active' : ''} onClick={() => setMode('daily')}><Landmark />每日榜单</button><button className={mode === 'history' ? 'is-active' : ''} onClick={() => setMode('history')}><History />历史库</button></div>
        <label className="dragon-search"><Search /><input aria-label="检索龙虎榜" value={query} onChange={event => setQuery(event.target.value)} placeholder="股票、代码、上榜原因或营业部" /></label>
      </div>

      {mode === 'daily' ? <>
        <section className="dragon-date-tape" aria-label="龙虎榜交易日">
          {(history?.trend ?? []).slice(-12).reverse().map(item => <button key={item.tradeDate} className={item.tradeDate === daily?.tradeDate ? 'is-active' : ''} onClick={() => void loadDaily(compactDate(item.tradeDate))}><span>{compactDate(item.tradeDate).slice(5)}</span><strong>{item.symbols}</strong><small>只上榜股票</small></button>)}
          {!history?.trend.length ? <span className="dragon-tape-empty">历史交易日将在首次补齐后显示</span> : null}
        </section>
        <section className="dragon-date-control"><label>交易日<input type="date" value={tradeDate} onChange={event => setTradeDate(event.target.value)} /></label><button onClick={() => void loadDaily(tradeDate)} disabled={loading}>读取该日</button><button onClick={() => void loadDaily(tradeDate || undefined, true)} disabled={loading}><RefreshCw className={loading ? 'animate-spin' : ''} />直接刷新接口</button></section>
        <section className="dragon-stats">
          <SummaryCell label="上榜股票" value={summary?.symbolCount ?? '—'} note={`${summary?.rowCount ?? 0} 条上榜原因`} />
          <SummaryCell label="营业部席位" value={summary?.seatCount ?? '—'} note="top_inst 原始席位行" />
          <SummaryCell label="榜单买入" value={amount(summary?.buyAmount)} note="买入前五合计" />
          <SummaryCell label="榜单卖出" value={amount(summary?.sellAmount)} note="卖出前五合计" />
          <SummaryCell label="榜单净额" value={amount(summary?.netAmount)} note={`${summary?.positiveCount ?? 0} 正 / ${summary?.negativeCount ?? 0} 负`} />
        </section>
        <section className="dragon-list">
          <div className="dragon-list-head"><div><Database /><span>{compactDate(daily?.tradeDate ?? '')} 全市场榜单</span></div><span>点击股票展开营业部席位</span></div>
          {filteredDaily.map(item => <DailyRow key={`${item.tsCode}-${item.reason}`} item={item} open={openKey === `${item.tsCode}-${item.reason}`} onToggle={() => setOpenKey(value => value === `${item.tsCode}-${item.reason}` ? '' : `${item.tsCode}-${item.reason}`)} />)}
          {!loading && !filteredDaily.length ? <EmptyState title="该交易日没有龙虎榜记录" description="非交易日或晚间披露前可能暂时为空，可切换上一交易日。" /> : null}
        </section>
      </> : <>
        <section className="dragon-history-controls">
          <label>开始日期<input type="date" value={startDate} onChange={event => setStartDate(event.target.value)} /></label><label>结束日期<input type="date" value={endDate} onChange={event => setEndDate(event.target.value)} /></label><label>股票代码<input value={symbol} onChange={event => setSymbol(event.target.value)} placeholder="如 603306.SH" /></label>
          <button onClick={() => void loadHistory()} disabled={loading}><Search />查询本地库</button><button onClick={() => void syncHistory()} disabled={syncing}><RefreshCw className={syncing ? 'animate-spin' : ''} />增量补齐区间</button>
        </section>
        <section className="dragon-history-grid">
          <div className="dragon-history-chart"><div className="dragon-section-title"><TrendingUp /><span>每日净额与上榜家数</span></div><ResponsiveContainer width="100%" height={270}><BarChart data={history?.trend ?? []}><CartesianGrid stroke="#242a31" vertical={false}/><XAxis dataKey="tradeDate" tickFormatter={value => String(value).slice(4)} tick={{ fill: '#87909c', fontSize: 10 }} axisLine={false}/><YAxis yAxisId="left" tickFormatter={value => amount(value)} tick={{ fill: '#87909c', fontSize: 10 }} axisLine={false}/><YAxis yAxisId="right" orientation="right" tick={{ fill: '#87909c', fontSize: 10 }} axisLine={false}/><Tooltip formatter={(value, name) => name === '净额' ? amount(Number(value)) : value}/><Bar yAxisId="left" dataKey="netAmount" fill="#00E676" name="净额"/><Bar yAxisId="right" dataKey="symbols" fill="#56606b" name="上榜家数"/></BarChart></ResponsiveContainer></div>
          <div className="dragon-history-summary"><span>缓存交易日</span><strong>{history?.cachedTradeDays ?? 0}</strong><small>查询范围 {startDate} 至 {endDate}</small><span>命中明细</span><strong>{history?.total ?? 0}</strong><small>按股票与上榜原因逐条保留</small></div>
        </section>
        <section className="dragon-history-table"><div className="dragon-history-head"><span>日期 / 股票</span><span>涨跌幅</span><span>换手率</span><span>榜单买入</span><span>榜单卖出</span><span>净额</span><span>上榜原因</span></div>{history?.items.map(item => <div className="dragon-history-row" key={`${item.eventId}`}><span><strong>{item.name}</strong><small>{compactDate(item.tradeDate)} · {item.tsCode}</small></span><span>{percent(item.pctChange)}</span><span>{percent(item.turnoverRate)}</span><span>{amount(item.lBuy)}</span><span>{amount(item.lSell)}</span><span data-tone={(item.netAmount ?? 0) >= 0 ? 'positive' : 'negative'}>{amount(item.netAmount)}</span><span aria-label={item.reason}>{item.reason}</span></div>)}</section>
      </>}
    </div>
  </AppPage>;
}
