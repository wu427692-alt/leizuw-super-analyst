import { useCallback, useMemo, useState } from 'react';
import { Activity, AudioLines, Boxes, CheckCircle2, Clock3, DatabaseZap, FlaskConical, RefreshCw, Search, XCircle } from 'lucide-react';
import { useNavigate } from 'react-router-dom';
import { dataAcquisitionApi } from '../api/dataAcquisition';
import { essayQuantApi } from '../api/essayQuant';
import { essayRadarApi } from '../api/essayRadar';
import { industryResearchApi } from '../api/industryResearch';
import { AppPage } from '../components/common';
import { usePageActivationRefresh } from '../hooks/usePageActivationRefresh';
import './TasksHubPage.css';

type TaskState = 'queued' | 'running' | 'completed' | 'limited' | 'failed';
type TaskKind = 'research' | 'quant' | 'audio-analysis' | 'audio-package' | 'acquisition';
type UnifiedTask = {
  id: string;
  kind: TaskKind;
  title: string;
  detail: string;
  status: TaskState;
  progress: number;
  updatedAt: string;
  href: string;
};

const KIND_META: Record<TaskKind, { label: string; icon: typeof Search }> = {
  research: { label: '行业与公司调研', icon: Search },
  quant: { label: '量化研究', icon: FlaskConical },
  'audio-analysis': { label: '录音转写与纪要', icon: AudioLines },
  'audio-package': { label: '录音文件打包', icon: Boxes },
  acquisition: { label: '一站式取数', icon: DatabaseZap },
};

const ACTIVE_STATES = new Set<TaskState>(['queued', 'running']);

function safeDate(value: string) {
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? value || '时间未记录' : parsed.toLocaleString('zh-CN', { hour12: false });
}

function statusLabel(status: TaskState) {
  return ({ queued: '排队中', running: '执行中', completed: '已完成', limited: '部分完成', failed: '失败' })[status];
}

