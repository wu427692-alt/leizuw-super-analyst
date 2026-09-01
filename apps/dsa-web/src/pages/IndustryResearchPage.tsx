import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { motion } from 'motion/react';
import Markdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import {
  Area, AreaChart, Bar, BarChart, CartesianGrid, Legend, Line, LineChart,
  ResponsiveContainer, Scatter, ScatterChart, Tooltip, XAxis, YAxis,
} from 'recharts';
import {
  ArrowRight, AudioLines, BookOpenCheck, Boxes, Building2, CheckCircle2, ChevronRight,
  CircleAlert, Clock3, Database, Download, FileCheck2, FileSearch, FileText, Gauge,
  LibraryBig, ListChecks, LoaderCircle, MessageSquareText, Network, Play,
  RadioTower, ScanText, Search, ShieldCheck, Sparkles, Telescope, Workflow,
} from 'lucide-react';
import { Link } from 'react-router-dom';
import { industryResearchApi } from '../api/industryResearch';
import { AppPage } from '../components/common';
import { usePageActivationRefresh } from '../hooks/usePageActivationRefresh';
import type {
  IndustryEvidence, IndustryResearchBlueprint, IndustryResearchProject, IndustryResearchReport, IndustryResearchSnapshot,
} from '../types/industryResearch';
import './IndustryResearchPage.css';

type WorkspaceTab = 'map' | 'companies' | 'evidence' | 'questions' | 'report';
type ProjectFilter = 'all' | 'running' | 'completed' | 'limited' | 'failed';
type ResearchType = 'industry' | 'company';

const compact = (value: number) => new Intl.NumberFormat('zh-CN', {
  notation: value >= 10_000 ? 'compact' : 'standard', maximumFractionDigits: 1,
}).format(value);

const timestamp = (value?: string) => {
  if (!value) return '等待数据';
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleString('zh-CN', {
    month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit',
  });
};

const evidenceHref = (item: IndustryEvidence) => {
  if (item.kind === 'institution_note') return `/essay-radar/feed?query=${encodeURIComponent(item.title)}`;
  if (item.evidenceId.startsWith('event:')) return `/investment-monitor/feed?event=${item.evidenceId.split(':')[1]}`;
  return item.url || '/data-acquisition';
};

const statusLabel: Record<string, string> = {
  queued: '排队中', collecting: '收集证据', analyzing: '交叉分析', completed: '完整完成',
  limited: '有限完成', failed: '需要重试',
};

const evidenceKindLabel: Record<string, string> = {
  broker_report: '券商研报', institution_note: '机构段子', financial_announcement: '定期报告与公告',
  financial_statement: '财务报表', earnings_expectation: '业绩与一致预期', market_series: '行情',
  company_profile: '公司资料', audio_transcript: '录音转写', announcements: '公告',
  market_financial: '行情与财务', enterprise: '企业事实', news_comments: '新闻与股评',
};

const runningStatuses = new Set(['queued', 'collecting', 'analyzing']);
const readableStatuses = new Set(['completed', 'limited']);

const isCompleteSnapshot = (value?: IndustryResearchSnapshot): value is IndustryResearchSnapshot => Boolean(
  value?.totals && Array.isArray(value.coverage) && Array.isArray(value.evidence),
);

const revealWorkbench = () => {
  window.requestAnimationFrame(() => {
    const workbench = document.getElementById('industry-research-workbench');
    workbench?.scrollIntoView({ behavior: 'smooth', block: 'start' });
    workbench?.focus({ preventScroll: true });
  });
};

function ProgressRing({ value }: { value: number }) {
  const safe = Math.max(0, Math.min(100, value));
  const circumference = 2 * Math.PI * 31;
  return <div className="ir-progress-ring">
    <svg viewBox="0 0 72 72" aria-hidden="true"><circle cx="36" cy="36" r="31" /><circle className="ir-progress-value" cx="36" cy="36" r="31" strokeDasharray={circumference} strokeDashoffset={circumference * (1 - safe / 100)} /></svg>
    <strong>{safe}</strong><span>%</span>
  </div>;
}

const qualityStatusLabel: Record<string, string> = {
  ready: '通过发布门', limited: '存在关键缺口', insufficient: '证据不足',
};

const qualityDimensionLabel: Record<string, string> = {
  completeness: '关键源完整性', uniqueness: '证据唯一性', validity: '时点有效性',
  consistency: '口径一致性', timeliness: '数据时效', traceability: '原文可追溯',
  sourceQuality: '来源质量', reproducibility: '快照可复现',
};

const qualityMetricLabel: Record<string, string> = {
  citationCoveragePct: '长段落引用率', numericCitationCoveragePct: '数字段落引用率',
  uniqueCitations: '正文唯一引用', narrativeChars: '正文字符', fallbackChapters: '失败占位章',
  databaseMatches: '数据库命中', snapshotEvidence: '快照保存', modelReadyEvidence: '实际入模',
  factualEvidence: '事实层证据', independentSources: '独立来源', traceableEvidence: '可回原文',
  requiredSourcesCovered: '必需源已覆盖', futureDatedItems: '未来数据穿越',
};

const formatQualityMetric = (value: unknown) => {
  if (Array.isArray(value)) return value.join('、') || '—';
  if (typeof value === 'number') return Number.isInteger(value) ? value.toLocaleString('zh-CN') : value.toFixed(1);
  if (value === null || value === undefined || value === '') return '—';
  return String(value);
};

function ResearchDataLedger({ snapshot }: { snapshot: IndustryResearchSnapshot }) {
  const matched = snapshot.totals.evidence ?? 0;
  const stored = snapshot.totals.evidenceStored ?? snapshot.evidence.length;
  const modelReady = snapshot.totals.evidenceModelReady;
  const filingDocuments = snapshot.filingDocuments ?? [];
  const filingTextCount = filingDocuments.filter(item => (item.textChars ?? item.excerptChars ?? 0) > 0).length;
  const filingChars = filingDocuments.reduce((sum, item) => sum + Number(item.textChars ?? item.excerptChars ?? 0), 0);
  const reportDocuments = snapshot.brokerReportDocuments ?? [];
  const reportTextCount = reportDocuments.filter(item => (item.textChars ?? 0) > 0).length;
  const reportChars = reportDocuments.reduce((sum, item) => sum + Number(item.textChars ?? 0), 0);
  return <section className="ir-data-ledger" aria-label="研究数据流量账本">
    <div className="ir-section-heading"><div><span>数据流量账本</span><h2>从数据库命中到实际入模</h2></div><small>三个数字口径分开，避免把命中量误当作模型实际阅读量。</small></div>
    <div className="ir-data-ledger-grid">
      <article><Database /><span>数据库 / 接口命中</span><strong>{matched.toLocaleString('zh-CN')}</strong><small>筛选前相关记录</small></article>
      <article><FileCheck2 /><span>固定快照保存</span><strong>{stored.toLocaleString('zh-CN')}</strong><small>去重后可复核证据</small></article>
      <article className={modelReady === undefined ? 'is-warning' : ''}><Workflow /><span>实际进入模型</span><strong>{modelReady?.toLocaleString('zh-CN') ?? '—'}</strong><small>{modelReady === undefined ? '后端尚未返回入模口径' : '本轮 Kimi 证据包'}</small></article>
      <article className={filingTextCount ? '' : 'is-warning'}><ScanText /><span>定期报告正文读取</span><strong>{filingTextCount}</strong><small>{filingTextCount ? `${filingChars.toLocaleString('zh-CN')} 字已抽取` : '当前仅有链接或尚未解析'}</small></article>
      <article className={reportTextCount ? '' : 'is-warning'}><FileSearch /><span>券商研报 PDF 正文</span><strong>{reportTextCount}</strong><small>{reportTextCount ? `${reportChars.toLocaleString('zh-CN')} 字已抽取` : '不把摘要或链接冒充已读正文'}</small></article>
      <article><AudioLines /><span>录音转写</span><strong>{snapshot.totals.audioTranscripts ?? 0}</strong><small>{snapshot.audioPipeline?.message || `${snapshot.totals.audioCandidates ?? 0} 个候选文件`}</small></article>
      <article className={snapshot.factLedger?.length ? '' : 'is-warning'}><ListChecks /><span>可审计事实卡</span><strong>{snapshot.factLedger?.length ?? 0}</strong><small>指标 + 期间 + 单位 + 证据编号</small></article>
    </div>
  </section>;
}

