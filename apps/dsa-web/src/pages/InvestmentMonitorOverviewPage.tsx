import { useCallback, useEffect, useMemo, useState } from 'react';
import { AlertTriangle, ArrowDownRight, ArrowUpRight, ExternalLink, RefreshCw, ShieldCheck } from 'lucide-react';
import { Area, AreaChart, Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import { investmentMonitorApi } from '../api/investmentMonitor';
import { AppPage, EmptyState } from '../components/common';
import { InvestmentMonitorNav } from '../components/investmentMonitor/InvestmentMonitorNav';
import { CHANNEL_LABELS, eventTime } from '../components/investmentMonitor/investmentMonitorMeta';
import type { IntelligenceDashboard, MonitorEvent } from '../types/investmentMonitor';

function evidence(event: MonitorEvent) {
  const row = (event.metrics._evidence ?? {}) as { evidenceLevel?: string; channel?: string };
  return { level: row.evidenceLevel ?? (event.sourceKey === 'zsxq.essays' ? 'unverified' : 'licensed'), channel: row.channel ?? 'other' };
}

const EventRow = ({ event, rank }: { event: MonitorEvent; rank?: number }) => {
  const meta = evidence(event);
  return <div className="grid grid-cols-[32px_minmax(0,1fr)_76px] gap-3 border-t border-[#D8DADF] py-3 first:border-t-0">
    <span className="font-mono text-xs text-[#7B7F87]">{rank ? String(rank).padStart(2, '0') : eventTime(event.eventAt).slice(-5)}</span>
    <div className="min-w-0"><p className="line-clamp-1 text-sm font-semibold text-[#17181A]">{event.title}</p><p className="mt-1 line-clamp-1 text-xs text-[#6B7078]">{event.sourceName} · {CHANNEL_LABELS[meta.channel] ?? meta.channel} · {event.symbols.join(' / ') || '全市场'}</p></div>
    <div className="text-right"><p className="font-mono text-base font-semibold text-[#17181A]">{event.importanceScore}</p><p className={`text-[10px] ${meta.level === 'unverified' ? 'text-[#B54708]' : 'text-[#027A48]'}`}>{meta.level === 'unverified' ? '待核验' : '事实'}</p></div>
  </div>;
};

export default function InvestmentMonitorOverviewPage() {
  const [data, setData] = useState<IntelligenceDashboard | null>(null);
  const [days, setDays] = useState(14);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const load = useCallback(async () => {
    setLoading(true); setError('');
    try { setData(await investmentMonitorApi.intelligenceDashboard(days)); }
    catch (err) { setError(err instanceof Error ? err.message : '看板加载失败'); }
    finally { setLoading(false); }
  }, [days]);
  useEffect(() => { void load(); }, [load]);

  const chart = useMemo(() => (data?.dailyTrend ?? []).map(item => ({ ...item, label: item.date.slice(5) })), [data]);
  const freshSources = data?.sources.fresh ?? data?.sources.healthy ?? 0;
  const sourceHealth = data?.sources.enabled ? Math.round(freshSources * 100 / data.sources.enabled) : 0;
  const stats = [
    ['事实情报', data?.summary.factualCount ?? '—', `${days} 天原始证据`],
    ['决策信号', data?.summary.highPriorityCount ?? '—', '快照折叠后 · 重要度 ≥ 75'],
    ['自选股命中', data?.summary.watchlistHits ?? '—', '快照折叠后'],
    ['活跃来源', data?.summary.sourceCount ?? '—', data ? `${freshSources}/${data.sources.enabled} 数据新鲜` : '正在读取链路状态'],
  ];

  return <AppPage className="max-w-[1680px]">
    <div className="overflow-hidden border border-[#C9CCD2] bg-[#F7F7F8] font-sans text-[#17181A] shadow-[0_18px_50px_rgba(17,24,39,0.08)]">
      <div className="flex flex-col justify-between gap-4 border-b border-[#17181A] bg-white px-5 py-5 lg:flex-row lg:items-end">
        <div><p className="font-mono text-[9px] uppercase tracking-[0.2em] text-[#155EEF]">Intelligence desk / live evidence</p><h1 className="mt-1 text-2xl font-bold tracking-[-0.04em]">投资情报总览</h1><p className="mt-1 text-xs text-[#62666D]">先看异动，再看标的，最后回到证据。所有数字均来自本地统一事件库。</p></div>
        <div className="flex items-center gap-2"><select aria-label="统计周期" className="h-9 border border-[#C9CCD2] bg-white px-3 text-xs" value={days} onChange={e => setDays(Number(e.target.value))}><option value={7}>近 7 天</option><option value={14}>近 14 天</option><option value={30}>近 30 天</option></select><button onClick={() => void load()} className="inline-flex h-9 items-center gap-2 bg-[#155EEF] px-4 text-xs font-semibold text-white"><RefreshCw className={`h-3.5 w-3.5 ${loading ? 'animate-spin' : ''}`} />刷新</button></div>
      </div>
      <InvestmentMonitorNav />
      {error ? <div className="border-b border-[#FDA29B] bg-[#FEF3F2] px-5 py-3 text-sm text-[#B42318]">{error}</div> : null}

      <section className="grid border-b border-[#C9CCD2] sm:grid-cols-2 xl:grid-cols-4">
        {stats.map(([label, value, note], index) => <div key={String(label)} className={`bg-white px-4 py-3 ${index ? 'border-l border-[#D8DADF]' : ''}`}><p className="text-[10px] font-semibold text-[#62666D]">{label}</p><p className="mt-1 font-mono text-2xl font-bold tracking-[-0.04em]">{value}</p><p className="mt-1 text-[9px] text-[#8A8E95]">{note}</p></div>)}
      </section>

      <section className="grid border-b border-[#C9CCD2] xl:grid-cols-[1.55fr_1fr]">
        <div className="min-w-0 bg-white p-5 xl:border-r xl:border-[#C9CCD2]"><div className="mb-4 flex items-end justify-between"><div><p className="text-xs font-semibold uppercase tracking-[0.12em] text-[#155EEF]">情报密度</p><h2 className="mt-1 text-lg font-bold">每日事实与待核验信息</h2></div><div className="flex gap-3 text-[10px] text-[#62666D]"><span>■ 事实</span><span className="text-[#B54708]">■ 待核验</span></div></div><div className="h-64 min-w-0"><ResponsiveContainer width="100%" height="100%" minWidth={0} minHeight={256} initialDimension={{ width: 500, height: 256 }}><AreaChart data={chart}><CartesianGrid stroke="#E5E7EB" vertical={false}/><XAxis dataKey="label" tick={{ fontSize: 10, fill: '#6B7078' }} axisLine={false} tickLine={false}/><YAxis tick={{ fontSize: 10, fill: '#6B7078' }} axisLine={false} tickLine={false}/><Tooltip/><Area type="monotone" dataKey="factual" stackId="1" stroke="#155EEF" fill="#D9E5FF" name="事实"/><Area type="monotone" dataKey="unverified" stackId="1" stroke="#B54708" fill="#FDEAD7" name="待核验"/></AreaChart></ResponsiveContainer></div></div>
        <div className="bg-[#EEF2F7] p-5"><div className="flex items-center justify-between"><div><p className="text-xs font-semibold uppercase tracking-[0.12em] text-[#155EEF]">来源可靠性</p><h2 className="mt-1 text-lg font-bold">数据链路状态</h2></div><ShieldCheck className="h-6 w-6 text-[#155EEF]" /></div><p className="mt-8 font-mono text-6xl font-bold tracking-[-0.07em]">{data ? sourceHealth : '—'}{data ? <span className="text-2xl">%</span> : null}</p><p className="mt-2 text-sm text-[#62666D]">{data ? `${data.sources.healthy} 个来源同步成功；${freshSources} 个事实新鲜，${data.sources.stale ?? 0} 个陈旧，${data.sources.empty ?? 0} 个空源。` : '正在检查全部数据链路…'}</p><div className="mt-5 h-2 bg-[#D8DADF]"><div className="h-full bg-[#155EEF]" style={{ width: `${sourceHealth}%` }} /></div><p className="mt-8 text-xs leading-5 text-[#62666D]">口径：同步成功只表示接口无报错；百分比按最新事实时间计算。官方披露、授权接口与媒体记录计入事实，知识星球观点仍单独标为待核验。</p></div>
      </section>

      <section className="grid border-b border-[#C9CCD2] xl:grid-cols-[1fr_1.15fr]">
        <div className="min-w-0 bg-white p-5 xl:border-r xl:border-[#C9CCD2]"><p className="text-xs font-semibold uppercase tracking-[0.12em] text-[#155EEF]">渠道温度</p><h2 className="mt-1 text-lg font-bold">本周期信息增量与变化</h2><div className="mt-4 h-64 min-w-0"><ResponsiveContainer width="100%" height="100%" minWidth={0} minHeight={256} initialDimension={{ width: 500, height: 256 }}><BarChart data={(data?.channels ?? []).slice(0, 8)} layout="vertical"><CartesianGrid stroke="#E5E7EB" horizontal={false}/><XAxis type="number" hide/><YAxis type="category" dataKey="name" width={74} tickFormatter={v => CHANNEL_LABELS[v] ?? v} tick={{ fontSize: 10, fill: '#4B5058' }} axisLine={false} tickLine={false}/><Tooltip labelFormatter={v => CHANNEL_LABELS[String(v)] ?? v}/><Bar dataKey="count" fill="#155EEF" name="本期数量" radius={0}/></BarChart></ResponsiveContainer></div></div>
        <div className="bg-white p-5"><div className="flex items-end justify-between"><div><p className="text-xs font-semibold uppercase tracking-[0.12em] text-[#155EEF]">高价值信号</p><h2 className="mt-1 text-lg font-bold">按重要度排序的事实证据</h2></div><span className="text-[10px] text-[#7B7F87]">不是模型猜测</span></div><div className="mt-3">{(data?.signalEvents ?? []).slice(0, 5).map((event, index) => <EventRow key={event.id} event={event} rank={index + 1} />)}{!loading && !data?.signalEvents.length ? <EmptyState title="暂无高价值事实" description="同步数据源后将在这里按重要度排列。" /> : null}</div></div>
      </section>

      <section className="grid xl:grid-cols-[1.3fr_1fr]">
        <div className="bg-white p-5 xl:border-r xl:border-[#C9CCD2]"><p className="text-xs font-semibold uppercase tracking-[0.12em] text-[#155EEF]">超级关注股</p><h2 className="mt-1 text-lg font-bold">机会、风险与信息覆盖</h2><div className="mt-4 grid gap-3 sm:grid-cols-2">{(data?.watchlist ?? []).map(card => <a href={`/investment-monitor/watchlist?symbol=${encodeURIComponent(card.symbol)}`} key={card.symbol} className="border border-[#C9CCD2] p-4 transition-colors hover:border-[#155EEF]"><div className="flex items-start justify-between"><div><p className="text-lg font-bold">{card.name}</p><p className="font-mono text-xs text-[#6B7078]">{card.symbol}</p></div><span className="bg-[#EEF4FF] px-2 py-1 font-mono text-xs text-[#155EEF]">{card.eventCount} 条</span></div><div className="mt-6 grid grid-cols-3 gap-2"><div><p className="text-[10px] text-[#7B7F87]">机会</p><p className="font-mono text-xl font-bold text-[#155EEF]">{card.opportunityScore}</p></div><div><p className="text-[10px] text-[#7B7F87]">风险</p><p className="font-mono text-xl font-bold">{card.riskScore}</p></div><div><p className="text-[10px] text-[#7B7F87]">高优先</p><p className="font-mono text-xl font-bold">{card.highPriorityCount}</p></div></div></a>)}</div></div>
        <div className="bg-[#F7F7F8] p-5"><div className="flex items-center gap-2"><AlertTriangle className="h-4 w-4 text-[#B54708]"/><p className="text-xs font-semibold uppercase tracking-[0.12em] text-[#B54708]">证据冲突</p></div><h2 className="mt-1 text-lg font-bold">同一标的多空事实并存</h2><div className="mt-4 space-y-3">{(data?.contradictions ?? []).map(item => <div key={item.symbol} className="border-l-2 border-[#B54708] bg-white p-3"><p className="font-semibold">{item.name} <span className="font-mono text-xs text-[#6B7078]">{item.symbol}</span></p><div className="mt-2 flex gap-4 text-xs"><span className="inline-flex items-center gap-1 text-[#027A48]"><ArrowUpRight className="h-3 w-3"/>{item.bullishCount} 条利多</span><span className="inline-flex items-center gap-1 text-[#B42318]"><ArrowDownRight className="h-3 w-3"/>{item.bearishCount} 条利空</span></div></div>)}{!data?.contradictions.length ? <p className="border border-dashed border-[#C9CCD2] p-4 text-xs text-[#6B7078]">当前周期未发现自选股事实层面的明显多空冲突。</p> : null}</div><a href="/investment-monitor/analysis" className="mt-5 inline-flex items-center gap-2 text-xs font-semibold text-[#155EEF]">进入综合研判 <ExternalLink className="h-3 w-3"/></a></div>
      </section>
    </div>
  </AppPage>;
}
