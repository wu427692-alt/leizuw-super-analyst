import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  Activity, ArrowRight, BarChart3, Bot, BrainCircuit, Building2, CalendarClock,
  FileSearch, Globe2, Landmark, MessageSquareText, RadioTower, RefreshCw, ShieldAlert,
  Sparkles, Zap,
} from 'lucide-react';
import { useNavigate } from 'react-router-dom';
import { homeDashboardApi } from '../api/homeDashboard';
import { AppPage, Badge, Card, EmptyState } from '../components/common';
import type { HomeDashboard, HomeWatchlistCard, MarketIndexCard, MarketPoint } from '../types/homeDashboard';
import type { MonitorEvent } from '../types/investmentMonitor';

const SOURCE_LABELS: Record<string, string> = {
  investor: '投资者', company: '上市公司', institution: '机构',
};

function tone(value?: number | null) {
  if ((value ?? 0) > 0) return 'text-danger';
  if ((value ?? 0) < 0) return 'text-success';
  return 'text-secondary-text';
}

function formatNumber(value?: number | null, digits = 2) {
  return value == null || Number.isNaN(value) ? '—' : value.toLocaleString('zh-CN', { maximumFractionDigits: digits, minimumFractionDigits: digits });
}

function formatCompact(value?: number | null) {
  if (value == null || Number.isNaN(value)) return '—';
  if (Math.abs(value) >= 100_000_000) return `${(value / 100_000_000).toFixed(2)}亿`;
  if (Math.abs(value) >= 10_000) return `${(value / 10_000).toFixed(2)}万`;
  return value.toLocaleString('zh-CN');
}

function percent(value?: number | null) {
  if (value == null || Number.isNaN(value)) return '—';
  return `${value > 0 ? '+' : ''}${value.toFixed(2)}%`;
}

function Sparkline({ points, positive, height = 44 }: { points: MarketPoint[]; positive: boolean; height?: number }) {
  const values = points.map((point) => Number(point.value)).filter(Number.isFinite);
  if (values.length < 2) return <div className="flex h-11 items-center justify-center text-xs text-secondary-text">暂无趋势</div>;
  const min = Math.min(...values); const max = Math.max(...values); const range = max - min || 1;
  const coords = values.map((value, index) => `${(index / (values.length - 1)) * 100},${height - 4 - ((value - min) / range) * (height - 8)}`).join(' ');
  return <svg viewBox={`0 0 100 ${height}`} className="h-full w-full overflow-visible" preserveAspectRatio="none" aria-hidden="true">
    <polyline points={coords} fill="none" stroke={positive ? 'hsl(var(--danger))' : 'hsl(var(--success))'} strokeWidth="2" vectorEffect="non-scaling-stroke" />
  </svg>;
}

function IndexTicker({ item }: { item: MarketIndexCard }) {
  const positive = (item.changePct ?? 0) >= 0;
  return <div className="min-w-[190px] flex-1 border-r border-border/50 px-4 py-3 last:border-r-0">
    <div className="flex items-start justify-between gap-3">
      <div><p className="text-xs text-secondary-text">{item.name}</p><p className={`mt-1 text-xl font-semibold tabular-nums ${tone(item.changePct)}`}>{formatNumber(item.close)}</p>
        <p className={`mt-0.5 text-xs tabular-nums ${tone(item.changePct)}`}>{percent(item.changePct)}</p></div>
      <div className="mt-2 h-10 w-20"><Sparkline points={item.history} positive={positive} height={40} /></div>
    </div>
  </div>;
}

function OverseasCard({ item }: { item: MarketIndexCard }) {
  const positive = (item.changePct ?? 0) >= 0;
  return <div className="rounded-2xl border border-border/65 bg-background/35 p-3.5">
    <div className="flex items-center justify-between"><div><p className="text-sm font-medium text-foreground">{item.name}</p><p className="text-[11px] text-secondary-text">{item.region}</p></div><span className={`text-xs font-medium ${tone(item.changePct)}`}>{percent(item.changePct)}</span></div>
    <p className={`mt-3 text-xl font-semibold tabular-nums ${tone(item.changePct)}`}>{formatNumber(item.close)}</p>
    <div className="mt-2 h-9"><Sparkline points={item.history} positive={positive} height={36} /></div>
  </div>;
}

