import { useCallback, useEffect, useMemo, useState } from 'react';
import { DatabaseZap, ExternalLink, FileText, RefreshCw, Search } from 'lucide-react';
import { Link } from 'react-router-dom';
import { investmentMonitorApi } from '../api/investmentMonitor';
import { AppPage, EmptyState } from '../components/common';
import { InvestmentMonitorNav } from '../components/investmentMonitor/InvestmentMonitorNav';
import { useDebouncedValue } from '../hooks/useDebouncedValue';
import type { MonitorEvent, MonitorEventList, MonitoringSource, MonitorStatus } from '../types/investmentMonitor';

const GREEN = '#00E676';
const SOURCE_GROUPS = [
  { key: 'official', title: '官方披露', description: '公告与法定披露' },
  { key: 'licensed', title: '授权数据', description: '行情、财务与企业数据' },
  { key: 'reported', title: '研究与新闻', description: '媒体和券商发布记录' },
  { key: 'unverified', title: '另类情报', description: '观点型内容，需二次核验' },
] as const;

function sourceLevel(source: MonitoringSource) {
  if (source.config?.evidenceLevel) return source.config.evidenceLevel;
  if (source.sourceKey === 'cninfo.announcements') return 'official';
  if (source.sourceKey === 'zsxq.essays') return 'unverified';
  if (source.category === 'news' || source.category === 'research') return 'reported';
  return 'licensed';
}

