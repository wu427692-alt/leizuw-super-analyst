import { useCallback, useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  AudioLines, ClipboardCheck, FileText, MessageSquareQuote, MessagesSquare, Network, Orbit, Search,
} from 'lucide-react';
import { homeDashboardApi } from '../api/homeDashboard';
import { industryResearchApi } from '../api/industryResearch';
import { invalidateCachedQueries } from '../api/requestCache';
import { AppPage } from '../components/common';
import { MobileGroup, MobileListRow, MobileSkeleton } from '../components/mobile/MobilePrimitives';
import { useUserAccess } from '../contexts/UserAccessContext';
import { usePageActivationRefresh } from '../hooks/usePageActivationRefresh';
import type { HomeDashboard } from '../types/homeDashboard';
import type { IndustryResearchProject } from '../types/industryResearch';
import './WorkbenchPage.css';

type Tone = 'up' | 'down' | 'flat';

const toneOf = (value?: number | null): Tone => ((value ?? 0) > 0 ? 'up' : (value ?? 0) < 0 ? 'down' : 'flat');
const pct = (value?: number | null) => (
  value == null || !Number.isFinite(value) ? '—' : `${value > 0 ? '+' : ''}${value.toFixed(2)}%`
);
const price = (value?: number | null) => (
  value == null || !Number.isFinite(value) ? '—' : value.toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
);

const RUNNING = new Set<IndustryResearchProject['status']>(['queued', 'collecting', 'analyzing']);
const STATUS_TEXT: Record<IndustryResearchProject['status'], string> = {
  queued: '排队中', collecting: '收集资料', analyzing: '分析中', completed: '已完成', limited: '资料有限', failed: '失败',
};

function greeting(now: Date) {
  const hour = now.getHours();
  if (hour < 6) return '夜深了';
  if (hour < 11) return '早上好';
  if (hour < 14) return '中午好';
  if (hour < 18) return '下午好';
  return '晚上好';
}

/** 「总览」：个人工作台。先看自己的东西（自选异动、进行中的研究、最新成果），再去各模块。 */
const WorkbenchPage = () => {
  const navigate = useNavigate();
  const { user } = useUserAccess();
  const [dashboard, setDashboard] = useState<HomeDashboard | null>(null);
  const [projects, setProjects] = useState<IndustryResearchProject[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

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
  const news = [
    ...(dashboard?.pinnedNews ?? []),
    ...(dashboard?.latestNews ?? []),
  ].slice(0, 5).map((item) => ({
    id: String(item.id),
    title: (item.title ?? '').replace(/<[^>]*>/g, '').slice(0, 42),
    time: item.eventAt ? new Date(item.eventAt).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' }) : '',
  }));
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

  return (
    <AppPage className="workbench">
      <section className="workbench__hello">
        <div className="workbench__hello-text">
          <p>{greeting(new Date())}{user?.name ? `，${user.name}` : ''}</p>
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

      {error ? <p className="workbench__error" role="status">{error}</p> : null}

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
      <MobileGroup title="最近快讯" action={<button type="button" onClick={() => navigate('/investment-monitor/feed?channel=news', { state: { returnTo: '/home' } })}>更多</button>}>
        {news.length ? news.slice(0, 5).map((item) => (
          <button
            key={item.id}
            type="button"
            className="workbench-news"
            onClick={() => navigate('/investment-monitor/feed?channel=news', { state: { returnTo: '/home' } })}
          >
            <span className="workbench-news__title">{item.title}</span>
            <span className="workbench-news__time">{item.time}</span>
          </button>
        )) : (
          <div className="mobile-row"><span className="mobile-row__main"><strong>暂无快讯</strong><small>新闻同步后自动显示</small></span></div>
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
          <MobileListRow title="还没有自选股" subtitle="添加后这里显示涨跌最大的几只" onClick={() => navigate('/super-watchlist')} />
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
          <MobileListRow title="没有进行中的研究" subtitle="在深度研究里输入一个公司或行业即可开始" onClick={() => navigate('/industry-research')} />
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

      <p className="workbench__disclaimer">数据仅供参考，不构成投资建议</p>
    </AppPage>
  );
};

export default WorkbenchPage;
