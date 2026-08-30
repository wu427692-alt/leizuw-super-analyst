import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { CSSProperties } from 'react';
import {
  Activity, ArrowRight, Binary, Boxes, BrainCircuit, ChevronRight, CircleDot,
  Database, Download, GitBranch, Layers3, Network, RefreshCw, Scale, Search, ShieldCheck,
  Sparkles, Star, Target, TrendingUp, X,
} from 'lucide-react';
import { Bar, BarChart, CartesianGrid, ReferenceLine, ResponsiveContainer, Scatter, ScatterChart, Tooltip, XAxis, YAxis, ZAxis } from 'recharts';
import { AppPage } from '../components/common';
import { EmptyState } from '../components/common/EmptyState';
import { conceptThemesApi, type ConceptClusterDetail, type ConceptLeaders, type ConceptOverview, type ConceptRotation, type ConceptStock, type ConceptTheme, type StockThemeLens, type ThemeDetail } from '../api/conceptThemes';
import { useDebouncedValue } from '../hooks/useDebouncedValue';
import { usePageActivationRefresh } from '../hooks/usePageActivationRefresh';
import './ConceptThemesPage.css';

const SOURCE_COLOR: Record<string, string> = {
  ths: '#5AA7FF', dc_board: '#7C5CFC', dc_theme: '#00D4B8', kpl: '#FFB547', tdx: '#F06C9B', sw: '#98A2B3',
};
const SOURCE_LABEL: Record<string, string> = {
  ths: '同花顺', dc_board: '东财板块', dc_theme: '东财题材', kpl: '开盘啦', tdx: '通达信', sw: '申万',
};
const TYPE_LABEL: Record<string, string> = {
  concept: '概念', theme: '题材', industry: '行业', region: '地域', style: '风格', feature: '特色', broad: '宽基',
};

const number = (value?: number | null) => new Intl.NumberFormat('zh-CN').format(value ?? 0);
const metric = (value?: number | null, digits = 2) => value == null ? '—' : Number(value).toFixed(digits);
const signed = (value?: number | null, suffix = '%') => value == null ? '—' : `${value >= 0 ? '+' : ''}${Number(value).toFixed(2)}${suffix}`;

function ScoreRing({ value, label }: { value: number; label: string }) {
  const safe = Math.max(0, Math.min(100, value || 0));
  return <div className="concept-score-ring" style={{ '--score-angle': `${safe * 3.6}deg` } as CSSProperties}>
    <strong>{safe.toFixed(0)}</strong><span>{label}</span>
  </div>;
}

function RotationSparkline({ points }: { points: ConceptRotation['items'][number]['points'] }) {
  const values = points.map(item => item.pctChange).filter((value): value is number => typeof value === 'number');
  if (values.length < 2) return <span className="concept-rotation-new">NEW</span>;
  const low = Math.min(...values); const high = Math.max(...values); const spread = Math.max(0.1, high - low);
  const path = values.map((value, index) => `${index ? 'L' : 'M'}${(index / Math.max(1, values.length - 1) * 72).toFixed(1)},${(23 - (value - low) / spread * 20).toFixed(1)}`).join(' ');
  return <svg className="concept-rotation-spark" viewBox="0 0 72 26" role="img" aria-label={`${values.length}日题材涨跌轨迹`}><path d={path} /></svg>;
}

function ThemeCard({ item, active, onClick }: { item: ConceptTheme; active: boolean; onClick: () => void }) {
  return <button type="button" className={`concept-theme-card ${active ? 'is-active' : ''}`} onClick={onClick}>
    <div className="concept-theme-card__top">
      <span className="concept-source-dot" style={{ background: SOURCE_COLOR[item.source] ?? '#98A2B3' }} />
      <span>{item.sourceLabel}</span><code>{item.sourceCode}</code>
    </div>
    <h3>{item.canonicalName}</h3>
    {item.name !== item.canonicalName ? <p>源名称 · {item.name}</p> : <p>{item.family} → {item.cluster}</p>}
    <div className="concept-theme-card__metrics">
      <span><b>{number(item.constituentCount)}</b>成分</span>
      <span><b>{metric(item.heatScore, 0)}</b>热度</span>
      <span data-tone={(item.pctChange ?? 0) >= 0 ? 'up' : 'down'}><b>{signed(item.pctChange)}</b>当日</span>
    </div>
    <div className="concept-theme-card__foot"><span>{TYPE_LABEL[item.themeType] ?? item.themeType} · L{item.level} · {item.canonicalSourceCount || 1}源/{item.canonicalNodeCount || 1}节点</span><ChevronRight /></div>
  </button>;
}

function SourceRail({ themes }: { themes: ConceptTheme[] }) {
  return <div className="concept-source-rail">
    {themes.map((item, index) => <div className="concept-source-node" key={`${item.source}-${item.sourceCode}`}>
      <span style={{ background: SOURCE_COLOR[item.source] ?? '#98A2B3' }}>{index + 1}</span>
      <div><strong>{item.sourceLabel}</strong><small>{item.name} · {item.sourceCode}</small></div>
      <em>{number(item.constituentCount)}股</em>
    </div>)}
  </div>;
}

function ConsensusMatrix({ stocks }: { stocks: ConceptStock[] }) {
  const sources = ['ths', 'dc_board', 'dc_theme', 'kpl', 'tdx', 'sw'];
  const rows = [...stocks].sort((left, right) => right.sourceCount - left.sourceCount || right.weightScore - left.weightScore).slice(0, 12);
  if (!rows.length) return null;
  return <section className="concept-consensus-matrix">
    <header><div><strong>核心成分 × 六源共识矩阵</strong><small>亮点表示该来源明确将股票纳入当前规范题材</small></div><span>TOP {rows.length}</span></header>
    <div className="concept-matrix-grid" style={{ '--matrix-columns': sources.length } as CSSProperties}>
      <b>股票</b>{sources.map(source => <b key={source}>{SOURCE_LABEL[source] ?? source}</b>)}
      {rows.map(stock => <div className="concept-matrix-row" key={stock.tsCode}>
        <span><strong>{stock.name}</strong><small>{stock.tsCode}</small></span>
        {sources.map(source => <i key={source} className={stock.sources.includes(source) ? 'is-hit' : ''} title={`${stock.name} · ${SOURCE_LABEL[source]} · ${stock.sources.includes(source) ? '已纳入' : '未纳入'}`} />)}
      </div>)}
    </div>
  </section>;
}

