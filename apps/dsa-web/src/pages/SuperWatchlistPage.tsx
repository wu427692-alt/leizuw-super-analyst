import { useCallback, useEffect, useMemo, useState } from 'react';
import type { FormEvent } from 'react';
import { ExternalLink, Plus, RefreshCw, Sparkles } from 'lucide-react';
import { Area, Bar, CartesianGrid, ComposedChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import { Link, useNavigate, useSearchParams } from 'react-router-dom';
import { investmentMonitorApi } from '../api/investmentMonitor';
import { systemConfigApi } from '../api/systemConfig';
import { AppPage, EmptyState } from '../components/common';
import { eventTime } from '../components/investmentMonitor/investmentMonitorMeta';
import type { MonitorEvent, SuperWatchlistDashboard, SuperWatchlistStock, WatchlistBackfillJob } from '../types/investmentMonitor';

type Section = 'overview' | 'fundamental' | 'capital' | 'institution' | 'alternative' | 'evidence';
const SECTIONS: Array<{ key: Section; label: string }> = [
  { key: 'overview', label: '全景' }, { key: 'fundamental', label: '财务估值' },
  { key: 'capital', label: '资金筹码' }, { key: 'institution', label: '公告研报' },
  { key: 'alternative', label: '另类情报' }, { key: 'evidence', label: '全部证据' },
];
const CHANNEL_LABELS: Record<string, string> = {
  tushare_market: 'Tushare 行情', tushare_fundamental: 'Tushare 财务',
  tushare_capital: 'Tushare 资金', tushare_research: '券商研报',
  tushare_news: '财经新闻', cninfo: '巨潮公告', zsxq: '知识星球',
  tianyancha: '天眼查', external_feeds: '外部消息源',
  tushareMarket: 'Tushare 行情', tushareFundamental: 'Tushare 财务',
  tushareCapital: 'Tushare 资金', tushareResearch: '券商研报',
  tushareNews: '财经新闻', externalFeeds: '外部消息源',
};
const STATUS_LABELS: Record<string, string> = {
  pending: '等待中', running: '进行中', completed: '已完成', partial: '部分完成',
  failed: '失败', not_supported: '无历史接口', empty: '无数据',
};
const DEFAULT_CHANNEL_KEYS = ['tushareMarket', 'tushareFundamental', 'tushareCapital', 'tushareResearch', 'tushareNews', 'cninfo', 'zsxq', 'tianyancha', 'externalFeeds'];

function number(value: unknown, digits = 2) {
  return typeof value === 'number' && Number.isFinite(value) ? value.toLocaleString('zh-CN', { maximumFractionDigits: digits }) : '—';
}
function percent(value: unknown) {
  return typeof value === 'number' && Number.isFinite(value) ? `${value > 0 ? '+' : ''}${value.toFixed(2)}%` : '—';
}
function money(value: unknown, tushareWan = false) {
  if (typeof value !== 'number' || !Number.isFinite(value)) return '—';
  const yuan = tushareWan ? value * 10_000 : value;
  return Math.abs(yuan) >= 100_000_000 ? `${(yuan / 100_000_000).toFixed(2)} 亿` : Math.abs(yuan) >= 10_000 ? `${(yuan / 10_000).toFixed(2)} 万` : number(yuan, 0);
}

function EvidenceRow({ event }: { event: MonitorEvent }) {
  const evidence = (event.metrics._evidence ?? {}) as { evidenceLevel?: string };
  const body = <>
    <span className="font-mono text-[10px] text-[#6B7078]">{eventTime(event.eventAt)}</span>
    <span className="truncate text-[11px] text-[#4B5058]">{event.sourceName}</span>
    <span className="min-w-0 truncate text-[12px] font-medium text-[#17181A]">{event.title}</span>
    <span className={`text-[10px] ${evidence.evidenceLevel === 'unverified' ? 'text-[#B54708]' : 'text-[#027A48]'}`}>{evidence.evidenceLevel === 'unverified' ? '待核验' : '事实'}</span>
    <span className="inline-flex items-center justify-end gap-1 text-[10px] font-semibold text-[#155EEF]">查看原文<ExternalLink className="h-3 w-3" /></span>
  </>;
  const cls = 'grid grid-cols-[92px_92px_minmax(220px,1fr)_52px_72px] items-center gap-3 border-t border-[#E1E3E7] px-3 py-2 text-left hover:bg-[#F7F8FA]';
  return event.url ? <a className={cls} href={event.url} target="_blank" rel="noreferrer">{body}</a> : <Link className={cls} to={`/investment-monitor/feed?event=${event.id}`}>{body}</Link>;
}

function BackfillRail({ job, onRetry, busy }: { job?: WatchlistBackfillJob; onRetry: () => void; busy: boolean }) {
  const rows: Array<[string, { status: string; created?: number; received?: number }]> = Object.entries(job?.channels ?? {});
  return <aside className="border-l border-[#D8DADF] bg-[#F8F9FB] p-4">
    <div className="flex items-center justify-between"><h2 className="text-[13px] font-bold">渠道覆盖</h2><span className="font-mono text-[10px] text-[#62666D]">{job?.progress ?? 0}%</span></div>
    <div className="mt-2 h-1.5 bg-[#D8DADF]"><div className="h-full bg-[#155EEF] transition-all" style={{ width: `${job?.progress ?? 0}%` }} /></div>
    <p className="mt-2 text-[10px] leading-4 text-[#6B7078]">近半年回填：{STATUS_LABELS[job?.status ?? 'pending'] ?? job?.status ?? '未开始'}</p>
    <div className="mt-4 border border-[#D8DADF] bg-white">
      {(rows.length ? rows : DEFAULT_CHANNEL_KEYS.map(key => [key, { status: 'pending' }] as [string, { status: string; created?: number; received?: number }])).map(([key, value]) => {
        const tone = value.status === 'completed' ? 'text-[#027A48]' : value.status === 'failed' ? 'text-[#B42318]' : 'text-[#B54708]';
        return <div key={key} className="grid grid-cols-[1fr_64px_44px] gap-2 border-t border-[#E5E7EB] px-3 py-2 first:border-t-0">
          <span className="truncate text-[10px] text-[#344054]">{CHANNEL_LABELS[key] ?? key}</span>
          <span className={`text-[10px] ${tone}`}>{STATUS_LABELS[value.status] ?? value.status}</span>
          <span className="text-right font-mono text-[10px] text-[#62666D]">{value.received ?? value.created ?? 0}</span>
        </div>;
      })}
    </div>
    {job?.error ? <p className="mt-3 line-clamp-3 text-[10px] leading-4 text-[#B42318]">{job.error}</p> : null}
    <button disabled={busy || job?.status === 'running'} onClick={onRetry} className="mt-4 h-8 w-full border border-[#155EEF] bg-white text-[11px] font-semibold text-[#155EEF] disabled:opacity-40">补齐最近半年</button>
  </aside>;
}

function Metric({ label, value, tone = '' }: { label: string; value: string; tone?: string }) {
  return <div className="border-r border-[#E1E3E7] px-3 last:border-r-0"><p className="text-[9px] text-[#7B7F87]">{label}</p><p className={`mt-1 font-mono text-[14px] font-bold ${tone}`}>{value}</p></div>;
}

function Overview({ stock }: { stock: SuperWatchlistStock }) {
  return <div className="grid min-h-[220px] grid-cols-2">
    <section className="border-r border-[#D8DADF] p-4"><h3 className="text-[12px] font-bold">事实驱动观察</h3><div className="mt-3 grid grid-cols-2 gap-x-5 gap-y-2">{stock.signals.slice(0, 6).map((item, i) => <div key={`${item.title}-${i}`} className={`border-l-2 py-1 pl-2 ${item.kind === 'risk' ? 'border-[#B42318]' : item.kind === 'catalyst' ? 'border-[#027A48]' : 'border-[#155EEF]'}`}><p className="truncate text-[11px] font-semibold">{item.title}</p><p className="mt-0.5 line-clamp-2 text-[10px] leading-4 text-[#62666D]">{item.detail}</p></div>)}</div></section>
    <section className="p-4"><h3 className="text-[12px] font-bold">基本面与估值快照</h3><div className="mt-3 grid grid-cols-4 gap-y-4"><Metric label="PE(TTM)" value={number(stock.valuation.peTtm)} /><Metric label="PB" value={number(stock.valuation.pb)} /><Metric label="ROE" value={percent(stock.fundamentals.roe)} /><Metric label="毛利率" value={percent(stock.fundamentals.grossMargin)} /><Metric label="收入同比" value={percent(stock.fundamentals.revenueYoy)} /><Metric label="利润同比" value={percent(stock.fundamentals.netProfitYoy)} /><Metric label="筹码获利" value={percent(stock.capital.winnerRate)} /><Metric label="事实证据" value={String(stock.evidence.factualCount)} /></div></section>
  </div>;
}

function DetailSection({ section, stock }: { section: Section; stock: SuperWatchlistStock }) {
  if (section === 'overview') return <Overview stock={stock} />;
  if (section === 'fundamental') return <div className="grid grid-cols-4 gap-y-5 p-5"><Metric label="营业收入" value={money(stock.fundamentals.revenue)} /><Metric label="归母净利润" value={money(stock.fundamentals.netProfit)} /><Metric label="经营现金流" value={money(stock.fundamentals.operatingCashflow)} /><Metric label="总市值" value={money(stock.valuation.totalMv, true)} /><Metric label="收入同比" value={percent(stock.fundamentals.revenueYoy)} /><Metric label="利润同比" value={percent(stock.fundamentals.netProfitYoy)} /><Metric label="净利率" value={percent(stock.fundamentals.netMargin)} /><Metric label="资产负债率" value={percent(stock.fundamentals.debtRatio)} /></div>;
  if (section === 'capital') return <div className="grid grid-cols-4 gap-y-5 p-5"><Metric label="获利比例" value={percent(stock.capital.winnerRate)} /><Metric label="加权成本" value={number(stock.capital.weightedCost)} /><Metric label="RSI(6)" value={number(stock.technical.rsi6)} /><Metric label="MACD" value={number(stock.technical.macd, 3)} /><Metric label="50%成本" value={number(stock.capital.cost50pct)} /><Metric label="85%成本" value={number(stock.capital.cost85pct)} /><Metric label="布林中轨" value={number(stock.technical.bollMid)} /><Metric label="CCI" value={number(stock.technical.cci)} /></div>;
  const events = section === 'institution' ? [...stock.company.announcements, ...stock.institution.latest] : section === 'alternative' ? stock.alternative.essays : stock.timeline;
  return <div className="max-h-[300px] overflow-auto">{events.slice(0, section === 'evidence' ? 30 : 10).map(event => <EvidenceRow key={event.id} event={event} />)}{!events.length ? <EmptyState title="暂无数据" description="等待对应渠道完成回填。" /> : null}</div>;
}

export default function SuperWatchlistPage() {
  const [data, setData] = useState<SuperWatchlistDashboard | null>(null);
  const [loading, setLoading] = useState(true); const [busy, setBusy] = useState(false); const [error, setError] = useState('');
  const [section, setSection] = useState<Section>('overview'); const [newSymbol, setNewSymbol] = useState('');
  const [params, setParams] = useSearchParams(); const navigate = useNavigate();
  const load = useCallback(async () => { setLoading(true); setError(''); try { setData(await investmentMonitorApi.superWatchlist(183)); } catch (err) { setError(err instanceof Error ? err.message : '加载失败'); } finally { setLoading(false); } }, []);
  useEffect(() => { void load(); const timer = window.setInterval(() => void load(), 15000); return () => window.clearInterval(timer); }, [load]);
  const active = useMemo(() => data?.stocks.find(row => row.symbol === params.get('symbol')) ?? data?.stocks[0] ?? null, [data, params]);
  const job = data?.backfillJobs.find(row => row.symbol === active?.symbol);
  const addStock = async (event: FormEvent) => { event.preventDefault(); if (!newSymbol.trim()) return; setBusy(true); try { await systemConfigApi.addToWatchlist(newSymbol.trim()); setNewSymbol(''); await load(); } catch (err) { setError(err instanceof Error ? err.message : '添加失败'); } finally { setBusy(false); } };
  const retry = async () => { if (!active) return; setBusy(true); try { await investmentMonitorApi.backfillWatchlist(active.symbol); await load(); } finally { setBusy(false); } };
  const openModel = () => { if (!active) return; const evidence = active.timeline.slice(0, 12).map(row => `[${row.id}] ${row.sourceName}｜${row.title}`).join('\n'); navigate(`/chat?prompt=${encodeURIComponent(`请对${active.name}（${active.symbol}）基于以下事实证据做多空、催化、风险和证伪条件分析，不得补造数据。\n${evidence}`)}`); };
  const chart = active?.history.map(row => ({ ...row, label: row.date.slice(5), volumeWan: (row.volume ?? 0) / 10000 })) ?? [];

  return <AppPage className="max-w-[1760px] !p-0"><div className="min-h-[calc(100vh-40px)] border border-[#C9CCD2] bg-white text-[#17181A]">
    <header className="flex h-14 items-center justify-between border-b border-[#D8DADF] px-4"><h1 className="text-[18px] font-bold tracking-[-0.03em]">自选股超级看板</h1><div className="flex items-center gap-3 text-[10px] text-[#62666D]"><span>近半年数据回填：{STATUS_LABELS[job?.status ?? 'pending'] ?? '未开始'} {job ? `(${job.progress}%)` : ''}</span><button disabled={!active || busy} onClick={() => void retry()} className="h-8 border border-[#155EEF] px-3 font-semibold text-[#155EEF] disabled:opacity-40">补齐最近半年</button><button onClick={() => void load()} aria-label="刷新" className="p-2"><RefreshCw className={`h-4 w-4 ${loading ? 'animate-spin' : ''}`} /></button></div></header>
    {error ? <div className="border-b border-[#FDA29B] bg-[#FEF3F2] px-4 py-2 text-[11px] text-[#B42318]">{error}</div> : null}
    <div className="grid min-h-[760px] grid-cols-1 xl:grid-cols-[220px_minmax(620px,1fr)_300px]">
      <aside className="border-b border-r border-[#D8DADF] bg-[#FAFBFC] xl:border-b-0">
        <form onSubmit={addStock} className="border-b border-[#D8DADF] p-3"><div className="flex"><input value={newSymbol} onChange={e => setNewSymbol(e.target.value)} placeholder="股票代码，如 603306" className="h-8 min-w-0 flex-1 border border-[#C9CCD2] px-2 text-[11px] outline-none focus:border-[#155EEF]" /><button disabled={busy} className="flex h-8 w-8 items-center justify-center bg-[#155EEF] text-white"><Plus className="h-4 w-4" /></button></div><p className="mt-1.5 text-[9px] text-[#7B7F87]">加入后自动全渠道补齐最近半年</p></form>
        {(data?.stocks ?? []).map(stock => { const selected = stock.symbol === active?.symbol; const stockJob = data?.backfillJobs.find(row => row.symbol === stock.symbol); return <button key={stock.symbol} onClick={() => setParams({ symbol: stock.symbol }, { replace: true })} className={`w-full border-b border-[#D8DADF] p-3 text-left ${selected ? 'bg-[#EEF4FF]' : 'hover:bg-white'}`}><div className="flex items-center justify-between"><span className="text-[13px] font-bold">{stock.name}</span><span className={`font-mono text-[12px] font-bold ${(stock.market.changePct ?? 0) >= 0 ? 'text-[#B42318]' : 'text-[#027A48]'}`}>{number(stock.market.price)}</span></div><div className="mt-1 flex justify-between font-mono text-[9px] text-[#62666D]"><span>{stock.symbol}</span><span>{percent(stock.market.changePct)}</span></div><div className="mt-3 flex items-center gap-2"><div className="h-1 flex-1 bg-[#D8DADF]"><div className="h-full bg-[#155EEF]" style={{ width: `${stockJob?.progress ?? 0}%` }} /></div><span className="font-mono text-[9px] text-[#62666D]">{stockJob?.progress ?? 0}%</span></div></button>; })}
      </aside>
      <main className="min-w-0">{active ? <>
        <section className="flex h-20 items-center justify-between border-b border-[#D8DADF] px-4"><div><div className="flex items-center gap-3"><h2 className="text-[20px] font-bold">{active.name}</h2><span className="font-mono text-[11px] text-[#62666D]">{active.symbol}</span><span className={`font-mono text-[20px] font-bold ${(active.market.changePct ?? 0) >= 0 ? 'text-[#B42318]' : 'text-[#027A48]'}`}>{number(active.market.price)} <small className="text-[12px]">{percent(active.market.changePct)}</small></span></div><p className="mt-1 text-[10px] text-[#7B7F87]">最后更新 {eventTime(active.market.updatedAt)} · 半年日线 {active.history.length} 条 · 原文覆盖 {active.evidence.originalLinkCoverage}%</p></div><button onClick={openModel} className="inline-flex h-8 items-center gap-1.5 bg-[#155EEF] px-3 text-[11px] font-semibold text-white"><Sparkles className="h-3.5 w-3.5" />深度研判</button></section>
        <section className="border-b border-[#D8DADF] p-3"><div className="grid grid-cols-3 border border-[#E1E3E7] py-2 md:grid-cols-6"><Metric label="开盘" value={number(active.market.open)} /><Metric label="最高" value={number(active.market.high)} /><Metric label="最低" value={number(active.market.low)} /><Metric label="成交额" value={money(active.market.amount)} /><Metric label="PE(TTM)" value={number(active.valuation.peTtm)} /><Metric label="PB" value={number(active.valuation.pb)} /></div><div className="mt-2 h-[300px] min-w-0"><ResponsiveContainer width="100%" height="100%" minWidth={0} minHeight={300} initialDimension={{ width: 700, height: 300 }}><ComposedChart data={chart}><CartesianGrid stroke="#E8EAED" vertical={false} /><XAxis dataKey="label" tick={{ fontSize: 9, fill: '#6B7078' }} minTickGap={32} /><YAxis yAxisId="price" tick={{ fontSize: 9, fill: '#6B7078' }} domain={['auto', 'auto']} /><YAxis yAxisId="volume" hide orientation="right" /><Tooltip contentStyle={{ fontSize: 10 }} /><Bar yAxisId="volume" dataKey="volumeWan" fill="#D9E5FF" name="成交量(万)" /><Area yAxisId="price" type="monotone" dataKey="close" stroke="#155EEF" fill="#EEF4FF" strokeWidth={2} name="收盘" /></ComposedChart></ResponsiveContainer></div></section>
        <nav className="flex h-10 border-b border-[#D8DADF] px-2">{SECTIONS.map(item => <button key={item.key} onClick={() => setSection(item.key)} className={`border-b-2 px-4 text-[11px] font-semibold ${section === item.key ? 'border-[#155EEF] text-[#155EEF]' : 'border-transparent text-[#62666D]'}`}>{item.label}</button>)}</nav>
        <DetailSection section={section} stock={active} />
        <section className="overflow-x-auto border-t border-[#D8DADF]"><div className="flex items-center justify-between px-3 py-2"><h3 className="text-[12px] font-bold">事实时间线</h3><Link to={`/investment-monitor/feed?symbol=${encodeURIComponent(active.symbol)}`} className="text-[10px] font-semibold text-[#155EEF]">查看全部证据</Link></div>{active.timeline.slice(0, 6).map(event => <EvidenceRow key={event.id} event={event} />)}</section>
      </> : !loading ? <EmptyState title="暂无自选股" description="在左侧输入股票代码加入，自选后会自动触发半年回填。" /> : null}</main>
      <BackfillRail job={job} onRetry={() => void retry()} busy={busy} />
    </div>
  </div></AppPage>;
}
