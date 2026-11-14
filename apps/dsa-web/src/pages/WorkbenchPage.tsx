import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  AudioLines, ClipboardCheck, FileText, MessageSquareQuote, MessagesSquare, Network, Orbit, Search,
} from 'lucide-react';
import { homeDashboardApi } from '../api/homeDashboard';
import { industryResearchApi } from '../api/industryResearch';
import { invalidateCachedQueries } from '../api/requestCache';
import {
  AppPage, BarMeter, HeatCell, MetricRow, PageTabs, ShareBar, Sparkline, type PageTabItem,
} from '../components/common';
import { PixelCrew } from '../components/illustration/PixelCrew';
import { MobileGroup, MobileListRow, MobileSkeleton } from '../components/mobile/MobilePrimitives';
import { NewsSheet, type NewsSheetView } from '../components/mobile/NewsSheet';
import { useUserAccess } from '../contexts/UserAccessContext';
import { useIsMobile } from '../hooks/useIsMobile';
import { usePageActivationRefresh } from '../hooks/usePageActivationRefresh';
import type { HomeDashboard, MarketIndexCard, MarketPoint } from '../types/homeDashboard';
import type { MonitorEvent } from '../types/investmentMonitor';
import { newsHeadline } from '../utils/newsText';
import { stripHtmlText } from '../utils/plainText';
import type { IndustryResearchProject } from '../types/industryResearch';
import './WorkbenchPage.css';

type Tone = 'up' | 'down' | 'flat';
type WorkbenchTab = 'overview' | 'focus' | 'news';

const toneOf = (value?: number | null): Tone => ((value ?? 0) > 0 ? 'up' : (value ?? 0) < 0 ? 'down' : 'flat');
const pct = (value?: number | null) => (
  value == null || !Number.isFinite(value) ? '—' : `${value > 0 ? '+' : ''}${value.toFixed(2)}%`
);
const price = (value?: number | null) => (
  value == null || !Number.isFinite(value) ? '—' : value.toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
);
const sparkValues = (points?: MarketPoint[] | null) => (
  (points ?? []).map((point) => Number(point?.value)).filter((value) => Number.isFinite(value))
);
const signed = (value: number) => `${value > 0 ? '+' : ''}${value.toFixed(2)}%`;

const RUNNING = new Set<IndustryResearchProject['status']>(['queued', 'collecting', 'analyzing']);
const STATUS_TEXT: Record<IndustryResearchProject['status'], string> = {
  queued: '排队中', collecting: '收集资料', analyzing: '分析中', completed: '已完成', limited: '资料有限', failed: '失败',
};

/** 首页快讯行只显示「时刻」；时间戳无效时不硬排版成 Invalid Date。 */
function newsClock(value?: string | null) {
  if (!value) return '';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? '' : date.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' });
}

function greeting(now: Date) {
  const hour = now.getHours();
  if (hour < 6) return '夜深了';
  if (hour < 11) return '早上好';
  if (hour < 14) return '中午好';
  if (hour < 18) return '下午好';
  return '晚上好';
}

/** 卡片头统一结构：标题 + 右侧 meta（统计入标题栏，不另占一行统计条）。 */
function Panel({
  title, meta, action, children, className,
}: {
  title: string;
  meta?: ReactNode;
  action?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section className={className ? `wb-panel ${className}` : 'wb-panel'}>
      <header className="wb-panel__head">
        <h2>{title}</h2>
        {meta ? <span className="wb-panel__meta">{meta}</span> : null}
        {action ? <div className="wb-panel__action">{action}</div> : null}
      </header>
      <div className="wb-panel__body">{children}</div>
    </section>
  );
}