function AIResearchWorkflow({ blueprint, project }: { blueprint: IndustryResearchBlueprint; project?: IndustryResearchProject }) {
  const snapshot = project?.snapshot ?? blueprint.snapshot;
  const report = project?.report;
  const flow = report?.aiWorkflow ?? blueprint.methodology.aiFlow ?? [];
  const stored = snapshot.totals.evidenceStored ?? snapshot.evidence.length;
  const filingRead = (snapshot.filingDocuments ?? []).some(item => (item.textChars ?? item.excerptChars ?? 0) > 0);
  const hasParsedMaterial = filingRead || (snapshot.totals.audioTranscripts ?? 0) > 0 || stored > 0;
  const hasModeling = Boolean(snapshot.financialSeries?.length || snapshot.marketSeries?.length || snapshot.conceptContext?.items?.length);
  const dataQuality = snapshot.dataQuality;
  const reportQa = report?.qualityAssurance;

  const stateFor = (stage: string): 'done' | 'active' | 'warning' | 'waiting' => {
    if (stage === 'contract') return snapshot.researchContract ? 'done' : 'active';
    if (stage === 'retrieval_plan') return snapshot.sourcePlan?.length ? 'done' : 'waiting';
    if (stage === 'ingestion') return stored > 0 ? 'done' : 'active';
    if (stage === 'parsing') return hasParsedMaterial ? 'done' : readableStatuses.has(project?.status ?? '') ? 'warning' : 'active';
    if (stage === 'normalization') return stored > 0 && snapshot.sourceHash ? 'done' : 'waiting';
    if (stage === 'quality') return dataQuality ? dataQuality.status === 'ready' ? 'done' : 'warning' : 'waiting';
    if (stage === 'modeling') return hasModeling ? 'done' : readableStatuses.has(project?.status ?? '') ? 'warning' : 'waiting';
    if (stage === 'reasoning') return report?.oneSentence ? 'done' : project?.status === 'analyzing' ? 'active' : 'waiting';
    if (stage === 'writing') return report?.chapters?.length ? 'done' : report ? 'active' : 'waiting';
    if (stage === 'verification') return reportQa ? reportQa.status === 'ready' ? 'done' : 'warning' : 'waiting';
    return 'waiting';
  };

  return <section className="ir-ai-flow" aria-label="AI 研究流程">
    <div className="ir-section-heading"><div><span>AI 研究流程</span><h2>Evidence-to-Decision · 十步研究流水线</h2></div><small>AI 负责规划、提取、反证与写作；数字计算和发布门由程序执行。</small></div>
    <div className="ir-ai-flow-grid">{flow.map((item, index) => {
      const state = stateFor(item.stage);
      return <article className={`is-${state}`} key={item.stage}>
        <header><span>{String(index + 1).padStart(2, '0')}</span><em>{state === 'done' ? '已完成' : state === 'active' ? '处理中' : state === 'warning' ? '受限' : '等待'}</em></header>
        <small>{item.role}</small><h3>{item.title}</h3><p>{item.output}</p><footer>{item.gate}</footer>
      </article>;
    })}</div>
  </section>;
}

function ResearchMethodMatrix({ blueprint }: { blueprint: IndustryResearchBlueprint }) {
  const rows = blueprint.methodology.dataRequirements ?? [];
  if (!rows.length) return null;
  return <section className="ir-method-matrix" aria-label="深度报告数据方法矩阵">
    <div className="ir-section-heading"><div><span>报告方法论</span><h2>每类数据进入报告后解决什么问题</h2></div><small>不是把资料拼在一起：每层数据都有计算或验证用途，也有明确的当前能力边界。</small></div>
    <div className="ir-method-matrix-grid">{rows.map((item, index) => <article key={item.layer}>
      <header><span>{String(index + 1).padStart(2, '0')}</span><h3>{item.layer}</h3></header>
      <div>{item.inputs.map(input => <em key={input}>{input}</em>)}</div>
      <p><strong>如何使用</strong>{item.use}</p>
      <footer><CircleAlert /><span><strong>当前边界</strong>{item.currentBoundary}</span></footer>
    </article>)}</div>
  </section>;
}

