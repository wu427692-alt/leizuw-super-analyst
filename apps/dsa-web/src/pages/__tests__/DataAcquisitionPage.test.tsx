import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { MemoryRouter, useLocation, useNavigate } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { dataAcquisitionApi } from '../../api/dataAcquisition';
import { clearRequestCache } from '../../api/requestCache';
import { useUserAccess } from '../../contexts/UserAccessContext';
import type { AcquisitionDownloadProgress, AcquisitionJob, AcquisitionPlan, AcquisitionRunTask } from '../../types/dataAcquisition';
import DataAcquisitionPage from '../DataAcquisitionPage';

vi.mock('../../api/dataAcquisition', () => ({ dataAcquisitionApi: {
  capabilities: vi.fn(), tasks: vi.fn(), jobs: vi.fn(), task: vi.fn(), plan: vi.fn(), validatePlan: vi.fn(), runAsync: vi.fn(), download: vi.fn(),
} }));
vi.mock('../../contexts/UserAccessContext', () => ({ useUserAccess: vi.fn() }));
vi.mock('../../components/data-acquisition/ResearchReportLibrary', () => ({ default: () => <div>研报库独立区域</div> }));
const plan: AcquisitionPlan = {
  title: '公告取数计划', objective: '获取公告', model: 'test', generatedAt: '2026-09-04T00:00:00Z',
  outputFormats: ['zip'], caveats: [], scope: { symbols: ['603306.SH'], companyNames: ['华懋科技'], keywords: [], startDate: '', endDate: '', marketWide: false },
  tasks: [{ id: 'ann', label: '华懋科技公告', source: 'cninfo', resource: 'announcements', reason: '', params: {}, fields: [] }],
};
const task: AcquisitionRunTask = {
  taskId: 'acq-a', status: 'running', phase: 'fetching', progress: 45, message: '正在获取公告', completedTasks: 0, totalTasks: 1,
  tasks: plan.tasks, createdAt: '2026-09-04T00:00:00Z', updatedAt: '2026-09-04T00:00:00Z',
};
const job: AcquisitionJob = {
  jobId: 'job-a', title: '华懋科技公告包', request: '获取公告', status: 'partial', generatedAt: '2026-09-04T00:00:00Z', plan,
  datasets: [{ taskId: 'ann', source: 'cninfo', resource: 'announcements', label: '公告', status: 'success', rowCount: 12, files: [] },
    { taskId: 'news', source: 'tushare', resource: 'news', label: '新闻', status: 'failed', rowCount: 0, error: '上游暂时不可用', files: [] }],
  summary: { taskCount: 2, successCount: 1, failedCount: 1, rowCount: 12 }, downloadUrl: '/download', formats: ['zip'],
};
const auth = () => ({
  user: { id: 1, name: '用户 A', status: 'approved' }, loggedIn: true, isLoading: false, authMethod: 'password', accessEnabled: true,
  registrationMode: 'approval' as const, smsLoginEnabled: false, sendSms: vi.fn(), smsLogin: vi.fn(), bindPhone: vi.fn(),
  register: vi.fn(), login: vi.fn(), logout: vi.fn(), refresh: vi.fn(),
});
function Nav() {
  const navigate = useNavigate(), location = useLocation();
  return <><button onClick={() => navigate('/data-acquisition?task=acq-b')}>测试切换任务</button>
    <button onClick={() => navigate(-1)}>测试返回前一任务</button>
    <output aria-label="当前路径">{location.search}</output></>;
}
function fixture(path = '/data-acquisition?view=tasks') { return <MemoryRouter initialEntries={[path]}><DataAcquisitionPage /><Nav /></MemoryRouter>; }
async function flush() { await act(async () => { await Promise.resolve(); }); }
async function advance(ms: number) { await act(async () => { await vi.advanceTimersByTimeAsync(ms); }); }
const storageKey = 'dsa:data-acquisition:v2:user:1:active-task';

