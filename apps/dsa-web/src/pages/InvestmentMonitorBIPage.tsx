import { useCallback, useMemo, useState } from 'react';
import { Activity, Boxes, CircleAlert, Database, RefreshCw, Search, ServerCog } from 'lucide-react';
import { investmentMonitorApi } from '../api/investmentMonitor';
import { AppPage, EmptyState } from '../components/common';
import { InvestmentMonitorNav } from '../components/investmentMonitor/InvestmentMonitorNav';
import type { SourceBI } from '../types/investmentMonitor';
import { usePageActivationRefresh } from '../hooks/usePageActivationRefresh';

const GREEN = '#00E676';
const YELLOW = '#FFB800';

function compact(value: number) {
  return new Intl.NumberFormat('zh-CN', { notation: value >= 10_000 ? 'compact' : 'standard', maximumFractionDigits: 1 }).format(value);
}

function stamp(value?: string | null) {
  if (!value) return '—';
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' });
}

function duration(seconds?: number | null) {
  if (seconds == null) return '无数据';
  if (seconds < 60) return `${seconds}秒`;
  if (seconds < 3600) return `${Math.round(seconds / 60)}分钟`;
  if (seconds < 86400) return `${Math.round(seconds / 3600)}小时`;
  return `${Math.round(seconds / 86400)}天`;
}

function state(source: SourceBI['sources'][number]) {
  if (source.lastStatus === 'failed') return { label: '同步失败', color: 'var(--danger)' };
  if (source.lastStatus === 'not_configured') return { label: '未配置', color: 'var(--text-3)' };
  if (source.monitoringStatus === 'delayed') return { label: '监控延迟', color: YELLOW };
  if (source.monitoringStatus === 'live' && source.freshnessStatus === 'stale') return { label: '巡检正常 · 上游静默', color: GREEN };
  if (source.freshnessStatus === 'empty') return { label: '空库', color: 'var(--text-3)' };
  if (source.freshnessStatus === 'stale') return { label: '数据陈旧', color: YELLOW };
  return { label: '可用', color: GREEN };
}

function Trend({ rows }: { rows: SourceBI['dailyTrend'] }) {
  const width = 900; const height = 190; const pad = 18;
  const max = Math.max(1, ...rows.map(row => row.count));
  const points = rows.map((row, index) => {
    const x = pad + (index * (width - pad * 2)) / Math.max(1, rows.length - 1);
    const y = height - pad - (row.count / max) * (height - pad * 2);
    return `${x},${y}`;
  }).join(' ');
  return <div className="h-[300px] w-full overflow-hidden">
    <svg viewBox={`0 0 ${width} ${height}`} className="h-full w-full" role="img" aria-label="近期事件量趋势">
      {[0.25, 0.5, 0.75, 1].map(ratio => <line key={ratio} x1={pad} x2={width - pad} y1={height - pad - ratio * (height - pad * 2)} y2={height - pad - ratio * (height - pad * 2)} stroke="var(--line-1)" strokeWidth="1" />)}
      <polyline points={points} fill="none" stroke={GREEN} strokeWidth="2" vectorEffect="non-scaling-stroke" />
      {rows.map((row, index) => {
        const [x, y] = points.split(' ')[index].split(',');
        return <circle key={row.date} cx={x} cy={y} r="2.5" fill="var(--bg-1)" stroke={GREEN}><title>{row.date} · {row.count} 条</title></circle>;
      })}
    </svg>
    <div className="-mt-5 flex justify-between px-4 text-[12px] text-[var(--text-3)]"><span>{rows[0]?.date.slice(5)}</span><span>{rows.at(-1)?.date.slice(5)}</span></div>
  </div>;
}