function ResearchQualityPanel({ snapshot, report, compact = false }: {
  snapshot?: IndustryResearchSnapshot; report?: IndustryResearchReport; compact?: boolean;
}) {
  const dataQuality = report?.dataQuality ?? snapshot?.dataQuality;
  const reportQa = report?.qualityAssurance;
  if (!dataQuality && !reportQa) return <section className="ir-quality is-pending"><ListChecks /><div><strong>数据质量门等待执行</strong><p>证据快照生成后会显示关键源完整性、时效、引用与发布验收。</p></div></section>;
  const score = reportQa?.score ?? dataQuality?.overallScore ?? 0;
  const status = reportQa?.status ?? dataQuality?.status ?? 'insufficient';
  const gaps = reportQa?.criticalFailures?.length ? reportQa.criticalFailures : dataQuality?.criticalGaps ?? [];
  const warnings = Array.from(new Set([...(reportQa?.warnings ?? []), ...(dataQuality?.warnings ?? [])]));
  const metrics = reportQa?.metrics ?? dataQuality?.metrics ?? {};
  const dimensions = Object.entries(dataQuality?.dimensions ?? {});
  const sourcePlan = report?.sourcePlan ?? snapshot?.sourcePlan ?? [];
  return <section className={`ir-quality is-${status} ${compact ? 'is-compact' : ''}`} aria-label="数据与报告质量门">
    <header>
      <div className="ir-quality-score"><strong>{score}</strong><span>/ 100</span></div>
      <div><span>{reportQa ? '报告发布 QA' : '数据质量 QA'}</span><h2>{qualityStatusLabel[status] || status}</h2><p>{reportQa?.rule || dataQuality?.rule}</p></div>
      <em>{status === 'ready' ? <CheckCircle2 /> : <CircleAlert />}{status === 'ready' ? '可发布完整报告' : '只能作为有限完成打开'}</em>
    </header>
    {dimensions.length ? <div className="ir-quality-dimensions">{dimensions.map(([key, value]) => <div key={key}><span>{qualityDimensionLabel[key] || key}<strong>{value}</strong></span><i><b style={{ width: `${Math.max(0, Math.min(100, value))}%` }} /></i></div>)}</div> : null}
    {Object.keys(metrics).length ? <div className="ir-quality-metrics">{Object.entries(metrics).slice(0, compact ? 6 : 10).map(([key, value]) => <div key={key}><span>{qualityMetricLabel[key] || key}</span><strong>{formatQualityMetric(value)}</strong></div>)}</div> : null}
    {!compact && sourcePlan.length ? <div className="ir-source-plan"><h3>必需数据源门</h3>{sourcePlan.map(item => <article className={`is-${item.status}`} key={item.key}><span>{item.required ? '必需' : '补强'}</span><div><strong>{item.name}</strong><small>{item.message || `${item.count} 条已进入快照`}</small></div><em>{item.status === 'covered' ? '通过' : item.status === 'partial' ? '部分' : '缺口'}</em></article>)}</div> : null}
    {gaps.length || warnings.length ? <div className="ir-quality-notes">
      {gaps.length ? <div><strong>关键缺口</strong>{gaps.map(item => <p key={item}><CircleAlert />{item}</p>)}</div> : null}
      {warnings.length ? <div><strong>质量警告</strong>{warnings.map(item => <p key={item}><CircleAlert />{item}</p>)}</div> : null}
    </div> : null}
  </section>;
}

function ResearchFigure({ figure }: { figure: NonNullable<IndustryResearchReport['visualizations']>[number] }) {
  const colors = ['#b9581e', '#d2a24f', '#5a8695', '#8667a8'];
  const common = <>
    <CartesianGrid vertical={false} stroke="rgba(91,119,160,.14)" />
    <XAxis dataKey={figure.xKey} tick={{ fill: '#72839a', fontSize: 10 }} tickLine={false} axisLine={false} minTickGap={18} />
    <YAxis tick={{ fill: '#72839a', fontSize: 10 }} tickLine={false} axisLine={false} width={58} />
    <Tooltip contentStyle={{ background: '#fff', border: '1px solid #cdd8e7', borderRadius: 4, color: '#14243b' }} />
    {figure.yKeys.length > 1 ? <Legend wrapperStyle={{ fontSize: 10 }} /> : null}
  </>;
  return <article className="ir-figure" id={`ir-figure-${figure.id}`}>
    <header><div><span>图表【{figure.id}】</span><h3>{figure.title}</h3></div><small>{figure.subtitle}</small></header>
    <div className="ir-figure-canvas">
      <ResponsiveContainer width="100%" height="100%" minWidth={0} minHeight={260} initialDimension={{ width: 640, height: 260 }}>
        {figure.type === 'scatter' ? <ScatterChart data={figure.data} margin={{ top: 12, right: 14, left: 2, bottom: 8 }}>
          <CartesianGrid stroke="rgba(91,119,160,.14)" />
          <XAxis type="number" dataKey={figure.xKey} name={figure.xKey} tick={{ fill: '#72839a', fontSize: 10 }} tickLine={false} axisLine={false} />
          <YAxis type="number" dataKey={figure.yKeys[0]} name={figure.yKeys[0]} tick={{ fill: '#72839a', fontSize: 10 }} tickLine={false} axisLine={false} width={58} />
          <Tooltip cursor={{ strokeDasharray: '3 3' }} contentStyle={{ background: '#fff', border: '1px solid #cdd8e7', borderRadius: 4, color: '#14243b' }} />
          <Scatter data={figure.data} fill={colors[0]} name={figure.labelKey || figure.title} />
        </ScatterChart> : figure.type === 'line' ? <LineChart data={figure.data} margin={{ top: 12, right: 14, left: 0, bottom: 8 }}>
          {common}{figure.yKeys.map((key, index) => <Line key={key} type="monotone" dataKey={key} stroke={colors[index % colors.length]} dot={false} strokeWidth={2} connectNulls />)}
        </LineChart> : figure.type === 'area' ? <AreaChart data={figure.data} margin={{ top: 12, right: 14, left: 0, bottom: 8 }}>
          {common}{figure.yKeys.map((key, index) => <Area key={key} type="monotone" dataKey={key} stroke={colors[index % colors.length]} fill={`${colors[index % colors.length]}22`} strokeWidth={2} />)}
        </AreaChart> : <BarChart data={figure.data} margin={{ top: 12, right: 14, left: 0, bottom: 8 }}>
          {common}{figure.yKeys.map((key, index) => <Bar key={key} dataKey={key} fill={colors[index % colors.length]} maxBarSize={34} />)}
        </BarChart>}
      </ResponsiveContainer>
    </div>
    {figure.insight ? <p className="ir-figure-insight"><strong>如何阅读</strong>{figure.insight}</p> : null}
    <footer>来源：{figure.source || '本次研究固定证据快照'}{figure.unit ? ` · 单位：${figure.unit}` : ''}</footer>
  </article>;
}

function ResearchMap({ blueprint }: { blueprint: IndustryResearchBlueprint }) {
  const snapshot = blueprint.snapshot;
  const companyMode = blueprint.researchType === 'company';
  const stages = companyMode ? [
    { icon: Building2, name: '公司与业务边界', note: '主体、产品结构、收入来源与商业模式' },
    { icon: Network, name: '产业位置与竞争', note: '上下游依赖、客户价值、对手与差异化' },
    { icon: Database, name: '财务与经营质量', note: '三表、盈利质量、现金流、产能与执行' },
    { icon: Telescope, name: '预期差与验证信号', note: '机构预期、行情反馈、风险与证伪条件' },
  ] : [
    { icon: Boxes, name: '上游与关键投入', note: '原料、设备、芯片、工艺与标准' },
    { icon: Network, name: '核心产品与系统', note: '产品形态、价值量、成本与议价权' },
    { icon: Building2, name: '客户与应用场景', note: '谁付钱、为何采用、需求指标是什么' },
    { icon: Telescope, name: '趋势与验证信号', note: '技术路线、供需拐点、反证条件' },
  ];
  return <div className="ir-map-grid">
    <section className="ir-chain" aria-label="研究分析地图">
      <div className="ir-section-heading"><div><span>研究框架</span><h2>{companyMode ? '上市公司尽调骨架' : '产业链拆解骨架'}</h2></div><small>Kimi 只会填入有证据支持的节点</small></div>
      <div className="ir-chain-track">
        {stages.map(({ icon: Icon, name, note }, index) => <article key={name}>
          <div><Icon /><span>0{index + 1}</span></div><h3>{name}</h3><p>{note}</p>
          {index < stages.length - 1 ? <ArrowRight className="ir-chain-arrow" /> : null}
        </article>)}
      </div>
      <div className="ir-terms"><span>本次召回词</span>{snapshot.queryTerms.map(term => <em key={term}>{term}</em>)}</div>
    </section>
    <section className="ir-timeline-panel">
      <div className="ir-section-heading"><div><span>证据时序</span><h2>资料时间分布</h2></div><small>只表示资料数量，不代表价格趋势</small></div>
      <div className="ir-chart">
        {snapshot.timeline.length ? <ResponsiveContainer width="100%" height="100%" minWidth={0} minHeight={250} initialDimension={{ width: 520, height: 250 }}>
          <AreaChart data={snapshot.timeline} margin={{ top: 12, right: 8, left: -18, bottom: 0 }}>
            <CartesianGrid vertical={false} stroke="rgba(128,160,214,.14)" />
            <XAxis dataKey="month" tick={{ fill: '#8799b8', fontSize: 11 }} tickLine={false} axisLine={false} minTickGap={28} />
            <YAxis tick={{ fill: '#8799b8', fontSize: 11 }} tickLine={false} axisLine={false} />
            <Tooltip contentStyle={{ background: '#0b1930', border: '1px solid #294d7e', borderRadius: 10 }} labelStyle={{ color: '#fff' }} />
            <Area type="monotone" dataKey="count" stroke="#49b5ff" fill="#163d68" strokeWidth={2.2} />
          </AreaChart>
        </ResponsiveContainer> : <div className="ir-empty">当前范围尚无可画出的时间序列</div>}
      </div>
    </section>
  </div>;
}

