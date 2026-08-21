import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  Archive, ArrowRight, BarChart3, Brain, Database, ExternalLink, FileText, GitBranch, Image,
  ListFilter, RefreshCw, Search, Settings2, ShieldAlert, Sparkles, TrendingUp,
} from 'lucide-react';
import { Link, useLocation, useNavigate, useSearchParams } from 'react-router-dom';
import {
  Bar, CartesianGrid, ComposedChart, Line, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from 'recharts';
import { essayRadarApi } from '../api/essayRadar';
import { AppPage, Badge, Drawer, EmptyState } from '../components/common';
import { useDebouncedValue } from '../hooks/useDebouncedValue';
import type {
  EssayAnalysis, EssayAnalysisList, EssayDailyReportList, EssayDashboard, EssayDeepInsights, EssayInsights,
  EssayHistoricalBacklog, EssayStatus, EssayWordCloud,
} from '../types/essayRadar';
import './EssayRadarPage.css';

type RadarView = 'overview' | 'atlas' | 'feed' | 'trends' | 'reports' | 'system';

const VIEW_META: Array<{ view: RadarView; path: string; label: string; icon: typeof Sparkles }> = [
  { view: 'overview', path: '/essay-radar', label: '今日研判', icon: Sparkles },
  { view: 'atlas', path: '/essay-radar/insights', label: '洞察图谱', icon: GitBranch },
  { view: 'feed', path: '/essay-radar/feed', label: '信息流', icon: ListFilter },
  { view: 'trends', path: '/essay-radar/trends', label: '趋势追踪', icon: TrendingUp },
  { view: 'reports', path: '/essay-radar/reports', label: '每日报告', icon: Brain },
  { view: 'system', path: '/essay-radar/system', label: '数据管理', icon: Settings2 },
];

const SENTIMENT_LABELS: Record<string, string> = {
  bullish: '看多', bearish: '看空', neutral: '中性', mixed: '分歧',
};
const CATEGORY_LABELS: Record<string, string> = {
  company_research: '公司调研', broker_view: '券商观点', industry_chain: '产业链',
  macro_policy: '宏观政策', market_flow: '资金市场', event_catalyst: '事件催化',
  earnings: '业绩', risk_warning: '风险预警', rumor: '传闻', other: '其他',
};
const ANALYSIS_STATUS_LABELS: Record<string, string> = {
  not_queued: '未分析', pending: '排队中', processing: '分析中', failed: '分析失败', completed: '已分析',
};
const FEED_PAGE_CACHE_LIMIT = 80;

function radarView(pathname: string): RadarView {
  if (pathname.endsWith('/insights')) return 'atlas';
  if (pathname.endsWith('/feed')) return 'feed';
  if (pathname.endsWith('/trends')) return 'trends';
  if (pathname.endsWith('/reports')) return 'reports';
  if (pathname.endsWith('/system')) return 'system';
  return 'overview';
}

function formatTime(value?: string | null, withYear = false) {
  if (!value) return '—';
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;
  return parsed.toLocaleString('zh-CN', {
    ...(withYear ? { year: 'numeric' as const } : {}),
    month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit',
  });
}

function formatDate(value?: string | null) {
  if (!value) return '—';
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;
  return parsed.toLocaleDateString('zh-CN', { year: 'numeric', month: '2-digit', day: '2-digit' });
}

function errorText(value: unknown, fallback: string) {
  return value instanceof Error ? value.message : fallback;
}

function sentimentVariant(value?: string | null) {
  if (value === 'bullish') return 'success' as const;
  if (value === 'bearish') return 'danger' as const;
  if (value === 'mixed') return 'warning' as const;
  return 'default' as const;
}

function RadarNavigation({ active }: { active: RadarView }) {
  return (
    <nav className="essay-tabs" aria-label="小作文工作台页面">
      {VIEW_META.map(({ view, path, label, icon: Icon }) => (
        <Link key={view} to={path} className={active === view ? 'is-active' : ''}>
          <Icon className="h-4 w-4" />
          <span>{label}</span>
        </Link>
      ))}
    </nav>
  );
}

function Metric({ label, value, note, tone = 'plain' }: { label: string; value: string | number; note?: string; tone?: 'plain' | 'signal' | 'danger' }) {
  return (
    <div className={`essay-metric essay-metric-${tone}`}>
      <span>{label}</span>
      <strong>{value}</strong>
      {note ? <small>{note}</small> : null}
    </div>
  );
}

function DeepInsightsView({ data, modelComparison, onOpen, onFilter }: {
  data: EssayDeepInsights | null;
  modelComparison?: EssayInsights['modelComparison'];
  onOpen: (item: EssayAnalysis) => void;
  onFilter: (term: string) => void;
}) {
  if (!data) return null;
  const layers = [
    { key: 'sources' as const, label: '来源', note: '知识星球' },
    { key: 'themes' as const, label: '主题', note: 'AI 提取' },
    { key: 'stocks' as const, label: '个股', note: '明确提及' },
    { key: 'signals' as const, label: '催化 / 风险', note: '可验证线索' },
  ];
  const relationCount = (stage: string, key: string) => data.layers.edges
    .filter((edge) => (edge.fromStage === stage && edge.from === key) || (edge.toStage === stage && edge.to === key))
    .reduce((sum, edge) => sum + edge.count, 0);
  const heatMax = Math.max(...data.themeHeatmap.items.flatMap((item) => item.points.map((point) => point.count)), 1);
  const funnelMax = Math.max(data.evidenceFunnel[0]?.count ?? 0, 1);

  return (
    <div className="essay-view essay-atlas-view">
      <section className="essay-atlas-summary" aria-label="30日语料洞察摘要">
        <Metric label="已分析语料" value={data.summary.analyzedCount.toLocaleString()} note={`${data.windowDays} 日窗口`} tone="signal" />
        <Metric label="真实来源" value={data.summary.sourceCount} note="按知识星球去重" />
        <Metric label="主题 / 个股" value={`${data.summary.themeCount} / ${data.summary.stockCount}`} note="AI结构化实体" />
        <Metric label="证据覆盖" value={`${data.summary.evidenceCoveragePercent}%`} note="含原文证据链" />
        <Metric label="高增量" value={data.summary.highNoveltyCount} note="增量分 ≥ 70" />
        <Metric label="多空分歧" value={data.summary.divergenceCount} note="同一标的双向观点" tone="danger" />
      </section>

      <div className="essay-atlas-primary">
        <section className="essay-panel essay-atlas-pulse">
          <div className="essay-panel-head"><div><span>近 {data.pulse.length} 日 · 按纪要创建时间</span><h2>信息脉冲与情绪结构</h2></div><strong>最新 {formatDate(data.latestDataAt)}</strong></div>
          <div className="essay-pulse-legend"><span className="is-bull">看多</span><span className="is-bear">看空</span><span className="is-neutral">中性</span><span className="is-mixed">分歧</span></div>
          <div className="essay-atlas-chart"><ResponsiveContainer width="100%" height="100%" minWidth={0} initialDimension={{ width: 620, height: 270 }}><ComposedChart data={data.pulse}><CartesianGrid vertical={false} stroke="rgba(148,163,184,.14)" /><XAxis dataKey="date" tickFormatter={(value) => String(value).slice(5)} tick={{ fontSize: 9 }} /><YAxis allowDecimals={false} tick={{ fontSize: 9 }} /><Tooltip contentStyle={{ background: '#0b0c0a', border: '1px solid rgba(198,255,74,.25)', fontSize: 10 }} labelFormatter={(value) => `日期 ${value}`} /><Bar stackId="sentiment" dataKey="bullish" name="看多" fill="#f87171" /><Bar stackId="sentiment" dataKey="bearish" name="看空" fill="#34d399" /><Bar stackId="sentiment" dataKey="neutral" name="中性" fill="#64748b" /><Bar stackId="sentiment" dataKey="mixed" name="分歧" fill="#fbbf24" /><Line type="monotone" dataKey="total" name="总量" stroke="#c6ff4a" strokeWidth={2} dot={false} /></ComposedChart></ResponsiveContainer></div>
        </section>

        <section className="essay-panel essay-atlas-chain">
          <div className="essay-panel-head"><div><span>仅统计同一篇原文中的真实共现</span><h2>来源 → 主题 → 个股 → 线索</h2></div><strong>{data.layers.edges.length} 条关系</strong></div>
          <div className="essay-chain-grid">
            {layers.map((layer, layerIndex) => {
              const nodes = data.layers[layer.key];
              const max = Math.max(...nodes.map((node) => node.count), 1);
              return <div className="essay-chain-stage" key={layer.key}>
                <header><span>0{layerIndex + 1}</span><div><strong>{layer.label}</strong><small>{layer.note}</small></div>{layerIndex < layers.length - 1 ? <ArrowRight /> : null}</header>
                <div>{nodes.map((node) => <button key={node.key} type="button" onClick={() => onFilter(node.label)} title={`检索原文：${node.label}`}>
                  <span>{node.kind ? <i className={`is-${node.kind}`} /> : null}{node.label}</span>
                  <b><i style={{ width: `${Math.max(5, node.count / max * 100)}%` }} /></b>
                  <small>{node.count} 篇 · {relationCount(layer.key, node.key)} 次关联</small>
                </button>)}</div>
              </div>;
            })}
          </div>
        </section>
      </div>

      <div className="essay-atlas-secondary">
        <section className="essay-panel essay-heatmap-panel">
          <div className="essay-panel-head"><div><span>颜色越亮，当日提及越集中</span><h2>主题热力迁移</h2></div><strong>{data.themeHeatmap.dates.length} 日</strong></div>
          <div className="essay-heatmap-axis"><span />{data.themeHeatmap.dates.map((day, index) => <small key={day} className={index % Math.ceil(data.themeHeatmap.dates.length / 5) ? 'is-muted' : ''}>{day.slice(5)}</small>)}</div>
          <div className="essay-heatmap">{data.themeHeatmap.items.map((item) => <div key={item.name}><button type="button" onClick={() => onFilter(item.name)}><strong>{item.name}</strong><small>{item.total}</small></button>{item.points.map((point) => <i key={point.date} title={`${point.date} · ${point.count} 篇`} className={point.count ? 'has-value' : ''} style={{ opacity: point.count ? .2 + point.count / heatMax * .8 : .08 }} />)}</div>)}</div>
        </section>

        <section className="essay-panel essay-momentum-panel">
          <div className="essay-panel-head"><div><span>本周 vs 上周 · 不是价格涨跌</span><h2>个股讨论动量</h2></div><strong>按提及增量排序</strong></div>
          <div className="essay-momentum-table"><header><span>标的</span><span>本周</span><span>上周</span><span>变化</span><span>多 / 空</span></header>{data.stockMomentum.slice(0, 8).map((stock) => <button key={stock.tsCode || stock.name} type="button" onClick={() => onFilter(stock.name)}><span><strong>{stock.name}</strong><small>{stock.tsCode || '未匹配代码'}</small></span><b>{stock.currentCount}</b><span>{stock.previousCount}</span><strong className={stock.change > 0 ? 'is-up' : stock.change < 0 ? 'is-down' : ''}>{stock.change > 0 ? '+' : ''}{stock.change}</strong><small><i className="is-bull">{stock.bullish}</i> / <i className="is-bear">{stock.bearish}</i></small></button>)}</div>
        </section>
      </div>

      <div className="essay-atlas-tertiary">
        <section className="essay-panel essay-verification-panel">
          <div className="essay-panel-head"><div><span>高增量、低置信或内部矛盾</span><h2>优先核验队列</h2></div><strong>{data.verificationQueue.length} 条</strong></div>
          <div className="essay-verification-list">{data.verificationQueue.slice(0, 6).map((item) => <button key={item.topicId} type="button" onClick={() => onOpen(item)}><strong>{item.noveltyScore ?? 0}</strong><span><b>{item.note.title || item.summary || '无标题纪要'}</b><small>{item.note.groupName} · 置信 {Math.round((item.confidenceScore ?? 0) * 100)}%</small></span><ArrowRight /></button>)}</div>
        </section>

        <section className="essay-panel essay-model-panel">
          <div className="essay-panel-head"><div><span>{modelComparison?.reportDate || '最近报告日'}</span><h2>模型共识与语料分歧</h2></div><strong>{modelComparison?.reports.length ?? 0} 个模型</strong></div>
          <div className="essay-model-columns"><div><h3>跨模型共识</h3>{modelComparison?.consensus.slice(0, 3).map((item) => <p key={item.text}>{item.text}<span>{item.modelCount}</span></p>)}{!modelComparison?.consensus.length ? <p>需要至少两个模型完成同一日报后比较。</p> : null}</div><div className="is-risk"><h3>个股观点分歧</h3>{data.divergence.slice(0, 4).map((item) => <button type="button" key={item.key} onClick={() => onFilter(item.name)}><span>{item.name}</span><small>多 {item.bullish} / 空 {item.bearish}</small><strong>{item.divergenceScore}</strong></button>)}{!data.divergence.length ? <p>当前窗口未发现同股多空双向观点。</p> : null}</div></div>
        </section>

        <section className="essay-panel essay-funnel-panel">
          <div className="essay-panel-head"><div><span>从观点走向可跟踪事实</span><h2>证据链完整度</h2></div></div>
          <div className="essay-funnel">{data.evidenceFunnel.map((item, index) => <div key={item.name}><span><b>0{index + 1}</b>{item.name}</span><strong>{item.count.toLocaleString()}</strong><i><b style={{ width: `${item.count / funnelMax * 100}%` }} /></i></div>)}</div>
        </section>
      </div>
    </div>
  );
}

function SignalRow({ item, onOpen }: { item: EssayAnalysis; onOpen: (item: EssayAnalysis) => void }) {
  const analyzed = item.status === 'completed';
  return (
    <button type="button" className="essay-signal-row" onClick={() => onOpen(item)}>
      <div className="essay-score"><strong>{analyzed ? (item.noveltyScore ?? 0) : '—'}</strong><span>{analyzed ? '增量' : '待研判'}</span></div>
      <div className="min-w-0">
        <div className="essay-row-meta">
          <Badge variant={analyzed ? sentimentVariant(item.sentiment) : item.status === 'failed' ? 'danger' : 'default'}>
            {analyzed ? (SENTIMENT_LABELS[item.sentiment ?? ''] ?? '待判断') : (ANALYSIS_STATUS_LABELS[item.status] ?? item.status)}
          </Badge>
          <span>{analyzed ? (CATEGORY_LABELS[item.primaryCategory ?? ''] ?? item.primaryCategory ?? '未分类') : item.note.groupName}</span>
          <span>{formatTime(item.note.createdAt)}</span>
        </div>
        <h3>{item.note.title || '无标题纪要'}</h3>
        <p>{item.summary || item.errorMessage || item.note.content || '原文正文为空，点击查看图片或附件。'}</p>
      </div>
      <div className="essay-confidence"><strong>{analyzed ? `${Math.round((item.confidenceScore ?? 0) * 100)}%` : '原文'}</strong><span>{analyzed ? '置信' : '可检索'}</span></div>
    </button>
  );
}

function EssayDetail({ selected, onClose }: { selected: EssayAnalysis | null; onClose: () => void }) {
  const images = selected?.note.images ?? [];
  const files = selected?.note.files ?? [];
  const analyzed = selected?.status === 'completed';
  return (
    <Drawer isOpen={Boolean(selected)} onClose={onClose} title={selected?.note.title || '纪要详情'}>
      {selected ? (
        <div className="essay-detail">
          <div className="essay-detail-meta">
            <Badge variant={analyzed ? sentimentVariant(selected.sentiment) : selected.status === 'failed' ? 'danger' : 'default'}>{analyzed ? (SENTIMENT_LABELS[selected.sentiment ?? ''] ?? '待判断') : (ANALYSIS_STATUS_LABELS[selected.status] ?? selected.status)}</Badge>
            <Badge>{analyzed ? (CATEGORY_LABELS[selected.primaryCategory ?? ''] ?? selected.primaryCategory ?? '未分类') : '未分类'}</Badge>
            <span>{selected.note.groupName}</span>
            <span>{selected.note.authorName || '作者未标注'}</span>
            <span>{formatTime(selected.note.createdAt, true)}</span>
          </div>

          <section className="essay-original">
            <div className="essay-section-title"><FileText className="h-4 w-4" />原文</div>
            <p>{selected.note.content || '原文正文为空；如有图片或附件，请通过下方远端链接查看。'}</p>
          </section>

          {images.length || files.length ? (
            <section className="essay-detail-section">
              <div className="essay-section-title"><Archive className="h-4 w-4" />图片与文件</div>
              <p className="essay-help">同步时不下载到本机，仅在点击时打开知识星球远端地址。</p>
              <div className="essay-assets">
                {images.map((item, index) => item.viewUrl ? (
                  <a key={item.imageId || index} href={item.viewUrl} target="_blank" rel="noreferrer">
                    <Image className="h-4 w-4" />查看图片 {index + 1}<ExternalLink className="h-3.5 w-3.5" />
                  </a>
                ) : null)}
                {files.map((item, index) => item.viewUrl ? (
                  <a key={item.fileId || index} href={item.viewUrl} target="_blank" rel="noreferrer">
                    <FileText className="h-4 w-4" />{item.name || `附件 ${index + 1}`}<ExternalLink className="h-3.5 w-3.5" />
                  </a>
                ) : null)}
              </div>
            </section>
          ) : null}

          <section className="essay-detail-section">
            <div className="essay-section-title"><Brain className="h-4 w-4" />AI 研判</div>
            {analyzed ? <>
              <div className="essay-detail-scoreline">
                <span>重要度 <strong>{selected.importanceScore ?? '—'}</strong></span>
                <span>信息增量 <strong>{selected.noveltyScore ?? '—'}</strong></span>
                <span>信源质量 <strong>{selected.sourceQuality || '未判断'}</strong></span>
              </div>
              <p className="essay-summary">{selected.summary || '尚未形成有效摘要。'}</p>
            </> : <p className="essay-summary">本条已进入本地小作文库，当前状态：{ANALYSIS_STATUS_LABELS[selected.status] ?? selected.status}。原文仍可正常检索与查看。</p>}
          </section>

          {selected.evidence?.length ? (
            <section className="essay-detail-section">
              <div className="essay-section-title"><ShieldAlert className="h-4 w-4" />观点与原文依据</div>
              <div className="essay-evidence-list">{selected.evidence.map((item) => (
                <div key={`${item.claim}-${item.evidence}`}>
                  <strong>{item.claim}</strong><Badge>{item.strength}</Badge>
                  <p>{item.evidence}</p>
                </div>
              ))}</div>
            </section>
          ) : null}

          <div className="essay-detail-grid">
            <section><h4>催化剂</h4>{selected.catalysts.length ? selected.catalysts.map((item) => <p key={item}>{item}</p>) : <p>未识别</p>}</section>
            <section className="is-risk"><h4>风险与证伪</h4>{[...selected.risks, ...selected.contradictions, ...selected.falsificationConditions].length ? [...selected.risks, ...selected.contradictions, ...selected.falsificationConditions].map((item) => <p key={item}>{item}</p>) : <p>未识别</p>}</section>
          </div>

          {selected.stockMentions.length ? (
            <section className="essay-detail-section">
              <div className="essay-section-title"><BarChart3 className="h-4 w-4" />提及标的</div>
              <div className="essay-stock-mentions">{selected.stockMentions.map((stock) => (
                <div key={`${stock.tsCode}-${stock.name}`}><strong>{stock.name || stock.tsCode}</strong><span>{stock.tsCode}</span><p>{stock.rationale}</p></div>
              ))}</div>
            </section>
          ) : null}
        </div>
      ) : null}
    </Drawer>
  );
}

const EssayRadarPage = () => {
  const location = useLocation();
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const view = radarView(location.pathname);
  const [dashboard, setDashboard] = useState<EssayDashboard | null>(null);
  const [deepInsights, setDeepInsights] = useState<EssayDeepInsights | null>(null);
  const [insights, setInsights] = useState<EssayInsights | null>(null);
  const [status, setStatus] = useState<EssayStatus | null>(null);
  const [list, setList] = useState<EssayAnalysisList | null>(null);
  const [reports, setReports] = useState<EssayDailyReportList | null>(null);
  const [cloud, setCloud] = useState<EssayWordCloud | null>(null);
  const [historicalBacklog, setHistoricalBacklog] = useState<EssayHistoricalBacklog | null>(null);
  const [libraryStatsLoading, setLibraryStatsLoading] = useState(false);
  const [selected, setSelected] = useState<EssayAnalysis | null>(null);
  const [query, setQuery] = useState(() => searchParams.get('query') || '');
  const deferredQuery = useDebouncedValue(query, 350);
  const [sentiment, setSentiment] = useState('');
  const [category, setCategory] = useState('');
  const [analysisStatus, setAnalysisStatus] = useState('');
  const [minImportance, setMinImportance] = useState(0);
  const [days, setDays] = useState(0);
  const [page, setPage] = useState(1);
  const [cloudPeriod, setCloudPeriod] = useState<'day' | 'week' | 'month'>('day');
  const [cloudKind, setCloudKind] = useState<'stocks' | 'tags' | 'themes'>('stocks');
  const [historyYears, setHistoryYears] = useState<1 | 2>(1);
  const [analysisBatchCount, setAnalysisBatchCount] = useState(100);
  const [analysisOrder, setAnalysisOrder] = useState<'newest' | 'oldest'>('newest');
  const [queueMessage, setQueueMessage] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [actionLoading, setActionLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [feedNotice, setFeedNotice] = useState<string | null>(null);
  const [refreshKey, setRefreshKey] = useState(0);
  const feedPageCacheRef = useRef(new Map<string, EssayAnalysisList>());
  const feedInflightRef = useRef(new Map<string, Promise<EssayAnalysisList>>());
  const feedRequestVersionRef = useRef(0);

  useEffect(() => {
    const viewLabel = VIEW_META.find((item) => item.view === view)?.label ?? '小作文雷达';
    document.title = `${viewLabel} · 小作文雷达 - DSA`;
  }, [view]);

  const loadView = useCallback(async (_requestVersion: number) => {
    void _requestVersion;
    if (view === 'feed') return;
    setLoading(true);
    setError(null);
    try {
      if (view === 'overview') {
        const [nextInsights, nextStatus] = await Promise.allSettled([
          essayRadarApi.insights(30, 14), essayRadarApi.status(30),
        ]);
        if (nextInsights.status === 'fulfilled') setInsights(nextInsights.value);
        if (nextStatus.status === 'fulfilled') setStatus(nextStatus.value);
        if (nextInsights.status === 'rejected' && nextStatus.status === 'rejected') throw nextInsights.reason;
        if (nextInsights.status === 'rejected' || nextStatus.status === 'rejected') {
          setError('部分模块暂时不可用，已保留成功加载的真实数据。');
        }
      } else if (view === 'atlas') {
        const [nextDeepInsights, nextInsights] = await Promise.allSettled([
          essayRadarApi.deepInsights(30, 14), essayRadarApi.insights(30, 14),
        ]);
        if (nextDeepInsights.status === 'fulfilled') setDeepInsights(nextDeepInsights.value);
        if (nextInsights.status === 'fulfilled') setInsights(nextInsights.value);
        if (nextDeepInsights.status === 'rejected') throw nextDeepInsights.reason;
        if (nextInsights.status === 'rejected') setError('跨模型比较暂时不可用，语料洞察图谱仍可正常查看。');
      } else if (view === 'trends') {
        const [nextDashboard, nextInsights, nextCloud] = await Promise.allSettled([
          essayRadarApi.dashboard(30), essayRadarApi.insights(30, 14), essayRadarApi.wordCloud(cloudPeriod, cloudKind),
        ]);
        if (nextDashboard.status === 'fulfilled') setDashboard(nextDashboard.value);
        if (nextInsights.status === 'fulfilled') setInsights(nextInsights.value);
        if (nextCloud.status === 'fulfilled') setCloud(nextCloud.value);
        const failures = [nextDashboard, nextInsights, nextCloud].filter(item => item.status === 'rejected');
        if (failures.length === 3) throw (failures[0] as PromiseRejectedResult).reason;
        if (failures.length) setError(`部分模块暂时不可用（${failures.length}/3），其余数据仍可查看。`);
      } else if (view === 'reports') {
        const [nextReports, nextInsights] = await Promise.allSettled([
          essayRadarApi.dailyReports(), essayRadarApi.insights(30, 14),
        ]);
        if (nextReports.status === 'fulfilled') setReports(nextReports.value);
        if (nextInsights.status === 'fulfilled') setInsights(nextInsights.value);
        if (nextReports.status === 'rejected' && nextInsights.status === 'rejected') throw nextReports.reason;
        if (nextReports.status === 'rejected' || nextInsights.status === 'rejected') {
          setError('日报或研判摘要暂时不可用，已保留成功加载的模块。');
        }
      } else {
        const [nextStatus, nextBacklog] = await Promise.all([
          essayRadarApi.status(30), essayRadarApi.historicalBacklog(),
        ]);
        setStatus(nextStatus);
        setHistoricalBacklog(nextBacklog);
      }
    } catch (caught) {
      setError(errorText(caught, '页面数据加载失败'));
    } finally {
      setLoading(false);
    }
  }, [cloudKind, cloudPeriod, view]);

  const loadFeed = useCallback(async (refreshVersion: number) => {
    if (view !== 'feed') return;
    const baseFilters = {
      days, query: deferredQuery, analysisStatus, sentiment, category, minImportance, pageSize: 20,
    };
    const queryKey = `${refreshVersion}:${JSON.stringify(baseFilters)}`;
    const pageKey = (targetPage: number) => `${queryKey}:page:${targetPage}`;
    const fetchPage = (targetPage: number, knownTotal?: number) => {
      const key = pageKey(targetPage);
      const cached = feedPageCacheRef.current.get(key);
      if (cached) return Promise.resolve(cached);
      const inflight = feedInflightRef.current.get(key);
      if (inflight) return inflight;
      const request = essayRadarApi.list({
        ...baseFilters,
        page: targetPage,
        knownTotal,
      }).then((result) => {
        if (feedPageCacheRef.current.size >= FEED_PAGE_CACHE_LIMIT) {
          const oldestKey = feedPageCacheRef.current.keys().next().value;
          if (oldestKey) feedPageCacheRef.current.delete(oldestKey);
        }
        feedPageCacheRef.current.set(key, result);
        return result;
      }).finally(() => {
        feedInflightRef.current.delete(key);
      });
      feedInflightRef.current.set(key, request);
      return request;
    };
    const prefetchNext = (result: EssayAnalysisList) => {
      const lastPage = Math.max(1, Math.ceil(result.total / 20));
      if (page < lastPage) void fetchPage(page + 1, result.total).catch(() => undefined);
    };

    const requestVersion = ++feedRequestVersionRef.current;
    setFeedNotice(null);
    const cached = feedPageCacheRef.current.get(pageKey(page));
    if (cached) {
      setList(cached);
      setLoading(false);
      prefetchNext(cached);
      return;
    }
    setLoading(true);
    try {
      const result = await fetchPage(page, feedPageCacheRef.current.get(pageKey(1))?.total);
      if (requestVersion !== feedRequestVersionRef.current) return;
      setList(result);
      prefetchNext(result);
    } catch {
      if (requestVersion !== feedRequestVersionRef.current) return;
      setFeedNotice(list
        ? '检索服务刚才没有及时响应，当前仍保留上一次结果，并会在下次筛选时自动重试。'
        : '本地知识库正在准备索引，请稍等片刻后继续输入。');
    } finally {
      if (requestVersion === feedRequestVersionRef.current) setLoading(false);
    }
  }, [analysisStatus, category, days, deferredQuery, list, minImportance, page, sentiment, view]);

  useEffect(() => { void loadView(refreshKey); }, [loadView, refreshKey]);
  useEffect(() => { void loadFeed(refreshKey); }, [loadFeed, refreshKey]);
  useEffect(() => {
    if (view !== 'overview') return undefined;
    const timer = window.setTimeout(() => {
      void essayRadarApi.deepInsights(30, 14).then(setDeepInsights).catch(() => undefined);
    }, 1200);
    return () => window.clearTimeout(timer);
  }, [view]);
  useEffect(() => {
    if (view !== 'feed') return;
    let active = true;
    setLibraryStatsLoading(true);
    essayRadarApi.historicalBacklog()
      .then((result) => { if (active) setHistoricalBacklog(result); })
      .catch(() => { /* Keep search usable while knowledge-base statistics warm up. */ })
      .finally(() => { if (active) setLibraryStatsLoading(false); });
    return () => { active = false; };
  }, [refreshKey, view]);

  const workerActive = Boolean(status?.worker.running || status?.mcpSync.syncing || status?.mcpSync.historyBackfill?.running);
  useEffect(() => {
    if ((view !== 'overview' && view !== 'system') || !workerActive) return undefined;
    const timer = window.setInterval(async () => {
      try {
        if (view === 'system') {
          const [nextStatus, nextBacklog] = await Promise.all([
            essayRadarApi.status(30), essayRadarApi.historicalBacklog(),
          ]);
          setStatus(nextStatus);
          setHistoricalBacklog(nextBacklog);
        } else {
          setStatus(await essayRadarApi.status(30));
        }
      } catch { /* retain last factual state */ }
    }, status?.mcpSync.historyBackfill?.running ? 2000 : 10000);
    return () => window.clearInterval(timer);
  }, [status?.mcpSync.historyBackfill?.running, view, workerActive]);

  const act = async (action: () => Promise<unknown>) => {
    setActionLoading(true); setError(null);
    try { await action(); setRefreshKey((value) => value + 1); }
    catch (caught) { setError(errorText(caught, '操作失败')); }
    finally { setActionLoading(false); }
  };

  const queueHistoricalAnalysis = async () => {
    const available = historicalBacklog?.unqueued ?? 0;
    const selected = Math.min(analysisBatchCount, available);
    if (!selected) return;
    const direction = analysisOrder === 'newest' ? '最近' : '最早';
    if (!window.confirm(`将本地库中${direction}的 ${selected} 篇未入队小作文交给 AI 分析，可能消耗模型额度。确认继续？`)) return;
    setActionLoading(true); setError(null); setQueueMessage(null);
    try {
      const result = await essayRadarApi.backfillCount(analysisBatchCount, analysisOrder);
      setHistoricalBacklog(result.backlog);
      setQueueMessage(`已加入 ${result.queue.selected.toLocaleString()} 篇，后台正在分析；未入队还剩 ${result.backlog.unqueued.toLocaleString()} 篇。`);
      setRefreshKey((value) => value + 1);
    } catch (caught) {
      const message = errorText(caught, '历史小作文加入分析队列失败');
      setError(message.includes('连接上游服务超时')
        ? '补分析入队请求等待超时。该步骤只写入本地 AI 队列，不代表 DeepSeek 不可用；请刷新本页确认队列数量后再决定是否重试。'
        : message);
    } finally {
      setActionLoading(false);
    }
  };

  const yesterday = insights?.yesterday;
  const totalPages = Math.max(1, Math.ceil((list?.total ?? 0) / 20));
  const history = status?.mcpSync.historyBackfill;
  const historyPercent = Math.max(0, Math.min(history?.progressPercent ?? 0, 100));
  const modelComparison = insights?.modelComparison;
  const libraryTotal = historicalBacklog?.totalNotes ?? 0;
  const libraryCompleted = historicalBacklog?.completed ?? 0;
  const libraryPending = historicalBacklog?.pending ?? 0;
  const libraryProcessing = historicalBacklog?.processing ?? 0;
  const libraryFailed = historicalBacklog?.failed ?? 0;
  const libraryUnqueued = historicalBacklog?.unqueued ?? 0;
  const librarySegments = [
    { key: 'completed', label: '已分析', value: libraryCompleted },
    { key: 'processing', label: '分析中', value: libraryProcessing },
    { key: 'pending', label: '排队中', value: libraryPending },
    { key: 'failed', label: '失败', value: libraryFailed },
    { key: 'unqueued', label: '未入队', value: libraryUnqueued },
  ];
  const activityWindows = [
    { label: '近24小时', value: historicalBacklog?.notes24h ?? 0 },
    { label: '近7日', value: historicalBacklog?.notes7d ?? 0 },
    { label: '近30日', value: historicalBacklog?.notes30d ?? 0 },
  ];
  const activityMax = Math.max(...activityWindows.map((item) => item.value), 1);
  const trendMax = useMemo(() => Math.max(...(insights?.trend.map((item) => item.total) ?? [1]), 1), [insights?.trend]);
  const openFeedFor = (term: string) => {
    setQuery(term);
    setPage(1);
    navigate(`/essay-radar/feed?query=${encodeURIComponent(term)}`);
  };

  return (
    <AppPage className="essay-terminal max-w-none">
      <header className="essay-header">
        <div>
          <div className="essay-live-line"><span className={workerActive ? 'is-live' : ''} />知识星球增量库 · {formatTime(insights?.latestDataAt || status?.mcpSync.lastSyncAt || historicalBacklog?.latestSyncedAt, true)}</div>
          <h1>{view === 'atlas' ? '小作文洞察图谱' : '小作文研判台'}</h1>
          <p>{view === 'atlas' ? '把非结构化语料拆成可追溯的来源、主题、个股与可验证线索。' : '先读原文，再看证据与观点；趋势、日报和数据同步分开处理。'}</p>
        </div>
        <button type="button" className="essay-refresh" disabled={loading} onClick={() => setRefreshKey((value) => value + 1)}>
          <RefreshCw className={`h-4 w-4 ${loading ? 'animate-spin' : ''}`} />刷新当前页
        </button>
      </header>

      <RadarNavigation active={view} />
      {error ? <div className="essay-error">{error}</div> : null}
      {loading && view !== 'feed' ? <div className="essay-loading"><RefreshCw className="h-4 w-4 animate-spin" />正在读取当前页真实数据…</div> : null}

      {view === 'overview' ? (
        <div className="essay-view">
          <section className="essay-metric-grid">
            <Metric label="昨日新增" value={yesterday?.analyzedCount ?? '—'} note={yesterday?.date || '等待数据'} tone="signal" />
            <Metric label="高重要度" value={yesterday?.highImportanceCount ?? '—'} note="重要度 ≥ 80" />
            <Metric label="低置信" value={yesterday?.lowConfidenceCount ?? '—'} note="需要原文复核" tone="danger" />
            <Metric label="传闻" value={yesterday?.rumorCount ?? '—'} note="与事实分开展示" tone="danger" />
            <Metric label="证据覆盖" value={yesterday ? `${yesterday.evidenceCoveragePercent}%` : '—'} note="包含原文依据" />
          </section>

          <div className="essay-overview-grid">
            <section className="essay-panel essay-priority">
              <div className="essay-panel-head"><div><span>昨日新增 · 按信息增量排序</span><h2>优先核验</h2></div><Link to="/essay-radar/feed">进入全部信息流</Link></div>
              <div className="essay-signal-list">
                {insights?.highNoveltySignals.slice(0, 7).map((item) => <SignalRow key={item.topicId} item={item} onOpen={setSelected} />)}
                {!loading && !insights?.highNoveltySignals.length ? <EmptyState title="暂无高增量信号" description="等待新增纪要完成分析。" icon={<Sparkles className="h-7 w-7" />} /> : null}
              </div>
            </section>

            <aside className="essay-panel essay-watch-panel">
              <div className="essay-panel-head"><div><span>当前自选股</span><h2>相关信号</h2></div><Link to="/super-watchlist">超级看板</Link></div>
              <div className="essay-watch-list">{insights?.watchlist.map((stock) => (
                <article key={stock.symbol}>
                  <div><h3>{stock.name}</h3><span>{stock.symbol}</span></div>
                  <strong>{stock.monthMentions}</strong><small>近30日提及</small>
                  <div className="essay-watch-counts"><span>今日 {stock.dayMentions}</span><span>7日 {stock.weekMentions}</span></div>
                  <p>{stock.latestThesis || '尚未形成有效摘要，进入信息流查看原文。'}</p>
                  <button type="button" onClick={() => openFeedFor(stock.name)}>按该股票筛选</button>
                </article>
              ))}</div>
              <Link className="essay-wide-link" to="/essay-radar/trends">查看趋势与词频</Link>
            </aside>
          </div>
        </div>
      ) : null}

      {view === 'atlas' ? <DeepInsightsView data={deepInsights} modelComparison={insights?.modelComparison} onOpen={setSelected} onFilter={openFeedFor} /> : null}

      {view === 'feed' ? (
        <div className="essay-view">
          <section className="essay-library-board" aria-label="小作文知识库总览">
            <div className="essay-library-identity">
              <div><span>本地 SQLite 知识库</span><strong>{libraryStatsLoading && !historicalBacklog ? '读取中' : libraryTotal.toLocaleString()}</strong><small>篇小作文已入库</small></div>
              <div className="essay-library-timeline">
                <span>{formatDate(historicalBacklog?.earliestNoteAt)}</span>
                <i><b /></i>
                <span>{formatDate(historicalBacklog?.latestNoteAt)}</span>
              </div>
              <p>最近入库 {formatTime(historicalBacklog?.latestSyncedAt, true)} · {historicalBacklog?.groupCount ?? 0} 个知识星球</p>
            </div>
            <div className="essay-library-analysis">
              <div className="essay-library-analysis-head"><span>AI 分析覆盖</span><strong>{(historicalBacklog?.coveragePercent ?? 0).toFixed(1)}%</strong></div>
              <div className="essay-library-segments" aria-label="AI处理状态分布">
                {librarySegments.map((item) => <i key={item.key} className={`is-${item.key}`} style={{ width: `${libraryTotal ? (item.value / libraryTotal) * 100 : 0}%` }} />)}
              </div>
              <div className="essay-library-legend">{librarySegments.map((item) => <div key={item.key}><i className={`is-${item.key}`} /><span>{item.label}</span><strong>{item.value.toLocaleString()}</strong></div>)}</div>
            </div>
            <div className="essay-library-activity">
              <div className="essay-library-analysis-head"><span>原文新增</span><strong>{(historicalBacklog?.notes30d ?? 0).toLocaleString()}<small> / 30日</small></strong></div>
              <div>{activityWindows.map((item) => <div key={item.label}><span>{item.label}</span><i><b style={{ width: `${(item.value / activityMax) * 100}%` }} /></i><strong>{item.value.toLocaleString()}</strong></div>)}</div>
            </div>
          </section>
          <section className="essay-filter-panel">
            <label className="essay-search"><Search className="h-4 w-4" /><input aria-label="搜索小作文" value={query} onChange={(event) => { setQuery(event.target.value); setPage(1); }} placeholder="检索全库：标题、原文、作者、星球、股票或 AI 标签" /></label>
            <select aria-label="时间范围" value={days} onChange={(event) => { setDays(Number(event.target.value)); setPage(1); }}><option value={0}>全部入库</option><option value={1}>今日</option><option value={7}>近7日</option><option value={30}>近30日</option><option value={365}>近1年</option><option value={730}>近2年</option></select>
            <select aria-label="AI状态筛选" value={analysisStatus} onChange={(event) => { setAnalysisStatus(event.target.value); setPage(1); }}><option value="">全部小作文</option><option value="completed">已分析</option><option value="uncompleted">未完成分析</option><option value="not_queued">未入队</option><option value="pending">排队中</option><option value="processing">分析中</option><option value="failed">分析失败</option></select>
            <select aria-label="情绪筛选" value={sentiment} onChange={(event) => { setSentiment(event.target.value); setPage(1); }}><option value="">全部情绪</option>{Object.entries(SENTIMENT_LABELS).map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select>
            <select aria-label="类型筛选" value={category} onChange={(event) => { setCategory(event.target.value); setPage(1); }}><option value="">全部类型</option>{Object.entries(CATEGORY_LABELS).map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select>
            <select aria-label="重要度筛选" value={minImportance} onChange={(event) => { setMinImportance(Number(event.target.value)); setPage(1); }}><option value={0}>全部重要度</option><option value={60}>≥ 60</option><option value={75}>≥ 75</option><option value={85}>≥ 85</option></select>
          </section>
          <div className="essay-feed-summary" aria-live="polite"><span>{query !== deferredQuery ? '等待输入完成…' : loading ? '正在检索整个本地库，当前结果继续保留…' : feedNotice || <>当前条件命中 <strong>{(list?.total ?? 0).toLocaleString()}</strong> 篇 · {days ? `近 ${days} 日` : '全部已入库'} · 包含未分析原文</>}</span><button type="button" onClick={() => { setQuery(''); setAnalysisStatus(''); setSentiment(''); setCategory(''); setMinImportance(0); setDays(0); setPage(1); }}>清除筛选</button></div>
          <section className="essay-panel essay-feed-panel">
            <div className="essay-feed-list">{list?.items.map((item) => <SignalRow key={item.topicId} item={item} onOpen={setSelected} />)}</div>
            {!loading && !list?.items.length ? <EmptyState title="没有匹配的小作文" description="调整时间或筛选条件。" icon={<Database className="h-7 w-7" />} /> : null}
            {list?.total ? <div className="essay-pagination"><span>第 {page} / {totalPages} 页</span><div><button disabled={page <= 1} onClick={() => setPage((value) => value - 1)}>上一页</button><button disabled={page >= totalPages} onClick={() => setPage((value) => value + 1)}>下一页</button></div></div> : null}
          </section>
        </div>
      ) : null}

      {view === 'trends' ? (
        <div className="essay-view">
          <div className="essay-trend-grid">
            <section className="essay-panel essay-chart-panel">
              <div className="essay-panel-head"><div><span>近14天完成分析记录</span><h2>提及量与平均重要度</h2></div><strong>峰值 {trendMax}</strong></div>
              <div className="essay-chart"><ResponsiveContainer width="100%" height="100%" minWidth={0} initialDimension={{ width: 900, height: 330 }}><ComposedChart data={insights?.trend ?? []}><CartesianGrid vertical={false} stroke="rgba(148,163,184,.14)" /><XAxis dataKey="date" tickFormatter={(value) => String(value).slice(5)} tick={{ fontSize: 11 }} /><YAxis yAxisId="count" tick={{ fontSize: 11 }} /><YAxis yAxisId="score" orientation="right" domain={[0, 100]} tick={{ fontSize: 11 }} /><Tooltip /><Bar yAxisId="count" dataKey="total" fill="#22d3ee" opacity={0.62} /><Line yAxisId="score" type="monotone" dataKey="averageImportance" stroke="#c6ff4a" strokeWidth={2} dot={false} /></ComposedChart></ResponsiveContainer></div>
            </section>
            <section className="essay-panel essay-cloud-panel">
              <div className="essay-panel-head"><div><span>{cloud?.startDate} 至 {cloud?.endDate} · {cloud?.sourceCount ?? 0} 篇</span><h2>提及变化</h2></div></div>
              <div className="essay-cloud-controls"><div>{(['day', 'week', 'month'] as const).map((value) => <button key={value} className={cloudPeriod === value ? 'is-active' : ''} onClick={() => setCloudPeriod(value)}>{value === 'day' ? '日' : value === 'week' ? '周' : '月'}</button>)}</div><div>{(['stocks', 'tags', 'themes'] as const).map((value) => <button key={value} className={cloudKind === value ? 'is-active' : ''} onClick={() => setCloudKind(value)}>{value === 'stocks' ? '股票' : value === 'tags' ? '标签' : '主题'}</button>)}</div></div>
              <div className="essay-cloud">{cloud?.items.slice(0, 24).map((item, index) => (
                <button key={item.name} style={{ fontSize: `${12 + Math.round((item.count / (cloud.items[0]?.count || 1)) * 18)}px`, opacity: Math.max(.5, 1 - index * .02) }} onClick={() => openFeedFor(item.name)}><span>{item.name}</span><small>{item.count}{item.change > 0 ? ' ↑' : item.change < 0 ? ' ↓' : ''}</small></button>
              ))}</div>
            </section>
          </div>

          <div className="essay-trend-grid essay-trend-grid-bottom">
            <section className="essay-panel">
              <div className="essay-panel-head"><div><span>自选股日 / 周 / 月口径</span><h2>关注股趋势</h2></div></div>
              <div className="essay-watch-table">{insights?.watchlist.map((stock) => (
                <button key={stock.symbol} onClick={() => openFeedFor(stock.name)}>
                  <div><strong>{stock.name}</strong><span>{stock.symbol}</span></div><span>{stock.dayMentions}</span><span>{stock.weekMentions}</span><span>{stock.monthMentions}</span><small>{stock.averageImportance.toFixed(1)}</small>
                </button>
              ))}<div className="essay-watch-table-head"><span>标的</span><span>日</span><span>周</span><span>月</span><span>重要度</span></div></div>
            </section>
            <section className="essay-panel">
              <div className="essay-panel-head"><div><span>近30天分析结果</span><h2>高频标的</h2></div></div>
              <div className="essay-stock-table">{dashboard?.topStocks.slice(0, 12).map((stock) => (
                <div key={stock.key}><div><strong>{stock.name || stock.tsCode}</strong><span>{stock.tsCode}</span></div><strong>{stock.mentionCount}</strong><span className="is-bull">{stock.bullish}</span><span className="is-bear">{stock.bearish}</span><small>{stock.averageImportance.toFixed(1)}</small></div>
              ))}</div>
            </section>
          </div>
        </div>
      ) : null}

      {view === 'reports' ? (
        <div className="essay-view">
          <section className="essay-panel essay-report-summary">
            <div className="essay-panel-head"><div><span>{modelComparison?.reportDate || '最近报告日'}</span><h2>模型共识与分歧</h2></div><button disabled={actionLoading} onClick={() => void act(() => essayRadarApi.runDailyReports())}><Brain className="h-4 w-4" />生成昨日报告</button></div>
            <div className="essay-consensus-grid"><div><h3>共识</h3>{modelComparison?.consensus.length ? modelComparison.consensus.map((item) => <p key={item.text}>{item.text}<span>{item.modelCount} 个模型</span></p>) : <p>目前只有单模型报告，暂无跨模型共识。</p>}</div><div className="is-risk"><h3>分歧</h3>{modelComparison?.divergences.length ? modelComparison.divergences.map((item) => <p key={item.text}>{item.text}<span>{item.modelCount} 个模型</span></p>) : <p>暂无可比较的跨模型分歧。</p>}</div></div>
          </section>
          <section className="essay-panel">
            <div className="essay-panel-head"><div><span>每个模型独立汇总前一日新增小作文</span><h2>历史日报</h2></div><strong>{reports?.total ?? 0} 份</strong></div>
            <div className="essay-report-list">{reports?.items.map((item, index) => (
              <details key={`${item.reportDate}-${item.model}`} open={index === 0}>
                <summary><div><strong>{item.reportDate}</strong><span>{item.model}</span></div><div><span>{item.sourceCount} 篇</span><Badge variant={item.status === 'completed' ? 'success' : item.status === 'failed' ? 'danger' : 'warning'}>{item.status}</Badge></div></summary>
                {item.report ? <div className="essay-report-body"><section><h3>核心结论</h3><p>{item.report.executiveSummary || '—'}</p></section><div><section><h3>新增信号</h3>{item.report.novelSignals?.map((value) => <p key={value}>{value}</p>)}</section><section className="is-risk"><h3>风险与分歧</h3>{[...(item.report.riskWatch ?? []), ...(item.report.divergences ?? [])].map((value) => <p key={value}>{value}</p>)}</section></div></div> : <p className="essay-error-inline">{item.errorMessage || '报告尚未完成。'}</p>}
              </details>
            ))}</div>
          </section>
        </div>
      ) : null}

      {view === 'system' ? (
        <div className="essay-view essay-system-grid">
          <section className="essay-panel">
            <div className="essay-panel-head"><div><span>新增纪要入库后进入 AI 队列</span><h2>实时分析</h2></div><span className={`essay-status ${status?.worker.running ? 'is-running' : ''}`}>{status?.worker.running ? '运行中' : '已停止'}</span></div>
            <div className="essay-progress"><div><span>近30天分析覆盖</span><strong>{status?.progress.coveragePercent ?? 0}%</strong></div><div><i style={{ width: `${status?.progress.coveragePercent ?? 0}%` }} /></div></div>
            <div className="essay-system-metrics"><Metric label="已完成" value={status?.progress.completed ?? 0} /><Metric label="待处理" value={status?.progress.pending ?? 0} /><Metric label="失败" value={status?.progress.failed ?? 0} tone="danger" /></div>
            <div className="essay-actions"><button disabled={actionLoading} onClick={() => void act(status?.worker.running ? essayRadarApi.stopWorker : essayRadarApi.startWorker)}>{status?.worker.running ? '停止实时分析' : '启动实时分析'}</button><button disabled={actionLoading || !status?.progress.failed} onClick={() => void act(essayRadarApi.retryFailed)}>重试失败记录</button></div>
          </section>

          <section className="essay-panel">
            <div className="essay-panel-head"><div><span>直接 MCP → SQLite · 附件仅保存链接</span><h2>知识星球增量同步</h2></div><span className={`essay-status ${status?.mcpSync.running ? 'is-running' : ''}`}>{status?.mcpSync.running ? '运行中' : '已停止'}</span></div>
            <p className="essay-system-note">轮询间隔 {status?.mcpSync.pollSeconds ?? '—'} 秒 · 最近成功 {formatTime(status?.mcpSync.lastSyncAt, true)} · 模式 {status?.mcpSync.mode || '—'}</p>
            <div className="essay-actions"><button disabled={actionLoading} onClick={() => void act(essayRadarApi.syncMcp)}>立即增量同步</button><button disabled={actionLoading} onClick={() => void act(status?.mcpSync.running ? essayRadarApi.stopMcpWorker : essayRadarApi.startMcpWorker)}>{status?.mcpSync.running ? '停止自动同步' : '启动自动同步'}</button></div>
            <div className="essay-group-list">{status?.mcpSync.groups.map((group) => <div key={group.groupId}><strong>{group.groupName}</strong><Badge variant={group.lastStatus === 'success' ? 'success' : group.lastStatus === 'failed' ? 'danger' : 'default'}>{group.lastStatus}</Badge><span>累计入库 {group.totalSaved.toLocaleString()}</span><span>最新游标 {formatTime(group.lastTopicAt)}</span></div>)}</div>
          </section>

          <section className="essay-panel essay-history-panel">
            <div className="essay-panel-head"><div><span>只使用本地已入库、尚未创建 AI 任务的纪要</span><h2>历史小作文补分析</h2></div><Badge variant={historicalBacklog?.unqueued ? 'warning' : 'success'}>{historicalBacklog?.unqueued ? `${historicalBacklog.unqueued.toLocaleString()} 篇可选` : '已全部入队'}</Badge></div>
            <p className="essay-system-note">不重新抓取知识星球，不扩大所选数量。失败任务仍通过“重试失败记录”单独处理。</p>
            <div className="essay-system-metrics">
              <Metric label="未入队" value={(historicalBacklog?.unqueued ?? 0).toLocaleString()} note={`${formatTime(historicalBacklog?.earliestUnqueuedAt, true)} 至 ${formatTime(historicalBacklog?.latestUnqueuedAt, true)}`} />
              <Metric label="已完成" value={(historicalBacklog?.completed ?? 0).toLocaleString()} tone="signal" />
              <Metric label="排队 / 处理中" value={((historicalBacklog?.pending ?? 0) + (historicalBacklog?.processing ?? 0)).toLocaleString()} />
              <Metric label="失败" value={(historicalBacklog?.failed ?? 0).toLocaleString()} tone="danger" />
            </div>
            <div className="essay-history-controls">
              <select aria-label="历史补分析篇数" value={analysisBatchCount} onChange={(event) => setAnalysisBatchCount(Number(event.target.value))}><option value={50}>50篇</option><option value={100}>100篇</option><option value={500}>500篇</option><option value={1000}>1,000篇</option><option value={2000}>2,000篇</option><option value={5000}>5,000篇</option></select>
              <select aria-label="历史补分析顺序" value={analysisOrder} onChange={(event) => setAnalysisOrder(event.target.value as 'newest' | 'oldest')}><option value="newest">最近的优先</option><option value="oldest">最早的优先</option></select>
              <button disabled={actionLoading || !historicalBacklog?.unqueued} onClick={() => void queueHistoricalAnalysis()}>{actionLoading ? '正在加入队列' : `分析 ${Math.min(analysisBatchCount, historicalBacklog?.unqueued ?? analysisBatchCount).toLocaleString()} 篇`}</button>
            </div>
            {queueMessage ? <p className="essay-action-feedback">{queueMessage}</p> : null}
          </section>

          <section className="essay-panel essay-history-panel">
            <div className="essay-panel-head"><div><span>旧数据默认只入库供检索和回测</span><h2>历史纪要库</h2></div><Badge variant={history?.running ? 'warning' : 'default'}>{history?.running ? '同步中' : '按需启动'}</Badge></div>
            <div className="essay-history-controls">
              <select aria-label="知识星球历史范围" value={historyYears} onChange={(event) => setHistoryYears(Number(event.target.value) as 1 | 2)}><option value={1}>近1年</option><option value={2}>近2年</option></select>
              <button disabled={actionLoading || history?.running} onClick={() => void act(() => essayRadarApi.backfillMcpHistory(historyYears))}>{history?.running ? '正在同步入库' : '只同步入库'}</button>
            </div>
            <div className="essay-progress"><div><span>{history?.message || '尚未启动历史同步'}</span><strong>{historyPercent.toFixed(1)}%</strong></div><div><i style={{ width: `${historyPercent}%` }} /></div></div>
            <div className="essay-system-metrics">
              <Metric label="已获取" value={(history?.received ?? 0).toLocaleString()} note="本次任务" />
              <Metric label="新增" value={(history?.created ?? 0).toLocaleString()} note={`跳过 ${(history?.unchanged ?? 0).toLocaleString()}`} tone="signal" />
              <Metric label="分页" value={(history?.pagesFetched ?? 0).toLocaleString()} note={history?.currentGroupName || '等待开始'} />
              <Metric label="覆盖至" value={history?.oldestAt ? formatTime(history.oldestAt, true) : '—'} note="旧数据不自动分析" />
            </div>
          </section>
        </div>
      ) : null}

      <EssayDetail selected={selected} onClose={() => setSelected(null)} />
    </AppPage>
  );
};

export default EssayRadarPage;