function BetaAlphaMap({ stocks, horizonDays }: { stocks: ConceptStock[]; horizonDays: number }) {
  const points = stocks.filter(item => typeof item.beta === 'number' && typeof item.residualReturn === 'number')
    .sort((left, right) => right.weightScore - left.weightScore).slice(0, 160)
    .map(item => ({ name: item.name, code: item.tsCode, beta: item.beta, alpha: item.residualReturn, weight: Math.max(16, item.weightScore) }));
  if (points.length < 3) return null;
  return <section className="concept-beta-alpha-map">
    <header><div><strong>Beta / Alpha 四象限</strong><small>横轴题材敏感度 · 纵轴 {horizonDays} 日窗口 Alpha · 气泡为题材权重</small></div><span>{points.length} 只已归因</span></header>
    <div className="concept-quadrant-labels"><i>独立强势</i><i>高弹性领涨</i><i>低相关/弱势</i><i>高弹性落后</i></div>
    <ResponsiveContainer width="100%" height={245}>
      <ScatterChart margin={{ top: 12, right: 16, bottom: 8, left: 4 }}>
        <CartesianGrid stroke="rgba(90,135,190,.12)" />
        <XAxis type="number" dataKey="beta" name="题材 Beta" tick={{ fill: '#607a9a', fontSize: 8 }} axisLine={false} tickLine={false} domain={['auto', 'auto']} />
        <YAxis type="number" dataKey="alpha" name={`${horizonDays}日 Alpha`} unit="%" tick={{ fill: '#607a9a', fontSize: 8 }} axisLine={false} tickLine={false} width={36} domain={['auto', 'auto']} />
        <ZAxis type="number" dataKey="weight" range={[24, 150]} />
        <ReferenceLine x={1} stroke="rgba(90,167,255,.55)" strokeDasharray="4 4" />
        <ReferenceLine y={0} stroke="rgba(49,215,197,.45)" strokeDasharray="4 4" />
        <Tooltip cursor={{ strokeDasharray: '3 3' }} contentStyle={{ background: '#071326', border: '1px solid #29476c', fontSize: 10 }} formatter={(value, name) => [typeof value === 'number' ? value.toFixed(2) : value, name]} />
        <Scatter name="成分股" data={points} fill="#4e8cff" fillOpacity={0.66} stroke="#83bdff" />
      </ScatterChart>
    </ResponsiveContainer>
  </section>;
}

function ThemeResearchQueue({ stocks, onOpen }: { stocks: ConceptStock[]; onOpen: (tsCode: string) => void }) {
  const take = (values: ConceptStock[]) => values.slice(0, 6);
  const groups = [
    { key: 'consensus', title: '多源核心', note: '来源≥2且权重靠前', values: take([...stocks].filter(item => item.sourceCount >= 2).sort((a, b) => b.weightScore - a.weightScore)) },
    { key: 'elastic', title: '高 Beta 弹性', note: 'Beta≥1且样本有效', values: take([...stocks].filter(item => (item.beta ?? -99) >= 1 && item.confidence !== 'insufficient').sort((a, b) => (b.beta ?? 0) - (a.beta ?? 0))) },
    { key: 'alpha', title: '独立正 Alpha', note: 'Beta<0.8且窗口Alpha>0', values: take([...stocks].filter(item => (item.beta ?? 99) < .8 && (item.residualReturn ?? 0) > 0).sort((a, b) => (b.residualReturn ?? 0) - (a.residualReturn ?? 0))) },
    { key: 'divergence', title: '负 Alpha 背离', note: 'Beta≥0.8但窗口Alpha<0', values: take([...stocks].filter(item => (item.beta ?? -99) >= .8 && (item.residualReturn ?? 0) < 0).sort((a, b) => (a.residualReturn ?? 0) - (b.residualReturn ?? 0))) },
  ];
  return <section className="concept-research-queue"><header><div><strong>题材研究优先队列</strong><small>由来源共识与归因结果生成，用于决定先核验谁，不构成买卖信号</small></div><Target /></header><div>{groups.map(group => <article key={group.key} data-kind={group.key}><h3>{group.title}</h3><p>{group.note}</p>{group.values.map(item => <button type="button" key={item.tsCode} title={item.betaInterpretation} onClick={() => onOpen(item.tsCode)}><span>{item.name}<small>{item.tsCode}</small></span><b>{group.key === 'consensus' ? `${item.sourceCount}源 · W${metric(item.weightScore, 0)}` : group.key === 'elastic' ? `β ${metric(item.beta)}` : signed(item.residualReturn)}</b></button>)}{!group.values.length ? <em>当前无满足条件的成分</em> : null}</article>)}</div></section>;
}