/** 涨跌幅分档 → 密度格矩阵：A 股红涨绿跌，左侧跌档走跌色。 */
function DistributionMatrix({ buckets, ariaLabel }: { buckets: Array<{ label: string; count: number }>; ariaLabel: string }) {
  if (!buckets.length) return <p className="wb-empty">暂无分布数据</p>;
  const max = Math.max(1, ...buckets.map((bucket) => bucket.count));
  const midpoint = Math.floor(buckets.length / 2);
  return (
    <div className="wb-heat" role="img" aria-label={ariaLabel}>
      {buckets.map((bucket, index) => {
        const sign = index < midpoint ? -1 : index > midpoint ? 1 : 0;
        // 强度上限压到 0.7：HeatCell 的 0.8 档在浅色下接近纯色平涂（§11 禁止大面积平涂）。
        const intensity = sign === 0 ? 0 : Math.min(0.7, (bucket.count / max)) * sign;
        return (
          <div className="wb-heat__col" key={bucket.label}>
            <HeatCell size={22} intensity={intensity} ariaLabel={`${bucket.label} ${bucket.count} 家`} />
            <span className={`wb-heat__count is-${sign < 0 ? 'down' : sign > 0 ? 'up' : 'flat'}`}>{bucket.count}</span>
            <span className="wb-heat__label">{bucket.label}</span>
          </div>
        );
      })}
    </div>
  );
}

