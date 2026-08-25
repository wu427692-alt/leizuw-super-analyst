import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { motion } from 'motion/react';
import Markdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { Area, AreaChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import {
  ArrowRight, BookOpenCheck, Boxes, Building2, CheckCircle2, ChevronRight, CircleAlert,
  Clock3, Database, FileSearch, Gauge, LibraryBig, LoaderCircle, MessageSquareText,
  Network, Play, RadioTower, Search, ShieldCheck, Sparkles, Telescope,
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
type ProjectFilter = 'all' | 'running' | 'completed' | 'failed';

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
  queued: '排队中', collecting: '收集证据', analyzing: '交叉分析', completed: '已完成', failed: '需要重试',
};

const runningStatuses = new Set(['queued', 'collecting', 'analyzing']);

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

function ResearchMap({ blueprint }: { blueprint: IndustryResearchBlueprint }) {
  const snapshot = blueprint.snapshot;
  const stages = [
    { icon: Boxes, name: '上游与关键投入', note: '原料、设备、芯片、工艺与标准' },
    { icon: Network, name: '核心产品与系统', note: '产品形态、价值量、成本与议价权' },
    { icon: Building2, name: '客户与应用场景', note: '谁付钱、为何采用、需求指标是什么' },
    { icon: Telescope, name: '趋势与验证信号', note: '技术路线、供需拐点、反证条件' },
  ];
  return <div className="ir-map-grid">
    <section className="ir-chain" aria-label="产业链研究地图">
      <div className="ir-section-heading"><div><span>RESEARCH MAP</span><h2>产业链拆解骨架</h2></div><small>AI 结论只会填入有证据支持的节点</small></div>
      <div className="ir-chain-track">
        {stages.map(({ icon: Icon, name, note }, index) => <article key={name}>
          <div><Icon /><span>0{index + 1}</span></div><h3>{name}</h3><p>{note}</p>
          {index < stages.length - 1 ? <ArrowRight className="ir-chain-arrow" /> : null}
        </article>)}
      </div>
      <div className="ir-terms"><span>本次召回词</span>{snapshot.queryTerms.map(term => <em key={term}>{term}</em>)}</div>
    </section>
    <section className="ir-timeline-panel">
      <div className="ir-section-heading"><div><span>SIGNAL DENSITY</span><h2>资料时间分布</h2></div><small>只表示资料数量，不代表价格趋势</small></div>
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
    <div className="ir-section-heading"><div><span>COMPANY CANDIDATES</span><h2>证据中明确出现的企业候选</h2></div><small>出现频次是研究优先级，不是龙头结论</small></div>
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
    <div className="ir-section-heading"><div><span>EVIDENCE LEDGER</span><h2>可回到原文的证据库</h2></div><small>当前展示优先级最高的 {items.length} 条</small></div>
    <div className="ir-filter-row"><button type="button" className={kind === 'all' ? 'is-active' : ''} onClick={() => setKind('all')}>全部</button>{kinds.map(value => <button type="button" className={kind === value ? 'is-active' : ''} onClick={() => setKind(value)} key={value}>{value}</button>)}</div>
    <div className="ir-evidence-list">
      {items.slice(0, 80).map(item => {
        const external = Boolean(item.url && !item.evidenceId.startsWith('event:'));
        return <a key={item.evidenceId} href={evidenceHref(item)} target={external ? '_blank' : undefined} rel={external ? 'noreferrer' : undefined}>
          <span className={`ir-level is-${item.evidenceLevel}`}>{item.evidenceLevel}</span>
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
    <section><div className="ir-section-heading"><div><span>INTERVIEW GUIDE</span><h2>专家与公司访谈问题</h2></div></div><ol>{interview.map((item, index) => <li key={item}><span>{String(index + 1).padStart(2, '0')}</span><p>{item}</p></li>)}</ol></section>
    <section><div className="ir-section-heading"><div><span>OPEN QUESTIONS</span><h2>尚未闭环的研究问题</h2></div></div><ol>{unknowns.map((item, index) => <li key={item}><span>Q{index + 1}</span><p>{item}</p></li>)}</ol></section>
  </div>;
}

function ResearchAnswerBrief({ project, onOpen }: { project: IndustryResearchProject; onOpen: () => void }) {
  const report = project.report;
  if (!report) return <section className="ir-answer-loading" aria-live="polite">
    <ProgressRing value={project.progress} />
    <div><span>AI RESEARCH IN PROGRESS</span><h2>{project.message}</h2><p>证据召回完成后会先显示可用结论，不必等待八章长报告全部写完。</p></div>
  </section>;
  const leaders = (report.leaders ?? []).slice(0, 4);
  const trends = (report.trends ?? []).slice(0, 3);
  const bottlenecks = (report.bottlenecks ?? []).slice(0, 3);
  const indicators = (report.monitoringIndicators ?? []).slice(0, 4);
  const isDraft = project.status !== 'completed';
  return <section className={`ir-answer-brief ${isDraft ? 'is-draft' : ''}`} aria-label={`${project.topic}研究结论`}>
    <header>
      <div><span>{isDraft ? 'AI FIRST-PASS ANSWER' : 'RESEARCH ANSWER'}</span><h2>{project.topic} · 研究结论先行</h2></div>
      <div className="ir-answer-status"><i />{isDraft ? `首轮结论可读 · 长报告 ${project.progress}%` : '完整报告已完成'}</div>
    </header>
    <div className="ir-answer-thesis"><strong>{report.oneSentence || '首轮研究结论正在形成'}</strong><p>{report.executiveSummary || '已固定证据快照，正在生成可核验的产业链、趋势、公司与风险判断。'}</p></div>
    <div className="ir-answer-grid">
      <article><span><Telescope />趋势判断</span>{trends.length ? trends.map(item => <p key={`${item.horizon}-${item.claim}`}><strong>{item.horizon || '待验证'}</strong>{item.claim}</p>) : <p>正在从证据中提取短中长期趋势。</p>}</article>
      <article><span><Building2 />龙头与关键公司</span>{leaders.length ? leaders.map(item => <p key={`${item.symbol}-${item.name}`}><strong>{item.name || '待识别'} {item.symbol || ''}</strong>{item.rationale}</p>) : <p>当前证据不足以确认龙头，报告会明确保留为待验证。</p>}</article>
      <article><span><CircleAlert />核心瓶颈</span>{bottlenecks.length ? bottlenecks.map(item => <p key={item.issue}><strong>{item.issue || '待验证'}</strong>{item.whyItMatters}</p>) : <p>正在核对成本、技术、产能、客户验证与商业化瓶颈。</p>}</article>
      <article><span><Gauge />持续跟踪指标</span>{indicators.length ? indicators.map(item => <p key={`${item.indicator}-${item.frequency}`}><strong>{item.indicator || '待验证'}</strong>{item.frequency ? `${item.frequency} · ${item.source || '待确认来源'}` : item.source}</p>) : <p>正在生成可用于后续更新的验证指标。</p>}</article>
    </div>
    <footer><span>{project.message}</span><button type="button" onClick={onOpen}>{isDraft ? '查看当前研究底稿' : '打开完整 8 章报告'}<ChevronRight /></button></footer>
  </section>;
}

function ReportView({ project }: { project?: IndustryResearchProject }) {
  const report = project?.report;
  if (!project) return <div className="ir-report-placeholder"><BookOpenCheck /><h2>启动课题后生成研究报告</h2><p>后台会先固定证据快照，再分析产业链、趋势、企业、瓶颈、应用与证伪条件。</p></div>;
  if (!report) return <div className="ir-report-placeholder"><LoaderCircle className="is-spinning" /><h2>{project.message}</h2><p>任务在后台运行，可以离开本页；首轮结论生成后会直接在这里出现。</p><ProgressRing value={project.progress} /></div>;
  const blocks = [
    { title: '产业链节点', items: report.chainNodes, get: (item: Record<string, unknown>) => `${item.stage || ''} · ${item.role || ''}`, sub: (item: Record<string, unknown>) => String(item.economics || '待验证') },
    { title: '趋势与拐点', items: report.trends, get: (item: Record<string, unknown>) => `${item.horizon || ''} · ${item.claim || ''}`, sub: (item: Record<string, unknown>) => Array.isArray(item.drivers) ? item.drivers.join('；') : '待验证' },
    { title: '龙头候选', items: report.leaders, get: (item: Record<string, unknown>) => `${item.name || '待识别'} ${item.symbol || ''}`, sub: (item: Record<string, unknown>) => String(item.rationale || '待验证') },
    { title: '核心瓶颈', items: report.bottlenecks, get: (item: Record<string, unknown>) => String(item.issue || '待验证'), sub: (item: Record<string, unknown>) => String(item.whyItMatters || '') },
    { title: '应用场景', items: report.applications, get: (item: Record<string, unknown>) => String(item.scenario || '待验证'), sub: (item: Record<string, unknown>) => String(item.demandLogic || '') },
  ];
  const chapters = report.chapters ?? [];
  const reportChars = report.longFormCharCount ?? chapters.reduce((sum, chapter) => sum + (chapter.charCount || 0), 0);
  return <div className="ir-report">
    {project.status !== 'completed' ? <section className="ir-report-live"><LoaderCircle className="is-spinning" /><div><strong>首轮研究结论已经可读</strong><p>{project.message}；下方结论会保留，完整章节生成后自动补入。</p></div><ProgressRing value={project.progress} /></section> : null}
    <section className="ir-report-hero"><span>EXECUTIVE VIEW</span><h2>{report.oneSentence || '研究结论待验证'}</h2><p>{report.executiveSummary}</p></section>
    {chapters.length ? <>
      <section className="ir-long-report-head">
        <div><span>AI LONG-FORM REPORT</span><h2>完整行业深度报告</h2><p>固定证据快照后分章并行撰写；正文中的方括号编号可回到证据库核验。</p></div>
        <div className="ir-long-report-metrics"><strong>{reportChars.toLocaleString('zh-CN')}</strong><span>报告字符</span><strong>{chapters.length}</strong><span>完整章节</span><strong>{project.snapshot?.totals.evidence?.toLocaleString('zh-CN') ?? '—'}</strong><span>参考证据</span></div>
      </section>
      <nav className="ir-report-toc" aria-label="长篇报告章节目录">
        {chapters.map((chapter, index) => <a href={`#ir-chapter-${chapter.chapterId}`} key={chapter.chapterId}><span>{String(index + 1).padStart(2, '0')}</span>{chapter.title}<em>{chapter.charCount.toLocaleString('zh-CN')}字</em></a>)}
      </nav>
      <section className="ir-long-report-body">
        {chapters.map((chapter, index) => <article id={`ir-chapter-${chapter.chapterId}`} key={chapter.chapterId}>
          <header><span>CHAPTER {String(index + 1).padStart(2, '0')}</span><h2>{chapter.title}</h2><p>{chapter.summary}</p><small>{chapter.charCount.toLocaleString('zh-CN')} 字 · {chapter.evidenceIds.length} 条直接引用</small></header>
          <div className="ir-markdown"><Markdown remarkPlugins={[remarkGfm]}>{chapter.bodyMarkdown}</Markdown></div>
          {chapter.openQuestions.length ? <footer><strong>仍需验证</strong>{chapter.openQuestions.map(question => <p key={question}>{question}</p>)}</footer> : null}
        </article>)}
      </section>
    </> : null}
    <div className="ir-report-blocks">{blocks.map(block => <section key={block.title}><h3>{block.title}</h3>{(block.items ?? []).map((raw, index) => {
      const item = raw as Record<string, unknown>;
      return <article key={`${block.title}-${index}`}><span>{String(index + 1).padStart(2, '0')}</span><div><strong>{block.get(item)}</strong><p>{block.sub(item)}</p></div></article>;
    })}<>{!(block.items ?? []).length ? <p className="ir-muted">当前证据不足，保留为待验证项。</p> : null}</></section>)}</div>
    <section className="ir-falsification"><h3>证伪条件与持续监控</h3><div>{(report.falsificationConditions ?? []).map(item => <p key={item}><CircleAlert />{item}</p>)}</div></section>
  </div>;
}

export default function IndustryResearchPage() {
  const [topic, setTopic] = useState('光模块');
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

  useEffect(() => { document.title = '行业调研 - 乐子乌超级价值'; }, []);

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

  const loadBlueprint = useCallback(async (requestedTopic = topic, requestedDays = lookbackDays) => {
    const trimmed = requestedTopic.trim();
    if (trimmed.length < 2) { setError('请输入至少两个字的行业或公司名称'); return; }
    const requestId = ++blueprintRequestRef.current;
    setLoading(true);
    setActiveProject(undefined);
    try {
      const next = await industryResearchApi.blueprint(trimmed, requestedDays);
      if (requestId !== blueprintRequestRef.current) return;
      setBlueprint(next); setError('');
    } catch (caught) {
      if (requestId !== blueprintRequestRef.current) return;
      setError(caught instanceof Error ? caught.message : '行业证据蓝图暂时无法读取，页面会保留当前内容');
    } finally {
      if (requestId === blueprintRequestRef.current) setLoading(false);
    }
  }, [lookbackDays, topic]);

  useEffect(() => { void Promise.allSettled([loadBlueprint('光模块', 730), loadProjects()]); }, []); // eslint-disable-line react-hooks/exhaustive-deps
  usePageActivationRefresh(loadProjects, { intervalMs: 20_000, minIntervalMs: 3_000, runOnMount: false });

  const hasRunningProjects = projects.some(project => runningStatuses.has(project.status));
  useEffect(() => {
    if (!hasRunningProjects) return undefined;
    const timer = window.setInterval(() => { void loadProjects(); }, 3_000);
    return () => window.clearInterval(timer);
  }, [hasRunningProjects, loadProjects]);

  const pollingProjectId = activeProject?.projectId;
  const pollingProjectStatus = activeProject?.status;
  useEffect(() => {
    if (!pollingProjectId || !pollingProjectStatus || !['queued', 'collecting', 'analyzing'].includes(pollingProjectStatus)) return undefined;
    const timer = window.setInterval(async () => {
      try {
        const next = await industryResearchApi.project(pollingProjectId);
        setActiveProject(next);
        setProjects(current => current.map(item => item.projectId === next.projectId ? next : item));
        if (next.status === 'completed' && isCompleteSnapshot(next.snapshot)) {
          const completedSnapshot = next.snapshot;
          setBlueprint(current => current && current.topic === next.topic && current.lookbackDays === next.lookbackDays
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
    const requestId = ++blueprintRequestRef.current;
    setStarting(true);
    setLoading(true);
    setActiveProject(undefined);
    try {
      const sourceBlueprint = blueprint?.topic === requestedTopic && blueprint.lookbackDays === lookbackDays
        ? blueprint
        : await industryResearchApi.blueprint(requestedTopic, lookbackDays);
      if (requestId !== blueprintRequestRef.current) return;
      setBlueprint(sourceBlueprint);
      const project = await industryResearchApi.createProject({
        topic: sourceBlueprint.topic, researchType: 'industry', lookbackDays,
        objective: `尽快搞明白${sourceBlueprint.topic}的产业脉络、发展趋势、龙头企业、最大痛点和应用场景，并形成不少于2万字的可追溯深度报告`,
        queryTerms: sourceBlueprint.queryTerms,
      });
      setActiveProject(project); setProjects(current => [project, ...current.filter(item => item.projectId !== project.projectId)]); setProjectFilter('running'); setTab('report'); setError(''); revealWorkbench();
    } catch (caught) {
      if (requestId !== blueprintRequestRef.current) return;
      setError(caught instanceof Error ? caught.message : '课题创建失败');
    } finally {
      if (requestId === blueprintRequestRef.current) {
        setStarting(false);
        setLoading(false);
      }
    }
  };

  const openProject = useCallback(async (projectId: string, shouldReveal = true) => {
    const requestId = ++blueprintRequestRef.current;
    setLoading(false);
    try {
      const detail = await industryResearchApi.project(projectId);
      if (requestId !== blueprintRequestRef.current) return;
      setTopic(detail.topic);
      setLookbackDays(detail.lookbackDays);
      setActiveProject(detail);
      setProjects(current => current.map(item => item.projectId === detail.projectId ? { ...item, ...detail } : item));
      if (isCompleteSnapshot(detail.snapshot)) {
        const snapshot = detail.snapshot;
        setBlueprint(current => current ? {
          ...current, topic: detail.topic, lookbackDays: detail.lookbackDays, snapshot, queryTerms: snapshot.queryTerms,
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
    const matching = projects.find(project => project.status === 'completed'
      && project.topic.trim() === blueprint.topic.trim()
      && project.lookbackDays === blueprint.lookbackDays);
    if (matching) void openProject(matching.projectId, false);
  }, [activeProject, blueprint, loading, openProject, projects, topic]);

  const projectCounts = useMemo(() => ({
    all: projects.length,
    running: projects.filter(project => runningStatuses.has(project.status)).length,
    completed: projects.filter(project => project.status === 'completed').length,
    failed: projects.filter(project => project.status === 'failed').length,
  }), [projects]);
  const visibleProjects = useMemo(() => projects.filter(project => {
    if (projectFilter === 'running') return runningStatuses.has(project.status);
    if (projectFilter === 'completed') return project.status === 'completed';
    if (projectFilter === 'failed') return project.status === 'failed';
    return true;
  }).slice(0, 12), [projectFilter, projects]);

  const activeProjectMatchesBlueprint = Boolean(activeProject && blueprint
    && activeProject.topic.trim() === blueprint.topic.trim()
    && activeProject.lookbackDays === blueprint.lookbackDays);
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
          <p className="ir-kicker"><Sparkles /> RAPID RESEARCH · EVIDENCE FIRST</p>
          <h1>行业调研</h1>
          <p>先给结论，再展开证据：快速回答产业链、趋势、龙头、痛点、应用和关键验证指标，八章深度报告在后台继续补强。</p>
        </motion.div>
        <div className="ir-live"><i /><span>六类本地数据统一召回</span><strong>后台任务可离开页面</strong></div>
      </header>

      <section className="ir-command">
        <div className="ir-query"><Search /><label><span>我现在想搞明白</span><input value={topic} onChange={event => { setTopic(event.target.value); if (event.target.value.trim() !== blueprint?.topic.trim()) setActiveProject(undefined); }} onKeyDown={event => { if (event.key === 'Enter') void startProject(event.currentTarget.value); }} placeholder="输入行业或公司，例如：光模块" /></label></div>
        <select value={lookbackDays} onChange={event => { setLookbackDays(Number(event.target.value)); setActiveProject(undefined); }} aria-label="资料时间范围">
          <option value={365}>最近 1 年</option><option value={730}>最近 2 年</option><option value={1095}>最近 3 年</option><option value={1825}>最近 5 年</option>
        </select>
        <button type="button" className="ir-secondary" onClick={() => void loadBlueprint()} disabled={topic.trim().length < 2}>{loading ? <LoaderCircle className="is-spinning" /> : <FileSearch />}仅查看资料覆盖</button>
        <button type="button" className="ir-primary" onClick={() => void startProject()} disabled={topic.trim().length < 2 || starting}>{starting ? <LoaderCircle className="is-spinning" /> : <Play />}开始行业研究</button>
      </section>

      {contextualProject ? <ResearchAnswerBrief project={contextualProject} onOpen={() => { setTab('report'); revealWorkbench(); }} /> : null}

      <section className="ir-task-center" aria-label="行业调研任务中心">
        <div className="ir-task-center-head">
          <div><span>RESEARCH HISTORY</span><h2>研究任务与历史报告</h2><p>这里负责进度和历史管理；真正的研究答案优先显示在上方。</p></div>
          <button type="button" onClick={() => void loadProjects()}><Clock3 />刷新任务</button>
        </div>
        <div className="ir-task-filters" role="group" aria-label="任务状态筛选">
          {([
            ['all', '全部'], ['running', '运行中'], ['completed', '已完成'], ['failed', '失败'],
          ] as Array<[ProjectFilter, string]>).map(([key, label]) => <button type="button" aria-pressed={projectFilter === key} className={projectFilter === key ? 'is-active' : ''} onClick={() => setProjectFilter(key)} key={key}>
            {label}<strong>{projectCounts[key]}</strong>
          </button>)}
        </div>
        {visibleProjects.length ? <div className="ir-task-list">
          {visibleProjects.map(project => <button type="button" className={`ir-task-row is-${project.status} ${project.projectId === activeProject?.projectId ? 'is-selected' : ''}`} key={project.projectId} onClick={() => void openProject(project.projectId)}>
            <span className="ir-task-icon">{project.status === 'completed' ? <CheckCircle2 /> : project.status === 'failed' ? <CircleAlert /> : <LoaderCircle className="is-spinning" />}</span>
            <span className="ir-task-copy"><strong>{project.topic}</strong><small>{project.message}</small><em>{timestamp(project.updatedAt || project.createdAt)} · {project.lookbackDays} 天资料范围</em></span>
            <span className="ir-task-meter"><i><b style={{ width: `${Math.max(0, Math.min(100, project.progress))}%` }} /></i><strong>{project.progress}%</strong></span>
            <span className="ir-task-action">{project.status === 'completed' ? '打开报告' : project.status === 'failed' ? '查看原因' : statusLabel[project.status]}<ChevronRight /></span>
          </button>)}
        </div> : <div className="ir-task-empty"><Clock3 /><strong>{projects.length ? '当前筛选没有任务' : '还没有行业调研任务'}</strong><span>{projects.length ? '切换其他状态查看历史任务。' : '输入行业名称后可直接启动 AI 深度报告。'}</span></div>}
      </section>

      {error ? <div className="ir-inline-error" role="status"><CircleAlert />{error}</div> : null}
      {loading && !blueprint ? <div className="ir-loading"><LoaderCircle /><strong>正在跨研报、机构语料、公告、行情财务、企业事实与新闻建立证据蓝图…</strong><span>数据未准备好时只显示等待，不弹窗打断。</span></div> : null}

      {blueprint ? <>
        <section className="ir-mission-strip">
          {blueprint.methodology.stages.map((stage, index) => {
            const active = contextualProject?.stage === stage.stage;
            const done = contextualProject?.status === 'completed' || (contextualProject && blueprint.methodology.stages.findIndex(item => item.stage === contextualProject.stage) > index);
            return <article className={active ? 'is-active' : done ? 'is-done' : ''} key={stage.stage}>
              <span>{done ? <CheckCircle2 /> : `0${index + 1}`}</span><div><small>{stage.hours}</small><h2>{stage.title}</h2><p>{stage.goal}</p></div>
            </article>;
          })}
        </section>

        <section className="ir-overview">
          <div className="ir-stat"><Database /><span>相关证据</span><strong>{compact(activeSnapshot?.totals.evidence ?? 0)}</strong><small>按关键词从本地库召回</small></div>
          <div className="ir-stat"><Building2 /><span>企业候选</span><strong>{activeSnapshot?.companies.length ?? 0}</strong><small>必须在证据中明确出现</small></div>
          <div className="ir-stat"><RadioTower /><span>数据渠道</span><strong>{activeSnapshot?.coverage.filter(item => item.count > 0).length ?? 0}/6</strong><small>空缺会明确显示</small></div>
          <div className="ir-stat"><ShieldCheck /><span>原文证据</span><strong>{activeSnapshot?.evidence.filter(item => item.originalAvailable).length ?? 0}</strong><small>保留来源、日期与入口</small></div>
          <div className="ir-task-state">
            {contextualProject ? <><ProgressRing value={contextualProject.progress} /><div><span>{statusLabel[contextualProject.status]}</span><strong>{contextualProject.topic}</strong><p>{contextualProject.message}</p></div></> : <><Gauge /><div><span>证据蓝图就绪</span><strong>{blueprint.topic}</strong><p>启动课题后后台生成结论报告</p></div></>}
          </div>
        </section>

        <section className="ir-coverage">
          <div className="ir-section-heading"><div><span>EVIDENCE COVERAGE</span><h2>本次课题的数据覆盖</h2></div><small>{blueprint.methodology.evidenceRule}</small></div>
          <div>{activeSnapshot?.coverage.map(item => <article className={item.status === 'missing' ? 'is-missing' : ''} key={item.key}>
            <span>{item.status === 'covered' ? <CheckCircle2 /> : <CircleAlert />}</span><div><strong>{item.name}</strong><small>{item.evidenceLevel} · {item.mediaFiles ? `${item.mediaFiles} 个文件` : '可追溯来源'}</small></div><em>{compact(item.count)}</em>
          </article>)}</div>
        </section>

        <nav className="ir-tabs" id="industry-research-workbench" tabIndex={-1} aria-label="行业调研工作台栏目">{tabs.map(({ key, label, icon: Icon }) => <button type="button" className={tab === key ? 'is-active' : ''} onClick={() => setTab(key)} key={key}><Icon />{label}</button>)}</nav>

        {tab === 'map' ? <ResearchMap blueprint={blueprint} /> : null}
        {tab === 'companies' ? <CompanyMatrix blueprint={blueprint} /> : null}
        {tab === 'evidence' ? <EvidenceLibrary blueprint={blueprint} /> : null}
        {tab === 'questions' ? <QuestionsView blueprint={blueprint} report={activeReport} /> : null}
        {tab === 'report' ? <ReportView project={contextualProject} /> : null}

      </> : null}
    </main>
  </AppPage>;
}