function MarketConsensusRadar({ value, mode, onMode, onOpen }: {
  value: ConceptLeaders | null;
  mode: ConceptLeaders['mode'];
  onMode: (mode: ConceptLeaders['mode']) => void;
  onOpen: (tsCode: string) => void;
}) {
  const modes: Array<[ConceptLeaders['mode'], string]> = [['consensus', '多题材共识'], ['beta', '高弹性'], ['alpha', '独立强势'], ['specificity', '独特题材']];
  return <section className="concept-market-radar">
    <header><div><span><Target /> CROSS-THEME STOCK RADAR</span><h2>跨题材股票雷达</h2><p>不是按单个概念涨跌排名，而是在同一归因截止日比较股票的多源题材覆盖、Beta 与独特 Alpha。</p></div><div>{modes.map(([key, label]) => <button type="button" className={mode === key ? 'is-active' : ''} onClick={() => onMode(key)} key={key}>{label}</button>)}</div></header>
    <div className="concept-market-radar__grid">
      {value?.items.slice(0, 12).map((item, index) => { const focus = mode === 'alpha' ? item.alphaFocus : mode === 'beta' ? item.betaFocus : mode === 'specificity' ? item.specificityFocus : item.primaryThemes[0]; return <button type="button" key={item.tsCode} onClick={() => onOpen(item.tsCode)}>
        <i>{String(index + 1).padStart(2, '0')}</i><div><strong>{item.name}{item.inWatchlist ? <Star aria-label="我的自选" /> : null}</strong><code>{item.tsCode}</code></div>
        <b>{mode === 'alpha' ? signed(focus?.residualReturn) : mode === 'beta' ? `β ${metric(focus?.beta)}` : mode === 'specificity' ? metric(focus?.specificityScore, 0) : `${item.consensusThemeCount}题材`}</b>
        <p>{focus?.canonicalName || item.primaryThemes.map(theme => theme.canonicalName).slice(0, 2).join(' · ') || '待补充归因'}</p>
        <span>W{metric(item.averageWeight, 0)} · 最宽 {item.sourceBreadth} 源 · 雷达 {metric(item.radarScore, 0)}</span><ChevronRight />
      </button>; })}
      {!value?.items.length ? <p className="concept-market-radar__empty">跨题材归因正在随成分扫描自动扩展；不会用单源题材凑榜。</p> : null}
    </div>
    <footer>{value?.asOfDate || '最新交易日'} · {value?.totalCandidates || 0} 个合格候选 · {value?.method || '仅展示同一截止日、满足共识与回归质量条件的真实结果。'}</footer>
  </section>;
}

function ClusterAggregate({ value, onOpen }: { value: ConceptClusterDetail; onOpen: (tsCode: string) => void }) {
  return <section className="concept-cluster-aggregate">
    <header><div><span>LEVEL 2 AGGREGATE</span><h3>{value.cluster}</h3><p>{value.themeNodes} 个原始节点 · {value.canonicalThemes} 个规范题材 · {value.totalStocks} 只去重股票 · {value.sourceCount} 个来源</p></div><b>{value.asOfDate || '最新快照'}</b></header>
    <div>{value.items.slice(0, 12).map(item => <button type="button" onClick={() => onOpen(item.tsCode)} key={item.tsCode}>
      <span><strong>{item.name}{item.inWatchlist ? <Star aria-label="我的自选" /> : null}</strong><code>{item.tsCode}</code></span><b>{metric(item.clusterScore, 0)}</b><small>{item.themeCount} 题材 · {item.sourceCount} 源</small><p>{item.dominantExposure?.canonicalName || item.themes.slice(0, 2).join(' · ')}</p>
    </button>)}</div>
    <footer>{value.method}</footer>
  </section>;
}

function StockRow({ item, selected, onClick }: { item: ConceptStock; selected: boolean; onClick: () => void }) {
  return <button type="button" className={`concept-stock-row ${selected ? 'is-active' : ''}`} onClick={onClick}>
    <span className="concept-stock-name"><strong>{item.name || item.tsCode}{item.inWatchlist ? <Star aria-label="我的自选" /> : null}</strong><small>{item.tsCode}</small></span>
    <span><strong>{item.weightScore ? item.weightScore.toFixed(1) : '待算'}</strong><small>题材权重</small></span>
    <span title={item.betaInterpretation}><strong>{metric(item.beta)}</strong><small>题材 Beta · {item.confidence === 'high' ? '高' : item.confidence === 'medium' ? '中' : '低'}置信</small></span>
    <span data-tone={(item.residualReturn ?? 0) >= 0 ? 'up' : 'down'}><strong>{signed(item.residualReturn)}</strong><small>窗口 Alpha</small></span>
    <span><strong>{item.sourceCount}</strong><small>独立来源</small></span>
    <span className="concept-stock-reason">{item.reasons?.[0] || '结构化来源已确认，尚无文字入选原因'}</span>
    <ChevronRight />
  </button>;
}