function CompanyMatrix({ blueprint }: { blueprint: IndustryResearchBlueprint }) {
  const companies = blueprint.snapshot.companies;
  const max = Math.max(1, ...companies.map(item => item.evidenceCount));
  return <section className="ir-full-panel">
    <div className="ir-section-heading"><div><span>公司实体</span><h2>证据中明确出现的企业候选</h2></div><small>出现频次是研究优先级，不是龙头结论</small></div>
    <div className="ir-company-table">
      <div className="ir-company-row is-head"><span>企业</span><span>证券代码</span><span>证据密度</span><span>下一步</span></div>
      {companies.map((item, index) => <div className="ir-company-row" key={`${item.symbol}-${item.name}`}>
        <span><em>{String(index + 1).padStart(2, '0')}</em><strong>{item.name}</strong></span>
        <span>{item.symbol || '待识别'}</span>
        <span><i><b style={{ width: `${Math.max(4, item.evidenceCount / max * 100)}%` }} /></i>{item.evidenceCount} 条</span>
        <span>{item.symbol ? <Link to={`/super-watchlist?symbol=${encodeURIComponent(item.symbol)}`}>进入公司证据库<ChevronRight /></Link> : '等待实体识别'}</span>
      </div>)}
      {!companies.length ? <div className="ir-empty">当前证据尚未解析出可核验的公司候选</div> : null}
    </div>
  </section>;
}

function EvidenceLibrary({ blueprint }: { blueprint: IndustryResearchBlueprint }) {
  const [kind, setKind] = useState('all');
  const items = useMemo(() => blueprint.snapshot.evidence.filter(item => kind === 'all' || item.kind === kind), [blueprint, kind]);
  const kinds = Array.from(new Set(blueprint.snapshot.evidence.map(item => item.kind)));
  return <section className="ir-full-panel">
    <div className="ir-section-heading"><div><span>证据账本</span><h2>可回到原文的证据库</h2></div><small>当前展示优先级最高的 {items.length} 条</small></div>
    <div className="ir-filter-row"><button type="button" className={kind === 'all' ? 'is-active' : ''} onClick={() => setKind('all')}>全部</button>{kinds.map(value => <button type="button" className={kind === value ? 'is-active' : ''} onClick={() => setKind(value)} key={value}>{evidenceKindLabel[value] || value}</button>)}</div>
    <div className="ir-evidence-list">
      {items.slice(0, 80).map(item => {
        const external = Boolean(item.url && !item.evidenceId.startsWith('event:'));
        return <a key={item.evidenceId} href={evidenceHref(item)} target={external ? '_blank' : undefined} rel={external ? 'noreferrer' : undefined}>
          <span className={`ir-level is-${item.evidenceLevel}`}>{item.evidenceLevel === 'factual' ? '事实' : item.evidenceLevel === 'unverified' ? '待核验' : item.evidenceLevel === 'ai_transcript' ? 'AI转写' : '有来源观点'}</span>
          <div><strong>{item.title}</strong><p>{item.summary || '原文已入库，等待结构化摘要'}</p><small>{timestamp(item.date)} · {item.source} · {item.evidenceId}</small></div>
          <ChevronRight />
        </a>;
      })}
    </div>
  </section>;
}

function QuestionsView({ blueprint, report }: { blueprint: IndustryResearchBlueprint; report?: IndustryResearchReport }) {
  const interview = report?.interviewQuestions ?? [
    '客户真正为哪项性能、成本或交付改善付费？', '行业最大技术与产能瓶颈在哪里，谁掌握解决能力？',
    '未来 12–24 个月最关键的需求验证指标是什么？', '什么变化会让当前增长或竞争格局判断失效？',
  ];
  const unknowns = report?.openQuestions ?? blueprint.methodology.requiredQuestions;
  return <div className="ir-question-grid">
    <section><div className="ir-section-heading"><div><span>访谈提纲</span><h2>专家与公司访谈问题</h2></div></div><ol>{interview.map((item, index) => <li key={item}><span>{String(index + 1).padStart(2, '0')}</span><p>{item}</p></li>)}</ol></section>
    <section><div className="ir-section-heading"><div><span>研究空缺</span><h2>尚未闭环的研究问题</h2></div></div><ol>{unknowns.map((item, index) => <li key={item}><span>Q{index + 1}</span><p>{item}</p></li>)}</ol></section>
  </div>;
}

const reportCitationPattern = /\[((?:report|note|event|announcement|filing|financial|industry-peer|web|audio):[^\]\s]+)\]/gi;
const superscriptDigit: Record<string, string> = {
  '0': '⁰', '1': '¹', '2': '²', '3': '³', '4': '⁴', '5': '⁵', '6': '⁶', '7': '⁷', '8': '⁸', '9': '⁹',
};
const toSuperscript = (value: number) => String(value).split('').map(digit => superscriptDigit[digit] ?? digit).join('');
const reportReadingMarkdown = (value: string | undefined, citations: Map<string, number>) => String(value || '')
  .replace(new RegExp(reportCitationPattern.source, 'gi'), (_, evidenceId: string) => {
    const number = citations.get(evidenceId);
    return number ? toSuperscript(number) : '';
  })
  .replace(/(?:图表)?【([^｜】]+)(?:｜([^】]+))?】/g, (_, id: string, title?: string) => `见图表 ${id}${title ? ` ${title}` : ''}`)
  .replace(/```markdown|```/g, '')
  .trim();