function time(value?: string | null, withDate = true) {
  if (!value) return '—';
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;
  return parsed.toLocaleString('zh-CN', withDate
    ? { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' }
    : { hour: '2-digit', minute: '2-digit' });
}

function freshness(source: MonitoringSource) {
  if (source.lastStatus === 'failed') return { label: '同步失败', color: '#FF5D5D' };
  if (source.lastStatus === 'not_configured') return { label: '未配置上游', color: '#717A84' };
  if (source.monitoringStatus === 'delayed') return { label: '监控延迟', color: '#FFB800' };
  if (source.monitoringStatus === 'live' && source.freshnessStatus === 'stale') return { label: '实时巡检 · 上游暂无新事实', color: GREEN };
  if (source.monitoringStatus === 'live' && source.freshnessStatus === 'empty') return { label: '实时巡检 · 尚无匹配数据', color: GREEN };
  if (source.freshnessStatus === 'fresh') return { label: '新鲜', color: GREEN };
  if (source.freshnessStatus === 'stale') return { label: '陈旧', color: '#FFB800' };
  return { label: '暂无数据', color: '#717A84' };
}

function EventRow({ event }: { event: MonitorEvent }) {
  const content = <>
    <div className="min-w-0">
      <div className="flex flex-wrap items-center gap-2 text-[9px] text-[#717A84]">
        <span className="font-mono text-[#00E676]">{time(event.eventAt)}</span>
        {event.symbols.map(symbol => <span key={symbol} className="border border-[#303740] px-1.5 py-0.5">{symbol}</span>)}
      </div>
      <h3 className="mt-1 line-clamp-2 text-xs font-semibold leading-5 text-[#D7DCE2] group-hover:text-white">{event.title}</h3>
      {event.summary ? <p className="mt-1 line-clamp-2 text-[10px] leading-4 text-[#8B949E]">{event.summary}</p> : null}
    </div>
    <div className="text-right">
      <p className="font-mono text-[9px] text-[#717A84]">入库 {time(event.ingestedAt, false)}</p>
      <span className="mt-2 inline-flex items-center gap-1 text-[9px] font-semibold text-[#00E676]">
        {event.url ? '打开原文' : '接口原文'} {event.url ? <ExternalLink className="h-3 w-3" /> : <FileText className="h-3 w-3" />}
      </span>
    </div>
  </>;
  const className = 'group grid grid-cols-[minmax(0,1fr)_86px] gap-3 border-t border-[#242A31] px-3 py-3 text-left first:border-t-0 hover:bg-[#0D100E]';
  return event.url
    ? <a href={event.url} target="_blank" rel="noreferrer" className={className}>{content}</a>
    : <Link to={`/investment-monitor/feed?event=${event.id}`} className={className}>{content}</Link>;
}

export default function InvestmentMonitorOverviewPage() {
  const [status, setStatus] = useState<MonitorStatus | null>(null);
  const [events, setEvents] = useState<MonitorEventList | null>(null);
  const [selectedSourceKey, setSelectedSourceKey] = useState('');
  const [query, setQuery] = useState('');
  const deferredQuery = useDebouncedValue(query, 350);
  const [loadingStatus, setLoadingStatus] = useState(true);
  const [loadingEvents, setLoadingEvents] = useState(false);
  const [syncing, setSyncing] = useState(false);
  const [syncingAll, setSyncingAll] = useState(false);
  const [error, setError] = useState('');

  const loadStatus = useCallback(async () => {
    try {
      const next = await investmentMonitorApi.status();
      setStatus(next);
      setSelectedSourceKey(current => {
        if (current && next.sources.items.some(source => source.sourceKey === current)) return current;
        return next.sources.items.find(source => (source.storedEventCount ?? 0) > 0)?.sourceKey
          ?? next.sources.items[0]?.sourceKey
          ?? '';
      });
      setError('');
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : '渠道状态暂时不可用');
    } finally {
      setLoadingStatus(false);
    }
  }, []);

  const loadEvents = useCallback(async () => {
    if (!selectedSourceKey) { setEvents(null); return; }
    setLoadingEvents(true);
    try {
      setEvents(await investmentMonitorApi.events({ days: 3650, sourceKey: selectedSourceKey,
        query: deferredQuery || undefined, pageSize: 40 }));
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : '渠道消息暂时不可用');
    } finally {
      setLoadingEvents(false);
    }
  }, [deferredQuery, selectedSourceKey]);

  useEffect(() => { void loadStatus(); }, [loadStatus]);
  useEffect(() => { void loadEvents(); }, [loadEvents]);
  useEffect(() => {
    const timer = window.setInterval(() => {
      if (document.visibilityState === 'visible') void Promise.allSettled([loadStatus(), loadEvents()]);
    }, 10_000);
    return () => window.clearInterval(timer);
  }, [loadEvents, loadStatus]);

  const groups = useMemo(() => SOURCE_GROUPS.map(group => ({ ...group,
    items: (status?.sources.items ?? []).filter(source => sourceLevel(source) === group.key),
  })), [status?.sources.items]);
  const selectedSource = status?.sources.items.find(source => source.sourceKey === selectedSourceKey) ?? null;

  const syncSelected = async () => {
    if (!selectedSourceKey || syncing) return;
    setSyncing(true); setError('');
    try {
      await investmentMonitorApi.syncSource(selectedSourceKey);
      await Promise.allSettled([loadStatus(), loadEvents()]);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : '渠道同步失败');
    } finally { setSyncing(false); }
  };

  const syncAll = async () => {
    if (syncingAll) return;
    setSyncingAll(true); setError('');
    try {
      await investmentMonitorApi.sync();
      await Promise.allSettled([loadStatus(), loadEvents()]);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : '全部渠道同步失败');
    } finally { setSyncingAll(false); }
  };

  return <AppPage className="max-w-[1760px]">
    <main className="overflow-hidden border border-[#242A31] bg-[#050605] font-mono text-[#D7DCE2]">
      <header className="flex flex-col gap-4 border-b border-[#242A31] bg-[#090B09] px-4 py-4 lg:flex-row lg:items-end lg:justify-between">
        <div><p className="text-[9px] uppercase tracking-[0.22em] text-[#00E676]">统一事件库 · 全部真实来源</p><h1 className="mt-2 text-2xl font-black tracking-[-0.05em] text-white">全渠道情报</h1><p className="mt-1 text-[10px] text-[#717A84]">按信息渠道查看存量、最新时间、同步状态与原始消息；不混入行情看板和自选股分析。</p></div>
        <div className="flex gap-2"><button onClick={() => void Promise.allSettled([loadStatus(), loadEvents()])} disabled={loadingStatus || loadingEvents} className="inline-flex h-8 items-center gap-2 border border-[#303740] bg-[#0B0C0A] px-3 text-[10px] hover:border-[#00E676] disabled:opacity-50"><RefreshCw className={`h-3.5 w-3.5 ${loadingStatus || loadingEvents ? 'animate-spin' : ''}`} />刷新本地情报</button><button onClick={() => void syncAll()} disabled={syncingAll} className="inline-flex h-8 items-center gap-2 border border-[#00E676] px-3 text-[10px] font-bold text-[#00E676] disabled:opacity-50"><DatabaseZap className="h-3.5 w-3.5"/>{syncingAll ? '逐源同步中' : '从上游同步全部'}</button></div>
      </header>

      <InvestmentMonitorNav />
      {error ? <div role="alert" className="border-b border-[#6B4423] bg-[#19130A] px-4 py-2 text-[10px] text-[#FFB800]">{error}</div> : null}

      <section className="grid grid-cols-2 border-b border-[#242A31] lg:grid-cols-5">
        {[
          ['渠道总数', status?.sources.enabled ?? '—'], ['真正有数据', status?.sources.withData ?? '—'],
          ['实时巡检', status?.sources.monitoringLive ?? '—'], ['延迟 / 失败', status?.sources.monitoringDelayed ?? '—'],
          ['最后调度', time(status?.worker.lastSyncAt, false)],
        ].map(([label, value]) => <div key={String(label)} className="border-r border-t border-[#242A31] px-4 py-3 first:border-t-0 lg:border-t-0"><p className="text-[9px] text-[#717A84]">{label}</p><p className="mt-1 text-lg font-bold text-white">{value}</p></div>)}
      </section>

      <section aria-labelledby="source-channel-heading">
        <div className="flex items-center justify-between border-b border-[#242A31] px-4 py-3"><div><h2 id="source-channel-heading" className="text-xs font-bold text-white">信息渠道</h2><p className="mt-1 text-[9px] text-[#717A84]">选择一个渠道查看其真实消息；每个渠道独立同步、独立失败。</p></div><span className={`text-[9px] ${status?.worker.running ? 'text-[#00E676]' : 'text-[#717A84]'}`}>{status?.worker.running ? '后台监控运行中' : '后台监控已停止'}</span></div>
        <div className="grid xl:grid-cols-4">
          {groups.map((group, groupIndex) => <section key={group.key} className={groupIndex ? 'border-t border-[#242A31] xl:border-l xl:border-t-0' : ''}>
            <header className="border-b border-[#242A31] bg-[#090B09] px-3 py-2.5"><div className="flex items-center justify-between"><h3 className="text-[10px] font-bold text-white">{group.title}</h3><span className="text-[9px] text-[#717A84]">{group.items.length}</span></div><p className="mt-0.5 text-[9px] text-[#59616A]">{group.description}</p></header>
            <div>{group.items.map(source => { const state = freshness(source); const active = source.sourceKey === selectedSourceKey; return <button key={source.sourceKey} onClick={() => setSelectedSourceKey(source.sourceKey)} className={`grid w-full grid-cols-[minmax(0,1fr)_70px] gap-2 border-b border-[#1C211E] px-3 py-2.5 text-left transition-colors ${active ? 'bg-[#0C1710] shadow-[inset_2px_0_0_#00E676]' : 'hover:bg-[#0D100E]'}`}>
              <span className="min-w-0"><span className={`block truncate text-[10px] font-semibold ${active ? 'text-[#00E676]' : 'text-[#D7DCE2]'}`}>{source.name}</span><span className="mt-1 block truncate text-[8px] text-[#59616A]">{source.config?.originApis?.join(' · ') || source.adapterType}</span><span className="mt-1 block truncate text-[8px]" style={{ color: state.color }}>{state.label} · 检查 {time(source.lastCheckAt)}</span><span className="mt-0.5 block truncate text-[8px] text-[#59616A]">最新事实 {time(source.latestEventAt)}</span></span>
              <span className="text-right"><span className="block text-sm font-bold text-white">{source.storedEventCount ?? 0}</span><span className="text-[8px] text-[#59616A]">存量</span><span className="mt-1 block text-[8px] text-[#717A84]">收到 {source.lastReceivedCount ?? 0} · 新 {source.lastCreatedCount ?? 0}</span></span>
            </button>; })}{!group.items.length ? <p className="px-3 py-5 text-[9px] text-[#59616A]">暂无渠道</p> : null}</div>
          </section>)}
        </div>
      </section>

      <section className="border-t border-[#303740]">
        <div className="flex flex-col gap-3 border-b border-[#242A31] bg-[#090B09] px-4 py-3 lg:flex-row lg:items-center lg:justify-between">
          <div className="min-w-0"><p className="truncate text-xs font-bold text-white">{selectedSource?.name ?? '请选择信息渠道'}</p><p className="mt-1 truncate text-[9px] text-[#717A84]">{selectedSource ? `${selectedSource.provider} · 最后尝试 ${time(selectedSource.lastSuccessAt)} · 上轮收到 ${selectedSource.lastReceivedCount ?? 0} / 新增 ${selectedSource.lastCreatedCount ?? 0} / 更新 ${selectedSource.lastUpdatedCount ?? 0} · 存量 ${events?.total ?? selectedSource.storedEventCount ?? 0}` : '上方列出全部已接入来源'}</p>{selectedSource?.lastError ? <p className="mt-1 truncate text-[9px] text-[#FFB800]">{selectedSource.lastError}</p> : null}</div>
          <div className="flex gap-2"><label className="relative min-w-[240px]"><Search className="absolute left-2.5 top-2 h-3.5 w-3.5 text-[#59616A]"/><input aria-label="搜索当前渠道" value={query} onChange={event => setQuery(event.target.value)} placeholder="搜索当前渠道标题或摘要" className="h-8 w-full border border-[#303740] bg-[#050605] pl-8 pr-3 text-[10px] text-white outline-none focus:border-[#00E676]"/></label><button onClick={() => void syncSelected()} disabled={!selectedSource || syncing} className="inline-flex h-8 items-center gap-1.5 border border-[#00E676] px-3 text-[9px] font-bold text-[#00E676] disabled:border-[#303740] disabled:text-[#59616A]"><DatabaseZap className="h-3.5 w-3.5"/>{syncing ? '同步中' : '同步当前渠道'}</button></div>
        </div>
        <div aria-live="polite">{!loadingEvents && selectedSource && !events?.items.length ? <EmptyState title="当前渠道暂无匹配消息" description="清除关键词或同步该渠道后再查看。" /> : null}{events?.items.map(event => <EventRow key={event.id} event={event}/>)}</div>
        <div className="flex items-center justify-between border-t border-[#242A31] px-4 py-2 text-[9px] text-[#59616A]"><span>{loadingEvents ? '正在读取本地事件库，保留现有结果…' : `显示 ${events?.items.length ?? 0} / ${events?.total ?? 0} 条`}</span><Link to={selectedSourceKey ? `/investment-monitor/feed?source=${encodeURIComponent(selectedSourceKey)}` : '/investment-monitor/feed'} className="text-[#00E676] hover:underline">进入实时流水</Link></div>
      </section>
    </main>
  </AppPage>;
}