function StockLensPanel({ value, onClose }: { value: StockThemeLens; onClose: () => void }) {
  const chart = value.primaryThemes.map(item => ({
    name: item.canonicalName.length > 9 ? `${item.canonicalName.slice(0, 9)}…` : item.canonicalName,
    weight: item.weightScore,
    beta: item.beta ?? 0,
  }));
  return <aside className="concept-stock-lens">
    <div className="concept-stock-lens__head">
      <div><span>STOCK EXPOSURE LENS</span><h2>{value.name || value.tsCode}</h2><p>{value.tsCode} · 截止 {value.asOfDate || '最新入库交易日'}</p></div>
      <button type="button" onClick={onClose} aria-label="关闭股票题材透镜"><X /></button>
    </div>
    <div className="concept-lens-summary">
      <div><strong>{value.summary.themeCount}</strong><span>归属题材</span></div>
      <div><strong>{value.summary.sourceCount}</strong><span>覆盖来源</span></div>
      <div><strong>{value.summary.consensusCount}</strong><span>多源共识</span></div>
      <div><strong>{value.summary.alphaPositiveCount}</strong><span>正向窗口Alpha</span></div>
    </div>
    <div className="concept-lens-chart">
      <ResponsiveContainer width="100%" height={230}>
        <BarChart data={chart} layout="vertical" margin={{ left: 8, right: 24 }}>
          <CartesianGrid stroke="rgba(130,150,190,.13)" horizontal={false} />
          <XAxis type="number" domain={[0, 100]} tick={{ fill: '#7e91ad', fontSize: 10 }} axisLine={false} tickLine={false} />
          <YAxis dataKey="name" type="category" width={102} tick={{ fill: '#c8d7eb', fontSize: 10 }} axisLine={false} tickLine={false} />
          <Tooltip cursor={{ fill: 'rgba(82,127,255,.08)' }} contentStyle={{ background: '#081327', border: '1px solid #29446b', fontSize: 11 }} />
          <Bar dataKey="weight" name="题材权重" fill="#4e8cff" radius={[0, 5, 5, 0]} />
        </BarChart>
      </ResponsiveContainer>
    </div>
    <div className="concept-lens-list">
      {value.primaryThemes.map(item => {
        const rawCiLow = item.components?.betaCiLow;
        const rawCiHigh = item.components?.betaCiHigh;
        const ciLow = typeof rawCiLow === 'number' ? rawCiLow : Number.NaN;
        const ciHigh = typeof rawCiHigh === 'number' ? rawCiHigh : Number.NaN;
        const hasInterval = Number.isFinite(ciLow) && Number.isFinite(ciHigh) && (item.observations ?? 0) >= 20;
        return <article key={item.canonicalName}>
        <div><h3>{item.canonicalName}</h3><span>{item.sources.map(source => SOURCE_LABEL[source] ?? source).join(' · ')}</span></div>
        <ScoreRing value={item.weightScore} label="权重" />
        <dl><div><dt>题材 Beta</dt><dd>{metric(item.beta)}</dd></div><div><dt>{value.horizonDays}日 Alpha</dt><dd>{signed(item.residualReturn)}</dd></div><div><dt>拟合度 R²</dt><dd>{metric(item.rSquared)}</dd></div></dl>
        <div className="concept-weight-decomp" aria-label="题材权重分项">
          <span><i style={{ width: `${Number(item.components?.consensus) || 0}%` }} /><b>{metric(Number(item.components?.consensus) || 0, 0)}</b><small>来源共识 · 36%</small></span>
          <span title={`供应商理由 ${metric(Number(item.components?.providerReasonScore) || 0, 0)} · 本地机构语料 ${metric(Number(item.components?.localCorpusScore) || 0, 0)}`}><i style={{ width: `${Number(item.components?.relevance) || 0}%` }} /><b>{metric(Number(item.components?.relevance) || 0, 0)}</b><small>业务证据 · 29%{Number(item.components?.localCorpusEvidenceCount) > 0 ? ` · 机构${Number(item.components?.localCorpusEvidenceCount)}篇` : ''}</small></span>
          <span><i style={{ width: `${Number(item.components?.market) || 0}%` }} /><b>{metric(Number(item.components?.market) || 0, 0)}</b><small>市场热度 · 20%</small></span>
          <span><i style={{ width: `${Number(item.components?.specificity) || 0}%` }} /><b>{metric(Number(item.components?.specificity) || 0, 0)}</b><small>题材专属性 · 15%</small></span>
        </div>
        <small className="concept-beta-audit">{hasInterval ? `Beta 95%区间 ${ciLow.toFixed(2)} ~ ${ciHigh.toFixed(2)}` : 'Beta区间待足够样本'} · {item.observations ?? 0}个交易日样本</small>
        <p>{item.betaInterpretation || '样本不足，暂不解释 Beta。'} · {item.reasons?.[0] || '该题材由结构化成分表确认，暂无文字证据。'}</p>
        {item.evidence?.find(entry => typeof entry === 'object' && entry.kind === 'institution_corpus') ? <p className="concept-corpus-proof">本地语料交叉证据 · {(() => { const entry = item.evidence?.find(value => typeof value === 'object' && value.kind === 'institution_corpus'); return typeof entry === 'object' ? `${entry.title || ''} ${entry.summary || ''}`.trim() : ''; })()}</p> : null}
      </article>;})}
      {!value.primaryThemes.length ? <p className="concept-muted concept-no-consensus">当前没有达到两个独立来源的主线题材；下方单源线索不会冒充市场共识。</p> : null}
    </div>
    <section className="concept-unique-themes">
      <header><GitBranch /><div><h3>非共识题材线索</h3><p>单一来源且专属性较高，只作为待核验 Alpha 方向，不计作市场共识</p></div></header>
      {value.uniqueThemes?.map(item => <article key={item.canonicalName}>
        <div><strong>{item.canonicalName}</strong><span>{SOURCE_LABEL[item.sources[0]] ?? item.sources[0] ?? '单一来源'}</span></div>
        <b>{metric(item.specificityScore, 0)}</b>
        <p>{item.reasons?.[0] || '来源成分表确认，尚缺少文字入选理由。'}</p>
      </article>)}
      {!value.uniqueThemes?.length ? <p className="concept-muted">当前没有同时满足“单源 + 高专属性 + 已归因”的题材。</p> : null}
    </section>
    <section className="concept-alpha-evidence">
      <header><Sparkles /><div><h3>公司独特 Alpha 线索</h3><p>与题材共振分开呈现，仅列本地事实库可回溯证据</p></div></header>
      {value.uniqueDrivers.slice(0, 8).map((item, index) => <a href={item.url || '#'} key={`${item.kind}-${item.title}-${index}`}>
        <span>{item.source}</span><strong>{item.title}</strong><p>{item.summary}</p>
      </a>)}
      {!value.uniqueDrivers.length ? <p className="concept-muted">近半年尚未检出可单独归因的公司事件。</p> : null}
    </section>
  </aside>;
}

