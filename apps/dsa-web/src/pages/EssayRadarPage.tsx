import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  Archive, ArrowRight, BarChart3, Brain, Database, Download, ExternalLink, FileText, GitBranch, Headphones, Image,
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
  EssayAudioFile, EssayAudioFileList, EssayHistoricalBacklog, EssayStatus, EssayWordCloud,
} from '../types/essayRadar';
import './EssayRadarPage.css';

type RadarView = 'overview' | 'atlas' | 'feed' | 'trends' | 'reports' | 'system';
type AtlasHorizon = 'short' | 'medium' | 'long' | 'custom';

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
  media_only: '录音资料',
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

function formatClock(value?: string | null) {
  if (!value) return '等待首轮数据';
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;
  return parsed.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit', second: '2-digit' });
}

function formatDuration(seconds?: number) {
  if (!seconds || seconds <= 0) return '';
  const minutes = Math.floor(seconds / 60);
  const remain = Math.round(seconds % 60);
  return `${minutes}:${String(remain).padStart(2, '0')}`;
}

function formatAssetSize(bytes?: number) {
  if (!bytes || bytes <= 0) return '';
  if (bytes >= 1024 * 1024) return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
  return `${Math.ceil(bytes / 1024)} KB`;
}

function errorText(value: unknown, fallback: string) {
  return value instanceof Error ? value.message : fallback;
}