describe('一站式取数后台生命周期', () => {
  beforeEach(() => {
    vi.useFakeTimers(); vi.resetAllMocks(); localStorage.clear(); clearRequestCache();
    Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'visible' });
    vi.mocked(useUserAccess).mockReturnValue(auth());
    vi.mocked(dataAcquisitionApi.capabilities).mockResolvedValue({ sources: [], planner: { available: true, model: 'test', maxTasks: 12, maxRowsPerDataset: 100 } });
    vi.mocked(dataAcquisitionApi.jobs).mockResolvedValue({ items: [], total: 0 });
    vi.mocked(dataAcquisitionApi.tasks).mockResolvedValue({ items: [], total: 0 });
    vi.mocked(dataAcquisitionApi.task).mockResolvedValue(task);
    vi.mocked(dataAcquisitionApi.plan).mockResolvedValue(plan);
    vi.mocked(dataAcquisitionApi.runAsync).mockResolvedValue({ ...task, status: 'queued' });
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});
    URL.createObjectURL = vi.fn(() => 'blob:test'); URL.revokeObjectURL = vi.fn();
  });
  afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.useRealTimers(); });

  it('恢复当前账号的任务，不读旧全局记录；短暂失败保留状态并退避重试', async () => {
    localStorage.setItem(storageKey, task.taskId);
    localStorage.setItem('dsa:data-acquisition:active-task', 'another-user');
    vi.mocked(dataAcquisitionApi.task).mockResolvedValueOnce(task).mockRejectedValueOnce(new Error('timeout'))
      .mockResolvedValue({ ...task, status: 'completed', phase: 'completed', progress: 100, result: job });
    render(fixture()); await flush();
    expect(screen.getByRole('progressbar', { name: '跨渠道取数与打包进度' })).toHaveAttribute('aria-valuenow', '45');
    await advance(3_000);
    expect(screen.getByText(/任务状态暂时未更新/)).toBeVisible();
    expect(localStorage.getItem(storageKey)).toBe(task.taskId);
    await advance(4_000); expect(dataAcquisitionApi.task).toHaveBeenCalledTimes(2);
    await advance(1_000);
    expect(screen.getByRole('button', { name: '下载 ZIP 数据包' })).toBeEnabled();
    expect(localStorage.getItem(storageKey)).toBeNull();
    expect(dataAcquisitionApi.runAsync).not.toHaveBeenCalled();
    expect(screen.getByLabelText('数据包渠道结果')).toHaveTextContent('上游暂时不可用');
  });

  it('同一页面改变任务链接，旧请求迟到不能覆盖新任务', async () => {
    let finish!: (value: AcquisitionRunTask) => void;
    vi.mocked(dataAcquisitionApi.task).mockImplementation(id => id === 'acq-a'
      ? new Promise(resolve => { finish = resolve; })
      : Promise.resolve({ ...task, taskId: 'acq-b', message: '任务 B 正在导出' }));
    render(fixture('/data-acquisition?task=acq-a')); await flush();
    fireEvent.click(screen.getByText('测试切换任务')); await flush();
    expect(screen.getAllByText('任务 B 正在导出').length).toBeGreaterThan(0);
    await act(async () => { finish({ ...task, message: '迟到的 A' }); });
    expect(screen.queryByText('迟到的 A')).not.toBeInTheDocument();
  });

  it('从历史包预览切到其他任务再后退，必须恢复URL任务的结果而不是旧预览包', async () => {
    const previewJob = { ...job, jobId: 'job-preview', title: '单独查看的历史包' };
    const secondTaskJob = { ...job, jobId: 'job-b', title: '任务B结果包' };
    vi.mocked(dataAcquisitionApi.jobs).mockResolvedValue({ items: [job, previewJob], total: 2 });
    vi.mocked(dataAcquisitionApi.task).mockImplementation(id => Promise.resolve({
      ...task, taskId: id, status: 'completed', phase: 'completed', progress: 100,
      result: id === 'acq-a' ? job : secondTaskJob,
    }));
    render(fixture('/data-acquisition?task=acq-a')); await flush();
    // JSDOM has no layout/scroll API; keep this DOM-only stub scoped to the
    // mounted detail anchor, not the production scroll behavior.
    Object.defineProperty(document.getElementById('acquisition-task-detail'), 'scrollIntoView', { value: vi.fn() });
    fireEvent.click(screen.getAllByRole('button', { name: '查看渠道结果' })[1]); await flush();
    expect(screen.getByText('单独查看的历史包 · job-preview')).toBeVisible();
    fireEvent.click(screen.getByText('测试切换任务')); await flush();
    expect(screen.getByText('任务B结果包 · job-b')).toBeVisible();
    fireEvent.click(screen.getByText('测试返回前一任务')); await flush();
    expect(screen.getByLabelText('当前路径')).toHaveTextContent('task=acq-a');
    expect(screen.getByText(`${job.title} · job-a`)).toBeVisible();
    expect(screen.queryByText('单独查看的历史包 · job-preview')).not.toBeInTheDocument();
  });

  it.each(['running', 'unavailable'])('新建取数任务可以退出%s任务跟踪，不取消后台任务或重复提交', async mode => {
    localStorage.setItem(storageKey, task.taskId);
    if (mode === 'unavailable') vi.mocked(dataAcquisitionApi.task).mockRejectedValue(new Error('timeout'));
    render(fixture('/data-acquisition?task=acq-a')); await flush();
    expect(screen.getByRole('button', { name: '生成取数计划' })).toBeDisabled();
    fireEvent.click(screen.getByRole('button', { name: '新建取数任务' })); await flush();
    expect(screen.getByLabelText('当前路径')).not.toHaveTextContent('task=');
    expect(localStorage.getItem(storageKey)).toBeNull();
    expect(screen.queryByRole('progressbar', { name: '跨渠道取数与打包进度' })).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: '生成取数计划' })).toBeEnabled();
    await advance(30_000);
    expect(dataAcquisitionApi.task).toHaveBeenCalledTimes(1);
    expect(dataAcquisitionApi.runAsync).not.toHaveBeenCalled();
    fireEvent.change(screen.getByLabelText('取数需求'), { target: { value: '获取中际旭创新公告' } });
    fireEvent.click(screen.getByRole('button', { name: '生成取数计划' })); await flush();
    expect(dataAcquisitionApi.plan).toHaveBeenCalledWith('获取中际旭创新公告');
    expect(screen.getByRole('button', { name: '确认执行 1 个任务' })).toBeEnabled();
  });

  it.each([401, 403, 404])('不可访问任务 %s 停止轮询并清除恢复句柄', async status => {
    localStorage.setItem(storageKey, task.taskId);
    vi.mocked(dataAcquisitionApi.task).mockRejectedValue({ isAxiosError: true, response: { status } });
    render(fixture()); await flush();
    expect(screen.getByText(/该任务不存在、已过期/)).toBeVisible();
    expect(localStorage.getItem(storageKey)).toBeNull();
    await advance(20_000); expect(dataAcquisitionApi.task).toHaveBeenCalledTimes(1);
    expect(screen.getByRole('button', { name: '生成取数计划' })).toBeEnabled();
  });

  it('隐藏标签页暂停轮询，返回后立即恢复且不重叠', async () => {
    render(fixture('/data-acquisition?task=acq-a')); await flush();
    Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'hidden' });
    fireEvent(document, new Event('visibilitychange')); await advance(20_000);
    expect(dataAcquisitionApi.task).toHaveBeenCalledTimes(1);
    Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'visible' });
    fireEvent(document, new Event('visibilitychange')); fireEvent(window, new Event('online')); await flush();
    expect(dataAcquisitionApi.task).toHaveBeenCalledTimes(2);
  });

  it('响应任务编号不匹配不显示别的任务；可重试原编号', async () => {
    vi.mocked(dataAcquisitionApi.task).mockResolvedValueOnce({ ...task, taskId: 'acq-b', message: '错误任务内容' }).mockResolvedValue(task);
    render(fixture('/data-acquisition?task=acq-a')); await flush();
    expect(screen.queryByText('错误任务内容')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '重试所选任务' })); await flush();
    expect(screen.getByRole('progressbar', { name: '跨渠道取数与打包进度' })).toHaveAttribute('aria-valuenow', '45');
  });

  it('修改自然语言需求后禁止执行旧计划；确认后用规划时的准确原文提交', async () => {
    render(fixture()); await flush();
    fireEvent.change(screen.getByLabelText('取数需求'), { target: { value: '获取华懋科技公告' } });
    fireEvent.click(screen.getByRole('button', { name: '生成取数计划' })); await flush();
    fireEvent.change(screen.getByLabelText('取数需求'), { target: { value: '获取胜宏科技财务' } });
    expect(screen.getByRole('button', { name: '确认执行 1 个任务' })).toBeDisabled();
    fireEvent.change(screen.getByLabelText('取数需求'), { target: { value: '获取华懋科技公告' } });
    fireEvent.click(screen.getByRole('button', { name: '确认执行 1 个任务' }));
    fireEvent.click(screen.getByRole('button', { name: /后台任务执行中/ })); await flush();
    expect(dataAcquisitionApi.runAsync).toHaveBeenCalledTimes(1);
    expect(dataAcquisitionApi.runAsync).toHaveBeenCalledWith('获取华懋科技公告', plan);
    expect(screen.getByLabelText('当前路径')).toHaveTextContent('task=acq-a');
  });

  it('计划阶段展示已核验的标准公司名和代码后再执行', async () => {
    const readyPlan: AcquisitionPlan = { ...plan, preflight: {
      status: 'ready', canExecute: true,
      entities: [{ query: '华懋', status: 'resolved', name: '华懋科技', symbol: '603306.SH', source: '本地证券主表', candidates: [] }],
      checks: [{ taskId: 'ann', status: 'ready', message: '巨潮公告代码已准备完成' }], blockers: [],
    } };
    vi.mocked(dataAcquisitionApi.plan).mockResolvedValue(readyPlan);
    render(fixture()); await flush();
    fireEvent.click(screen.getByRole('button', { name: '生成取数计划' })); await flush();
    const identity = screen.getByRole('region', { name: '取数目标校验' });
    expect(identity).toHaveTextContent('华懋科技');
    expect(identity).toHaveTextContent('603306.SH');
    expect(identity).toHaveTextContent('已核验');
    expect(screen.getByText('巨潮公告代码已准备完成')).toBeVisible();
    expect(screen.getByRole('button', { name: '确认执行 1 个任务' })).toBeEnabled();
  });

  it('显示证券目录真实来源和后台同步状态，未成功同步不伪造日期', async () => {
    vi.mocked(dataAcquisitionApi.capabilities).mockResolvedValue({ sources: [], planner: { available: true, model: 'test', maxTasks: 12, maxRowsPerDataset: 100 },
      stockDirectory: { source: '证券目录种子', lastSuccessAt: null, refreshing: true, stale: true, entryCount: 5500 },
    });
    render(fixture()); await flush();
    const directory = screen.getByLabelText('股票目录同步状态');
    expect(directory).toHaveTextContent('证券目录种子');
    expect(directory).toHaveTextContent('最近成功同步：未核验');
    expect(directory).toHaveTextContent('后台自动更新中');
    expect(directory).toHaveTextContent('5,500 只证券');
    expect(within(directory).queryByRole('button')).not.toBeInTheDocument();
  });

  it('歧义或缺码时提前阻止执行，选择候选后只做身份复核再允许执行', async () => {
    const blockedPlan: AcquisitionPlan = { ...plan, scope: { ...plan.scope, symbols: [], companyNames: ['国仪'], entityQueries: ['国仪'] }, preflight: {
      status: 'needs_confirmation', canExecute: false,
      entities: [{ query: '国仪', status: 'ambiguous', candidates: [{ name: '国仪量子', symbol: '688828.SH', source: '本地证券主表' }] }],
      checks: [{ taskId: 'ann', status: 'blocked', message: '公告渠道等待准确股票代码' }], blockers: ['请确认国仪对应的公司与股票代码'],
    } };
    const readyPlan: AcquisitionPlan = { ...blockedPlan, scope: { ...blockedPlan.scope, symbols: ['688828.SH'], companyNames: ['国仪量子'], entitySelections: { 国仪: '688828.SH' } },
      preflight: { status: 'ready', canExecute: true, entities: [{ query: '国仪', status: 'resolved', name: '国仪量子', symbol: '688828.SH', candidates: [] }], checks: [], blockers: [] },
    };
    vi.mocked(dataAcquisitionApi.plan).mockResolvedValue(blockedPlan);
    vi.mocked(dataAcquisitionApi.validatePlan).mockResolvedValue(readyPlan);
    render(fixture()); await flush();
    fireEvent.click(screen.getByRole('button', { name: '生成取数计划' })); await flush();
    expect(screen.getByRole('button', { name: '确认执行 1 个任务' })).toBeDisabled();
    expect(screen.getByText('请确认国仪对应的公司与股票代码')).toBeVisible();
    fireEvent.change(screen.getByRole('combobox', { name: '确认候选：国仪' }), { target: { value: '688828.SH' } });
    expect(screen.getByRole('textbox', { name: '股票代码：国仪' })).toHaveValue('688828.SH');
    expect(screen.getByRole('button', { name: '确认执行 1 个任务' })).toBeDisabled();
    expect(dataAcquisitionApi.runAsync).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: '重新校验目标与代码' })); await flush();
    expect(dataAcquisitionApi.validatePlan).toHaveBeenCalledWith(expect.any(String), expect.objectContaining({
      scope: expect.objectContaining({ entitySelections: { 国仪: '688828.SH' } }),
    }));
    expect(dataAcquisitionApi.plan).toHaveBeenCalledTimes(1);
    expect(screen.getByRole('button', { name: '确认执行 1 个任务' })).toBeEnabled();
    fireEvent.click(screen.getByRole('button', { name: '确认执行 1 个任务' })); await flush();
    expect(dataAcquisitionApi.runAsync).toHaveBeenCalledWith(expect.any(String), readyPlan);
  });

  it('手填代码但校验失败仍保留原计划，不提交任务或伪装为通过', async () => {
    vi.mocked(dataAcquisitionApi.plan).mockResolvedValue({ ...plan, preflight: {
      status: 'needs_confirmation', canExecute: false,
      entities: [{ query: '国仪', status: 'unresolved', candidates: [] }], checks: [], blockers: ['未能识别股票代码'],
    } });
    vi.mocked(dataAcquisitionApi.validatePlan).mockRejectedValue(new Error('代码与公司名称不匹配'));
    render(fixture()); await flush();
    fireEvent.click(screen.getByRole('button', { name: '生成取数计划' })); await flush();
    fireEvent.change(screen.getByRole('textbox', { name: '股票代码：国仪' }), { target: { value: '123456' } });
    fireEvent.click(screen.getByRole('button', { name: '重新校验目标与代码' })); await flush();
    expect(screen.getByText('代码与公司名称不匹配')).toBeVisible();
    expect(screen.getByRole('textbox', { name: '股票代码：国仪' })).toHaveValue('123456');
    expect(screen.getByRole('button', { name: '确认执行 1 个任务' })).toBeDisabled();
    expect(dataAcquisitionApi.runAsync).not.toHaveBeenCalled();
  });

  it('首次列表失败不是零条；后台刷新不抹去操作错误，重试只请求本渠道', async () => {
    vi.mocked(dataAcquisitionApi.jobs).mockRejectedValueOnce(new Error('timeout')).mockResolvedValue({ items: [job], total: 1 });
    render(fixture()); await flush();
    expect(screen.getByText('数据包记录尚未读取成功')).toBeVisible();
    expect(screen.queryByText('还没有数据包')).not.toBeInTheDocument();
    const sourceCalls = vi.mocked(dataAcquisitionApi.capabilities).mock.calls.length;
    fireEvent.click(screen.getByRole('button', { name: '重试数据包记录' })); await flush();
    expect(screen.getByText(job.title, { exact: true })).toBeVisible();
    expect(dataAcquisitionApi.capabilities).toHaveBeenCalledTimes(sourceCalls);
  });

  it('下载中禁止重叠；失败停止进度，可重试准确数据包', async () => {
    vi.mocked(dataAcquisitionApi.jobs).mockResolvedValue({ items: [job, { ...job, jobId: 'job-b', title: '第二个包' }], total: 2 });
    let fail!: (error: Error) => void;
    vi.mocked(dataAcquisitionApi.download).mockImplementationOnce((_id, progress) => {
      progress?.({ loaded: 128, total: 256, percent: 50 });
      return new Promise((_resolve, reject) => { fail = reject; });
    }).mockResolvedValue(new Blob(['zip']));
    render(fixture()); await flush();
    fireEvent.click(screen.getAllByRole('button', { name: '下载' })[0]); await flush();
    expect(screen.getByRole('button', { name: '下载' })).toBeDisabled();
    await act(async () => { fail(new Error('连接中断')); });
    expect(screen.getByText(/下载中断，可重新下载/)).toBeVisible();
    fireEvent.click(screen.getAllByRole('button', { name: '下载' })[0]); await flush();
    expect(screen.getByText('下载完成')).toBeVisible();
    expect(dataAcquisitionApi.download).toHaveBeenNthCalledWith(2, 'job-a', expect.any(Function));
  });

  it('浏览器下载只构造同源认证地址，不跟随数据包注入的外部地址或伪造完成', async () => {
    const opaqueId = 'job/a?redirect=https://outside.example/file';
    vi.mocked(dataAcquisitionApi.jobs).mockResolvedValue({ items: [{ ...job, jobId: opaqueId, downloadUrl: 'https://outside.example/tracker.zip' }], total: 1 });
    let handedOff: { href: string; filename: string } | undefined;
    vi.mocked(HTMLAnchorElement.prototype.click).mockImplementation(function (this: HTMLAnchorElement) {
      handedOff = { href: this.href, filename: this.download };
    });
    render(fixture()); await flush();
    fireEvent.click(screen.getByRole('button', { name: '浏览器下载' })); await flush();
    expect(handedOff).toEqual({ href: `${window.location.origin}/api/v1/data-acquisition/jobs/${encodeURIComponent(opaqueId)}/download`, filename: `财经数据包_${opaqueId}.zip` });
    expect(new URL(handedOff!.href).origin).toBe(window.location.origin);
    expect(dataAcquisitionApi.download).not.toHaveBeenCalled();
    expect(URL.createObjectURL).not.toHaveBeenCalled();
    expect(screen.getByText('已交给浏览器，请在下载列表查看进度')).toBeVisible();
    expect(screen.queryByText('下载完成')).not.toBeInTheDocument();
    expect(screen.queryByRole('progressbar', { name: '数据包下载进度' })).not.toBeInTheDocument();
    expect(document.querySelector('a[download]')).toBeNull();
  });

  it('正常离开SPA页面后，同一登录会话已经接收完整的数据包仍保存一次', async () => {
    vi.mocked(dataAcquisitionApi.jobs).mockResolvedValue({ items: [job], total: 1 });
    let finish!: (blob: Blob) => void;
    vi.mocked(dataAcquisitionApi.download).mockImplementation(() => new Promise(resolve => { finish = resolve; }));
    const page = render(fixture()); await flush();
    fireEvent.click(screen.getByRole('button', { name: '下载' })); await flush();
    page.unmount();
    const blob = new Blob(['complete-authorized-archive']);
    await act(async () => { finish(blob); });
    expect(URL.createObjectURL).toHaveBeenCalledExactlyOnceWith(blob);
    expect(HTMLAnchorElement.prototype.click).toHaveBeenCalledTimes(1);
    expect(document.querySelector('a[download]')).toBeNull();
  });

  it('浏览器移交未成功时不显示已移交或完成，错误留在当前页', async () => {
    vi.mocked(dataAcquisitionApi.jobs).mockResolvedValue({ items: [job], total: 1 });
    vi.mocked(HTMLAnchorElement.prototype.click).mockImplementation(() => { throw new Error('blocked'); });
    render(fixture()); await flush();
    fireEvent.click(screen.getByRole('button', { name: '浏览器下载' })); await flush();
    expect(screen.getByText('浏览器暂未接收下载，请使用旁边的下载按钮重试。')).toBeVisible();
    expect(screen.queryByText('已交给浏览器，请在下载列表查看进度')).not.toBeInTheDocument();
    expect(screen.queryByText('下载完成')).not.toBeInTheDocument();
    expect(document.querySelector('a[download]')).toBeNull();
  });

  it.each(['logout', 'account-switch'])('%s清除私有会话后，不保存旧账号迟到的数据包', async mode => {
    vi.mocked(dataAcquisitionApi.jobs).mockResolvedValueOnce({ items: [job], total: 1 }).mockResolvedValue({ items: [], total: 0 });
    let finish!: (blob: Blob) => void;
    vi.mocked(dataAcquisitionApi.download).mockImplementation(() => new Promise(resolve => { finish = resolve; }));
    const page = render(fixture()); await flush();
    fireEvent.click(screen.getByRole('button', { name: '下载' })); await flush();
    clearRequestCache();
    vi.mocked(useUserAccess).mockReturnValue(mode === 'logout'
      ? { ...auth(), loggedIn: false, user: null }
      : { ...auth(), user: { id: 2, name: '用户 B', status: 'approved' } });
    page.rerender(fixture()); await flush();
    await act(async () => { finish(new Blob(['private-old-account'])); });
    expect(URL.createObjectURL).not.toHaveBeenCalled();
    expect(HTMLAnchorElement.prototype.click).not.toHaveBeenCalled();
    expect(screen.queryByText('下载完成')).not.toBeInTheDocument();
  });

  it('下载速度与剩余时间按真实已接收字节和经过时间计算，并节制界面更新频率', async () => {
    vi.mocked(dataAcquisitionApi.jobs).mockResolvedValue({ items: [job], total: 1 });
    const clock = vi.spyOn(performance, 'now').mockReturnValue(0);
    let notify!: (value: AcquisitionDownloadProgress) => void;
    vi.mocked(dataAcquisitionApi.download).mockImplementation((_id, progress) => {
      notify = progress!; return new Promise(() => {});
    });
    render(fixture()); await flush();
    fireEvent.click(screen.getByRole('button', { name: '下载' })); await flush();
    clock.mockReturnValue(2000);
    act(() => notify({ loaded: 1024, total: 4096, percent: 25 }));
    expect(screen.getByLabelText('下载传输情况')).toHaveTextContent('速度 512 B/秒');
    expect(screen.getByLabelText('下载传输情况')).toHaveTextContent('预计剩余 6秒');
    clock.mockReturnValue(2100);
    act(() => notify({ loaded: 2048, total: 4096, percent: 50 }));
    expect(screen.getByRole('progressbar', { name: '数据包下载进度' })).toHaveAttribute('aria-valuenow', '25');
    clock.mockReturnValue(2300);
    act(() => notify({ loaded: 3072, total: 4096, percent: 75 }));
    expect(screen.getByRole('progressbar', { name: '数据包下载进度' })).toHaveAttribute('aria-valuenow', '75');
  });

  it('优先使用下载器提供的有效速度与ETA，未知总量或零速度不出现NaN/Infinity', async () => {
    vi.mocked(dataAcquisitionApi.jobs).mockResolvedValue({ items: [job], total: 1 });
    const clock = vi.spyOn(performance, 'now').mockReturnValue(0);
    let notify!: (value: AcquisitionDownloadProgress & { bytesPerSecond?: number; estimatedSecondsRemaining?: number }) => void;
    vi.mocked(dataAcquisitionApi.download).mockImplementation((_id, progress) => {
      notify = progress!; return new Promise(() => {});
    });
    render(fixture()); await flush();
    fireEvent.click(screen.getByRole('button', { name: '下载' })); await flush();
    act(() => notify({ loaded: 0, bytesPerSecond: 0, estimatedSecondsRemaining: Infinity }));
    expect(screen.getByLabelText('下载传输情况')).not.toHaveTextContent(/剩余|NaN|Infinity/);
    clock.mockReturnValue(1000);
    act(() => notify({ loaded: 1024, total: 4096, percent: 25, bytesPerSecond: 2048, estimatedSecondsRemaining: 4 }));
    expect(screen.getByLabelText('下载传输情况')).toHaveTextContent('速度 2.0 KB/秒');
    expect(screen.getByLabelText('下载传输情况')).toHaveTextContent('预计剩余 4秒');
    clock.mockReturnValue(2000);
    act(() => notify({ loaded: 1024, total: 4096, percent: 25, bytesPerSecond: 0, estimatedSecondsRemaining: NaN }));
    expect(screen.getByLabelText('下载传输情况')).toHaveTextContent('速度 0 B/秒');
    expect(screen.getByLabelText('下载传输情况')).not.toHaveTextContent(/剩余|NaN|Infinity/);
  });

  it('字节收到但Promise尚未完成时最多99%，浏览器移交不会沿用旧的完成进度', async () => {
    vi.mocked(dataAcquisitionApi.jobs).mockResolvedValue({ items: [job], total: 1 });
    let finish!: (value: Blob) => void;
    vi.mocked(dataAcquisitionApi.download).mockImplementation((_id, progress) => {
      progress?.({ loaded: 7, total: 7, percent: 100 });
      return new Promise(resolve => { finish = resolve; });
    });
    render(fixture()); await flush();
    fireEvent.click(screen.getByRole('button', { name: '下载' })); await flush();
    expect(screen.getByRole('progressbar', { name: '数据包下载进度' })).toHaveAttribute('aria-valuenow', '99');
    expect(screen.getByText('正在校验完整文件')).toBeVisible();
    expect(screen.getByRole('button', { name: '浏览器下载' })).toBeDisabled();
    await act(async () => { finish(new Blob(['package'])); });
    expect(screen.getByText('下载完成')).toBeVisible();
    fireEvent.click(screen.getByRole('button', { name: '浏览器下载' })); await flush();
    expect(screen.getByText('已交给浏览器，请在下载列表查看进度')).toBeVisible();
    expect(screen.queryByText('下载完成')).not.toBeInTheDocument();
    expect(screen.queryByRole('progressbar', { name: '数据包下载进度' })).not.toBeInTheDocument();
  });

  it('传输时暂停页面重复列表刷新，结束后恢复；真实后台任务仍继续轮询', async () => {
    vi.mocked(dataAcquisitionApi.jobs).mockResolvedValue({ items: [job], total: 1 });
    let finish!: (value: Blob) => void;
    vi.mocked(dataAcquisitionApi.download).mockImplementation(() => new Promise(resolve => { finish = resolve; }));
    render(fixture('/data-acquisition?task=acq-a')); await flush();
    fireEvent.click(screen.getByRole('button', { name: '下载' })); await flush();
    const jobsCalls = vi.mocked(dataAcquisitionApi.jobs).mock.calls.length;
    const sourceCalls = vi.mocked(dataAcquisitionApi.capabilities).mock.calls.length;
    await advance(35_000);
    fireEvent(window, new Event('focus')); await flush();
    expect(dataAcquisitionApi.jobs).toHaveBeenCalledTimes(jobsCalls);
    expect(dataAcquisitionApi.capabilities).toHaveBeenCalledTimes(sourceCalls);
    expect(vi.mocked(dataAcquisitionApi.task).mock.calls.length).toBeGreaterThan(1);
    await act(async () => { finish(new Blob(['package'])); });
    expect(dataAcquisitionApi.jobs).toHaveBeenCalledTimes(jobsCalls + 1);
    expect(dataAcquisitionApi.capabilities).toHaveBeenCalledTimes(sourceCalls + 1);
    expect(dataAcquisitionApi.runAsync).not.toHaveBeenCalled();
  });

  it('账号切换立即隐藏旧包/任务，不继承恢复句柄', async () => {
    localStorage.setItem(storageKey, task.taskId);
    vi.mocked(dataAcquisitionApi.jobs).mockResolvedValueOnce({ items: [job], total: 1 }).mockResolvedValue({ items: [], total: 0 });
    const page = render(fixture()); await flush();
    vi.mocked(useUserAccess).mockReturnValue({ ...auth(), user: { id: 2, name: '用户 B', status: 'approved' } });
    page.rerender(fixture()); await flush();
    expect(screen.queryByText(job.title, { exact: true })).not.toBeInTheDocument();
    expect(screen.queryByRole('progressbar', { name: '跨渠道取数与打包进度' })).not.toBeInTheDocument();
    expect(dataAcquisitionApi.task).toHaveBeenCalledTimes(1);
  });

  it('已处理但还没有数据集结果的渠道不能提前显示绿色成功', async () => {
    vi.mocked(dataAcquisitionApi.task).mockResolvedValue({ ...task, completedTasks: 1 });
    render(fixture('/data-acquisition?task=acq-a')); await flush();
    expect(screen.getByLabelText('渠道任务处理状态').querySelector('.text-success')).toBeNull();
  });

  it.each(['success', 'failed'] as const)('兼容旧%s空结果，不把空包或已处理渠道画成成功', async status => {
    const emptyJob: AcquisitionJob = { ...job, status, datasets: [{ ...job.datasets[0], rowCount: 0, status, outcome: 'no_data', error: '现场接口按范围未取到数据' }],
      summary: { taskCount: 1, successCount: status === 'success' ? 1 : 0, failedCount: status === 'failed' ? 1 : 0, rowCount: 0 } };
    vi.mocked(dataAcquisitionApi.task).mockResolvedValue({ ...task, status: 'completed', phase: 'completed', completedTasks: 1, progress: 100, result: emptyJob });
    render(fixture('/data-acquisition?task=acq-a')); await flush();
    expect(screen.getByText('未取得符合条件的数据')).toBeVisible();
    expect(screen.queryByText('数据包已生成', { exact: true })).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: '下载查询结果与诊断' })).toBeEnabled();
    expect(screen.getByLabelText('渠道任务处理状态').querySelector('.text-success')).toBeNull();
    expect(screen.getByLabelText('数据包渠道结果')).toHaveTextContent('未取得数据');
    expect(screen.getByLabelText('数据包渠道结果')).toHaveTextContent('现场接口按范围未取到数据');
  });

  it('部分完成的包说明未完成并保留渠道与附件失败证据', async () => {
    const partialJob: AcquisitionJob = { ...job, datasets: [{ ...job.datasets[0], attachmentError: '1 个原始文件下载失败' }, job.datasets[1]] };
    vi.mocked(dataAcquisitionApi.task).mockResolvedValue({ ...task, status: 'completed', phase: 'completed', completedTasks: 2, progress: 100, result: partialJob });
    render(fixture('/data-acquisition?task=acq-a')); await flush();
    expect(screen.getByText('取数尚未完整完成')).toBeVisible();
    expect(screen.getByLabelText('数据包渠道结果')).toHaveTextContent('1 个原始文件下载失败');
    expect(screen.getByLabelText('渠道任务处理状态').querySelector('.text-success')).not.toBeNull();
  });

  it('存储禁用时不崩溃；任务仍可用URL打开，账号未获批不查询私有数据', async () => {
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => { throw new Error('blocked'); });
    const page = render(fixture('/data-acquisition?task=acq-a')); await flush();
    expect(within(screen.getByRole('region', { name: '后台取数任务' })).getAllByText(/正在获取公告/)[0]).toBeVisible();
    vi.mocked(useUserAccess).mockReturnValue({ ...auth(), user: { id: 1, name: '用户 A', status: 'pending' } });
    page.rerender(fixture()); await advance(30_000);
    expect(screen.getByText('请登录已获准的账号查看个人取数任务。')).toBeVisible();
    expect(dataAcquisitionApi.task).toHaveBeenCalledTimes(1);
  });
});