/** 「总览」：个人工作台。先看自己的东西（自选异动、进行中的研究、最新成果），再去各模块。 */
const WorkbenchPage = () => {
  const navigate = useNavigate();
  const { user } = useUserAccess();
  const isMobile = useIsMobile();
  const [dashboard, setDashboard] = useState<HomeDashboard | null>(null);
  const [projects, setProjects] = useState<IndustryResearchProject[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [tab, setTab] = useState<WorkbenchTab>('overview');
  const tabsRef = useRef<HTMLDivElement | null>(null);

  const load = useCallback(async (force = false) => {
    const readDashboard = async () => homeDashboardApi.get(force);
    const [dash, research] = await Promise.allSettled([
      readDashboard(),
      industryResearchApi.projects({ limit: 8, force }),
    ]);
    if (dash.status === 'fulfilled') setDashboard(dash.value);
    // 缓存里可能是空的旧快照：清掉再取一次，避免工作台长期空白。
    if (dash.status === 'rejected' || !(dash.value?.cnIndices?.length)) {
      invalidateCachedQueries('home:');
      const retry = await Promise.allSettled([homeDashboardApi.get(false)]);
      if (retry[0].status === 'fulfilled') setDashboard(retry[0].value);
    }
    if (research.status === 'fulfilled') setProjects(research.value.items ?? []);
    // 行情与自选来自大盘接口，研究任务来自研究接口；任一侧失败都要说清楚，
    // 否则页面会安静地少一块内容。
    if (dash.status === 'rejected' && research.status === 'rejected') setError('暂时连不上服务，稍后会自动重试。');
    else if (dash.status === 'rejected') setError('行情与自选暂时没有读到，稍后会自动重试。');
    else if (research.status === 'rejected') setError('研究任务列表暂时没有读到，稍后会自动重试。');
    else setError('');
    setLoading(false);
  }, []);

  useEffect(() => {
    document.title = '总览 - 乐子乌智能投研平台';
    const timer = window.setTimeout(() => { void load(); }, 0);
    return () => window.clearTimeout(timer);
  }, [load]);
  usePageActivationRefresh(() => void load(false));

  const movers = useMemo(() => (
    [...(dashboard?.watchlist ?? [])]
      .filter((card) => card.latestQuote?.changePercent != null)
      .sort((a, b) => Math.abs(b.latestQuote?.changePercent ?? 0) - Math.abs(a.latestQuote?.changePercent ?? 0))
      .slice(0, 4)
  ), [dashboard]);

  const running = projects.filter((item) => RUNNING.has(item.status));
  const finished = projects.filter((item) => !RUNNING.has(item.status)).slice(0, 3);
  const leadIndex = dashboard?.cnIndices?.[0];
  /** 标题行只显示时刻：日期已经在同一行的前面出现过，秒级时间戳是数据管道细节。 */
  const marketClock = () => {
    const raw = dashboard?.marketTime ?? leadIndex?.tradeDate ?? '';
    const clock = raw.match(/(\d{2}):(\d{2})/);
    return clock ? `${clock[1]}:${clock[2]}` : '';
  };
  // 快讯列表 / 正文弹层要用完整事件（summary、url、sourceName、eventAt），
  // 不再拍扁成 {id,title,time}，否则抽屉里没有正文可展示。
  const news = useMemo(() => {
    const seen = new Set<number>();
    const merged: MonitorEvent[] = [];
    for (const item of [...(dashboard?.pinnedNews ?? []), ...(dashboard?.latestNews ?? [])]) {
      if (!Number.isInteger(item.id) || item.id <= 0 || seen.has(item.id)) continue;
      seen.add(item.id);
      merged.push(item);
    }
    return merged;
  }, [dashboard]);
  const [newsSheet, setNewsSheet] = useState<NewsSheetView | null>(null);
  const sectors = dashboard?.sectorDistribution?.available ? {
    leaders: (dashboard.sectorDistribution.leaders ?? []).slice(0, 4),
    laggards: (dashboard.sectorDistribution.laggards ?? []).slice(0, 4),
  } : null;
  const breadth = dashboard?.breadth?.available ? dashboard.breadth : null;

  const shortcuts = [
    { label: '投研团队', icon: MessagesSquare, to: '/meeting' },
    { label: 'AI 问股', icon: MessageSquareQuote, to: '/chat' },
    { label: '深度研究', icon: Network, to: '/industry-research' },
    { label: '题材图谱', icon: Orbit, to: '/concept-themes' },
    { label: '纪要录音', icon: AudioLines, to: '/essay-radar' },
    { label: '任务中心', icon: ClipboardCheck, to: '/tasks' },
    { label: '数据下载', icon: FileText, to: '/data-acquisition' },
    { label: '选股', icon: Search, to: '/screening' },
  ];

  const switchTab = (key: string) => {
    setTab(key as WorkbenchTab);
    // 切 Tab 滚回内容顶部（§10）。jsdom 没有实现 scrollIntoView，保留可选调用。
    tabsRef.current?.scrollIntoView?.({ block: 'start', behavior: 'auto' });
  };
  const openNews = (eventId: number, fromList: boolean) => setNewsSheet({ kind: 'detail', eventId, fromList });

  const newsList = (limit: number) => news.slice(0, limit).map((item) => (
    <button
      key={item.id}
      type="button"
      className="workbench-news"
      aria-label={`查看快讯全文：${newsHeadline(item.title) || stripHtmlText(item.summary) || '（无标题快讯）'}`}
      onClick={() => openNews(item.id, false)}
    >
      <span className="workbench-news__title">{newsHeadline(item.title) || stripHtmlText(item.summary) || '（无标题快讯）'}</span>
      <span className="workbench-news__time">{newsClock(item.eventAt)}</span>
    </button>
  ));

  const sheet = (
    <NewsSheet
      view={newsSheet}
      events={news}
      loading={loading && !dashboard}
      error={dashboard ? null : error || null}
      onRetry={() => { void load(false); }}
      onSelect={(eventId, fromList) => setNewsSheet({ kind: 'detail', eventId, fromList })}
      onBack={() => setNewsSheet({ kind: 'list' })}
      onClose={() => setNewsSheet(null)}
    />
  );

  const errorNotice = error ? <p className="workbench__error" role="status">{error}</p> : null;

  /* ------------------------------------------------------------------ 手机端
   * 手机端保持原来的单列流式版式（App Bar 已有「总览」大标题），不做 Tab 拆分。
   * 桌面端改动不落在这里，避免影响手机端既有交互与测试。 */
  if (isMobile) {
    return (
      <AppPage className="workbench">
        <>
        <section className="workbench__hello">
          <PixelCrew className="workbench__crew" />
          <div className="workbench__hello-text">
            <h1 data-type-free className="workbench__greeting">{greeting(new Date())}{user?.name ? `，${user.name}` : ''}</h1>
            <small>{new Date().toLocaleDateString('zh-CN', { month: 'long', day: 'numeric', weekday: 'long' })}</small>
          </div>
          {leadIndex ? (
            <button type="button" className="workbench__pulse" onClick={() => navigate('/app')}>
              <span>{leadIndex.name}</span>
              <b className={`is-${toneOf(leadIndex.changePct)}`}>{price(leadIndex.close)}</b>
              <em className={`is-${toneOf(leadIndex.changePct)}`}>{pct(leadIndex.changePct)}</em>
              {breadth ? <small>涨 {breadth.up} · 跌 {breadth.down}</small> : null}
            </button>
          ) : null}
        </section>

        {errorNotice}

        <div className="workbench__grid">

        <div className="workbench__col workbench__col--aside">
        <nav className="workbench__shortcuts" aria-label="常用入口">
          {shortcuts.map(({ label, icon: Icon, to }) => (
            <button key={to} type="button" onClick={() => navigate(to)}>
              <span><Icon aria-hidden="true" /></span>
              {label}
            </button>
          ))}
        </nav>

        {finished.length ? (
          <MobileGroup title="最新成果" action={<button type="button" onClick={() => navigate('/industry-research')}>全部</button>}>
            {finished.map((item) => (
              <MobileListRow
                key={item.projectId}
                leading={<FileText aria-hidden="true" />}
                title={item.topic}
                subtitle={`${item.researchType === 'company' ? '公司研究' : '行业研究'} · ${STATUS_TEXT[item.status]}`}
                onClick={() => navigate(`/industry-research?project=${encodeURIComponent(item.projectId)}`)}
              />
            ))}
          </MobileGroup>
        ) : null}
        <MobileGroup title="最近快讯" action={<button type="button" onClick={() => setNewsSheet({ kind: 'list' })}>更多</button>}>
          {news.length ? newsList(5) : (
            <div className="mobile-row"><span className="mobile-row__main"><strong>暂无快讯</strong></span></div>
          )}
        </MobileGroup>
        </div>

        <div className="workbench__col workbench__col--main">
        <MobileGroup title="自选异动" action={<button type="button" onClick={() => navigate('/super-watchlist')}>全部</button>}>
          {loading && !dashboard ? <MobileSkeleton rows={3} /> : movers.length ? movers.map((card) => (
            <MobileListRow
              key={card.symbol}
              title={card.name}
              subtitle={card.highPriorityCount ? `${card.highPriorityCount} 条重要情报` : card.symbol}
              value={price(card.latestQuote?.currentPrice)}
              valueNote={pct(card.latestQuote?.changePercent)}
              tone={toneOf(card.latestQuote?.changePercent)}
              onClick={() => navigate(`/investment-monitor/feed?symbol=${encodeURIComponent(card.symbol)}`)}
            />
          )) : (
            <MobileListRow title="添加自选股" onClick={() => navigate('/super-watchlist')} />
          )}
        </MobileGroup>

        <MobileGroup title="进行中" action={<button type="button" onClick={() => navigate('/tasks')}>任务中心</button>}>
          {loading && !projects.length ? <MobileSkeleton rows={2} /> : running.length ? running.slice(0, 3).map((item) => (
            <button
              key={item.projectId}
              type="button"
              className="workbench-task"
              onClick={() => navigate(`/industry-research?project=${encodeURIComponent(item.projectId)}`)}
            >
              <span className="workbench-task__head">
                <strong>{item.topic}</strong>
                <em>{STATUS_TEXT[item.status]}</em>
              </span>
              <span className="workbench-task__bar" aria-hidden="true">
                <i style={{ width: `${Math.max(4, Math.min(100, item.progress ?? 0))}%` }} />
              </span>
              {item.stage ? <small>{item.stage}</small> : null}
            </button>
          )) : (
            <MobileListRow title="开始一项研究" onClick={() => navigate('/industry-research')} />
          )}
        </MobileGroup>

        {breadth ? (
          <MobileGroup title="市场广度">
            <div className="workbench-breadth">
              <div className="workbench-breadth__bar" aria-hidden="true">
                <span style={{ width: `${(breadth.up / Math.max(1, breadth.total)) * 100}%`, background: 'var(--up)' }} />
                <span style={{ width: `${(breadth.down / Math.max(1, breadth.total)) * 100}%`, background: 'var(--down)' }} />
                <span style={{ flex: 1, background: 'var(--bg-3)' }} />
              </div>
              <div className="workbench-breadth__legend">
                <span className="is-up">涨 {breadth.up}</span>
                <span className="is-down">跌 {breadth.down}</span>
                <span>平 {breadth.flat}</span>
                <span className="workbench-breadth__total">共 {breadth.total} 只</span>
              </div>
            </div>
          </MobileGroup>
        ) : null}

        {sectors ? (
          <MobileGroup title="行业涨跌">
            <div className="workbench-sector">
              <div>
                <p className="is-up">领涨</p>
                {sectors.leaders.map((item) => (
                  <span key={`u-${item.name}`}><b>{item.name}</b><em className="is-up">{item.changePct > 0 ? '+' : ''}{item.changePct.toFixed(2)}%</em></span>
                ))}
              </div>
              <div>
                <p className="is-down">领跌</p>
                {sectors.laggards.map((item) => (
                  <span key={`d-${item.name}`}><b>{item.name}</b><em className="is-down">{item.changePct > 0 ? '+' : ''}{item.changePct.toFixed(2)}%</em></span>
                ))}
              </div>
            </div>
          </MobileGroup>
        ) : null}

        </div>
        </div>

        {sheet}
        </>
      </AppPage>
    );
  }

  /* ------------------------------------------------------------------ 桌面端
   * 一屏只讲一件事：市场概览 / 我的关注 / 快讯 三个互斥视图，默认只渲染当前 Tab。 */
  const indexCards: MarketIndexCard[] = dashboard?.cnIndices ?? [];
  const indexMetrics = indexCards.slice(0, 8).map((item) => ({
    label: item.name,
    value: price(item.close),
    delta: pct(item.changePct),
    trend: toneOf(item.changePct),
    viz: <Sparkline values={sparkValues(item.history)} width={64} height={18} ariaLabel={`${item.name}近 20 日走势`} />,
  }));
  const sectorRows = sectors
    ? [...sectors.leaders, ...sectors.laggards].sort((a, b) => b.changePct - a.changePct)
    : [];
  const sectorMax = Math.max(0.01, ...sectorRows.map((item) => Math.abs(item.changePct ?? 0)));
  const moversTotal = dashboard?.watchlist?.length ?? 0;
  const tabs: PageTabItem[] = [
    { key: 'overview', label: '市场概览' },
    { key: 'focus', label: '我的关注', badge: moversTotal || undefined },
    { key: 'news', label: '快讯', badge: news.length || undefined },
  ];

  return (
    <AppPage className="workbench workbench--desktop">
      <>
      <header className="wb-head">
        <div className="wb-head__text">
          <h1 data-type-free className="wb-head__greeting">{greeting(new Date())}{user?.name ? `，${user.name}` : ''}</h1>
          <p className="wb-head__meta">
            {new Date().toLocaleDateString('zh-CN', { month: 'long', day: 'numeric', weekday: 'long' })}
            {/* 日期已在前面，这里只补「几点」；秒级时间戳是数据管道细节，不进标题行。 */}
            {marketClock() ? ` · ${marketClock()}` : ''}
          </p>
        </div>
        {leadIndex ? (
          <div className="wb-head__lead">
            <span className="wb-head__lead-label">{leadIndex.name}</span>
            <b className={`wb-num is-${toneOf(leadIndex.changePct)}`}>{price(leadIndex.close)}</b>
            <em className={`is-${toneOf(leadIndex.changePct)}`}>{pct(leadIndex.changePct)}</em>
            <Sparkline values={sparkValues(leadIndex.history)} width={96} height={20} ariaLabel={`${leadIndex.name}近 20 日走势`} />
            <button type="button" className="wb-link-btn" onClick={() => navigate('/app')}>行情详情</button>
          </div>
        ) : null}
      </header>

      {errorNotice}

      <div ref={tabsRef}>
        <PageTabs items={tabs} value={tab} onChange={switchTab} ariaLabel="总览视图切换" />
      </div>

      {tab === 'overview' ? (
        <div className="wb-stack">
          <Panel
            title="主要指数"
            meta={loading && !dashboard ? '读取中…' : `${indexMetrics.length} 个`}
            action={<button type="button" className="wb-link-btn" onClick={() => navigate('/app')}>全部行情</button>}
          >
            {indexMetrics.length
              ? <MetricRow columns={4} items={indexMetrics} className="wb-index-row" />
              : <p className="wb-empty" role="status">{loading ? '正在读取指数…' : '指数数据暂缺'}</p>}
          </Panel>

          <Panel
            title="市场广度"
            meta={breadth ? `共 ${breadth.total} 只 · 涨停 ${breadth.limitUp} · 跌停 ${breadth.limitDown}` : '等待数据'}
          >
            {breadth ? (
              <>
                <div className="wb-breadth">
                  <ShareBar
                    height={6}
                    ariaLabel={`上涨 ${breadth.up} 家，平盘 ${breadth.flat} 家，下跌 ${breadth.down} 家`}
                    segments={[
                      { key: 'up', value: breadth.up, color: 'var(--up)', label: '上涨' },
                      { key: 'flat', value: breadth.flat, color: 'var(--flat)', label: '平盘' },
                      { key: 'down', value: breadth.down, color: 'var(--down)', label: '下跌' },
                    ]}
                  />
                  <div className="wb-legend">
                    <span className="is-up">涨 {breadth.up}</span>
                    <span>平 {breadth.flat}</span>
                    <span className="is-down">跌 {breadth.down}</span>
                  </div>
                </div>
                <DistributionMatrix buckets={dashboard?.breadth.distribution ?? []} ariaLabel="个股涨跌幅分布" />
              </>
            ) : <p className="wb-empty" role="status">{loading ? '正在读取市场广度…' : '市场广度数据暂缺'}</p>}
          </Panel>

          <Panel
            title="行业强弱"
            meta={sectors ? `领涨 ${sectors.leaders.length} · 领跌 ${sectors.laggards.length}` : '等待数据'}
          >
            {sectorRows.length ? (
              <>
                <div className="wb-rank">
                  {/* 没有表头时「左右两条数字」无法解读；表头只做列名，不重复统计。 */}
                  <div className="wb-rank__head" aria-hidden="true">
                    <span>行业</span>
                    <span>强弱（中线为零轴）</span>
                    <b>涨跌幅</b>
                  </div>
                  {sectorRows.map((item) => (
                    <div className="wb-rank__row" key={`${item.name}-${item.changePct}`}>
                      <span className="wb-rank__name">{item.name}</span>
                      <span className={`wb-rank__bar ${item.changePct < 0 ? 'is-down' : 'is-up'}`}>
                        <BarMeter
                          value={item.changePct ?? 0}
                          max={sectorMax}
                          height={4}
                          baseline="center"
                          ariaLabel={`${item.name} ${signed(item.changePct)}`}
                        />
                      </span>
                      <b className={`wb-num is-${toneOf(item.changePct)}`}>{signed(item.changePct)}</b>
                    </div>
                  ))}
                </div>
                <DistributionMatrix
                  buckets={dashboard?.sectorDistribution.distribution ?? []}
                  ariaLabel="行业涨跌幅分布"
                />
              </>
            ) : <p className="wb-empty" role="status">{loading ? '正在读取行业强弱…' : '行业数据暂缺'}</p>}
          </Panel>
        </div>
      ) : null}

      {tab === 'focus' ? (
        <div className="wb-stack">
          <Panel
            title="自选异动"
            meta={moversTotal ? `${moversTotal} 只自选 · 按振幅排序` : undefined}
            action={<button type="button" className="wb-link-btn" onClick={() => navigate('/super-watchlist')}>全部自选</button>}
          >
            {loading && !dashboard ? <p className="wb-empty" role="status">正在读取自选…</p> : movers.length ? (
              <div className="wb-watch">
                {movers.map((card) => {
                  const bull = Math.max(0, card.opportunityScore ?? 0);
                  const bear = Math.max(0, card.riskScore ?? 0);
                  return (
                    <button
                      key={card.symbol}
                      type="button"
                      className="wb-watch__row"
                      onClick={() => navigate(`/investment-monitor/feed?symbol=${encodeURIComponent(card.symbol)}`)}
                    >
                      <span className="wb-watch__name">
                        <strong>{card.name}</strong>
                        <small>{card.symbol}{card.highPriorityCount ? ` · ${card.highPriorityCount} 条重要情报` : ''}</small>
                      </span>
                      <Sparkline values={sparkValues(card.history)} width={80} height={18} ariaLabel={`${card.name}近期走势`} />
                      <span className="wb-watch__meter">
                        <ShareBar
                          height={4}
                          segments={[
                            { key: 'opp', value: bull, color: 'var(--chart-1)', label: '机会' },
                            { key: 'risk', value: bear, color: 'var(--chart-4)', label: '风险' },
                          ]}
                          ariaLabel={`${card.name}机会 ${bull} 风险 ${bear}`}
                        />
                        <small>机会 {bull} · 风险 {bear}</small>
                      </span>
                      <b className={`wb-num is-${toneOf(card.latestQuote?.changePercent)}`}>{price(card.latestQuote?.currentPrice)}</b>
                      <em className={`wb-num is-${toneOf(card.latestQuote?.changePercent)}`}>{pct(card.latestQuote?.changePercent)}</em>
                    </button>
                  );
                })}
              </div>
            ) : <p className="wb-empty">还没有自选股，先添加一只即可看到走势与情报结构。</p>}
          </Panel>

          <Panel
            title="进行中研究"
            meta={running.length ? `${running.length} 项进行中` : undefined}
            action={<button type="button" className="wb-link-btn" onClick={() => navigate('/tasks')}>任务中心</button>}
          >
            {loading && !projects.length ? <p className="wb-empty" role="status">正在读取研究任务…</p> : running.length ? (
              <div className="wb-research">
                {running.slice(0, 4).map((item) => (
                  <button
                    key={item.projectId}
                    type="button"
                    className="wb-research__row"
                    onClick={() => navigate(`/industry-research?project=${encodeURIComponent(item.projectId)}`)}
                  >
                    <span className="wb-research__head">
                      <strong>{item.topic}</strong>
                      <em>{STATUS_TEXT[item.status]}</em>
                    </span>
                    <BarMeter value={Math.max(0, Math.min(100, item.progress ?? 0))} max={100} height={4} tone="accent" ariaLabel={`研究进度 ${item.progress ?? 0}%`} />
                    <small>{item.stage ? `${item.stage} · ` : ''}{Math.round(item.progress ?? 0)}%</small>
                  </button>
                ))}
              </div>
            ) : <p className="wb-empty">没有进行中的研究，去开一项新的深度研究。</p>}
          </Panel>

          <Panel title="最新成果" action={<button type="button" className="wb-link-btn" onClick={() => navigate('/industry-research')}>全部成果</button>}>
            {finished.length ? (
              <div className="wb-research">
                {finished.map((item) => (
                  <button
                    key={item.projectId}
                    type="button"
                    className="wb-research__row"
                    onClick={() => navigate(`/industry-research?project=${encodeURIComponent(item.projectId)}`)}
                  >
                    <span className="wb-research__head">
                      <strong>{item.topic}</strong>
                      <em>{item.researchType === 'company' ? '公司研究' : '行业研究'} · {STATUS_TEXT[item.status]}</em>
                    </span>
                  </button>
                ))}
              </div>
            ) : <p className="wb-empty">还没有完成的研究成果。</p>}
          </Panel>

          <Panel title="常用入口" className="wb-panel--tools">
            <nav className="wb-tools" aria-label="常用入口">
              {shortcuts.map(({ label, icon: Icon, to }) => (
                <button key={to} type="button" onClick={() => navigate(to)}>
                  <Icon aria-hidden="true" size={16} />
                  {label}
                </button>
              ))}
            </nav>
          </Panel>
        </div>
      ) : null}

      {tab === 'news' ? (
        <Panel
          title="最近快讯"
          meta={news.length ? `${news.length} 条 · 点击就地看正文` : undefined}
          action={<button type="button" className="wb-link-btn" onClick={() => setNewsSheet({ kind: 'list' })}>更多</button>}
        >
          {loading && !dashboard ? <p className="wb-empty" role="status">正在读取快讯…</p> : news.length ? (
            <div className="wb-news-list">{newsList(12)}</div>
          ) : <p className="wb-empty">暂无快讯，财经快讯同步后会自动出现在这里。</p>}
        </Panel>
      ) : null}

      {sheet}
      </>
    </AppPage>
  );
};
export default WorkbenchPage;