const TasksHubPage = () => {
  const navigate = useNavigate();
  const [items, setItems] = useState<UnifiedTask[]>([]);
  const [loading, setLoading] = useState(true);
  const [failures, setFailures] = useState<string[]>([]);
  const [filter, setFilter] = useState<'all' | 'active' | 'completed' | 'failed'>('all');

  const load = useCallback(async () => {
    const results = await Promise.allSettled([
      industryResearchApi.projects(),
      essayQuantApi.tasks(),
      essayRadarApi.audioAnalysisTasks(),
      essayRadarApi.audioBatchTasks(),
      dataAcquisitionApi.tasks(),
    ]);
    const next: UnifiedTask[] = [];
    const failedSources: string[] = [];

    if (results[0].status === 'fulfilled') {
      results[0].value.items.forEach((task) => next.push({
        id: task.projectId, kind: 'research', title: task.topic,
        detail: task.message || task.stage,
        status: task.status === 'collecting' || task.status === 'analyzing' ? 'running' : task.status,
        progress: task.progress,
        updatedAt: task.updatedAt, href: `/industry-research?project=${encodeURIComponent(task.projectId)}`,
      }));
    } else failedSources.push('调研任务');
    if (results[1].status === 'fulfilled') {
      results[1].value.items.forEach((task) => next.push({
        id: task.taskId, kind: 'quant', title: task.name || '量化研究任务',
        detail: task.message || task.strategyType, status: task.status, progress: task.progress,
        updatedAt: task.completedAt || task.startedAt || task.createdAt || '', href: '/essay-quant?section=tasks',
      }));
    } else failedSources.push('量化任务');
    if (results[2].status === 'fulfilled') {
      results[2].value.items.forEach((task) => next.push({
        id: task.taskId, kind: 'audio-analysis', title: task.title || `录音纪要 · ${task.totalFiles} 个文件`,
        detail: task.message, status: task.status, progress: task.progress,
        updatedAt: task.updatedAt, href: `/essay-radar/feed?tab=audio&task=${encodeURIComponent(task.taskId)}`,
      }));
    } else failedSources.push('录音纪要任务');
    if (results[3].status === 'fulfilled') {
      results[3].value.items.forEach((task) => next.push({
        id: task.taskId, kind: 'audio-package', title: `录音打包 · ${task.totalFiles} 个文件`,
        detail: task.message, status: task.status, progress: task.progress,
        updatedAt: task.updatedAt, href: `/essay-radar/feed?tab=audio&package=${encodeURIComponent(task.taskId)}`,
      }));
    } else failedSources.push('录音打包任务');
    if (results[4].status === 'fulfilled') {
      results[4].value.items.forEach((task) => next.push({
        id: task.taskId, kind: 'acquisition', title: task.result?.title || task.tasks?.map((item) => item.label).slice(0, 2).join('、') || '数据获取任务',
        detail: task.message, status: task.status, progress: task.progress,
        updatedAt: task.updatedAt, href: `/data-acquisition?task=${encodeURIComponent(task.taskId)}`,
      }));
    } else failedSources.push('取数任务');

    next.sort((a, b) => Date.parse(b.updatedAt || '0') - Date.parse(a.updatedAt || '0'));
    setItems(next);
    setFailures(failedSources);
    setLoading(false);
  }, []);

  usePageActivationRefresh(load, { intervalMs: 10_000, minIntervalMs: 2_000 });

  const counts = useMemo(() => ({
    active: items.filter((item) => ACTIVE_STATES.has(item.status)).length,
    completed: items.filter((item) => item.status === 'completed' || item.status === 'limited').length,
    failed: items.filter((item) => item.status === 'failed').length,
  }), [items]);
  const visible = useMemo(() => items.filter((item) => (
    filter === 'all' || (filter === 'active' && ACTIVE_STATES.has(item.status))
    || (filter === 'completed' && (item.status === 'completed' || item.status === 'limited'))
    || (filter === 'failed' && item.status === 'failed')
  )), [filter, items]);

  return <AppPage className="tasks-hub max-w-none">
    <header className="tasks-hub__header">
      <div><p className="tasks-hub__kicker">WORK LEDGER</p><h1>任务与验证</h1><p>所有后台研究、转写、回测和取数任务集中在这里。离开原页面不影响执行。</p></div>
      <button type="button" className="tasks-hub__refresh" onClick={() => void load()}><RefreshCw aria-hidden="true" />刷新状态</button>
    </header>

    <section className="tasks-hub__summary" aria-label="任务摘要">
      <button onClick={() => setFilter('active')} className={filter === 'active' ? 'is-active' : ''}><Activity /><span>正在处理</span><strong>{counts.active}</strong></button>
      <button onClick={() => setFilter('completed')} className={filter === 'completed' ? 'is-active' : ''}><CheckCircle2 /><span>已完成</span><strong>{counts.completed}</strong></button>
      <button onClick={() => setFilter('failed')} className={filter === 'failed' ? 'is-active' : ''}><XCircle /><span>需要处理</span><strong>{counts.failed}</strong></button>
      <button onClick={() => setFilter('all')} className={filter === 'all' ? 'is-active' : ''}><Clock3 /><span>全部任务</span><strong>{items.length}</strong></button>
    </section>

    <nav className="tasks-hub__launch" aria-label="新建任务">
      <button onClick={() => navigate('/industry-research')}>新建调研</button>
      <button onClick={() => navigate('/essay-quant')}>新建量化研究</button>
      <button onClick={() => navigate('/essay-radar/feed?tab=audio')}>转写录音</button>
      <button onClick={() => navigate('/data-acquisition')}>获取数据</button>
    </nav>

    {failures.length ? <p className="tasks-hub__notice" role="status">{failures.join('、')}暂时没有返回；其他任务已正常显示，页面会自动重试。</p> : null}

    <section className="tasks-hub__list" aria-busy={loading}>
      {visible.map((task) => {
        const meta = KIND_META[task.kind]; const Icon = meta.icon;
        return <button key={`${task.kind}-${task.id}`} className="tasks-hub__task" onClick={() => navigate(task.href)}>
          <span className="tasks-hub__task-icon"><Icon aria-hidden="true" /></span>
          <span className="tasks-hub__task-main"><span className="tasks-hub__task-meta">{meta.label} · {safeDate(task.updatedAt)}</span><strong>{task.title}</strong><span>{task.detail}</span></span>
          <span className={`tasks-hub__status is-${task.status}`}>{statusLabel(task.status)}</span>
          <span className="tasks-hub__progress" aria-label={`进度 ${task.progress}%`}><i style={{ width: `${Math.max(0, Math.min(task.progress, 100))}%` }} /><em>{task.progress}%</em></span>
        </button>;
      })}
      {!loading && !visible.length ? <div className="tasks-hub__empty"><Boxes /><strong>当前筛选下没有任务</strong><span>可以从上方入口发起调研、量化、转写或取数任务。</span></div> : null}
      {loading && !items.length ? <div className="tasks-hub__empty"><RefreshCw className="animate-spin" /><strong>正在汇总任务</strong><span>五类后台任务并行读取，完成后一次展示。</span></div> : null}
    </section>
  </AppPage>;
};

export default TasksHubPage;