function IntelligenceLine({ event }: { event?: MonitorEvent | null }) {
  if (!event) return <span className="text-secondary-text">暂无新增信息</span>;
  return <span aria-label={event.title}>{event.title}</span>;
}

function WatchlistSuperCard({ card, onAnalyze }: { card: HomeWatchlistCard; onAnalyze: () => void }) {
  const quote = card.latestQuote; const changePct = quote?.changePercent; const positive = (changePct ?? 0) >= 0;
  const bullish = card.sentiment.bullish ?? 0; const bearish = card.sentiment.bearish ?? 0;
  const totalSentiment = Math.max(1, bullish + bearish + (card.sentiment.neutral ?? 0) + (card.sentiment.mixed ?? 0));
  return <div className="overflow-hidden rounded-2xl border border-cyan/28 bg-[linear-gradient(145deg,hsl(var(--card)/.92),hsl(var(--background)/.78))] shadow-soft-card">
    <div className="border-b border-border/60 p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div><div className="flex items-center gap-2"><h3 className="text-xl font-semibold text-foreground">{card.name}</h3><Badge>{card.symbol}</Badge></div>
          <div className="mt-3 flex items-baseline gap-3"><span className={`text-3xl font-semibold tabular-nums ${tone(changePct)}`}>{formatNumber(quote?.currentPrice)}</span><span className={`text-sm font-medium ${tone(changePct)}`}>{percent(changePct)}</span></div></div>
        <button className="btn-primary inline-flex items-center gap-2" onClick={onAnalyze}><Sparkles className="h-4 w-4" />深度分析</button>
      </div>
      <div className="mt-3 grid grid-cols-[minmax(0,1fr)_180px] gap-4">
        <div className="h-20"><Sparkline points={card.history} positive={positive} height={78} /></div>
        <div className="grid grid-cols-2 gap-x-3 gap-y-1 text-xs"><span className="text-secondary-text">今开</span><span className="text-right tabular-nums">{formatNumber(quote?.open)}</span><span className="text-secondary-text">最高</span><span className="text-right tabular-nums text-danger">{formatNumber(quote?.high)}</span><span className="text-secondary-text">最低</span><span className="text-right tabular-nums text-success">{formatNumber(quote?.low)}</span><span className="text-secondary-text">成交额</span><span className="text-right tabular-nums">{formatCompact(quote?.amount)}</span></div>
      </div>
    </div>
    <div className="grid gap-px bg-border/60 md:grid-cols-2">
      <div className="bg-card/85 p-4"><div className="flex items-center justify-between text-xs"><span className="text-secondary-text">市场情绪</span><span className={card.opportunityScore >= card.riskScore ? 'text-danger' : 'text-success'}>{card.opportunityScore >= card.riskScore ? '偏多' : '谨慎'}</span></div>
        <div className="mt-3 h-2 overflow-hidden rounded-full bg-elevated"><div className="h-full bg-success" style={{ width: `${Math.round((bullish / totalSentiment) * 100)}%` }} /></div>
        <div className="mt-2 flex justify-between text-[11px] text-secondary-text"><span>机会 {card.opportunityScore}</span><span>风险 {card.riskScore}</span><span>{card.eventCount} 条情报</span></div></div>
      <div className="bg-card/85 p-4"><p className="text-xs text-secondary-text">机构视角</p><p className="mt-2 line-clamp-2 text-sm leading-5 text-foreground"><IntelligenceLine event={card.latestInstitution} /></p><p className="mt-1 text-[11px] text-secondary-text">近7日机构信息 {card.perspectives.institution ?? 0} 条</p></div>
      <div className="bg-card/85 p-4"><p className="flex items-center gap-1.5 text-xs text-danger"><Zap className="h-3.5 w-3.5" />最新催化</p><p className="mt-2 line-clamp-2 text-sm leading-5 text-foreground"><IntelligenceLine event={card.latestCatalyst} /></p></div>
      <div className="bg-card/85 p-4"><p className="flex items-center gap-1.5 text-xs text-warning"><ShieldAlert className="h-3.5 w-3.5" />最新风险</p><p className="mt-2 line-clamp-2 text-sm leading-5 text-foreground"><IntelligenceLine event={card.latestRisk} /></p></div>
    </div>
  </div>;
}