export default function InvestmentMonitorBIPage() {
  const [data, setData] = useState<SourceBI | null>(null);
  const [query, setQuery] = useState('');
  const [filter, setFilter] = useState<'all' | 'attention' | 'fresh'>('all');
  const [loading, setLoading] = useState(true);
  const [syncing, setSyncing] = useState(false);
  const [error, setError] = useState('');

  const load = useCallback(async () => {
    try { setData(await investmentMonitorApi.sourceBI(30)); setError(''); }
    catch (caught) { setError(caught instanceof Error ? caught.message : '数据源 BI 暂时不可用'); }
    finally { setLoading(false); }
  }, []);

  usePageActivationRefresh(load, { intervalMs: 10_000, minIntervalMs: 2_000 });

  const visible = useMemo(() => (data?.sources ?? []).filter(source => {
    const matched = `${source.name} ${source.sourceKey} ${source.provider} ${source.category} ${source.directUse.originApis.join(' ')}`.toLowerCase().includes(query.trim().toLowerCase());
    if (!matched) return false;
    if (filter === 'fresh') return source.freshnessStatus === 'fresh';
    if (filter === 'attention') return source.freshnessStatus !== 'fresh' || source.lastStatus !== 'success';
    return true;
  }), [data?.sources, filter, query]);

  const syncAll = async () => {
    if (syncing) return;
    setSyncing(true); setError('');
    try { await investmentMonitorApi.sync(); await load(); }
    catch (caught) { setError(caught instanceof Error ? caught.message : '全部渠道同步失败'); }
    finally { setSyncing(false); }
  };

  const maxCategory = Math.max(1, ...(data?.categories ?? []).map(row => row.count));
  return <AppPage className="max-w-[1760px]">
    <main className="intelligence-terminal overflow-hidden border border-[var(--line-1)] bg-[var(--bg-1)] text-[var(--text-2)]">
      <header className="flex flex-col gap-4 border-b border-[var(--line-1)] bg-[var(--bg-1)] px-4 py-4 xl:flex-row xl:items-end xl:justify-between">
        <div><p className="text-[12px] uppercase tracking-normal text-[var(--success)]">Source inventory · freshness · throughput</p><h1 className="mt-2 text-2xl font-bold tracking-[-0.05em] text-white">全渠道数据源 BI</h1><p className="mt-1 text-[12px] text-[var(--text-3)]">回答三个问题：我有什么数据、数据现在怎么样、从哪里直接调用。所有数字来自本地事件库和真实同步状态。</p></div>
        <button onClick={() => void syncAll()} disabled={syncing} className="inline-flex h-8 items-center gap-2 border border-[var(--success)] px-3 text-[12px] font-bold text-[var(--success)] disabled:opacity-50"><RefreshCw className={`h-3.5 w-3.5 ${syncing ? 'animate-spin' : ''}`}/>{syncing ? '正在逐源同步' : '立即同步全部渠道'}</button>
      </header>
      <InvestmentMonitorNav />
      {error ? <div role="alert" className="border-b border-[var(--line-2)] bg-[var(--bg-1)] px-4 py-2 text-[12px] text-[var(--warning)]">{error}</div> : null}

      <section className="grid grid-cols-2 border-b border-[var(--line-1)] md:grid-cols-3 xl:grid-cols-6">
        {([
          { icon: Boxes, label: '已接入渠道', value: data?.summary.enabled, unit: '个' },
          { icon: Database, label: '本地事件总量', value: data?.summary.storedEventCount, unit: '条' },
          { icon: Activity, label: '近30日事件', value: data?.summary.periodEventCount, unit: '条' },
          { icon: ServerCog, label: '实时巡检', value: data?.summary.monitoringLive, unit: '个' },
          { icon: CircleAlert, label: '延迟 / 失败', value: data?.summary.monitoringDelayed, unit: '个' },
          { icon: RefreshCw, label: '上轮新增', value: data?.summary.lastRunCreated, unit: '条' },
        ] as const).map(({ icon: MetricIcon, label, value, unit }, index) => {
          return <div key={String(label)} className={`min-h-[92px] border-r border-t border-[var(--line-1)] px-4 py-3 ${index < 2 ? 'border-t-0' : 'md:border-t-0'}`}><MetricIcon className="h-3.5 w-3.5 text-[var(--success)]"/><p className="mt-2 text-[12px] text-[var(--text-3)]">{label}</p><p className="mt-1 text-xl font-bold text-white">{value == null ? '—' : compact(Number(value))}<span className="ml-1 text-[12px] font-normal text-[var(--text-3)]">{unit}</span></p></div>;
        })}
      </section>

      <section className="grid border-b border-[var(--line-1)] xl:grid-cols-[minmax(0,1.7fr)_minmax(320px,0.7fr)]">
        <div className="border-b border-[var(--line-1)] p-4 xl:border-b-0 xl:border-r"><div className="flex items-end justify-between"><div><h2 className="text-xs font-bold text-white">事件覆盖趋势</h2><p className="mt-1 text-[12px] text-[var(--text-3)]">按事实发生日统计，不用同步调用次数冒充数据量</p></div><span className="text-[12px] text-[var(--text-3)]">更新 {stamp(data?.generatedAt)}</span></div>{data ? <Trend rows={data.dailyTrend}/> : <div className="h-[300px] animate-pulse bg-[var(--bg-1)]"/>}</div>
        <div className="p-4"><h2 className="text-xs font-bold text-white">数据域存量</h2><p className="mt-1 text-[12px] text-[var(--text-3)]">统一事件库中的真实记录数</p><div className="mt-4 space-y-2.5">{data?.categories.slice(0, 10).map(row => <div key={row.name}><div className="mb-1 flex justify-between text-[12px]"><span className="text-[var(--text-3)]">{row.name}</span><span className="text-white">{compact(row.count)}</span></div><div className="h-1.5 bg-[var(--bg-2)]"><div className="h-full bg-[var(--success)]" style={{ width: `${Math.max(1, row.count / maxCategory * 100)}%` }}/></div></div>)}</div></div>
      </section>

      <section className="border-b border-[var(--line-1)] bg-[var(--bg-1)] px-4 py-3"><div className="grid items-center gap-2 text-center text-[12px] text-[var(--text-3)] md:grid-cols-[1fr_24px_1fr_24px_1fr_24px_1fr]"><span className="border border-[var(--line-2)] px-3 py-2 text-white">上游提供方 · {data?.providers.length ?? 0}</span><span className="text-[var(--success)]">→</span><span className="border border-[var(--line-2)] px-3 py-2 text-white">{data?.summary.enabled ?? '—'} 个独立适配器</span><span className="text-[var(--success)]">→</span><span className="border border-[var(--line-2)] px-3 py-2 text-white">SQLite 统一事件库</span><span className="text-[var(--success)]">→</span><span className="border border-[var(--line-2)] px-3 py-2 text-white">筛选 API · 看板 · AI 分析</span></div></section>

      <section>
        <div className="flex flex-col gap-3 border-b border-[var(--line-1)] px-4 py-3 lg:flex-row lg:items-center lg:justify-between"><div><h2 className="text-xs font-bold text-white">数据源清单与可调用能力</h2><p className="mt-1 text-[12px] text-[var(--text-3)]">“上轮收到”表示上游返回量，“新增”表示去重后新入库量；二者不再混淆。</p></div><div className="flex flex-wrap gap-2"><label className="relative"><Search className="absolute left-2.5 top-2 h-3.5 w-3.5 text-[var(--text-3)]"/><input value={query} onChange={event => setQuery(event.target.value)} aria-label="搜索数据源" placeholder="渠道、接口、提供方" className="h-8 w-[240px] border border-[var(--line-2)] bg-[var(--bg-1)] pl-8 pr-3 text-[12px] outline-none focus:border-[var(--success)]"/></label>{(['all', 'fresh', 'attention'] as const).map(value => <button key={value} onClick={() => setFilter(value)} className={`h-8 border px-3 text-[12px] ${filter === value ? 'border-[var(--success)] text-[var(--success)]' : 'border-[var(--line-2)] text-[var(--text-3)]'}`}>{value === 'all' ? '全部' : value === 'fresh' ? '可用' : '需处理'}</button>)}</div></div>
        <div className="overflow-x-auto"><table className="w-full min-w-[1240px] border-collapse text-left text-[12px]"><thead className="bg-[var(--bg-1)] text-[var(--text-3)]"><tr>{['状态 / 渠道', '提供方 / 数据域', '存量', '近30日', '最近检查 / 最新事实', '上轮收到 / 新增 / 更新', '耗时 / 频率', '底层接口', '直接调用'].map(label => <th key={label} className="border-b border-r border-[var(--line-1)] px-3 py-2 font-normal">{label}</th>)}</tr></thead><tbody>{visible.map(source => { const info = state(source); return <tr key={source.sourceKey} className="hover:bg-[var(--bg-1)]"><td className="border-b border-r border-[var(--line-1)] px-3 py-2.5"><div className="flex items-center gap-2"><span className="h-1.5 w-1.5" style={{ backgroundColor: info.color }}/><span className="font-semibold text-white">{source.name}</span></div><p className="mt-1 text-[12px] text-[var(--text-3)]">{info.label} · {source.sourceKey}</p></td><td className="border-b border-r border-[var(--line-1)] px-3 py-2.5"><p className="text-[var(--text-3)]">{source.provider}</p><p className="mt-1 text-[12px] text-[var(--text-3)]">{source.category} · {source.adapterType}</p></td><td className="border-b border-r border-[var(--line-1)] px-3 py-2.5 text-sm font-bold text-white">{compact(source.storedEventCount ?? 0)}</td><td className="border-b border-r border-[var(--line-1)] px-3 py-2.5 text-[var(--text-2)]">{compact(source.periodEventCount)}</td><td className="border-b border-r border-[var(--line-1)] px-3 py-2.5"><p className="text-[var(--success)]">检查 {stamp(source.lastCheckAt)}</p><p className="mt-1 text-[12px] text-[var(--text-3)]">事实 {stamp(source.latestEventAt)}</p><p className="mt-1 text-[12px]" style={{ color: info.color }}>{source.lastCheckAgeSeconds == null ? '尚未运行' : `巡检距今 ${duration(source.lastCheckAgeSeconds)}`}</p></td><td className="border-b border-r border-[var(--line-1)] px-3 py-2.5"><span className="text-white">{source.lastReceivedCount ?? 0}</span><span className="text-[var(--text-3)]"> / </span><span className="text-[var(--success)]">{source.lastCreatedCount ?? 0}</span><span className="text-[var(--text-3)]"> / {source.lastUpdatedCount ?? 0}</span>{source.lastError ? <p className="mt-1 max-w-[170px] truncate text-[12px] text-[var(--warning)]" title={source.lastError}>{source.lastError}</p> : null}</td><td className="border-b border-r border-[var(--line-1)] px-3 py-2.5"><p>{source.lastDurationMs ?? 0} ms</p><p className="mt-1 text-[12px] text-[var(--text-3)]">每 {duration(source.pollIntervalSeconds)}</p></td><td className="border-b border-r border-[var(--line-1)] px-3 py-2.5"><p className="max-w-[190px] truncate text-[var(--text-3)]" title={source.directUse.originApis.join(' · ')}>{source.directUse.originApis.join(' · ') || '外部注入'}</p><p className="mt-1 text-[12px] text-[var(--text-3)]">{source.directUse.localStore}</p></td><td className="border-b border-[var(--line-1)] px-3 py-2.5"><a href={source.directUse.eventsApi} target="_blank" rel="noreferrer" className="text-[var(--success)] hover:underline">事件 API</a><button onClick={() => void investmentMonitorApi.syncSource(source.sourceKey).then(load)} className="ml-3 text-[var(--text-3)] hover:text-white">同步</button></td></tr>; })}</tbody></table></div>
        {!loading && !visible.length ? <EmptyState title="没有符合条件的数据源" description="清除筛选条件后查看全部已接入渠道。"/> : null}
        <div className="flex justify-between border-t border-[var(--line-1)] px-4 py-2 text-[12px] text-[var(--text-3)]"><span>{loading ? '正在核对全部数据源…' : `显示 ${visible.length} / ${data?.sources.length ?? 0} 个来源`}</span><span>本地库 monitoring_events · 自动去重</span></div>
      </section>
    </main>
  </AppPage>;
}