export default function ConceptThemesPage() {
  const [overview, setOverview] = useState<ConceptOverview | null>(null);
  const [rotation, setRotation] = useState<ConceptRotation | null>(null);
  const [leaders, setLeaders] = useState<ConceptLeaders | null>(null);
  const [leaderMode, setLeaderMode] = useState<ConceptLeaders['mode']>('consensus');
  const [clusterDetail, setClusterDetail] = useState<ConceptClusterDetail | null>(null);
  const [detail, setDetail] = useState<ThemeDetail | null>(null);
  const [stockLens, setStockLens] = useState<StockThemeLens | null>(null);
  const [compareThemes, setCompareThemes] = useState<ThemeDetail[]>([]);
  const [query, setQuery] = useState('');
  const debouncedQuery = useDebouncedValue(query, 320);
  const [themeType, setThemeType] = useState('');
  const [source, setSource] = useState('');
  const [family, setFamily] = useState('');
  const [cluster, setCluster] = useState('');
  const [minSources, setMinSources] = useState(1);
  const [sortBy, setSortBy] = useState<'heat' | 'name' | 'size' | 'change'>('heat');
  const [page, setPage] = useState(1);
  const [horizonDays, setHorizonDays] = useState<20 | 60 | 120>(60);
  const [selectedTheme, setSelectedTheme] = useState<number | null>(null);
  const [stockQuery, setStockQuery] = useState('');
  const [stockSort, setStockSort] = useState<'weight' | 'beta' | 'alpha' | 'name'>('weight');
  const [watchlistOnly, setWatchlistOnly] = useState(false);
  const [stockPage, setStockPage] = useState(1);
  const [loading, setLoading] = useState(true);
  const [detailLoading, setDetailLoading] = useState(false);
  const [status, setStatus] = useState('正在读取共享题材图谱…');
  const requestSequence = useRef(0);

  const loadOverview = useCallback(async () => {
    const sequence = ++requestSequence.current;
    try {
      const value = await conceptThemesApi.overview({ query: debouncedQuery, themeType, source, family, cluster, minSources, sortBy, page, pageSize: 48 });
      if (sequence !== requestSequence.current) return;
      setOverview(value);
      setStatus(`${value.summary.marketDate || '最新交易日'} · ${number(value.summary.themes)} 个源题材 · ${number(value.summary.memberships)} 条成分关系`);
      setSelectedTheme(current => value.items.some(item => item.id === current) ? current : value.items.find(item => item.canonicalSourceCount >= 3 && item.constituentCount > 0)?.id ?? value.items.find(item => item.canonicalSourceCount >= 2 && item.constituentCount > 0)?.id ?? value.items[0]?.id ?? null);
    } catch (error) {
      if (sequence !== requestSequence.current) return;
      setStatus(error instanceof Error ? error.message : '题材库暂时不可用，系统会静默重试');
    } finally { if (sequence === requestSequence.current) setLoading(false); }
  }, [cluster, debouncedQuery, family, minSources, page, sortBy, source, themeType]);
  const loadRotation = useCallback(async () => {
    try { setRotation(await conceptThemesApi.rotation(20, 18)); }
    catch { /* keep the last successful rotation snapshot while the upstream refreshes */ }
  }, []);
  const loadLeaders = useCallback(async () => {
    try { setLeaders(await conceptThemesApi.leaders(horizonDays, leaderMode, 24)); }
    catch { /* keep the last successful market radar while attribution catches up */ }
  }, [horizonDays, leaderMode]);

  useEffect(() => { document.title = '概念题材查看 - 乐子乌超级价值'; }, []);
  useEffect(() => { void loadRotation(); }, [loadRotation]);
  useEffect(() => { void loadLeaders(); }, [loadLeaders]);
  useEffect(() => {
    if (!family || !cluster) { setClusterDetail(null); return; }
    let active = true;
    conceptThemesApi.cluster(family, cluster, horizonDays).then(value => { if (active) setClusterDetail(value); }).catch(() => { if (active) setClusterDetail(null); });
    return () => { active = false; };
  }, [cluster, family, horizonDays]);
  useEffect(() => { setPage(1); }, [cluster, debouncedQuery, family, minSources, sortBy, source, themeType]);
  useEffect(() => { setLoading(true); void loadOverview(); }, [loadOverview]);
  const refreshActivePage = useCallback(async () => { await Promise.all([loadOverview(), loadRotation(), loadLeaders()]); }, [loadLeaders, loadOverview, loadRotation]);
  usePageActivationRefresh(refreshActivePage, { intervalMs: 60_000, minIntervalMs: 8_000, runOnMount: false });

  useEffect(() => {
    if (selectedTheme == null) { setDetail(null); return; }
    let active = true;
    setDetailLoading(true);
    setDetail(null);
    conceptThemesApi.theme(selectedTheme, true, horizonDays).then(value => { if (active) setDetail(value); }).catch(error => {
      if (active) setStatus(error instanceof Error ? error.message : '题材成分读取失败');
    }).finally(() => { if (active) setDetailLoading(false); });
    return () => { active = false; };
  }, [horizonDays, selectedTheme]);

  const families = useMemo(() => Object.entries(overview?.summary.families ?? {}).sort((a, b) => b[1] - a[1]), [overview]);
  const clusters = useMemo(() => Object.entries(family ? overview?.summary.clusterFamilies?.[family] ?? {} : {}).sort((a, b) => b[1] - a[1]), [family, overview]);
  const visibleThemes = overview?.items ?? [];
  const filteredStocks = useMemo(() => {
    const term = stockQuery.trim().toLowerCase();
    const values = (detail?.stocks ?? []).filter(item => (!watchlistOnly || item.inWatchlist) && (!term || item.name.toLowerCase().includes(term) || item.tsCode.toLowerCase().includes(term)));
    values.sort((left, right) => stockSort === 'name' ? left.name.localeCompare(right.name, 'zh-CN')
      : stockSort === 'beta' ? (right.beta ?? -999) - (left.beta ?? -999)
        : stockSort === 'alpha' ? (right.residualReturn ?? -999) - (left.residualReturn ?? -999)
          : right.weightScore - left.weightScore);
    return values;
  }, [detail, stockQuery, stockSort, watchlistOnly]);
  const stockPageSize = 60;
  const visibleStocks = filteredStocks.slice((stockPage - 1) * stockPageSize, stockPage * stockPageSize);
  const consensusDistribution = detail?.consensusDistribution ?? {
    strong: detail?.stocks.filter(item => item.sourceCount >= 3).length ?? 0,
    confirmed: detail?.stocks.filter(item => item.sourceCount === 2).length ?? 0,
    singleSource: detail?.stocks.filter(item => item.sourceCount === 1).length ?? 0,
  };
  const comparison = useMemo(() => {
    if (compareThemes.length !== 2) return null;
    const [left, right] = compareThemes;
    const leftCodes = new Set(left.stocks.map(item => item.tsCode));
    const rightCodes = new Set(right.stocks.map(item => item.tsCode));
    const shared = left.stocks.filter(item => rightCodes.has(item.tsCode));
    const union = new Set([...leftCodes, ...rightCodes]).size;
    const exclusive = (source: ThemeDetail, otherCodes: Set<string>) => source.stocks
      .filter(item => !otherCodes.has(item.tsCode)).sort((a, b) => b.weightScore - a.weightScore).slice(0, 8);
    return {
      left, right, shared,
      similarity: union ? shared.length / union * 100 : 0,
      leftExclusive: exclusive(left, rightCodes), rightExclusive: exclusive(right, leftCodes),
    };
  }, [compareThemes]);

  useEffect(() => { setStockPage(1); setStockQuery(''); setWatchlistOnly(false); }, [selectedTheme]);
  useEffect(() => { setStockPage(1); }, [stockQuery, stockSort]);

  const openStock = async (tsCode: string) => {
    setDetailLoading(true);
    try { setStockLens(await conceptThemesApi.stock(tsCode, true, horizonDays)); }
    catch (error) { setStatus(error instanceof Error ? error.message : '股票题材透镜读取失败'); }
    finally { setDetailLoading(false); }
  };
  const toggleCompare = (value: ThemeDetail) => setCompareThemes(current => {
    const exists = current.some(item => item.theme.canonicalName === value.theme.canonicalName);
    if (exists) return current.filter(item => item.theme.canonicalName !== value.theme.canonicalName);
    return current.length >= 2 ? [current[1], value] : [...current, value];
  });

  return <AppPage className="concept-page max-w-[1840px]">
    <div className="concept-shell">
      <header className="concept-hero">
        <div className="concept-hero__copy"><span><Network /> CONCEPT CONSENSUS GRAPH</span><h1>概念题材查看</h1><p>把分散的市场题材、行业层级和成分股归属，变成有来源、有权重、有 Beta/Alpha 解释的共识地图。</p></div>
        <div className="concept-hero__status"><i className={loading || detailLoading ? 'is-loading' : ''} /><span>{status}</span><b><RefreshCw />自动更新</b></div>
        <div className="concept-orbit" aria-hidden="true"><span /><span /><span /><b /></div>
      </header>

      <section className="concept-kpis">
        <div><Database /><span>源题材节点</span><strong>{number(overview?.summary.themes)}</strong><small>语义分层 {metric(overview?.summary.semanticCoveragePct, 1)}% · 六套原始口径保留</small></div>
        <div><GitBranch /><span>成分关系</span><strong>{number(overview?.summary.memberships)}</strong><small>已扫描 {number(overview?.summary.attemptedThemes)} 个节点 · {metric(overview?.summary.scanCoveragePct, 1)}% · {number(overview?.summary.memberedThemes)} 个有成分</small></div>
        <div><Binary /><span>归因结果</span><strong>{number(overview?.summary.exposures)}</strong><small>60日双因子回归</small></div>
        <div><ShieldCheck /><span>最新交易日</span><strong>{overview?.summary.marketDate || '—'}</strong><small>{overview?.sync?.stage || '共享题材库'}</small></div>
      </section>

      <section className="concept-toolbar">
        <label className="concept-search"><Search /><input value={query} onChange={event => setQuery(event.target.value)} placeholder="搜索题材、行业、股票代码或源代码" /></label>
        <select value={themeType} onChange={event => setThemeType(event.target.value)} aria-label="题材类型"><option value="">全部层级</option><option value="theme">市场题材</option><option value="concept">概念</option><option value="industry">行业</option><option value="region">地域</option><option value="style">风格</option></select>
        <select value={source} onChange={event => setSource(event.target.value)} aria-label="数据来源"><option value="">全部来源</option>{overview?.methodology.sources.map(item => <option key={item.key} value={item.key}>{item.name}</option>)}</select>
        <select value={minSources} onChange={event => setMinSources(Number(event.target.value))} aria-label="共识门槛"><option value={1}>全部共识层级</option><option value={2}>至少 2 个来源</option><option value={3}>至少 3 个来源</option><option value={4}>至少 4 个来源</option></select>
        <select value={sortBy} onChange={event => setSortBy(event.target.value as typeof sortBy)} aria-label="排序"><option value="heat">市场热度</option><option value="change">当日涨跌</option><option value="size">成分规模</option><option value="name">名称</option></select>
      </section>
      {debouncedQuery && overview?.stockMatches?.length ? <section className="concept-stock-direct" aria-label="股票题材画像直达">
        <span><Target /> 股票画像直达</span>
        <div>{overview.stockMatches.map(item => <button type="button" key={item.tsCode} onClick={() => void openStock(item.tsCode)}>
          <strong>{item.name}</strong><code>{item.tsCode}</code><small>{item.themeCount} 个题材 · {item.sourceCount} 个来源</small><ChevronRight />
        </button>)}</div>
      </section> : null}

      <section className="concept-rotation-board">
        <header><div><span><Activity /> THEME ROTATION</span><h2>多源题材轮动</h2></div><p>{rotation?.latestDate || overview?.summary.marketDate || '最新交易日'} · {rotation?.availableDates || 0} 个历史交易日 · 日涨跌取多来源中位数</p></header>
        <div className="concept-rotation-track">
          {rotation?.items.slice(0, 12).map(item => <button type="button" key={item.canonicalName} onClick={() => setQuery(item.canonicalName)}>
            <div><strong>{item.canonicalName}</strong><span>{item.cluster}</span></div>
            <RotationSparkline points={item.points} />
            <b data-tone={(item.pctChange || 0) >= 0 ? 'up' : 'down'}>{signed(item.pctChange)}</b>
            <small>轮动 {metric(item.rotationScore, 0)} · {item.sourceCount}源 · 5日 {signed(item.momentum5d)}</small>
          </button>)}
          {!rotation?.items.length ? <p>题材历史快照正在自动建立；当前目录与成分股仍可正常检索。</p> : null}
        </div>
        <footer>{rotation?.method || '每日保留各来源原始快照后再聚合；数据不足时不外推历史。'}</footer>
      </section>

      <MarketConsensusRadar value={leaders} mode={leaderMode} onMode={setLeaderMode} onOpen={tsCode => void openStock(tsCode)} />

      <div className="concept-workspace">
        <aside className="concept-families">
          <div className="concept-panel-title"><Layers3 /><div><strong>题材分层</strong><small>规范族群 → 原始题材</small></div></div>
          <button type="button" className={!family ? 'is-active' : ''} onClick={() => { setFamily(''); setCluster(''); }}><span>全部题材宇宙</span><b>{number(overview?.summary.themes)}</b></button>
          {families.map(([name, count], index) => <button type="button" className={family === name ? 'is-active' : ''} onClick={() => { setFamily(name); setCluster(''); }} key={name}>
            <i>{String(index + 1).padStart(2, '0')}</i><span>{name}</span><b>{count}</b>
          </button>)}
          <section className="concept-source-legend"><h3>六源目录 · 更新与覆盖透明</h3>{overview?.methodology.sources.map(item => { const health = overview.summary.sourceHealth?.[item.key]; return <div key={item.key}><span style={{ background: SOURCE_COLOR[item.key] }} /><b>{item.name}<small>{health?.marketDate || '待更新'} · {metric(health?.scanCoveragePct, 0)}%已扫描</small></b><em data-tone={health?.status === 'fresh' ? 'fresh' : 'lagging'}>{number(overview?.summary.sources[item.key])} 节点 · W{Math.round(item.reliability * 100)}</em></div>; })}</section>
        </aside>

        <main className="concept-universe">
          <div className="concept-section-head"><div><span>THEME UNIVERSE</span><h2>{family || '全市场题材宇宙'}</h2></div><p>当前召回 {number(overview?.total)} 个源节点 · 点击进入成分与归因</p></div>
          {clusters.length ? <div className="concept-clusters"><button type="button" className={!cluster ? 'is-active' : ''} onClick={() => setCluster('')}>全部二级主题</button>{clusters.map(([name, count]) => <button type="button" className={cluster === name ? 'is-active' : ''} onClick={() => setCluster(name)} key={name}>{name}<b>{count}</b></button>)}</div> : null}
          {clusterDetail ? <ClusterAggregate value={clusterDetail} onOpen={tsCode => void openStock(tsCode)} /> : null}
          <div className="concept-grid">
            {visibleThemes.map(item => <ThemeCard key={item.id} item={item} active={selectedTheme === item.id} onClick={() => setSelectedTheme(item.id)} />)}
          </div>
          {overview && overview.total > overview.pageSize ? <nav className="concept-pagination" aria-label="题材分页"><button type="button" disabled={page <= 1 || loading} onClick={() => setPage(value => Math.max(1, value - 1))}>上一页</button><span>第 {page} / {Math.max(1, Math.ceil(overview.total / overview.pageSize))} 页</span><button type="button" disabled={page >= Math.ceil(overview.total / overview.pageSize) || loading} onClick={() => setPage(value => value + 1)}>下一页</button></nav> : null}
          {!loading && !visibleThemes.length ? <EmptyState title="没有命中题材" description="调整关键词或层级筛选；后台目录会按最新交易日自动更新。" /> : null}
        </main>

        <aside className="concept-detail">
          {detail ? <>
            <header className="concept-detail__head"><span>{detail.theme.family}</span><h2>{detail.theme.canonicalName}</h2><p>{detail.totalStocks} 只股票 · {detail.consensusStocks} 只获得多源确认 · {detail.attributionReady} 只已完成归因</p><div className="concept-detail-actions"><div className="concept-horizon" aria-label="归因窗口">{([20, 60, 120] as const).map(days => <button type="button" className={horizonDays === days ? 'is-active' : ''} onClick={() => setHorizonDays(days)} key={days}>{days}日</button>)}</div><button type="button" className={compareThemes.some(item => item.theme.canonicalName === detail.theme.canonicalName) ? 'is-active' : ''} onClick={() => toggleCompare(detail)}><Scale />{compareThemes.some(item => item.theme.canonicalName === detail.theme.canonicalName) ? '移出对照' : '加入对照'}</button><button type="button" onClick={() => void conceptThemesApi.exportTheme(detail.theme.id, horizonDays)}><Download />导出 CSV</button></div></header>
            <section className="concept-consensus-meter"><ScoreRing value={detail.totalStocks ? detail.consensusStocks / detail.totalStocks * 100 : 0} label="多源率" /><div><strong>市场共识不是 AI 猜测</strong><p>来源成分表独立保留；相同规范题材下合并投票，文字入选原因单独进入业务相关性评分。</p></div></section>
            <section className="concept-consensus-spectrum"><div><span>强共识 · 3+源</span><b>{consensusDistribution.strong}</b><i style={{ width: `${detail.totalStocks ? consensusDistribution.strong / detail.totalStocks * 100 : 0}%` }} /></div><div><span>交叉确认 · 2源</span><b>{consensusDistribution.confirmed}</b><i style={{ width: `${detail.totalStocks ? consensusDistribution.confirmed / detail.totalStocks * 100 : 0}%` }} /></div><div><span>单源待核验</span><b>{consensusDistribution.singleSource}</b><i style={{ width: `${detail.totalStocks ? consensusDistribution.singleSource / detail.totalStocks * 100 : 0}%` }} /></div></section>
            <SourceRail themes={detail.sourceNodes} />
            <ConsensusMatrix stocks={detail.stocks} />
            <BetaAlphaMap stocks={detail.stocks} horizonDays={horizonDays} />
            <ThemeResearchQueue stocks={detail.stocks} onOpen={tsCode => void openStock(tsCode)} />
            <div className="concept-stock-table-head"><span>成分股 / 权重 / Beta / {horizonDays}日 Alpha</span><span>点击展开股票题材透镜</span></div>
            <div className="concept-stock-tools"><label><Search /><input value={stockQuery} onChange={event => setStockQuery(event.target.value)} placeholder="搜索成分股名称或代码" /></label><button type="button" className={watchlistOnly ? 'is-active' : ''} disabled={!detail.watchlistStocks?.length} onClick={() => setWatchlistOnly(value => !value)}><Star />我的自选 {detail.watchlistStocks?.length || 0}</button><select value={stockSort} onChange={event => setStockSort(event.target.value as typeof stockSort)} aria-label="成分股排序"><option value="weight">题材权重</option><option value="beta">Beta弹性</option><option value="alpha">Alpha排序</option><option value="name">名称</option></select></div>
            <div className="concept-stock-table">{visibleStocks.map(item => <StockRow key={item.tsCode} item={item} selected={stockLens?.tsCode === item.tsCode} onClick={() => void openStock(item.tsCode)} />)}</div>
            {filteredStocks.length > stockPageSize ? <nav className="concept-pagination concept-stock-pages" aria-label="成分股分页"><button type="button" disabled={stockPage <= 1} onClick={() => setStockPage(value => Math.max(1, value - 1))}>上一页</button><span>{stockPage} / {Math.ceil(filteredStocks.length / stockPageSize)} · {filteredStocks.length}股</span><button type="button" disabled={stockPage >= Math.ceil(filteredStocks.length / stockPageSize)} onClick={() => setStockPage(value => value + 1)}>下一页</button></nav> : null}
          </> : <div className="concept-detail-empty"><CircleDot className={detailLoading ? 'is-loading' : ''} /><h2>{detailLoading ? '正在组装题材共识' : '选择一个题材'}</h2><p>{detailLoading ? '系统正在对齐多源成分股并计算当前窗口 Beta/Alpha，其他区域可继续使用。' : '这里将展开各来源节点、成分股共识、可解释权重和收益归因。'}</p></div>}
        </aside>
      </div>

      <section className="concept-compare">
        <header><div><span><Scale /> THEME COMPARISON</span><h2>题材对照台</h2></div><p>{comparison ? `${comparison.left.theme.canonicalName} × ${comparison.right.theme.canonicalName}` : `已选 ${compareThemes.length}/2 · 在右侧题材详情点击“加入对照”`}</p></header>
        {comparison ? <div className="concept-compare-grid">
          {[comparison.left, comparison.right].map((item, index) => <article key={item.theme.canonicalName}><span>{index ? 'B' : 'A'} · {item.theme.family}</span><h3>{item.theme.canonicalName}</h3><dl><div><dt>全部成分</dt><dd>{item.totalStocks}</dd></div><div><dt>多源确认</dt><dd>{item.consensusStocks}</dd></div><div><dt>归因完成</dt><dd>{item.attributionReady}</dd></div><div><dt>来源节点</dt><dd>{item.sourceNodes.length}</dd></div></dl><p>独占高权重 · {(index ? comparison.rightExclusive : comparison.leftExclusive).map(stock => stock.name).join('、') || '暂无'}</p></article>)}
          <aside><strong>{comparison.similarity.toFixed(1)}%</strong><span>成分 Jaccard 相似度</span><b>{comparison.shared.length} 只共同成分</b><p>{comparison.shared.slice(0, 12).map(item => item.name).join('、') || '没有共同成分'}</p></aside>
        </div> : <div className="concept-compare-empty"><Scale /><p>对照不是只比涨跌：系统会比较成分重合、市场共识、归因覆盖和双方独占的高权重股票。</p></div>}
      </section>

      <section className="concept-method">
        <header><BrainCircuit /><div><span>ATTRIBUTION METHOD</span><h2>Beta 与 Alpha 如何被计算</h2></div></header>
        <div className="concept-method__flow"><div><Boxes /><strong>来源共识</strong><p>同花顺、东财、开盘啦、通达信、申万独立投票</p></div><ArrowRight /><div><Target /><strong>题材权重</strong><p>36%共识 + 29%业务证据 + 20%热度 + 15%专属性</p></div><ArrowRight /><div><TrendingUp /><strong>题材 Beta</strong><p>剔除个股自身后的题材组合，同时控制沪深300</p></div><ArrowRight /><div><Activity /><strong>独特 Alpha</strong><p>统计窗口残差与公司独有事实证据分层展示</p></div></div>
        <p className="concept-method__formula">{overview?.methodology.betaFormula}</p>
        <small>{overview?.methodology.licenseNote}</small>
      </section>
    </div>
    {stockLens ? <StockLensPanel value={stockLens} onClose={() => setStockLens(null)} /> : null}
  </AppPage>;
}