const MarketDashboardPage = () => {
  const navigate = useNavigate();
  const [data, setData] = useState<HomeDashboard | null>(null);
  const [loading, setLoading] = useState(true); const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const load = useCallback(async (force = false) => {
    if (force) setRefreshing(true); else setLoading(true);
    setError(null);
    try { setData(await homeDashboardApi.get(force)); }
    catch (caught) { setError(caught instanceof Error ? caught.message : '市场看板加载失败'); }
    finally { setLoading(false); setRefreshing(false); }
  }, []);
  useEffect(() => { void load(); const timer = window.setInterval(() => void load(), 300_000); return () => window.clearInterval(timer); }, [load]);
  useEffect(() => { document.title = '市场总览 - DSA'; }, []);

  const leadIndex = data?.cnIndices[0];
  const maxDistribution = useMemo(() => Math.max(1, ...(data?.breadth.distribution ?? []).map((item) => item.count)), [data]);
  const aiActions = [
    { label: '个股深度分析', note: '多维度研判', icon: BrainCircuit, action: () => navigate('/chat') },
    { label: '财报解读', note: '财务质量与预期', icon: FileSearch, action: () => navigate('/data-acquisition') },
    { label: '行业对比', note: '板块与竞争格局', icon: BarChart3, action: () => navigate('/screening') },
    { label: '事件影响评估', note: '催化与风险传导', icon: Activity, action: () => navigate('/investment-monitor') },
    { label: '智能问答', note: '带数据上下文提问', icon: MessageSquareText, action: () => navigate('/chat') },
  ];

  return <AppPage className="max-w-[1680px] px-3 pb-8 pt-3 md:px-4 lg:px-5">
    <div className="space-y-3">
      <section className="overflow-hidden rounded-2xl border border-border/70 bg-card/78 shadow-soft-card">
        <div className="flex flex-col border-b border-border/60 px-4 py-3 sm:flex-row sm:items-center sm:justify-between">
          <div><div className="flex items-center gap-2"><RadioTower className="h-4 w-4 text-cyan" /><h1 className="text-lg font-semibold text-foreground">市场总览</h1><Badge variant="info">5分钟缓存</Badge></div><p className="mt-1 text-xs text-secondary-text">{data?.marketTime ?? '正在同步市场时间'} · A股、海外市场与自选股情报统一看板</p></div>
          <button className="btn-secondary mt-3 inline-flex items-center gap-2 sm:mt-0" disabled={refreshing} onClick={() => void load(true)}><RefreshCw className={`h-4 w-4 ${refreshing ? 'animate-spin' : ''}`} />刷新全市场</button>
        </div>
        <div className="flex overflow-x-auto">{(data?.cnIndices ?? []).map((item) => <IndexTicker key={item.code} item={item} />)}
          <div className="min-w-[190px] flex-1 px-4 py-3"><p className="text-xs text-secondary-text">北向资金</p><p className={`mt-1 text-xl font-semibold tabular-nums ${tone(data?.northbound.northMoneyYi)}`}>{formatNumber(data?.northbound.northMoneyYi)}亿</p><p className="mt-0.5 text-xs text-secondary-text">{data?.northbound.tradeDate ?? '—'}</p></div></div>
      </section>

      {error ? <div className="rounded-2xl border border-danger/30 bg-danger/10 px-4 py-3 text-sm text-danger">{error}</div> : null}
      {data?.warnings.length ? <div className="flex flex-wrap gap-2">{data.warnings.map((warning) => <Badge key={warning} variant="warning">{warning}</Badge>)}</div> : null}

      <div className="grid gap-3 xl:grid-cols-[minmax(0,1.45fr)_minmax(420px,.75fr)]">
        <Card className="min-h-[330px]" padding="lg">
          <div className="flex items-center justify-between"><div><p className="label-uppercase">CHINA MARKET</p><h2 className="mt-1 text-lg font-semibold">A股行情</h2></div><div className="flex gap-3 text-xs"><span className="text-danger">上涨 {data?.breadth.up ?? 0}</span><span className="text-success">下跌 {data?.breadth.down ?? 0}</span><span className="text-secondary-text">平盘 {data?.breadth.flat ?? 0}</span></div></div>
          <div className="mt-5 grid gap-5 lg:grid-cols-[minmax(0,1.35fr)_minmax(280px,.65fr)]">
            <div><div className="flex items-end justify-between"><div><p className="text-sm text-secondary-text">{leadIndex?.name ?? '上证指数'}</p><p className={`mt-1 text-3xl font-semibold tabular-nums ${tone(leadIndex?.changePct)}`}>{formatNumber(leadIndex?.close)}</p></div><span className={`text-sm ${tone(leadIndex?.changePct)}`}>{percent(leadIndex?.changePct)}</span></div><div className="mt-5 h-40"><Sparkline points={leadIndex?.history ?? []} positive={(leadIndex?.changePct ?? 0) >= 0} height={150} /></div><div className="mt-3 flex justify-between text-xs text-secondary-text"><span>低 {formatNumber(leadIndex?.low)}</span><span>高 {formatNumber(leadIndex?.high)}</span><span>成交 {leadIndex?.amountYi == null ? '--' : `${formatNumber(leadIndex.amountYi)}亿`}</span></div></div>
            <div><p className="text-sm font-medium">涨跌分布</p><div className="mt-4 space-y-3">{(data?.breadth.distribution ?? []).map((item, index) => <div key={item.label} className="grid grid-cols-[55px_1fr_42px] items-center gap-2 text-xs"><span className="text-secondary-text">{item.label}</span><div className="h-2 overflow-hidden rounded-full bg-elevated"><div className={`h-full ${index < 3 ? 'bg-success' : index === 3 ? 'bg-secondary-text' : 'bg-danger'}`} style={{ width: `${Math.max(2, (item.count / maxDistribution) * 100)}%` }} /></div><span className="text-right tabular-nums">{item.count}</span></div>)}</div><div className="mt-5 grid grid-cols-2 gap-2"><div className="rounded-xl border border-danger/20 bg-danger/8 p-3"><p className="text-xs text-secondary-text">涨停</p><p className="mt-1 text-lg font-semibold text-danger">{data?.breadth.limitUp ?? 0}</p></div><div className="rounded-xl border border-success/20 bg-success/8 p-3"><p className="text-xs text-secondary-text">跌停</p><p className="mt-1 text-lg font-semibold text-success">{data?.breadth.limitDown ?? 0}</p></div></div></div>
          </div>
        </Card>
        <Card padding="lg"><div className="flex items-center justify-between"><div><p className="label-uppercase">GLOBAL MARKETS</p><h2 className="mt-1 text-lg font-semibold">海外市场</h2></div><Globe2 className="h-5 w-5 text-cyan" /></div><div className="mt-4 grid grid-cols-2 gap-2">{(data?.globalIndices ?? []).map((item) => <OverseasCard key={item.code} item={item} />)}</div></Card>
      </div>

      <section><div className="mb-3 flex items-center justify-between"><div><p className="label-uppercase">SUPER WATCHLIST</p><h2 className="mt-1 text-xl font-semibold">自选股超级看板</h2></div><button className="btn-secondary inline-flex items-center gap-2" onClick={() => navigate('/investment-monitor')}><Building2 className="h-4 w-4" />全部情报<ArrowRight className="h-4 w-4" /></button></div>
        {!loading && !data?.watchlist.length ? <EmptyState title="暂无自选股数据" description="请在设置中添加自选股，或等待情报台首次同步。" /> : <div className="grid gap-3 xl:grid-cols-2">{(data?.watchlist ?? []).map((card) => <WatchlistSuperCard key={card.symbol} card={card} onAnalyze={() => navigate(`/chat?stock=${encodeURIComponent(card.symbol.split('.')[0])}&name=${encodeURIComponent(card.name)}`)} />)}</div>}
      </section>

      <div className="grid gap-3 xl:grid-cols-[minmax(0,1.1fr)_minmax(520px,.9fr)]">
        <Card padding="lg"><div className="flex items-center justify-between"><div><p className="label-uppercase">LIVE INTELLIGENCE</p><h2 className="mt-1 text-lg font-semibold">最新情报</h2></div><button className="text-xs text-cyan hover:underline" onClick={() => navigate('/investment-monitor')}>查看全部</button></div>
          <div className="mt-4 divide-y divide-border/55">{(data?.latestEvents ?? []).slice(0, 6).map((event) => <button key={event.id} className="grid w-full grid-cols-[48px_minmax(0,1fr)_auto] items-center gap-3 py-3 text-left" aria-label={`${event.url ? '打开来源原文' : '查看接口返回原文'}：${event.title}`} onClick={() => event.url ? window.open(event.url, '_blank', 'noopener,noreferrer') : navigate(`/investment-monitor/feed?event=${event.id}`)}><span className="text-xs tabular-nums text-secondary-text">{new Date(event.eventAt).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })}</span><span className="truncate text-sm text-foreground">{event.title}</span><div className="flex items-center gap-1.5"><Badge>{SOURCE_LABELS[event.perspective] ?? event.perspective}</Badge><Badge variant={event.sentiment === 'bullish' ? 'danger' : event.sentiment === 'bearish' ? 'success' : 'default'}>{event.importanceScore}</Badge></div></button>)}{!data?.latestEvents.length ? <EmptyState title="暂无最新情报" description="情报同步后将在这里分渠道展示。" /> : null}</div>
        </Card>
        <Card padding="lg"><div className="flex items-center justify-between"><div><p className="label-uppercase">AI WORKBENCH</p><h2 className="mt-1 text-lg font-semibold">AI 分析</h2></div><Bot className="h-5 w-5 text-cyan" /></div><div className="mt-4 grid grid-cols-2 gap-2 sm:grid-cols-5 xl:grid-cols-3 2xl:grid-cols-5">{aiActions.map((item) => <button key={item.label} onClick={item.action} className="group rounded-2xl border border-border/65 bg-background/35 p-3 text-left transition hover:border-cyan/35 hover:bg-hover"><item.icon className="h-6 w-6 text-cyan" /><p className="mt-3 text-sm font-medium text-foreground">{item.label}</p><p className="mt-1 text-[11px] leading-4 text-secondary-text">{item.note}</p></button>)}</div><div className="mt-4 grid grid-cols-3 gap-2 text-center text-xs"><div className="rounded-xl bg-elevated/60 p-3"><RadioTower className="mx-auto h-4 w-4 text-cyan" /><p className="mt-1 text-secondary-text">活跃源</p><p className="mt-1 font-semibold">{data?.intelligenceSummary.activeSourceCount ?? 0}</p></div><div className="rounded-xl bg-elevated/60 p-3"><CalendarClock className="mx-auto h-4 w-4 text-cyan" /><p className="mt-1 text-secondary-text">近7日情报</p><p className="mt-1 font-semibold">{data?.intelligenceSummary.eventCount ?? 0}</p></div><div className="rounded-xl bg-elevated/60 p-3"><Landmark className="mx-auto h-4 w-4 text-cyan" /><p className="mt-1 text-secondary-text">高优先级</p><p className="mt-1 font-semibold">{data?.intelligenceSummary.highPriorityCount ?? 0}</p></div></div></Card>
      </div>
      {loading ? <div className="fixed inset-x-0 bottom-4 flex justify-center"><div className="rounded-full border border-cyan/20 bg-card/90 px-4 py-2 text-xs text-cyan shadow-soft-card">正在聚合海内外市场与自选股数据…</div></div> : null}
    </div>
  </AppPage>;
};

export default MarketDashboardPage;