function ResearchAnswerBrief({ project, onOpen }: { project: IndustryResearchProject; onOpen: () => void }) {
  const report = project.report;
  if (!report && project.status === 'failed') return <section className="ir-terminal-state is-failed" role="status">
    <CircleAlert /><div><span>研究任务失败</span><h2>{project.message}</h2><p>{project.error || '任务现场已保留，可调整课题后重新创建；已有数据不会被清空。'}</p></div>
  </section>;
  if (!report && project.status === 'limited') return <section className="ir-terminal-state is-limited" role="status">
    <FileCheck2 /><div><span>有限完成</span><h2>{project.message}</h2><p>当前证据不足以发布完整研究报告，但已经取得的证据快照和质量缺口仍可查看。</p></div><button type="button" onClick={onOpen}>查看证据与缺口<ChevronRight /></button>
  </section>;
  if (!report) return <section className="ir-answer-loading" aria-live="polite">
    <ProgressRing value={project.progress} />
    <div><span>Kimi 正在研究</span><h2>{project.message}</h2><p>证据召回完成后会先显示可用结论，不必等待八章长报告全部写完。</p></div>
  </section>;
  const leaders = (report.leaders ?? []).slice(0, 4);
  const trends = (report.trends ?? []).slice(0, 3);
  const bottlenecks = (report.bottlenecks ?? []).slice(0, 3);
  const indicators = (report.monitoringIndicators ?? []).slice(0, 4);
  const isDraft = runningStatuses.has(project.status);
  const isLimited = project.status === 'limited';
  return <section className={`ir-answer-brief ${isDraft ? 'is-draft' : ''} ${isLimited ? 'is-limited' : ''}`} aria-label={`${project.topic}研究结论`}>
    <header>
      <div><span>{isDraft ? 'Kimi 首轮结论' : isLimited ? '受限研究结论' : '研究结论'}</span><h2>{project.topic} · 研究结论先行</h2></div>
      <div className="ir-answer-status"><i />{isDraft ? `首轮结论可读 · 长报告 ${project.progress}%` : isLimited ? '有限完成 · 缺口已标注' : '完整报告已完成'}</div>
    </header>
    <div className="ir-answer-thesis"><strong>{report.oneSentence || '首轮研究结论正在形成'}</strong><p>{report.executiveSummary || '已固定证据快照，正在生成可核验的产业链、趋势、公司与风险判断。'}</p></div>
    <div className="ir-answer-grid">
      <article><span><Telescope />趋势判断</span>{trends.length ? trends.map(item => <p key={`${item.horizon}-${item.claim}`}><strong>{item.horizon || '待验证'}</strong>{item.claim}</p>) : <p>正在从证据中提取短中长期趋势。</p>}</article>
      <article><span><Building2 />龙头与关键公司</span>{leaders.length ? leaders.map(item => <p key={`${item.symbol}-${item.name}`}><strong>{item.name || '待识别'} {item.symbol || ''}</strong>{item.rationale}</p>) : <p>当前证据不足以确认龙头，报告会明确保留为待验证。</p>}</article>
      <article><span><CircleAlert />核心瓶颈</span>{bottlenecks.length ? bottlenecks.map(item => <p key={item.issue}><strong>{item.issue || '待验证'}</strong>{item.whyItMatters}</p>) : <p>正在核对成本、技术、产能、客户验证与商业化瓶颈。</p>}</article>
      <article><span><Gauge />持续跟踪指标</span>{indicators.length ? indicators.map(item => <p key={`${item.indicator}-${item.frequency}`}><strong>{item.indicator || '待验证'}</strong>{item.frequency ? `${item.frequency} · ${item.source || '待确认来源'}` : item.source}</p>) : <p>正在生成可用于后续更新的验证指标。</p>}</article>
    </div>
    <footer><span>{project.message}</span><button type="button" onClick={onOpen}>{isDraft ? '查看当前研究底稿' : isLimited ? '打开有限报告与 QA' : '打开完整 8 章报告'}<ChevronRight /></button></footer>
  </section>;
}

function ReportView({ project }: { project?: IndustryResearchProject }) {
  const report = project?.report;
  if (!project) return <div className="ir-report-placeholder"><BookOpenCheck /><h2>启动课题后生成标准研究报告</h2><p>后台会固定研报、机构段子、录音转写、财务、公告、行情与互联网证据，再由 Kimi 撰写默认不少于 2 万字的报告。</p></div>;
  if (!report && project.status === 'failed') return <div className="ir-report-placeholder is-failed"><CircleAlert /><h2>研究任务没有完成</h2><p>{project.message}</p><small>{project.error || '错误现场已保留。请重新创建课题，系统不会删除已入库的数据。'}</small></div>;
  if (!report && project.status === 'limited') return <div className="ir-report-limited-empty">
    <FileCheck2 /><div><span>有限完成</span><h2>{project.message}</h2><p>质量门没有允许发布完整报告。下方保留本次真实取得的数据、来源门和关键缺口，不再显示为加载中。</p></div>
    <ResearchQualityPanel snapshot={project.snapshot} />
  </div>;
  if (!report) return <div className="ir-report-placeholder"><LoaderCircle className="is-spinning" /><h2>{project.message}</h2><p>任务在后台运行，可以离开本页；首轮结论生成后会直接在这里出现。</p><ProgressRing value={project.progress} /></div>;
  const companyMode = project.researchType === 'company';
  const chapters = report.chapters ?? [];
  const figures = report.visualizations ?? [];
  const citationIds: string[] = [];
  [report.oneSentence, report.executiveSummary, ...chapters.map(chapter => chapter.bodyMarkdown)].forEach(body => {
    for (const match of String(body || '').matchAll(new RegExp(reportCitationPattern.source, 'gi'))) {
      if (!citationIds.includes(match[1])) citationIds.push(match[1]);
    }
  });
  const citationNumbers = new Map(citationIds.map((evidenceId, index) => [evidenceId, index + 1]));
  const evidenceById = new Map((project.snapshot?.evidence ?? []).map(item => [item.evidenceId, item]));
  const citedSources = citationIds.map((evidenceId, index) => ({ number: index + 1, evidenceId, item: evidenceById.get(evidenceId) }));
  const symbol = report.subject?.symbol || project.snapshot?.subject?.symbol;
  return <div className="ir-report">
    <section className="ir-report-cover">
      <div className="ir-report-cover-copy">
        {symbol ? <strong>{symbol}</strong> : null}
        <span>{companyMode ? '上市公司深度研究报告' : '行业深度研究报告'}</span>
        <h1>{project.topic}<small>深度研究报告</small></h1>
        <h2>{reportReadingMarkdown(report.oneSentence, citationNumbers) || '研究结论待验证'}</h2>
        <p>{reportReadingMarkdown(report.executiveSummary, citationNumbers)}</p>
        <time>{timestamp(report.researchCutoff || project.completedAt)}</time>
      </div>
    </section>

    <div className="ir-report-actions">
      <div><BookOpenCheck /><span>正式研究报告</span></div>
      <a href={industryResearchApi.downloadUrl(project.projectId, 'docx')} download><Download />下载 Word</a>
      <a href={industryResearchApi.downloadUrl(project.projectId, 'pdf')} download><FileText />下载 PDF</a>
    </div>

    {chapters.length ? <>
      <section className="ir-long-report-head">
        <div><span>目录</span><h2>完整{companyMode ? '公司' : '行业'}深度报告</h2></div>
      </section>
      <nav className="ir-report-toc" aria-label="长篇报告章节目录">
        {chapters.map((chapter, index) => <a href={`#ir-chapter-${chapter.chapterId}`} key={chapter.chapterId}><span>{String(index + 1).padStart(2, '0')}</span>{chapter.title}</a>)}
      </nav>
      <section className="ir-long-report-body">
        {chapters.map((chapter, index) => <article id={`ir-chapter-${chapter.chapterId}`} key={chapter.chapterId}>
          <header><span>{index + 1}</span><h2>{chapter.title}</h2>{chapter.summary ? <p>{reportReadingMarkdown(chapter.summary, citationNumbers)}</p> : null}</header>
          <div className="ir-markdown"><Markdown remarkPlugins={[remarkGfm]}>{reportReadingMarkdown(chapter.bodyMarkdown, citationNumbers)}</Markdown></div>
        </article>)}
      </section>
    </> : null}
    {figures.length ? <section className="ir-figure-section ir-report-figures">
      <div className="ir-section-heading"><div><span>数据图表</span><h2>关键图表</h2></div></div>
      <div className="ir-figure-grid">{figures.map(figure => <ResearchFigure figure={figure} key={figure.id} />)}</div>
    </section> : null}
    {citedSources.length ? <section className="ir-report-sources">
      <h2>资料来源</h2>
      <ol>{citedSources.map(({ number, evidenceId, item }) => <li key={evidenceId}>
        <span>{number}</span><div><strong>{item?.source || item?.kind || '资料来源'}</strong><p>{[item?.date, item?.title || item?.summary || evidenceId].filter(Boolean).join(' · ')}</p>{item?.url ? <a href={item.url} target="_blank" rel="noreferrer">查看原文</a> : null}</div>
      </li>)}</ol>
    </section> : null}
    <footer className="ir-report-disclaimer">本报告仅供研究参考，不构成投资建议。</footer>
  </div>;
}

