import { useCallback, useEffect, useMemo, useState } from 'react';
import { motion } from 'motion/react';
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
  IndustryEvidence, IndustryResearchBlueprint, IndustryResearchProject, IndustryResearchReport,
} from '../types/industryResearch';
import './IndustryResearchPage.css';

type WorkspaceTab = 'map' | 'companies' | 'evidence' | 'questions' | 'report';

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

function ReportView({ project }: { project?: IndustryResearchProject }) {
  const report = project?.report;
  if (!project) return <div className="ir-report-placeholder"><BookOpenCheck /><h2>启动课题后生成研究报告</h2><p>后台会先固定证据快照，再分析产业链、趋势、企业、瓶颈、应用与证伪条件。</p></div>;
  if (project.status !== 'completed' || !report) return <div className="ir-report-placeholder"><LoaderCircle className="is-spinning" /><h2>{project.message}</h2><p>任务在后台运行，可以离开本页；完成后会保留在你的课题列表。</p><ProgressRing value={project.progress} /></div>;
  const blocks = [
    { title: '产业链节点', items: report.chainNodes, get: (item: Record<string, unknown>) => `${item.stage || ''} · ${item.role || ''}`, sub: (item: Record<string, unknown>) => String(item.economics || '待验证') },
    { title: '趋势与拐点', items: report.trends, get: (item: Record<string, unknown>) => `${item.horizon || ''} · ${item.claim || ''}`, sub: (item: Record<string, unknown>) => Array.isArray(item.drivers) ? item.drivers.join('；') : '待验证' },
    { title: '龙头候选', items: report.leaders, get: (item: Record<string, unknown>) => `${item.name || '待识别'} ${item.symbol || ''}`, sub: (item: Record<string, unknown>) => String(item.rationale || '待验证') },
    { title: '核心瓶颈', items: report.bottlenecks, get: (item: Record<string, unknown>) => String(item.issue || '待验证'), sub: (item: Record<string, unknown>) => String(item.whyItMatters || '') },
    { title: '应用场景', items: report.applications, get: (item: Record<string, unknown>) => String(item.scenario || '待验证'), sub: (item: Record<string, unknown>) => String(item.demandLogic || '') },
  ];
  return <div className="ir-report">
    <section className="ir-report-hero"><span>EXECUTIVE VIEW</span><h2>{report.oneSentence || '研究结论待验证'}</h2><p>{report.executiveSummary}</p></section>
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
  const [tab, setTab] = useState<WorkspaceTab>('map');
  const [loading, setLoading] = useState(true);
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => { document.title = '行业调研 - 乐子乌超级价值'; }, []);

  const loadProjects = useCallback(async () => {
    const next = await industryResearchApi.projects();
    setProjects(next.items);
    setActiveProject(current => current ? next.items.find(item => item.projectId === current.projectId) ?? current : next.items[0]);
  }, []);

  const loadBlueprint = useCallback(async (requestedTopic = topic, requestedDays = lookbackDays) => {
    const trimmed = requestedTopic.trim();
    if (trimmed.length < 2) { setError('请输入至少两个字的行业或公司名称'); return; }
    setLoading(true);
    try {
      const next = await industryResearchApi.blueprint(trimmed, requestedDays);
      setBlueprint(next); setError('');
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : '行业证据蓝图暂时无法读取，页面会保留当前内容');
    } finally { setLoading(false); }
  }, [lookbackDays, topic]);

  useEffect(() => { void Promise.allSettled([loadBlueprint('光模块', 730), loadProjects()]); }, []); // eslint-disable-line react-hooks/exhaustive-deps
  usePageActivationRefresh(loadProjects, { intervalMs: 20_000, minIntervalMs: 3_000, runOnMount: false });

  const pollingProjectId = activeProject?.projectId;
  const pollingProjectStatus = activeProject?.status;
  useEffect(() => {
    if (!pollingProjectId || !pollingProjectStatus || !['queued', 'collecting', 'analyzing'].includes(pollingProjectStatus)) return undefined;
    const timer = window.setInterval(async () => {
      try {
        const next = await industryResearchApi.project(pollingProjectId);
        setActiveProject(next);
        setProjects(current => current.map(item => item.projectId === next.projectId ? next : item));
        if (next.status === 'completed' && next.snapshot) {
          const completedSnapshot = next.snapshot;
          setBlueprint(current => current ? { ...current, topic: next.topic, queryTerms: completedSnapshot.queryTerms, snapshot: completedSnapshot } : current);
        }
      } catch { /* background polling remains silent and retries */ }
    }, 3_000);
    return () => window.clearInterval(timer);
  }, [pollingProjectId, pollingProjectStatus]);

  const startProject = async () => {
    if (!blueprint || starting) return;
    setStarting(true);
    try {
      const project = await industryResearchApi.createProject({
        topic: blueprint.topic, researchType: 'industry', lookbackDays,
        objective: `尽快搞明白${blueprint.topic}的产业脉络、发展趋势、龙头企业、最大痛点和应用场景`,
        queryTerms: blueprint.queryTerms,
      });
      setActiveProject(project); setProjects(current => [project, ...current.filter(item => item.projectId !== project.projectId)]); setTab('report'); setError('');
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : '课题创建失败');
    } finally { setStarting(false); }
  };

  const activeSnapshot = activeProject?.snapshot ?? blueprint?.snapshot;
  const activeReport = activeProject?.report;
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
          <p>立即生成行业或公司的首版研究底稿，后台持续补强产业链、趋势、龙头、痛点、应用、证据与反证。</p>
        </motion.div>
        <div className="ir-live"><i /><span>六类本地数据统一召回</span><strong>后台任务可离开页面</strong></div>
      </header>

      <section className="ir-command">
        <div className="ir-query"><Search /><label><span>我现在想搞明白</span><input value={topic} onChange={event => setTopic(event.target.value)} onKeyDown={event => { if (event.key === 'Enter') void loadBlueprint(); }} placeholder="输入行业或公司，例如：光模块" /></label></div>
        <select value={lookbackDays} onChange={event => setLookbackDays(Number(event.target.value))} aria-label="资料时间范围">
          <option value={365}>最近 1 年</option><option value={730}>最近 2 年</option><option value={1095}>最近 3 年</option><option value={1825}>最近 5 年</option>
        </select>
        <button type="button" className="ir-secondary" onClick={() => void loadBlueprint()} disabled={loading}>{loading ? <LoaderCircle className="is-spinning" /> : <FileSearch />}生成证据蓝图</button>
        <button type="button" className="ir-primary" onClick={() => void startProject()} disabled={!blueprint || starting}>{starting ? <LoaderCircle className="is-spinning" /> : <Play />}立即启动深度调研</button>
      </section>

      {error ? <div className="ir-inline-error" role="status"><CircleAlert />{error}</div> : null}
      {loading && !blueprint ? <div className="ir-loading"><LoaderCircle /><strong>正在跨研报、机构语料、公告、行情财务、企业事实与新闻建立证据蓝图…</strong><span>数据未准备好时只显示等待，不弹窗打断。</span></div> : null}

      {blueprint ? <>
        <section className="ir-mission-strip">
          {blueprint.methodology.stages.map((stage, index) => {
            const active = activeProject?.stage === stage.stage;
            const done = activeProject?.status === 'completed' || (activeProject && blueprint.methodology.stages.findIndex(item => item.stage === activeProject.stage) > index);
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
            {activeProject ? <><ProgressRing value={activeProject.progress} /><div><span>{statusLabel[activeProject.status]}</span><strong>{activeProject.topic}</strong><p>{activeProject.message}</p></div></> : <><Gauge /><div><span>证据蓝图就绪</span><strong>{blueprint.topic}</strong><p>启动课题后后台生成结论报告</p></div></>}
          </div>
        </section>

        <section className="ir-coverage">
          <div className="ir-section-heading"><div><span>EVIDENCE COVERAGE</span><h2>本次课题的数据覆盖</h2></div><small>{blueprint.methodology.evidenceRule}</small></div>
          <div>{activeSnapshot?.coverage.map(item => <article className={item.status === 'missing' ? 'is-missing' : ''} key={item.key}>
            <span>{item.status === 'covered' ? <CheckCircle2 /> : <CircleAlert />}</span><div><strong>{item.name}</strong><small>{item.evidenceLevel} · {item.mediaFiles ? `${item.mediaFiles} 个文件` : '可追溯来源'}</small></div><em>{compact(item.count)}</em>
          </article>)}</div>
        </section>

        <nav className="ir-tabs" aria-label="行业调研工作台栏目">{tabs.map(({ key, label, icon: Icon }) => <button type="button" className={tab === key ? 'is-active' : ''} onClick={() => setTab(key)} key={key}><Icon />{label}</button>)}</nav>

        {tab === 'map' ? <ResearchMap blueprint={blueprint} /> : null}
        {tab === 'companies' ? <CompanyMatrix blueprint={blueprint} /> : null}
        {tab === 'evidence' ? <EvidenceLibrary blueprint={blueprint} /> : null}
        {tab === 'questions' ? <QuestionsView blueprint={blueprint} report={activeReport} /> : null}
        {tab === 'report' ? <ReportView project={activeProject} /> : null}

        {projects.length ? <section className="ir-projects">
          <div className="ir-section-heading"><div><span>MY RESEARCH MISSIONS</span><h2>我的课题</h2></div><small>每个用户的课题、进度和报告独立保存</small></div>
          <div>{projects.slice(0, 8).map(project => <button type="button" className={project.projectId === activeProject?.projectId ? 'is-active' : ''} key={project.projectId} onClick={async () => { const detail = await industryResearchApi.project(project.projectId); setActiveProject(detail); if (detail.snapshot) setBlueprint(current => current ? { ...current, topic: detail.topic, snapshot: detail.snapshot!, queryTerms: detail.snapshot!.queryTerms } : current); setTab('report'); }}>
            <span className={`is-${project.status}`}><Clock3 /></span><div><strong>{project.topic}</strong><p>{project.message}</p><small>{timestamp(project.createdAt)}</small></div><em>{project.progress}%</em>
          </button>)}</div>
        </section> : null}
      </> : null}
    </main>
  </AppPage>;
}