function downloadBlob(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = filename;
  anchor.click();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
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

function heatLevel(score: number, sortedScores: number[]): number {
  if (score <= 0 || sortedScores.length === 0) return 0;
  let upperRank = 0;
  for (const candidate of sortedScores) {
    if (candidate <= score) upperRank += 1;
  }
  return Math.max(1, Math.min(5, Math.ceil(upperRank / sortedScores.length * 5)));
}

function DeepInsightsView({ data, horizon, startDate, endDate, onPeriodChange, onFilter }: {
  data: EssayDeepInsights | null;
  horizon: AtlasHorizon;
  startDate: string;
  endDate: string;
  onPeriodChange: (horizon: AtlasHorizon, startDate?: string, endDate?: string) => void;
  onFilter: (term: string) => void;
}) {
  const [selectedCode, setSelectedCode] = useState('');
  const [draftStart, setDraftStart] = useState(startDate);
  const [draftEnd, setDraftEnd] = useState(endDate);
  const [aiLoading, setAiLoading] = useState(false);
  const [aiError, setAiError] = useState<string | null>(null);
  const [aiResults, setAiResults] = useState<Record<string, {
    conclusion: string; evidence: string[]; limitations: string[]; nextChecks: string[];
  }>>({});
  useEffect(() => {
    if (!data?.marketImpact.items.length) return;
    if (!data.marketImpact.items.some((item) => item.tsCode === selectedCode)) {
      setSelectedCode(data.marketImpact.items[0].tsCode);
    }
  }, [data, selectedCode]);
  useEffect(() => { setDraftStart(startDate); setDraftEnd(endDate); }, [startDate, endDate]);
  if (!data) return null;
  const selectedMarket = data.marketImpact.items.find((item) => item.tsCode === selectedCode)
    ?? data.marketImpact.items[0];
  const selectedAi = selectedMarket ? aiResults[selectedMarket.tsCode] : undefined;
  const layers = [
    { key: 'sources' as const, label: '来源', note: '知识星球' },
    { key: 'themes' as const, label: '主题', note: 'AI 提取' },
    { key: 'stocks' as const, label: '个股', note: '明确提及' },
    { key: 'outcomes' as const, label: '行情验证', note: '事件后5日' },
  ];
  const relationCount = (stage: string, key: string) => data.layers.edges
    .filter((edge) => (edge.fromStage === stage && edge.from === key) || (edge.toStage === stage && edge.to === key))
    .reduce((sum, edge) => sum + edge.count, 0);
  const heatScores = data.themeHeatmap.items
    .flatMap((item) => item.points.map((point) => point.concentrationScore ?? 0))
    .filter((score) => score > 0)
    .sort((left, right) => left - right);
  const funnelMax = Math.max(data.evidenceFunnel[0]?.count ?? 0, 1);
  const periodLabel = data.period.granularity === 'day' ? '日' : data.period.granularity === 'week' ? '周' : '月';
  const runAiInterpretation = async () => {
    if (!selectedMarket) return;
    setAiLoading(true);
    setAiError(null);
    try {
      const result = await essayRadarApi.interpretMarketImpact({
        tsCode: selectedMarket.tsCode,
        horizon,
        startDate: horizon === 'custom' ? data.period.startDate : undefined,
        endDate: horizon === 'custom' ? data.period.endDate : undefined,
      });
      setAiResults((current) => ({ ...current, [selectedMarket.tsCode]: result.interpretation }));
    } catch (caught) {
      setAiError(errorText(caught, 'AI 解读暂时不可用，统计结果仍可正常查看。'));
    } finally {
      setAiLoading(false);
    }
  };

  return (
    <div className="essay-view essay-atlas-view">
      <section className="essay-period-rail" aria-label="洞察研究时间窗口">
        <div><span>研究窗口</span><strong>{data.period.startDate} — {data.period.endDate}</strong><small>{data.windowDays} 个自然日 · 按{periodLabel}聚合</small></div>
        <nav>{([
          ['short', '短期', '14日'], ['medium', '中期', '90日'], ['long', '长期', '1年'],
        ] as const).map(([value, label, note]) => <button key={value} type="button" className={horizon === value ? 'is-active' : ''} onClick={() => onPeriodChange(value)}><strong>{label}</strong><small>{note}</small></button>)}</nav>
        <div className={`essay-custom-period ${horizon === 'custom' ? 'is-active' : ''}`}>
          <label>开始<input type="date" value={draftStart} onChange={(event) => setDraftStart(event.target.value)} /></label>
          <label>结束<input type="date" value={draftEnd} onChange={(event) => setDraftEnd(event.target.value)} /></label>
          <button type="button" disabled={!draftStart || !draftEnd || draftStart > draftEnd} onClick={() => onPeriodChange('custom', draftStart, draftEnd)}>应用</button>
        </div>
      </section>

      <section className="essay-atlas-summary" aria-label="所选窗口语料洞察摘要">
        <Metric label="已分析语料" value={data.summary.analyzedCount.toLocaleString()} note={`${data.windowDays} 日窗口`} tone="signal" />
        <Metric label="真实来源" value={data.summary.sourceCount} note="按知识星球去重" />
        <Metric label="主题 / 个股" value={`${data.summary.themeCount} / ${data.summary.stockCount}`} note="AI结构化实体" />
        <Metric label="行情覆盖" value={`${data.marketImpact.coverage.eventCoveragePercent}%`} note={`${data.marketImpact.coverage.coveredEventDayCount}/${data.marketImpact.coverage.eventDayCount} 个事件日`} />
        <Metric label="已验证股票" value={data.marketImpact.coverage.pricedStockCount} note={`候选 ${data.marketImpact.coverage.candidateStockCount} 只`} />
        <Metric label="多空分歧" value={data.summary.divergenceCount} note="同一标的双向观点" tone="danger" />
      </section>

      <div className="essay-atlas-primary">
        <section className="essay-panel essay-market-link-panel">
          <div className="essay-panel-head"><div><span>真实日线 × 小作文事件日</span><h2>个股行情与提及关联</h2></div><strong>{selectedMarket?.dataSource || '本地行情库'}</strong></div>
          <div className="essay-market-stock-tabs">{data.marketImpact.items.map((item) => <button type="button" key={item.tsCode} className={item.tsCode === selectedMarket?.tsCode ? 'is-active' : ''} onClick={() => { setSelectedCode(item.tsCode); setAiError(null); }}><strong>{item.name}</strong><small>{item.mentionCount}篇</small></button>)}</div>
          {selectedMarket ? <>
            <div className="essay-market-chart"><ResponsiveContainer width="100%" height="100%" minWidth={0} initialDimension={{ width: 720, height: 300 }}><ComposedChart data={selectedMarket.series}><CartesianGrid vertical={false} stroke="rgba(148,163,184,.14)" /><XAxis dataKey="date" tickFormatter={(value) => String(value).slice(5)} tick={{ fontSize: 9 }} minTickGap={24} /><YAxis yAxisId="price" tickFormatter={(value) => `${Number(value).toFixed(0)}%`} tick={{ fontSize: 9 }} width={42} /><YAxis yAxisId="mention" orientation="right" allowDecimals={false} tick={{ fontSize: 9 }} width={28} /><Tooltip contentStyle={{ background: '#0b0c0a', border: '1px solid rgba(198,255,74,.25)', fontSize: 10 }} formatter={(value, name) => [name === '阶段涨跌' ? `${Number(value).toFixed(2)}%` : `${value} 篇`, name]} labelFormatter={(value) => `交易日 ${value}`} /><Bar yAxisId="mention" dataKey="mentionCount" name="小作文" fill="#22d3ee" opacity={0.55} /><Line yAxisId="price" type="monotone" dataKey="priceReturn" name="阶段涨跌" stroke="#c6ff4a" strokeWidth={2} dot={false} /></ComposedChart></ResponsiveContainer></div>
            <div className="essay-event-metrics">{selectedMarket.metrics.map((metric) => <div key={metric.period} aria-label={`95%置信区间：${metric.confidenceInterval95[0] == null ? '样本不足' : `${metric.confidenceInterval95[0]}% ~ ${metric.confidenceInterval95[1]}%`}`}><span>事件后 {metric.period} 日</span><strong className={(metric.averageReturn ?? 0) >= 0 ? 'is-up' : 'is-down'}>{metric.averageReturn == null ? '未成熟' : `${metric.averageReturn >= 0 ? '+' : ''}${metric.averageReturn.toFixed(2)}%`}</strong><small>胜率 {metric.winRate == null ? '—' : `${metric.winRate.toFixed(1)}%`} · 样本 {metric.sampleCount}</small><small>超额 {metric.averageExcessReturn == null ? '—' : `${metric.averageExcessReturn >= 0 ? '+' : ''}${metric.averageExcessReturn.toFixed(2)}%`}</small></div>)}</div>
            <p className="essay-method-note">{selectedMarket.insight} {data.marketImpact.causalityNote}</p>
          </> : <EmptyState title="当前窗口缺少行情匹配" description="需要小作文明确匹配股票代码，且本地行情库覆盖事件后的交易日。" icon={<BarChart3 className="h-7 w-7" />} />}
        </section>

        <section className="essay-panel essay-atlas-chain">
          <div className="essay-panel-head"><div><span>原文共现 + 真实行情结果</span><h2>来源 → 主题 → 个股 → 行情验证</h2></div><strong>{data.layers.edges.length} 条关系</strong></div>
          <div className="essay-chain-grid">
            {layers.map((layer, layerIndex) => {
              const nodes = data.layers[layer.key];
              const max = Math.max(...nodes.map((node) => node.count), 1);
              return <div className="essay-chain-stage" key={layer.key}>
                <header><span>0{layerIndex + 1}</span><div><strong>{layer.label}</strong><small>{layer.note}</small></div>{layerIndex < layers.length - 1 ? <ArrowRight /> : null}</header>
                <div>{nodes.map((node) => <button key={node.key} type="button" onClick={() => layer.key === 'outcomes' ? setSelectedCode(node.tsCode || '') : onFilter(node.label)} aria-label={layer.key === 'outcomes' ? `查看 ${node.stockName} 的行情验证` : `检索原文：${node.label}`}>
                  <span>{node.kind ? <i className={`is-${node.kind}`} /> : null}{layer.key === 'outcomes' ? `${node.stockName} · ${node.label}` : node.label}</span>
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
          <div className="essay-panel-head"><div><span>按每{periodLabel}主题占比与阶段峰值计算 · 颜色越亮越集中</span><h2>主题热力迁移</h2></div><strong>{data.themeHeatmap.dates.length} 个{periodLabel}段</strong></div>
          <div className="essay-heatmap-meta">
            <span>{data.themeHeatmap.taxonomy ? `${data.themeHeatmap.taxonomy.rawThemeCount} 个原始标签 → ${data.themeHeatmap.taxonomy.canonicalThemeCount} 个规范主题` : '规范主题聚合'}</span>
            <div aria-label="主题集中度图例"><small>低</small>{[1, 2, 3, 4, 5].map((level) => <i key={level} className={`heat-level-${level}`} />)}<small>高</small></div>
          </div>
          <div className="essay-heatmap-axis" style={{ gridTemplateColumns: `110px repeat(${data.themeHeatmap.dates.length}, minmax(9px, 1fr))` }}><span />{data.themeHeatmap.dates.map((day, index) => <small key={day} className={index % Math.max(1, Math.ceil(data.themeHeatmap.dates.length / 5)) ? 'is-muted' : ''}>{day.slice(5)}</small>)}</div>
          <div className="essay-heatmap">{data.themeHeatmap.items.map((item) => {
            const aliases = item.aliases?.map((alias) => alias.name) ?? [];
            return <div key={item.name} style={{ gridTemplateColumns: `110px repeat(${data.themeHeatmap.dates.length}, minmax(9px, 1fr))` }}><button type="button" onClick={() => onFilter(item.name)} aria-label={aliases.length ? `已合并：${aliases.join('、')}` : item.name}><strong>{item.name}</strong><small>{item.total}</small></button>{item.points.map((point) => {
              const level = heatLevel(point.concentrationScore ?? 0, heatScores);
              return <i key={point.date} aria-label={`${point.date} · ${point.count} 篇 · 当日主题提及 ${point.dailyTotal ?? 0} 次 · 占比 ${(point.sharePercent ?? 0).toFixed(1)}% · 集中度 ${(point.concentrationScore ?? 0).toFixed(1)}`} className={`heat-level-${level}`} />;
            })}</div>;
          })}</div>
        </section>

        <section className="essay-panel essay-market-ranking-panel">
          <div className="essay-panel-head"><div><span>同股同日去重 · 按5日结果比较</span><h2>小作文后行情验证</h2></div><strong>仅展示成熟样本</strong></div>
          <div className="essay-market-ranking"><header><span>标的</span><span>提及</span><span>样本</span><span>5日均值</span><span>胜率</span><span>超额</span></header>{data.marketImpact.items.map((item) => {
            const metric = item.metrics.find((row) => row.period === 5) ?? item.metrics[0];
            return <button key={item.tsCode} type="button" className={item.tsCode === selectedMarket?.tsCode ? 'is-active' : ''} onClick={() => setSelectedCode(item.tsCode)}><span><strong>{item.name}</strong><small>{item.tsCode}</small></span><b>{item.mentionCount}</b><span>{metric.sampleCount}</span><strong className={(metric.averageReturn ?? 0) >= 0 ? 'is-up' : 'is-down'}>{metric.averageReturn == null ? '—' : `${metric.averageReturn >= 0 ? '+' : ''}${metric.averageReturn.toFixed(2)}%`}</strong><span>{metric.winRate == null ? '—' : `${metric.winRate.toFixed(1)}%`}</span><span>{metric.averageExcessReturn == null ? '—' : `${metric.averageExcessReturn >= 0 ? '+' : ''}${metric.averageExcessReturn.toFixed(2)}%`}</span></button>;
          })}</div>
        </section>
      </div>

      <div className="essay-atlas-tertiary">
        <section className="essay-panel essay-lead-lag-panel">
          <div className="essay-panel-head"><div><span>提及量领先未来收益 · 皮尔逊相关</span><h2>领先 / 滞后检验</h2></div><strong>{selectedMarket?.name || '—'}</strong></div>
          <div className="essay-lead-lag">{selectedMarket?.leadLag.map((row) => <div key={row.lagSessions}><span>{row.lagSessions === 0 ? '同日' : `领先 ${row.lagSessions} 日`}</span><i><b className={(row.correlation ?? 0) >= 0 ? 'is-positive' : 'is-negative'} style={{ width: `${Math.min(Math.abs(row.correlation ?? 0) * 100, 100)}%` }} /></i><strong>{row.correlation == null ? '样本不足' : row.correlation.toFixed(3)}</strong><small>n={row.sampleCount}</small></div>)}</div>
          <div className="essay-attention-compare">{selectedMarket?.attentionComparison.map((row) => <div key={row.level}><span>{row.level}</span><strong className={(row.averageReturn5D ?? 0) >= 0 ? 'is-up' : 'is-down'}>{row.averageReturn5D == null ? '未成熟' : `${row.averageReturn5D >= 0 ? '+' : ''}${row.averageReturn5D.toFixed(2)}%`}</strong><small>5日胜率 {row.winRate5D == null ? '—' : `${row.winRate5D.toFixed(1)}%`} · n={row.sampleCount}</small></div>)}</div>
          <p className="essay-method-note">相关系数只描述关注强度和后续收益是否同步变化，不能解释因果；绝对值越接近1，同向或反向关系越强。</p>
        </section>

        <section className="essay-panel essay-ai-market-panel">
          <div className="essay-panel-head"><div><span>模型只解释上方统计，不补造行情</span><h2>AI 统计解读</h2></div><button type="button" disabled={!selectedMarket || aiLoading} onClick={() => void runAiInterpretation()}><Brain className={`h-3.5 w-3.5 ${aiLoading ? 'animate-pulse' : ''}`} />{aiLoading ? '分析中' : '生成解读'}</button></div>
          {selectedAi ? <div className="essay-ai-market-result"><strong>{selectedAi.conclusion}</strong><h3>统计依据</h3>{selectedAi.evidence.map((value) => <p key={value}>{value}</p>)}<h3>限制</h3>{selectedAi.limitations.map((value) => <p key={value}>{value}</p>)}<h3>后续验证</h3>{selectedAi.nextChecks.map((value) => <p key={value}>{value}</p>)}</div> : <p className="essay-method-note">选择股票后按需调用 DeepSeek。模型收到的是收益、胜率、样本量、置信区间与相关系数，不接收未来数据，也不把相关关系写成因果。</p>}
          {aiError ? <p className="essay-inline-error">{aiError}</p> : null}
        </section>

        <section className="essay-panel essay-funnel-panel">
          <div className="essay-panel-head"><div><span>行情 {data.marketImpact.coverage.priceStart || '—'} — {data.marketImpact.coverage.priceEnd || '—'}</span><h2>数据可用性</h2></div><strong>{data.marketImpact.coverage.benchmarkAvailable ? '沪深300可用' : '无基准'}</strong></div>
          <div className="essay-funnel">{data.evidenceFunnel.map((item, index) => <div key={item.name}><span><b>0{index + 1}</b>{item.name}</span><strong>{item.count.toLocaleString()}</strong><i><b style={{ width: `${item.count / funnelMax * 100}%` }} /></i></div>)}</div>
          <p className="essay-method-note">{data.marketImpact.entryRule}；{data.marketImpact.exitRule}。{data.marketImpact.priceBasis}；{data.marketImpact.dedupeRule}。来源：{data.marketImpact.coverage.sources.join('、') || '本地行情库'}。</p>
        </section>
      </div>
    </div>
  );
}

function SignalRow({
  item, onOpen, selectable = false, selected = false, onToggle,
}: {
  item: EssayAnalysis;
  onOpen: (item: EssayAnalysis) => void;
  selectable?: boolean;
  selected?: boolean;
  onToggle?: (topicId: string) => void;
}) {
  const analyzed = item.status === 'completed';
  const audioFiles = item.note.files.filter(file => file.assetKind === 'audio');
  const ordinaryFiles = item.note.files.filter(file => file.assetKind !== 'audio');
  const assetNames = [...audioFiles, ...ordinaryFiles].map(file => file.name).filter(Boolean).slice(0, 3);
  const row = (
    <button type="button" className="essay-signal-row" onClick={() => onOpen(item)}>
      <div className="essay-score"><strong>{analyzed ? (item.noveltyScore ?? 0) : '—'}</strong><span>{analyzed ? '增量' : item.status === 'media_only' ? '资料' : '待研判'}</span></div>
      <div className="min-w-0">
        <div className="essay-row-meta">
          <Badge variant={analyzed ? sentimentVariant(item.sentiment) : item.status === 'failed' ? 'danger' : 'default'}>
            {analyzed ? (SENTIMENT_LABELS[item.sentiment ?? ''] ?? '待判断') : (ANALYSIS_STATUS_LABELS[item.status] ?? item.status)}
          </Badge>
          {audioFiles.length ? <Badge variant="info"><Headphones className="h-3 w-3" />{audioFiles.length} 份录音</Badge> : null}
          {ordinaryFiles.length ? <Badge><Archive className="h-3 w-3" />{ordinaryFiles.length} 份文件</Badge> : null}
          <span>{analyzed ? (CATEGORY_LABELS[item.primaryCategory ?? ''] ?? item.primaryCategory ?? '未分类') : item.note.groupName}</span>
          <span>{formatTime(item.note.createdAt)}</span>
        </div>
        <h3>{item.note.title || '无标题纪要'}</h3>
        <p>{item.summary || (item.status === 'media_only' ? assetNames.join(' · ') : '') || item.errorMessage || item.note.content || '原文正文为空，点击查看图片或附件。'}</p>
      </div>
      <div className="essay-confidence"><strong>{analyzed ? `${Math.round((item.confidenceScore ?? 0) * 100)}%` : '原文'}</strong><span>{analyzed ? '置信' : '可检索'}</span></div>
    </button>
  );
  if (!selectable) return row;
  return (
    <div className={`essay-selectable-row ${selected ? 'is-selected' : ''}`}>
      <label className="essay-row-check">
        <input type="checkbox" checked={selected} onChange={() => onToggle?.(item.topicId)} aria-label={`选择小作文 ${item.note.title || item.topicId}`} />
      </label>
      {row}
    </div>
  );
}

function AudioFileRow({ item, selected, onToggle }: { item: EssayAudioFile; selected: boolean; onToggle: () => void }) {
  return (
    <article className={`essay-audio-row ${selected ? 'is-selected' : ''}`}>
      <label className="essay-row-check">
        <input type="checkbox" checked={selected} onChange={onToggle} aria-label={`选择录音 ${item.name}`} />
      </label>
      <div className="essay-audio-icon"><Headphones className="h-5 w-5" /></div>
      <div className="essay-audio-main">
        <h3>{item.name}</h3>
        <p>{item.noteTitle && item.noteTitle !== item.name ? `所属帖子：${item.noteTitle}` : '知识星球录音源文件'}</p>
        <div><span>{item.groupName}</span><span>{item.authorName || '作者未标注'}</span><span>{formatTime(item.createdAt)}</span></div>
      </div>
      <div className="essay-audio-facts">
        <strong>{formatDuration(item.durationSeconds ?? undefined) || '时长未知'}</strong>
        <span>{formatAssetSize(item.size ?? undefined) || '大小未知'}</span>
      </div>
      {item.downloadUrl ? <a className="essay-audio-download" href={item.downloadUrl} aria-label={`下载录音 ${item.name}`}><Download className="h-4 w-4" />单个下载</a> : <span className="essay-audio-unavailable">链接待恢复</span>}
    </article>
  );
}

function EssayDetail({ selected, onClose }: { selected: EssayAnalysis | null; onClose: () => void }) {
  const images = selected?.note.images ?? [];
  const files = selected?.note.files ?? [];
  const audioFiles = files.filter(file => file.assetKind === 'audio');
  const ordinaryFiles = files.filter(file => file.assetKind !== 'audio');
  const analyzed = selected?.status === 'completed';
  return (
    <Drawer isOpen={Boolean(selected)} onClose={onClose} title={selected?.note.title || '纪要详情'}>
      {selected ? (
        <div className="essay-detail">
          <div className="essay-detail-meta">
            <Badge variant={analyzed ? sentimentVariant(selected.sentiment) : selected.status === 'failed' ? 'danger' : 'default'}>{analyzed ? (SENTIMENT_LABELS[selected.sentiment ?? ''] ?? '待判断') : (ANALYSIS_STATUS_LABELS[selected.status] ?? selected.status)}</Badge>
            <Badge>{analyzed ? (CATEGORY_LABELS[selected.primaryCategory ?? ''] ?? selected.primaryCategory ?? '未分类') : selected.status === 'media_only' ? '源文件' : '未分类'}</Badge>
            <span>{selected.note.groupName}</span>
            <span>{selected.note.authorName || '作者未标注'}</span>
            <span>{formatTime(selected.note.createdAt, true)}</span>
          </div>

          <section className="essay-original">
            <div className="essay-section-title"><FileText className="h-4 w-4" />原文</div>
            <p>{selected.note.content || '原文正文为空；如有图片或附件，请通过下方远端链接查看。'}</p>
          </section>

          {audioFiles.length ? <section className="essay-detail-section">
            <div className="essay-section-title"><Headphones className="h-4 w-4" />录音源文件</div>
            <p className="essay-help">录音仅索引文件名和元数据，不做 AI 分析或语音转写；同步时不预下载到服务器，点击后按需下载知识星球源文件。</p>
            <div className="essay-assets">{audioFiles.map((item, index) => (item.downloadUrl || item.viewUrl) ? <a key={item.fileId || index} href={item.downloadUrl || item.viewUrl}>
              <Headphones className="h-4 w-4" /><span>{item.name || `录音 ${index + 1}`}{formatDuration(item.durationSeconds ?? item.duration) ? ` · ${formatDuration(item.durationSeconds ?? item.duration)}` : ''}{formatAssetSize(item.size) ? ` · ${formatAssetSize(item.size)}` : ''}</span><Download className="h-3.5 w-3.5" />
            </a> : null)}</div>
          </section> : null}

          {images.length || ordinaryFiles.length ? (
            <section className="essay-detail-section">
              <div className="essay-section-title"><Archive className="h-4 w-4" />图片与普通文件</div>
              <p className="essay-help">同步时不下载到本机，仅在点击时打开知识星球远端地址。</p>
              <div className="essay-assets">
                {images.map((item, index) => item.viewUrl ? (
                  <a key={item.imageId || index} href={item.viewUrl} target="_blank" rel="noreferrer">
                    <Image className="h-4 w-4" />查看图片 {index + 1}<ExternalLink className="h-3.5 w-3.5" />
                  </a>
                ) : null)}
                {ordinaryFiles.map((item, index) => (item.downloadUrl || item.viewUrl) ? (
                  <a key={item.fileId || index} href={item.downloadUrl || item.viewUrl}>
                    <FileText className="h-4 w-4" />{item.name || `附件 ${index + 1}`}<Download className="h-3.5 w-3.5" />
                  </a>
                ) : null)}
              </div>
            </section>
          ) : null}

          <section className="essay-detail-section">
            <div className="essay-section-title"><Brain className="h-4 w-4" />AI 研判</div>
            {selected.status === 'media_only' ? <p className="essay-summary">这是录音资料：已进入全库检索和信息流，只提供知识星球源文件下载，不进入 AI 分析。</p> : analyzed ? <>
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

          {selected.status !== 'media_only' ? <div className="essay-detail-grid">
            <section><h4>催化剂</h4>{selected.catalysts.length ? selected.catalysts.map((item) => <p key={item}>{item}</p>) : <p>未识别</p>}</section>
            <section className="is-risk"><h4>风险与证伪</h4>{[...selected.risks, ...selected.contradictions, ...selected.falsificationConditions].length ? [...selected.risks, ...selected.contradictions, ...selected.falsificationConditions].map((item) => <p key={item}>{item}</p>) : <p>未识别</p>}</section>
          </div> : null}

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
  const [audioList, setAudioList] = useState<EssayAudioFileList | null>(null);
  const [reports, setReports] = useState<EssayDailyReportList | null>(null);
  const [cloud, setCloud] = useState<EssayWordCloud | null>(null);
  const [historicalBacklog, setHistoricalBacklog] = useState<EssayHistoricalBacklog | null>(null);
  const [libraryStatsLoading, setLibraryStatsLoading] = useState(false);
  const [selected, setSelected] = useState<EssayAnalysis | null>(null);
  const [feedMode, setFeedMode] = useState<'essays' | 'audio'>('essays');
  const [selectedEssays, setSelectedEssays] = useState<Set<string>>(() => new Set());
  const [selectedAudio, setSelectedAudio] = useState<Map<string, EssayAudioFile>>(() => new Map());
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
  const [atlasHorizon, setAtlasHorizon] = useState<AtlasHorizon>('short');
  const [atlasStartDate, setAtlasStartDate] = useState('');
  const [atlasEndDate, setAtlasEndDate] = useState('');
  const [analysisBatchCount, setAnalysisBatchCount] = useState(100);
  const [analysisOrder, setAnalysisOrder] = useState<'newest' | 'oldest'>('newest');
  const [queueMessage, setQueueMessage] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [actionLoading, setActionLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [feedNotice, setFeedNotice] = useState<string | null>(null);
  const [exportingFeed, setExportingFeed] = useState(false);
  const [exportNotice, setExportNotice] = useState<string | null>(null);
  const [batchActionLoading, setBatchActionLoading] = useState(false);
  const [refreshKey, setRefreshKey] = useState(0);
  const [lastAutoRefreshAt, setLastAutoRefreshAt] = useState<string | null>(null);
  const feedPageCacheRef = useRef(new Map<string, EssayAnalysisList>());
  const audioListRef = useRef<EssayAudioFileList | null>(null);
  const feedInflightRef = useRef(new Map<string, Promise<EssayAnalysisList>>());
  const feedRequestVersionRef = useRef(0);
  const feedFilterSignatureRef = useRef('');
  const loadedViewsRef = useRef(new Set<RadarView>());

  useEffect(() => {
    const viewLabel = VIEW_META.find((item) => item.view === view)?.label ?? '小作文雷达';
    document.title = `${viewLabel} · 小作文雷达 - 乐子乌超级价值`;
  }, [view]);

  const loadView = useCallback(async (_requestVersion: number) => {
    void _requestVersion;
    if (view === 'feed') return;
    const initialLoad = !loadedViewsRef.current.has(view);
    if (initialLoad) {
      setLoading(true);
      setError(null);
    }
    let loaded = false;
    let partialError: string | null = null;
    try {
      if (view === 'overview') {
        const [nextInsights, nextStatus] = await Promise.allSettled([
          essayRadarApi.insights(30, 14), essayRadarApi.status(30),
        ]);
        if (nextInsights.status === 'fulfilled') { setInsights(nextInsights.value); loaded = true; }
        if (nextStatus.status === 'fulfilled') { setStatus(nextStatus.value); loaded = true; }
        if (nextInsights.status === 'rejected' && nextStatus.status === 'rejected') throw nextInsights.reason;
        if (nextInsights.status === 'rejected' || nextStatus.status === 'rejected') {
          partialError = '部分模块暂时不可用，已保留成功加载的真实数据。';
        }
      } else if (view === 'atlas') {
        setDeepInsights(await essayRadarApi.deepInsights({
          horizon: atlasHorizon,
          startDate: atlasHorizon === 'custom' ? atlasStartDate : undefined,
          endDate: atlasHorizon === 'custom' ? atlasEndDate : undefined,
        }));
        loaded = true;
      } else if (view === 'trends') {
        const [nextDashboard, nextInsights, nextCloud] = await Promise.allSettled([
          essayRadarApi.dashboard(30), essayRadarApi.insights(30, 14), essayRadarApi.wordCloud(cloudPeriod, cloudKind),
        ]);
        if (nextDashboard.status === 'fulfilled') { setDashboard(nextDashboard.value); loaded = true; }
        if (nextInsights.status === 'fulfilled') { setInsights(nextInsights.value); loaded = true; }
        if (nextCloud.status === 'fulfilled') { setCloud(nextCloud.value); loaded = true; }
        const failures = [nextDashboard, nextInsights, nextCloud].filter(item => item.status === 'rejected');
        if (failures.length === 3) throw (failures[0] as PromiseRejectedResult).reason;
        if (failures.length) partialError = `部分模块暂时不可用（${failures.length}/3），其余数据仍可查看。`;
      } else if (view === 'reports') {
        const [nextReports, nextInsights] = await Promise.allSettled([
          essayRadarApi.dailyReports(), essayRadarApi.insights(30, 14),
        ]);
        if (nextReports.status === 'fulfilled') { setReports(nextReports.value); loaded = true; }
        if (nextInsights.status === 'fulfilled') { setInsights(nextInsights.value); loaded = true; }
        if (nextReports.status === 'rejected' && nextInsights.status === 'rejected') throw nextReports.reason;
        if (nextReports.status === 'rejected' || nextInsights.status === 'rejected') {
          partialError = '日报或研判摘要暂时不可用，已保留成功加载的模块。';
        }
      } else {
        const [nextStatus, nextBacklog] = await Promise.all([
          essayRadarApi.status(30), essayRadarApi.historicalBacklog(),
        ]);
        setStatus(nextStatus);
        setHistoricalBacklog(nextBacklog);
        loaded = true;
      }
      if (loaded) {
        loadedViewsRef.current.add(view);
        setLastAutoRefreshAt(new Date().toISOString());
        if (!partialError) setError(null);
      }
      if (partialError && initialLoad) setError(partialError);
    } catch (caught) {
      if (initialLoad) setError(errorText(caught, '页面数据加载失败'));
    } finally {
      if (initialLoad) setLoading(false);
    }
  }, [atlasEndDate, atlasHorizon, atlasStartDate, cloudKind, cloudPeriod, view]);

  const loadFeed = useCallback(async (refreshVersion: number) => {
    if (view !== 'feed') return;
    if (feedMode === 'audio') {
      const backgroundRefresh = Boolean(audioListRef.current);
      if (!backgroundRefresh) setLoading(true);
      try {
        const result = await essayRadarApi.audioFiles({
          days, query: deferredQuery, page, pageSize: 20,
        });
        audioListRef.current = result;
        setAudioList(result);
        loadedViewsRef.current.add('feed');
        setLastAutoRefreshAt(new Date().toISOString());
        setFeedNotice(null);
      } catch {
        setFeedNotice(backgroundRefresh
          ? '录音文件索引暂时没有响应，当前保留上一次结果并自动重试。'
          : '录音文件索引正在加载，请稍等，页面会自动继续。');
      } finally {
        if (!backgroundRefresh) setLoading(false);
      }
      return;
    }
    const baseFilters = {
      days, query: deferredQuery, analysisStatus: analysisStatus || 'essay', sentiment, category, minImportance, pageSize: 20,
    };
    const filterSignature = `${JSON.stringify(baseFilters)}:page:${page}`;
    const backgroundRefresh = Boolean(list) && feedFilterSignatureRef.current === filterSignature;
    feedFilterSignatureRef.current = filterSignature;
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
    if (!backgroundRefresh) setFeedNotice(null);
    const cached = feedPageCacheRef.current.get(pageKey(page));
    if (cached) {
      setList(cached);
      setLoading(false);
      prefetchNext(cached);
      return;
    }
    if (!backgroundRefresh) setLoading(true);
    try {
      const result = await fetchPage(page, feedPageCacheRef.current.get(pageKey(1))?.total);
      if (requestVersion !== feedRequestVersionRef.current) return;
      setList(result);
      loadedViewsRef.current.add('feed');
      setLastAutoRefreshAt(new Date().toISOString());
      setFeedNotice(null);
      prefetchNext(result);
    } catch {
      if (requestVersion !== feedRequestVersionRef.current) return;
      if (!backgroundRefresh) {
        setFeedNotice(list
          ? '检索服务刚才没有及时响应，当前仍保留上一次结果，并会自动重试。'
          : '本地知识库正在准备索引，请稍等片刻，页面会自动继续加载。');
      }
    } finally {
      if (!backgroundRefresh && requestVersion === feedRequestVersionRef.current) setLoading(false);
    }
  }, [analysisStatus, category, days, deferredQuery, feedMode, list, minImportance, page, sentiment, view]);

  useEffect(() => { void loadView(refreshKey); }, [loadView, refreshKey]);
  useEffect(() => { void loadFeed(refreshKey); }, [loadFeed, refreshKey]);
  useEffect(() => {
    const requestAutomaticRefresh = () => {
      if (document.visibilityState === 'visible') setRefreshKey((value) => value + 1);
    };
    const handleVisibility = () => {
      if (document.visibilityState === 'visible') requestAutomaticRefresh();
    };
    // The feed is incremental and cheap; atlas/trend/report views aggregate
    // thousands of records and should reuse their prepared result instead of
    // forcing a full recomputation every 15 seconds. Source ingestion remains
    // realtime in the backend worker.
    const refreshInterval = view === 'feed' ? 15_000 : view === 'overview' ? 30_000 : 60_000;
    const timer = window.setInterval(requestAutomaticRefresh, refreshInterval);
    window.addEventListener('focus', requestAutomaticRefresh);
    document.addEventListener('visibilitychange', handleVisibility);
    return () => {
      window.clearInterval(timer);
      window.removeEventListener('focus', requestAutomaticRefresh);
      document.removeEventListener('visibilitychange', handleVisibility);
    };
  }, [view]);
  useEffect(() => {
    let active = true;
    const loadStatus = async () => {
      if (document.visibilityState !== 'visible') return;
      try {
        const nextStatus = await essayRadarApi.status(30);
        if (active) setStatus(nextStatus);
      } catch { /* Automatic status polling keeps the last factual state. */ }
    };
    void loadStatus();
    const timer = window.setInterval(() => void loadStatus(), 10000);
    return () => { active = false; window.clearInterval(timer); };
  }, []);
  useEffect(() => {
    if (view !== 'overview') return undefined;
    const timer = window.setTimeout(() => {
      void essayRadarApi.deepInsights({ horizon: 'short' }).then(setDeepInsights).catch(() => undefined);
    }, 1200);
    return () => window.clearTimeout(timer);
  }, [view]);
  useEffect(() => {
    if (view !== 'feed') return;
    let active = true;
    const initialLoad = !loadedViewsRef.current.has('feed');
    if (initialLoad) setLibraryStatsLoading(true);
    essayRadarApi.historicalBacklog()
      .then((result) => { if (active) setHistoricalBacklog(result); })
      .catch(() => { /* Keep search usable while knowledge-base statistics warm up. */ })
      .finally(() => { if (active && initialLoad) setLibraryStatsLoading(false); });
    return () => { active = false; };
  }, [refreshKey, view]);

  const workerActive = Boolean(status?.worker.running && status?.mcpSync.running);

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

  const exportFeed = async () => {
    if (exportingFeed || query !== deferredQuery || !(list?.total ?? 0)) return;
    setExportingFeed(true);
    setExportNotice(null);
    try {
      const blob = await essayRadarApi.exportFeed({
        days,
        query: deferredQuery,
        analysisStatus,
        sentiment,
        category,
        minImportance,
      });
      const timestamp = new Date().toISOString().replace(/[-:T]/g, '').slice(0, 14);
      downloadBlob(blob, `小作文检索结果_${timestamp}.xlsx`);
      setExportNotice(`已导出当前条件命中的 ${(list?.total ?? 0).toLocaleString()} 篇小作文。`);
    } catch (caught) {
      setExportNotice(errorText(caught, 'Excel 导出失败，请稍后重试。'));
    } finally {
      setExportingFeed(false);
    }
  };

  const toggleEssaySelection = (topicId: string) => {
    setSelectedEssays((current) => {
      const next = new Set(current);
      if (next.has(topicId)) next.delete(topicId); else next.add(topicId);
      return next;
    });
  };

  const toggleAudioSelection = (item: EssayAudioFile) => {
    setSelectedAudio((current) => {
      const next = new Map(current);
      if (next.has(item.assetId)) next.delete(item.assetId); else next.set(item.assetId, item);
      return next;
    });
  };

  const toggleCurrentPageSelection = () => {
    if (feedMode === 'audio') {
      const items = audioList?.items ?? [];
      const allSelected = items.length > 0 && items.every((item) => selectedAudio.has(item.assetId));
      setSelectedAudio((current) => {
        const next = new Map(current);
        items.forEach((item) => { if (allSelected) next.delete(item.assetId); else next.set(item.assetId, item); });
        return next;
      });
      return;
    }
    const items = list?.items ?? [];
    const allSelected = items.length > 0 && items.every((item) => selectedEssays.has(item.topicId));
    setSelectedEssays((current) => {
      const next = new Set(current);
      items.forEach((item) => { if (allSelected) next.delete(item.topicId); else next.add(item.topicId); });
      return next;
    });
  };

  const downloadSelected = async () => {
    const selectedCount = feedMode === 'audio' ? selectedAudio.size : selectedEssays.size;
    if (!selectedCount || batchActionLoading) return;
    setBatchActionLoading(true);
    setExportNotice(null);
    try {
      if (feedMode === 'audio') {
        const blob = await essayRadarApi.downloadSelectedAudio([...selectedAudio.values()].map((item) => ({
          topicId: item.topicId, fileId: item.fileId,
        })));
        downloadBlob(blob, `知识星球录音_已选${selectedCount}个_${new Date().toISOString().slice(0, 10)}.zip`);
        setExportNotice(`已将 ${selectedCount} 个录音源文件打包下载。`);
      } else {
        const blob = await essayRadarApi.exportSelected([...selectedEssays]);
        downloadBlob(blob, `小作文已选原文_${selectedCount}篇_${new Date().toISOString().slice(0, 10)}.xlsx`);
        setExportNotice(`已下载 ${selectedCount} 篇小作文的完整原文和分析标签。`);
      }
    } catch (caught) {
      setExportNotice(errorText(caught, feedMode === 'audio' ? '录音批量下载失败，请稍后重试。' : '小作文批量下载失败，请稍后重试。'));
    } finally {
      setBatchActionLoading(false);
    }
  };

  const yesterday = insights?.yesterday;
  const currentFeedTotal = feedMode === 'audio' ? (audioList?.total ?? 0) : (list?.total ?? 0);
  const totalPages = Math.max(1, Math.ceil(currentFeedTotal / 20));
  const modelComparison = insights?.modelComparison;
  const libraryTotal = historicalBacklog?.totalNotes ?? 0;
  const libraryCompleted = historicalBacklog?.completed ?? 0;
  const libraryPending = historicalBacklog?.pending ?? 0;
  const libraryProcessing = historicalBacklog?.processing ?? 0;
  const libraryFailed = historicalBacklog?.failed ?? 0;
  const libraryUnqueued = historicalBacklog?.unqueued ?? 0;
  const libraryMediaOnly = historicalBacklog?.mediaOnly ?? 0;
  const librarySegments = [
    { key: 'completed', label: '已分析', value: libraryCompleted },
    { key: 'processing', label: '分析中', value: libraryProcessing },
    { key: 'pending', label: '排队中', value: libraryPending },
    { key: 'failed', label: '失败', value: libraryFailed },
    { key: 'media', label: '录音资料', value: libraryMediaOnly },
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
  const changeAtlasPeriod = (nextHorizon: AtlasHorizon, nextStart?: string, nextEnd?: string) => {
    if (nextHorizon === 'custom' && (!nextStart || !nextEnd || nextStart > nextEnd)) return;
    setAtlasHorizon(nextHorizon);
    if (nextHorizon === 'custom') {
      setAtlasStartDate(nextStart || '');
      setAtlasEndDate(nextEnd || '');
    }
  };

  return (
    <AppPage className="essay-terminal max-w-none">
      <header className="essay-header">
        <div>
          <div className="essay-live-line"><span className={workerActive ? 'is-live' : ''} />知识星球自动增量库 · 最近入库 {formatTime(insights?.latestDataAt || status?.mcpSync.lastSyncAt || historicalBacklog?.latestSyncedAt, true)}</div>
          <h1>{view === 'atlas' ? '小作文洞察图谱' : '小作文研判台'}</h1>
          <p>{view === 'atlas' ? '按短中长期或自定义窗口，把主题提及与真实行情放在同一时间轴验证。' : '新增小作文自动入库、自动分析、自动生成日报；页面静默更新，不需要手动刷新。'}</p>
        </div>
        <div className="essay-auto-state" aria-label="自动更新状态"><strong>{workerActive ? '全自动运行中' : '自动恢复中'}</strong><span>MCP {status?.mcpSync.pollSeconds ?? 10} 秒 · 页面 15 秒</span><small>本页更新 {formatClock(lastAutoRefreshAt)}</small></div>
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

      {view === 'atlas' ? <DeepInsightsView
        data={deepInsights}
        horizon={atlasHorizon}
        startDate={atlasStartDate || deepInsights?.period.startDate || ''}
        endDate={atlasEndDate || deepInsights?.period.endDate || ''}
        onPeriodChange={changeAtlasPeriod}
        onFilter={openFeedFor}
      /> : null}

      {view === 'feed' ? (
        <div className="essay-view">
          <section className="essay-library-board" aria-label="小作文知识库总览">
            <div className="essay-library-identity">
              <div><span>本地 SQLite 知识库</span><strong>{libraryStatsLoading && !historicalBacklog ? '读取中' : libraryTotal.toLocaleString()}</strong><small>条正文/录音/文件主题已入库</small></div>
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
          <div className="essay-content-switch" role="tablist" aria-label="信息流内容类型">
            <button type="button" role="tab" aria-selected={feedMode === 'essays'} className={feedMode === 'essays' ? 'is-active' : ''} onClick={() => { setFeedMode('essays'); setPage(1); setFeedNotice(null); setExportNotice(null); }}><FileText className="h-4 w-4" /><span>小作文</span><small>原文与分析</small></button>
            <button type="button" role="tab" aria-selected={feedMode === 'audio'} className={feedMode === 'audio' ? 'is-active' : ''} onClick={() => { setFeedMode('audio'); setPage(1); setFeedNotice(null); setExportNotice(null); }}><Headphones className="h-4 w-4" /><span>录音文件</span><small>一个文件一行</small></button>
          </div>
          <section className="essay-filter-panel">
            <label className="essay-search"><Search className="h-4 w-4" /><input aria-label={feedMode === 'audio' ? '搜索录音文件' : '搜索小作文'} value={query} onChange={(event) => { setQuery(event.target.value); setPage(1); }} placeholder={feedMode === 'audio' ? '严格搜索每个录音文件名' : '检索全库：正文、作者、股票或 AI 标签'} /></label>
            <select aria-label="时间范围" value={days} onChange={(event) => { setDays(Number(event.target.value)); setPage(1); }}><option value={0}>全部入库</option><option value={1}>今日</option><option value={7}>近7日</option><option value={30}>近30日</option><option value={365}>近1年</option><option value={730}>近2年</option></select>
            {feedMode === 'essays' ? <>
              <select aria-label="AI状态筛选" value={analysisStatus} onChange={(event) => { setAnalysisStatus(event.target.value); setPage(1); }}><option value="">全部小作文</option><option value="completed">已分析</option><option value="uncompleted">未完成分析</option><option value="not_queued">未入队</option><option value="pending">排队中</option><option value="processing">分析中</option><option value="failed">分析失败</option></select>
              <select aria-label="情绪筛选" value={sentiment} onChange={(event) => { setSentiment(event.target.value); setPage(1); }}><option value="">全部情绪</option>{Object.entries(SENTIMENT_LABELS).map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select>
              <select aria-label="类型筛选" value={category} onChange={(event) => { setCategory(event.target.value); setPage(1); }}><option value="">全部类型</option>{Object.entries(CATEGORY_LABELS).map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select>
              <select aria-label="重要度筛选" value={minImportance} onChange={(event) => { setMinImportance(Number(event.target.value)); setPage(1); }}><option value={0}>全部重要度</option><option value={60}>≥ 60</option><option value={75}>≥ 75</option><option value={85}>≥ 85</option></select>
            </> : <div className="essay-audio-filter-note"><Headphones className="h-4 w-4" />严格按文件名召回，不会连带展示同帖其他录音</div>}
          </section>
          <div className="essay-feed-summary" aria-live="polite">
            <span>{query !== deferredQuery ? '等待输入完成…' : loading ? '正在检索整个本地库，当前结果继续保留…' : feedNotice || <>当前条件命中 <strong>{currentFeedTotal.toLocaleString()}</strong> {feedMode === 'audio' ? '个录音文件' : '篇小作文'} · {days ? `近 ${days} 日` : '全部已入库'}</>}</span>
            <div className="essay-feed-summary-actions">
              <button type="button" onClick={() => { setQuery(''); setAnalysisStatus(''); setSentiment(''); setCategory(''); setMinImportance(0); setDays(0); setPage(1); setExportNotice(null); }}>清除筛选</button>
              {feedMode === 'essays' ? <button
                type="button"
                className="essay-feed-export"
                onClick={() => void exportFeed()}
                disabled={exportingFeed || query !== deferredQuery || loading || !(list?.total ?? 0)}
                aria-label="导出当前搜索结果 Excel"
              >
                {exportingFeed ? <RefreshCw className="h-3.5 w-3.5 animate-spin" /> : <Download className="h-3.5 w-3.5" />}
                {exportingFeed ? '正在生成' : '导出全部结果'}
              </button> : null}
            </div>
          </div>
          <div className="essay-batch-bar">
            <span>已选 <strong>{feedMode === 'audio' ? selectedAudio.size : selectedEssays.size}</strong> {feedMode === 'audio' ? '个录音' : '篇小作文'}</span>
            <div>
              <button type="button" onClick={toggleCurrentPageSelection} disabled={!currentFeedTotal}>全选/取消本页</button>
              <button type="button" onClick={() => feedMode === 'audio' ? setSelectedAudio(new Map()) : setSelectedEssays(new Set())} disabled={feedMode === 'audio' ? !selectedAudio.size : !selectedEssays.size}>清空已选</button>
              <button type="button" className="is-primary" onClick={() => void downloadSelected()} disabled={batchActionLoading || (feedMode === 'audio' ? !selectedAudio.size : !selectedEssays.size)}>
                {batchActionLoading ? <RefreshCw className="h-3.5 w-3.5 animate-spin" /> : <Download className="h-3.5 w-3.5" />}
                {batchActionLoading ? '正在打包' : feedMode === 'audio' ? '批量下载录音 ZIP' : '下载已选原文 Excel'}
              </button>
            </div>
          </div>
          {exportNotice ? <div className="essay-export-notice" role="status">{exportNotice}</div> : null}
          <section className="essay-panel essay-feed-panel">
            {feedMode === 'essays' ? <div className="essay-feed-list">{list?.items.map((item) => <SignalRow key={item.topicId} item={item} onOpen={setSelected} selectable selected={selectedEssays.has(item.topicId)} onToggle={toggleEssaySelection} />)}</div> : <div className="essay-audio-list">{audioList?.items.map((item) => <AudioFileRow key={item.assetId} item={item} selected={selectedAudio.has(item.assetId)} onToggle={() => toggleAudioSelection(item)} />)}</div>}
            {!loading && !currentFeedTotal ? <EmptyState title={feedMode === 'audio' ? '没有匹配的录音文件' : '没有匹配的小作文'} description="调整关键词或时间范围。" icon={feedMode === 'audio' ? <Headphones className="h-7 w-7" /> : <Database className="h-7 w-7" />} /> : null}
            {currentFeedTotal ? <div className="essay-pagination"><span>第 {page} / {totalPages} 页</span><div><button disabled={page <= 1} onClick={() => setPage((value) => value - 1)}>上一页</button><button disabled={page >= totalPages} onClick={() => setPage((value) => value + 1)}>下一页</button></div></div> : null}
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
            <div className="essay-panel-head"><div><span>{modelComparison?.reportDate || '最近报告日'}</span><h2>模型共识与分歧</h2></div><Badge variant={status?.dailyReportWorker.running ? 'success' : 'warning'}>{status?.dailyReportWorker.running ? `每日 ${status.dailyReportWorker.runHourShanghai}:00 自动生成` : '自动任务恢复中'}</Badge></div>
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
            <div className="essay-panel-head"><div><span>新增或正文变化后自动进入 AI 队列</span><h2>实时分析</h2></div><span className={`essay-status ${status?.worker.running ? 'is-running' : ''}`}>{status?.worker.running ? '自动分析中' : '自动恢复中'}</span></div>
            <div className="essay-progress"><div><span>近30天分析覆盖</span><strong>{status?.progress.coveragePercent ?? 0}%</strong></div><div><i style={{ width: `${status?.progress.coveragePercent ?? 0}%` }} /></div></div>
            <div className="essay-system-metrics"><Metric label="已完成" value={status?.progress.completed ?? 0} /><Metric label="待处理" value={status?.progress.pending ?? 0} /><Metric label="失败" value={status?.progress.failed ?? 0} tone="danger" /></div>
            <p className="essay-system-note">队列每 {status?.worker.pollSeconds ?? '—'} 秒自动检查；临时失败按指数退避重试，旧语料不会因服务重启被重新入队。</p>
          </section>

          <section className="essay-panel">
            <div className="essay-panel-head"><div><span>直接 MCP → SQLite · 附件仅保存链接</span><h2>知识星球增量同步</h2></div><span className={`essay-status ${status?.mcpSync.running ? 'is-running' : ''}`}>{status?.mcpSync.running ? '运行中' : '已停止'}</span></div>
            <p className="essay-system-note">每 {status?.mcpSync.pollSeconds ?? 10} 秒自动检查增量 · 最近成功 {formatTime(status?.mcpSync.lastSyncAt, true)} · 失败由同步看门狗自动唤醒，不需要人工操作。</p>
            <div className="essay-group-list">{status?.mcpSync.groups.map((group) => <div key={group.groupId}><strong>{group.groupName}</strong><Badge variant={group.lastStatus === 'success' ? 'success' : group.lastStatus === 'failed' ? 'danger' : 'default'}>{group.lastStatus}</Badge><span>累计入库 {group.totalSaved.toLocaleString()}</span><span>最新游标 {formatTime(group.lastTopicAt)}</span></div>)}</div>
          </section>

          <section className="essay-panel essay-history-panel">
            <div className="essay-panel-head"><div><span>只使用本地已入库、尚未创建 AI 任务的纪要</span><h2>历史小作文补分析</h2></div><Badge variant={historicalBacklog?.unqueued ? 'warning' : 'success'}>{historicalBacklog?.unqueued ? `${historicalBacklog.unqueued.toLocaleString()} 篇可选` : '已全部入队'}</Badge></div>
            <p className="essay-system-note">不重新抓取知识星球，不扩大所选数量；临时失败仍由后台按受控退避自动重试。</p>
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
        </div>
      ) : null}

      <EssayDetail selected={selected} onClose={() => setSelected(null)} />
    </AppPage>
  );
};

export default EssayRadarPage;