export default function IndustryResearchPage() {
  const [topic, setTopic] = useState('光模块');
  const [researchType, setResearchType] = useState<ResearchType>('industry');
  const [lookbackDays, setLookbackDays] = useState(730);
  const [blueprint, setBlueprint] = useState<IndustryResearchBlueprint | null>(null);
  const [projects, setProjects] = useState<IndustryResearchProject[]>([]);
  const [activeProject, setActiveProject] = useState<IndustryResearchProject>();
  const [projectFilter, setProjectFilter] = useState<ProjectFilter>('all');
  const [tab, setTab] = useState<WorkspaceTab>('map');
  const [loading, setLoading] = useState(true);
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState('');
  const blueprintRequestRef = useRef(0);

  useEffect(() => { document.title = '行业与公司调研 - 乐子乌超级价值'; }, []);

  const loadProjects = useCallback(async () => {
    const next = await industryResearchApi.projects();
    setProjects(next.items);
    setActiveProject(current => {
      if (!current) return undefined;
      const summary = next.items.find(item => item.projectId === current.projectId);
      return summary ? {
        ...current, ...summary,
        snapshot: isCompleteSnapshot(current.snapshot) ? current.snapshot : summary.snapshot,
        report: current.report?.oneSentence ? current.report : summary.report,
      } : current;
    });
  }, []);

  const loadBlueprint = useCallback(async (
    requestedTopic = topic,
    requestedDays = lookbackDays,
    requestedType: ResearchType = researchType,
  ) => {
    const trimmed = requestedTopic.trim();
    if (trimmed.length < 2) { setError('请输入至少两个字的行业或公司名称'); return; }
    const requestId = ++blueprintRequestRef.current;
    setLoading(true);
    setActiveProject(undefined);
    try {
      const next = await industryResearchApi.blueprint(trimmed, requestedDays, requestedType);
      if (requestId !== blueprintRequestRef.current) return;
      setBlueprint(next); setError('');
    } catch (caught) {
      if (requestId !== blueprintRequestRef.current) return;
      setError(caught instanceof Error ? caught.message : '行业证据蓝图暂时无法读取，页面会保留当前内容');
    } finally {
      if (requestId === blueprintRequestRef.current) setLoading(false);
    }
  }, [lookbackDays, researchType, topic]);

  useEffect(() => { void Promise.allSettled([loadBlueprint('光模块', 730, 'industry'), loadProjects()]); }, []); // eslint-disable-line react-hooks/exhaustive-deps
  usePageActivationRefresh(loadProjects, { intervalMs: 20_000, minIntervalMs: 3_000, runOnMount: false });

  const hasRunningProjects = projects.some(project => runningStatuses.has(project.status));
  useEffect(() => {
    if (!hasRunningProjects) return undefined;
    const timer = window.setInterval(() => {
      if (document.visibilityState === 'visible') void loadProjects();
    }, 10_000);
    return () => window.clearInterval(timer);
  }, [hasRunningProjects, loadProjects]);

  const pollingProjectId = activeProject?.projectId;
  const pollingProjectStatus = activeProject?.status;
  useEffect(() => {
    if (!pollingProjectId || !pollingProjectStatus || !['queued', 'collecting', 'analyzing'].includes(pollingProjectStatus)) return undefined;
    const timer = window.setInterval(async () => {
      if (document.visibilityState === 'hidden') return;
      try {
        const next = await industryResearchApi.project(pollingProjectId);
        setActiveProject(next);
        setProjects(current => current.map(item => item.projectId === next.projectId ? next : item));
        if (readableStatuses.has(next.status) && isCompleteSnapshot(next.snapshot)) {
          const completedSnapshot = next.snapshot;
          setBlueprint(current => current && current.topic === next.topic && current.lookbackDays === next.lookbackDays && current.researchType === next.researchType
            ? { ...current, queryTerms: completedSnapshot.queryTerms, snapshot: completedSnapshot }
            : current);
        }
      } catch { /* background polling remains silent and retries */ }
    }, 3_000);
    return () => window.clearInterval(timer);
  }, [pollingProjectId, pollingProjectStatus]);

  const startProject = async (topicOverride = topic) => {
    const requestedTopic = topicOverride.trim();
    if (requestedTopic.length < 2 || starting) return;
    setStarting(true);
    setActiveProject(undefined);
    try {
      const matchingBlueprint = blueprint?.topic.trim() === requestedTopic && blueprint.lookbackDays === lookbackDays
        && blueprint.researchType === researchType ? blueprint : undefined;
      if (!matchingBlueprint) setBlueprint(null);
      const project = await industryResearchApi.createProject({
        topic: requestedTopic, researchType, lookbackDays,
        objective: researchType === 'company'
          ? `全面研究${requestedTopic}的商业模式、产品与产业位置、竞争优势、财务报表、盈利质量、一致预期、估值变量、公告事件和主要风险，并形成不少于2万字的可追溯标准上市公司研究报告`
          : `尽快搞明白${requestedTopic}的产业脉络、发展趋势、龙头企业、最大痛点和应用场景，并形成不少于2万字的可追溯标准行业研究报告`,
        queryTerms: matchingBlueprint?.queryTerms ?? [],
      });
      setActiveProject(project); setProjects(current => [project, ...current.filter(item => item.projectId !== project.projectId)]); setProjectFilter('running'); setTab('report'); setError(''); revealWorkbench();
      setLoading(false);

      // The task is already durable at this point.  Build the visual evidence
      // blueprint in parallel so a cold SQLite scan can never delay submission.
      const blueprintRequestId = ++blueprintRequestRef.current;
      void industryResearchApi.blueprint(requestedTopic, lookbackDays, researchType).then(next => {
        if (blueprintRequestId !== blueprintRequestRef.current) return;
        setBlueprint(next);
      }).catch(() => {
        // Task polling remains the source of truth; blueprint warm-up failure is
        // deliberately silent and will be replaced by the task snapshot later.
      });
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : '课题创建失败');
    } finally {
      setStarting(false);
      setLoading(false);
    }
  };

  const openProject = useCallback(async (projectId: string, shouldReveal = true) => {
    const requestId = ++blueprintRequestRef.current;
    setLoading(false);
    try {
      const detail = await industryResearchApi.project(projectId);
      if (requestId !== blueprintRequestRef.current) return;
      setTopic(detail.topic);
      setResearchType(detail.researchType);
      setLookbackDays(detail.lookbackDays);
      setActiveProject(detail);
      setProjects(current => current.map(item => item.projectId === detail.projectId ? { ...item, ...detail } : item));
      if (isCompleteSnapshot(detail.snapshot)) {
        const snapshot = detail.snapshot;
        setBlueprint(current => current ? {
          ...current, topic: detail.topic, researchType: detail.researchType,
          lookbackDays: detail.lookbackDays, snapshot, queryTerms: snapshot.queryTerms,
        } : current);
      }
      setTab('report');
      setError('');
      if (shouldReveal) revealWorkbench();
    } catch (caught) {
      if (requestId !== blueprintRequestRef.current) return;
      setError(caught instanceof Error ? caught.message : '任务详情暂时无法读取');
    }
  }, []);

  useEffect(() => {
    if (!blueprint || loading || activeProject || topic.trim() !== blueprint.topic.trim()) return;
    const matching = projects.find(project => readableStatuses.has(project.status)
      && project.topic.trim() === blueprint.topic.trim()
      && project.researchType === blueprint.researchType
      && project.lookbackDays === blueprint.lookbackDays);
    if (matching) void openProject(matching.projectId, false);
  }, [activeProject, blueprint, loading, openProject, projects, researchType, topic]);

  const projectCounts = useMemo(() => ({
    all: projects.length,
    running: projects.filter(project => runningStatuses.has(project.status)).length,
    completed: projects.filter(project => project.status === 'completed').length,
    limited: projects.filter(project => project.status === 'limited').length,
    failed: projects.filter(project => project.status === 'failed').length,
  }), [projects]);
  const visibleProjects = useMemo(() => projects.filter(project => {
    if (projectFilter === 'running') return runningStatuses.has(project.status);
    if (projectFilter === 'completed') return project.status === 'completed';
    if (projectFilter === 'limited') return project.status === 'limited';
    if (projectFilter === 'failed') return project.status === 'failed';
    return true;
  }).slice(0, 12), [projectFilter, projects]);

  const activeProjectMatchesBlueprint = Boolean(activeProject && blueprint
    && activeProject.topic.trim() === blueprint.topic.trim()
    && activeProject.researchType === blueprint.researchType
    && activeProject.lookbackDays === blueprint.lookbackDays);
  const blueprintMatchesQuery = Boolean(blueprint
    && blueprint.topic.trim() === topic.trim()
    && blueprint.researchType === researchType
    && blueprint.lookbackDays === lookbackDays);
  const contextualProject = activeProjectMatchesBlueprint ? activeProject : undefined;
  const activeSnapshot = isCompleteSnapshot(contextualProject?.snapshot) ? contextualProject.snapshot : blueprint?.snapshot;
  const activeReport = contextualProject?.report;
  const tabs: Array<{ key: WorkspaceTab; label: string; icon: typeof Network }> = [
    { key: 'map', label: '研究地图', icon: Network }, { key: 'companies', label: '公司对比', icon: Building2 },
    { key: 'evidence', label: '证据库', icon: LibraryBig }, { key: 'questions', label: '访谈问题', icon: MessageSquareText },
    { key: 'report', label: '研究报告', icon: BookOpenCheck },
  ];

  return <AppPage className="max-w-[1840px]">
    <main className="ir-shell">
      <header className="ir-hero">
        <motion.div initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }}>
          <p className="ir-kicker"><Sparkles /> 全渠道证据驱动研究</p>
          <h1>行业与公司调研</h1>
          <p>输入行业或上市公司，系统自动汇集研报、机构段子、相关录音转写、互联网信息、财务报表、公告和行情，由 Kimi 形成标准 2 万字深度报告。</p>
        </motion.div>
        <div className="ir-live"><i /><span>多层证据统一固化</span><strong>Kimi 后台分章研究</strong></div>
      </header>

      <section className="ir-command">
        <div className="ir-type-switch" role="group" aria-label="研究对象类型">
          <button type="button" className={researchType === 'industry' ? 'is-active' : ''} onClick={() => { setResearchType('industry'); setActiveProject(undefined); }}><Network />行业</button>
          <button type="button" className={researchType === 'company' ? 'is-active' : ''} onClick={() => { setResearchType('company'); setActiveProject(undefined); }}><Building2 />上市公司</button>
        </div>
        <div className="ir-query"><Search /><label><span>{researchType === 'company' ? '研究哪家上市公司' : '研究哪个行业或主题'}</span><input value={topic} onChange={event => { setTopic(event.target.value); if (event.target.value.trim() !== blueprint?.topic.trim()) setActiveProject(undefined); }} onKeyDown={event => { if (event.key === 'Enter') void startProject(event.currentTarget.value); }} placeholder={researchType === 'company' ? '输入股票名称或代码，例如：中际旭创 / 300308.SZ' : '输入行业，例如：光模块、低空经济'} /></label></div>
        <select value={lookbackDays} onChange={event => { setLookbackDays(Number(event.target.value)); setActiveProject(undefined); }} aria-label="资料时间范围">
          <option value={365}>最近 1 年</option><option value={730}>最近 2 年</option><option value={1095}>最近 3 年</option><option value={1825}>最近 5 年</option>
        </select>
        <button type="button" className="ir-secondary" onClick={() => void loadBlueprint()} disabled={topic.trim().length < 2}>{loading ? <LoaderCircle className="is-spinning" /> : <FileSearch />}仅查看资料覆盖</button>
        <button type="button" className="ir-primary" onClick={() => void startProject()} disabled={topic.trim().length < 2 || starting}>{starting ? <LoaderCircle className="is-spinning" /> : <Play />}开始{researchType === 'company' ? '公司' : '行业'}研究</button>
      </section>

      {activeProject ? <ResearchAnswerBrief project={activeProject} onOpen={() => { setTab('report'); revealWorkbench(); }} /> : null}

      <section className="ir-task-center" aria-label="行业与公司调研任务中心">
        <div className="ir-task-center-head">
          <div><span>任务账本</span><h2>研究任务与历史报告</h2><p>任务在服务器后台独立执行，离开页面后仍会继续；每位用户只看到自己的课题。</p></div>
          <button type="button" onClick={() => void loadProjects()}><Clock3 />刷新任务</button>
        </div>
        <div className="ir-task-filters" role="group" aria-label="任务状态筛选">
          {([
            ['all', '全部'], ['running', '运行中'], ['completed', '完整完成'], ['limited', '有限完成'], ['failed', '失败'],
          ] as Array<[ProjectFilter, string]>).map(([key, label]) => <button type="button" aria-pressed={projectFilter === key} className={projectFilter === key ? 'is-active' : ''} onClick={() => setProjectFilter(key)} key={key}>
            {label}<strong>{projectCounts[key]}</strong>
          </button>)}
        </div>
        {visibleProjects.length ? <div className="ir-task-list">
          {visibleProjects.map(project => <button type="button" className={`ir-task-row is-${project.status} ${project.projectId === activeProject?.projectId ? 'is-selected' : ''}`} key={project.projectId} onClick={() => void openProject(project.projectId)}>
            <span className="ir-task-icon">{project.status === 'completed' ? <CheckCircle2 /> : project.status === 'limited' ? <FileCheck2 /> : project.status === 'failed' ? <CircleAlert /> : <LoaderCircle className="is-spinning" />}</span>
            <span className="ir-task-copy"><strong>{project.topic}</strong><small>{project.message}</small><em>{project.researchType === 'company' ? '上市公司研究' : '行业研究'} · {timestamp(project.updatedAt || project.createdAt)} · {project.lookbackDays} 天资料范围</em></span>
            <span className="ir-task-meter"><i><b style={{ width: `${Math.max(0, Math.min(100, project.progress))}%` }} /></i><strong>{project.progress}%</strong></span>
            <span className="ir-task-action">{project.status === 'completed' ? '打开报告' : project.status === 'limited' ? '打开有限报告' : project.status === 'failed' ? '查看原因' : statusLabel[project.status]}<ChevronRight /></span>
          </button>)}
        </div> : <div className="ir-task-empty"><Clock3 /><strong>{projects.length ? '当前筛选没有任务' : '还没有调研任务'}</strong><span>{projects.length ? '切换其他状态查看历史任务。' : '输入行业或公司后可直接启动 Kimi 深度报告。'}</span></div>}
      </section>

      {error ? <div className="ir-inline-error" role="status"><CircleAlert />{error}</div> : null}
      {loading && !blueprint ? <div className="ir-loading"><LoaderCircle /><strong>正在跨研报、机构段子、录音、公告、行情财务与互联网建立证据蓝图…</strong><span>数据未准备好时只显示等待，不弹窗打断。</span></div> : null}

      {blueprint && blueprintMatchesQuery ? <>
        <section className="ir-mission-strip">
          {blueprint.methodology.stages.map((stage, index) => {
            const active = contextualProject?.stage === stage.stage;
            const done = readableStatuses.has(contextualProject?.status ?? '') || (contextualProject && blueprint.methodology.stages.findIndex(item => item.stage === contextualProject.stage) > index);
            return <article className={active ? 'is-active' : done ? 'is-done' : ''} key={stage.stage}>
              <span>{done ? <CheckCircle2 /> : `0${index + 1}`}</span><div><small>{stage.hours}</small><h2>{stage.title}</h2><p>{stage.goal}</p></div>
            </article>;
          })}
        </section>

        <section className="ir-overview">
          <div className="ir-stat"><Database /><span>相关证据</span><strong>{compact(activeSnapshot?.totals.evidence ?? 0)}</strong><small>按关键词从本地库召回</small></div>
          <div className="ir-stat"><Building2 /><span>企业候选</span><strong>{activeSnapshot?.companies.length ?? 0}</strong><small>必须在证据中明确出现</small></div>
          <div className="ir-stat"><RadioTower /><span>数据渠道</span><strong>{activeSnapshot?.coverage.filter(item => item.count > 0).length ?? 0}/{activeSnapshot?.coverage.length ?? 8}</strong><small>空缺会明确显示</small></div>
          <div className="ir-stat"><ShieldCheck /><span>原文证据</span><strong>{activeSnapshot?.evidence.filter(item => item.originalAvailable).length ?? 0}</strong><small>保留来源、日期与入口</small></div>
          <div className="ir-task-state">
            {contextualProject ? <><ProgressRing value={contextualProject.progress} /><div><span>{statusLabel[contextualProject.status]}</span><strong>{contextualProject.topic}</strong><p>{contextualProject.message}</p></div></> : <><Gauge /><div><span>证据蓝图就绪</span><strong>{blueprint.topic}</strong><p>启动课题后后台生成结论报告</p></div></>}
          </div>
        </section>

        <ResearchDataLedger snapshot={activeSnapshot ?? blueprint.snapshot} />
        <AIResearchWorkflow blueprint={blueprint} project={contextualProject} />
        <ResearchMethodMatrix blueprint={blueprint} />
        <ResearchQualityPanel snapshot={activeSnapshot ?? blueprint.snapshot} report={activeReport} />

        <section className="ir-coverage">
          <div className="ir-section-heading"><div><span>数据覆盖</span><h2>本次课题实际取得的材料</h2></div><small>{blueprint.methodology.evidenceRule}</small></div>
          <div>{activeSnapshot?.coverage.map(item => <article className={item.status !== 'covered' ? 'is-missing' : ''} key={item.key}>
            <span>{item.status === 'covered' ? <CheckCircle2 /> : <CircleAlert />}</span><div><strong>{item.name}</strong><small>{item.evidenceLevel} · {item.mediaFiles ? `${item.mediaFiles} 个文件` : '可追溯来源'}</small></div><em>{compact(item.count)}</em>
          </article>)}</div>
        </section>

        <nav className="ir-tabs" id="industry-research-workbench" tabIndex={-1} aria-label="行业与公司调研工作台栏目">{tabs.map(({ key, label, icon: Icon }) => <button type="button" className={tab === key ? 'is-active' : ''} onClick={() => setTab(key)} key={key}><Icon />{label}</button>)}</nav>

        {tab === 'map' ? <ResearchMap blueprint={blueprint} /> : null}
        {tab === 'companies' ? <CompanyMatrix blueprint={blueprint} /> : null}
        {tab === 'evidence' ? <EvidenceLibrary blueprint={blueprint} /> : null}
        {tab === 'questions' ? <QuestionsView blueprint={blueprint} report={activeReport} /> : null}
        {tab === 'report' ? <ReportView project={activeProject} /> : null}

      </> : null}
      {!blueprintMatchesQuery && activeProject ? <section id="industry-research-workbench" tabIndex={-1} className="ir-task-report-fallback" aria-label="当前研究任务">
        <ReportView project={activeProject} />
      </section> : null}
    </main>
  </AppPage>;
}
