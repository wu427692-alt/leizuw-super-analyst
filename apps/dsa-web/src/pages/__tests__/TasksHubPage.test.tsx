import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { MemoryRouter, useLocation } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { dataAcquisitionApi } from '../../api/dataAcquisition';
import { essayQuantApi } from '../../api/essayQuant';
import { essayRadarApi } from '../../api/essayRadar';
import { industryResearchApi } from '../../api/industryResearch';
import { useUserAccess } from '../../contexts/UserAccessContext';
import type { EssayQuantTask } from '../../types/essayQuant';
import type { IndustryResearchProject } from '../../types/industryResearch';
import TasksHubPage from '../TasksHubPage';

vi.mock('../../api/dataAcquisition', () => ({ dataAcquisitionApi: { tasks: vi.fn() } }));
vi.mock('../../api/essayQuant', () => ({ essayQuantApi: { tasks: vi.fn() } }));
vi.mock('../../api/essayRadar', () => ({ essayRadarApi: { audioAnalysisTasks: vi.fn(), audioBatchTasks: vi.fn() } }));
vi.mock('../../api/industryResearch', () => ({ industryResearchApi: { projects: vi.fn() } }));
vi.mock('../../contexts/UserAccessContext', () => ({ useUserAccess: vi.fn() }));

const running: EssayQuantTask = {
  taskId: 'quant/旧任务', name: '账号 A 的量化任务', status: 'running', progress: 45,
  message: '量化计算中', strategyType: 'essay_event', createdAt: '2026-09-04T01:00:00Z',
};
const auth = () => ({
  user: { id: 1, name: '用户 A', status: 'approved' }, loggedIn: true, isLoading: false,
  authMethod: 'password', accessEnabled: true,
  registrationMode: 'approval' as const, smsLoginEnabled: false, sendSms: vi.fn(), smsLogin: vi.fn(), bindPhone: vi.fn(),
  register: vi.fn(), login: vi.fn(), logout: vi.fn(), refresh: vi.fn(),
});
function LocationProbe() {
  const location = useLocation();
  return <output aria-label="当前路由">{location.pathname}{location.search}</output>;
}
function fixture() { return <MemoryRouter initialEntries={['/tasks']}><TasksHubPage /><LocationProbe /></MemoryRouter>; }
async function flush() { await act(async () => { await Promise.resolve(); }); }
async function advance(ms: number) { await act(async () => { await vi.advanceTimersByTimeAsync(ms); }); }
function expandFailures() { fireEvent.click(screen.getByText('异常详情')); }

describe('TasksHubPage 每渠道快照与个人任务隔离', () => {
  beforeEach(() => {
    vi.useFakeTimers(); vi.resetAllMocks();
    Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'visible' });
    vi.mocked(useUserAccess).mockReturnValue(auth());
    vi.mocked(industryResearchApi.projects).mockResolvedValue({ items: [], total: 0 });
    vi.mocked(essayQuantApi.tasks).mockResolvedValue({ items: [running], total: 1 });
    vi.mocked(essayRadarApi.audioAnalysisTasks).mockResolvedValue({ items: [], total: 0 });
    vi.mocked(essayRadarApi.audioBatchTasks).mockResolvedValue({ items: [], total: 0 });
    vi.mocked(dataAcquisitionApi.tasks).mockResolvedValue({ items: [], total: 0 });
  });
  afterEach(() => { cleanup(); vi.useRealTimers(); });

  it('updates the browser title when returning from a stock page', async () => {
    document.title = '自选股超级看板 - 乐子乌超级价值';
    render(fixture()); await flush();
    expect(document.title).toBe('任务与验证 - 乐子乌超级价值');
  });

  it('单个渠道轮询失败保留其进度；只重试失败渠道后显示恢复结果', async () => {
    vi.mocked(essayQuantApi.tasks).mockResolvedValueOnce({ items: [running], total: 1 })
      .mockRejectedValueOnce(new Error('timeout'))
      .mockResolvedValue({ items: [{ ...running, status: 'completed', progress: 100, resultRunId: 55 }], total: 1 });
    render(fixture()); await flush();
    await advance(10_000);
    const row = screen.getByRole('button', { name: /账号 A 的量化任务/ });
    expect(within(row).getByLabelText('进度 45%')).toBeInTheDocument();
    expect(within(row).getByText(/上次读取状态/)).toBeInTheDocument();
    expandFailures();
    expect(screen.getByText(/量化研究暂时未更新，保留最近成功读取/)).toBeInTheDocument();
    const otherCalls = vi.mocked(industryResearchApi.projects).mock.calls.length;
    fireEvent.click(screen.getByRole('button', { name: '重试量化研究' })); await flush();
    expect(screen.getByLabelText('进度 100%')).toBeInTheDocument();
    expect(screen.queryByText(/上次读取状态/)).not.toBeInTheDocument();
    expect(industryResearchApi.projects).toHaveBeenCalledTimes(otherCalls);
  });

  it('首次失败与真实空列表区分；下一次自动轮询可恢复', async () => {
    vi.mocked(essayQuantApi.tasks).mockRejectedValueOnce(new Error('Network Error'))
      .mockResolvedValue({ items: [running], total: 1 });
    render(fixture()); await flush();
    expect(screen.getByText('部分渠道尚未读取成功')).toBeInTheDocument();
    expect(screen.queryByText('当前筛选下没有任务')).not.toBeInTheDocument();
    await advance(20_000);
    expect(essayQuantApi.tasks).toHaveBeenCalledTimes(1);
    await advance(10_000);
    expect(screen.getByText(running.name)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '重试量化研究' })).not.toBeInTheDocument();
  });

  it('慢渠道不阻挡其他任务，手动刷新不重叠同一渠道请求', async () => {
    let finish!: (value: Awaited<ReturnType<typeof industryResearchApi.projects>>) => void;
    vi.mocked(industryResearchApi.projects).mockImplementation(() => new Promise(resolve => { finish = resolve; }));
    render(fixture()); await flush();
    expect(screen.getByText(running.name)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '刷新状态' })); await flush();
    expect(industryResearchApi.projects).toHaveBeenCalledTimes(1);
    await act(async () => { finish({ items: [], total: 0 }); });
    expect(screen.getByText(running.name)).toBeInTheDocument();
  });

  it('成功返回空列表会替换旧快照，不永久保留不存在的任务', async () => {
    render(fixture()); await flush();
    vi.mocked(essayQuantApi.tasks).mockResolvedValue({ items: [], total: 0 });
    await advance(10_000);
    expect(screen.queryByText(running.name)).not.toBeInTheDocument();
    expect(screen.getByText('当前筛选下没有任务')).toBeInTheDocument();
  });

  it.each([401, 403])('权限错误 %s 不保留该渠道的私有任务', async status => {
    render(fixture()); await flush();
    vi.mocked(essayQuantApi.tasks).mockRejectedValue({ isAxiosError: true, response: { status } });
    await advance(10_000);
    expect(screen.queryByText(running.name)).not.toBeInTheDocument();
    expandFailures();
    expect(screen.getByText(/访问权限已失效，已隐藏该类任务/)).toBeVisible();
    await advance(120_000);
    expect(essayQuantApi.tasks).toHaveBeenCalledTimes(2);
  });

  it.each([400, 404, 422])('接口不兼容 %s 保留旧进度但停止轮询，手动重试可恢复', async status => {
    vi.mocked(essayQuantApi.tasks).mockResolvedValueOnce({ items: [running], total: 1 })
      .mockRejectedValueOnce({ isAxiosError: true, response: { status } })
      .mockResolvedValue({ items: [{ ...running, status: 'completed', progress: 100, resultRunId: 55 }], total: 1 });
    render(fixture()); await flush(); await advance(10_000);
    expect(within(screen.getByRole('main')).getByRole('status')).toHaveTextContent('1 类接口版本不兼容');
    expect(screen.getByText(running.name)).toBeVisible();
    expect(screen.getByLabelText('进度 45%')).toBeVisible();
    expect(screen.getByText(/上次读取状态/)).toBeVisible();
    expect(screen.queryByText('当前筛选下没有任务')).not.toBeInTheDocument();
    await advance(120_000);
    expect(essayQuantApi.tasks).toHaveBeenCalledTimes(2);
    expandFailures();
    expect(screen.getByText(/量化研究接口版本不兼容，已停止自动重试/)).toBeVisible();
    fireEvent.click(screen.getByRole('button', { name: '重试量化研究' })); await flush();
    expect(within(screen.getByRole('main')).queryByRole('status')).not.toBeInTheDocument();
    expect(screen.getByLabelText('进度 100%')).toBeVisible();
    expect(essayQuantApi.tasks).toHaveBeenCalledTimes(3);
  });

  it('四渠道失败合并为一条折叠摘要，不遮住成功任务；只自动重试临时故障', async () => {
    vi.mocked(industryResearchApi.projects).mockRejectedValue({ isAxiosError: true, response: { status: 404 } });
    vi.mocked(essayRadarApi.audioAnalysisTasks).mockRejectedValue({ isAxiosError: true, response: { status: 422 } });
    vi.mocked(essayRadarApi.audioBatchTasks).mockRejectedValue({ isAxiosError: true, response: { status: 400 } });
    vi.mocked(dataAcquisitionApi.tasks).mockRejectedValue({ isAxiosError: true, response: { status: 503 } });
    render(fixture()); await flush();
    expect(within(screen.getByRole('main')).getAllByRole('status')).toHaveLength(1);
    expect(within(screen.getByRole('main')).getByRole('status')).toHaveTextContent('4 类任务读取异常 · 3 类接口版本不兼容');
    expect(screen.getByText(running.name)).toBeVisible();
    expect(screen.getByRole('button', { name: '重试行业与公司调研' })).not.toBeVisible();
    const failures = screen.getByText('异常详情').closest('details')!;
    expect(failures).not.toHaveAttribute('open');
    expandFailures();
    expect(failures).toHaveAttribute('open');
    expect(within(failures).getAllByRole('button')).toHaveLength(4);
    expect(screen.getByText(/一站式取数暂时无法读取/)).toBeVisible();
    fireEvent.click(screen.getByRole('button', { name: '重试行业与公司调研' })); await flush();
    expect(industryResearchApi.projects).toHaveBeenCalledTimes(2);
    await advance(30_000);
    expect(industryResearchApi.projects).toHaveBeenCalledTimes(2);
    expect(essayRadarApi.audioAnalysisTasks).toHaveBeenCalledTimes(1);
    expect(essayRadarApi.audioBatchTasks).toHaveBeenCalledTimes(1);
    expect(dataAcquisitionApi.tasks).toHaveBeenCalledTimes(2);
    expect(screen.getByText(running.name)).toBeVisible();
  });

  it('首次404不是空任务，停止重试后重新进入页面仍能读取恢复结果', async () => {
    vi.mocked(essayQuantApi.tasks).mockResolvedValue({ items: [], total: 0 });
    vi.mocked(industryResearchApi.projects).mockRejectedValueOnce({ isAxiosError: true, response: { status: 404 } })
      .mockResolvedValue({ items: [], total: 0 });
    render(fixture()); await flush();
    expect(screen.getByText('部分渠道尚未读取成功')).toBeVisible();
    expect(screen.queryByText('当前筛选下没有任务')).not.toBeInTheDocument();
    await advance(120_000);
    expect(industryResearchApi.projects).toHaveBeenCalledTimes(1);
    Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'hidden' });
    fireEvent(document, new Event('visibilitychange'));
    Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'visible' });
    fireEvent(document, new Event('visibilitychange')); await flush();
    expect(industryResearchApi.projects).toHaveBeenCalledTimes(2);
    expect(screen.getByText('当前筛选下没有任务')).toBeVisible();
    expect(within(screen.getByRole('main')).queryByRole('status')).not.toBeInTheDocument();
  });

  it('账号切换立即隔离旧快照，旧身份的慢请求不能覆盖新账号', async () => {
    let finishOld!: (value: { items: EssayQuantTask[]; total: number }) => void;
    vi.mocked(essayQuantApi.tasks).mockResolvedValueOnce({ items: [running], total: 1 })
      .mockImplementationOnce(() => new Promise(resolve => { finishOld = resolve; }))
      .mockResolvedValue({ items: [{ ...running, taskId: 'task-b', name: '账号 B 的任务' }], total: 1 });
    const view = render(fixture()); await flush(); await advance(10_000);
    vi.mocked(useUserAccess).mockReturnValue({ ...auth(), user: { id: 2, name: '用户 B', status: 'approved' } });
    view.rerender(fixture());
    expect(screen.queryByText(running.name)).not.toBeInTheDocument();
    await flush(); expect(screen.getByText('账号 B 的任务')).toBeInTheDocument();
    await act(async () => { finishOld({ items: [{ ...running, name: '旧账号迟到结果' }], total: 1 }); });
    expect(screen.queryByText('旧账号迟到结果')).not.toBeInTheDocument();
    expect(screen.getByText('账号 B 的任务')).toBeInTheDocument();
  });

  it.each(['logout', 'revoked'] as const)('%s 后立即隐藏所有个人任务并停止请求', async mode => {
    const view = render(fixture()); await flush();
    vi.mocked(useUserAccess).mockReturnValue(mode === 'logout'
      ? { ...auth(), loggedIn: false, user: null }
      : { ...auth(), user: { id: 1, name: '用户 A', status: 'pending' } });
    view.rerender(fixture());
    expect(screen.queryByText(running.name)).not.toBeInTheDocument();
    const calls = vi.mocked(essayQuantApi.tasks).mock.calls.length;
    await advance(30_000);
    expect(essayQuantApi.tasks).toHaveBeenCalledTimes(calls);
  });

  it.each([
    { status: 'completed' as const, resultRunId: 55, href: '/essay-quant?section=results&run=55' },
    { status: 'running' as const, resultRunId: null, href: '/essay-quant?section=history&task=quant%2F%E6%97%A7%E4%BB%BB%E5%8A%A1' },
    { status: 'completed' as const, resultRunId: null, href: '/essay-quant?section=history&task=quant%2F%E6%97%A7%E4%BB%BB%E5%8A%A1' },
  ])('$status 点击到精确结果或任务定位而非不存在的栏目', async ({ status, resultRunId, href }) => {
    vi.mocked(essayQuantApi.tasks).mockResolvedValue({ items: [{ ...running, status, resultRunId }], total: 1 });
    render(fixture()); await flush();
    fireEvent.click(screen.getByRole('button', { name: /账号 A 的量化任务/ }));
    expect(screen.getByLabelText('当前路由')).toHaveTextContent(href);
  });

  it.each(['empty', 'completed'] as const)('%s 渠道不持续轮询；重新进入可见状态后重新读取', async mode => {
    vi.mocked(essayQuantApi.tasks).mockResolvedValue({ items: mode === 'empty' ? [] : [{ ...running, status: 'completed' }], total: mode === 'empty' ? 0 : 1 });
    render(fixture()); await flush();
    await advance(120_000);
    for (const api of [industryResearchApi.projects, essayQuantApi.tasks, essayRadarApi.audioAnalysisTasks, essayRadarApi.audioBatchTasks, dataAcquisitionApi.tasks]) expect(api).toHaveBeenCalledTimes(1);
    Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'hidden' });
    fireEvent(document, new Event('visibilitychange'));
    Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'visible' });
    fireEvent(document, new Event('visibilitychange')); await flush();
    expect(essayQuantApi.tasks).toHaveBeenCalledTimes(2);
  });

  it('仅轮询活动渠道，完成后停止；隐藏页面不轮询', async () => {
    render(fixture()); await flush();
    await advance(10_000);
    expect(essayQuantApi.tasks).toHaveBeenCalledTimes(2);
    expect(industryResearchApi.projects).toHaveBeenCalledTimes(1);
    expect(essayRadarApi.audioAnalysisTasks).toHaveBeenCalledTimes(1);
    expect(essayRadarApi.audioBatchTasks).toHaveBeenCalledTimes(1);
    expect(dataAcquisitionApi.tasks).toHaveBeenCalledTimes(1);
    Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'hidden' });
    await advance(30_000);
    expect(essayQuantApi.tasks).toHaveBeenCalledTimes(2);
    Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'visible' });
    vi.mocked(essayQuantApi.tasks).mockResolvedValue({ items: [{ ...running, status: 'completed' }], total: 1 });
    await advance(10_000);
    expect(essayQuantApi.tasks).toHaveBeenCalledTimes(3);
    await advance(60_000);
    expect(essayQuantApi.tasks).toHaveBeenCalledTimes(3);
  });

  it('只分页已读取记录，准确显示总量边界和完成任务跳转', async () => {
    const tasks = Array.from({ length: 45 }, (_, index) => ({ ...running, taskId: `quant-${index}`, name: `已完成量化 ${index}`, status: 'completed' as const, progress: 100, resultRunId: index + 10 }));
    vi.mocked(essayQuantApi.tasks).mockResolvedValue({ items: tasks, total: 83 });
    render(fixture()); await flush();
    expect(screen.getByRole('button', { name: /已读取任务\s*45/ })).toBeInTheDocument();
    expect(screen.queryByText('全部任务')).not.toBeInTheDocument();
    expect(screen.getByText(/尚有历史任务未列入/)).toBeInTheDocument();
    expect(screen.getByText('已读取 45 / 83 条')).toBeInTheDocument();
    const list = screen.getByRole('region', { name: '任务记录' });
    expect(within(list).getAllByRole('button')).toHaveLength(20);
    fireEvent.click(screen.getByRole('button', { name: '下一页' }));
    expect(screen.getByText('第 2 / 3 页 · 本页 21–40 条')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '下一页' }));
    expect(within(list).getAllByRole('button')).toHaveLength(5);
    fireEvent.click(screen.getByRole('button', { name: /已完成量化 44/ }));
    expect(screen.getByLabelText('当前路由')).toHaveTextContent('/essay-quant?section=results&run=54');
  });

  it('状态和类型筛选会回到第一页，不把未读取的记录说成没有历史', async () => {
    const tasks = Array.from({ length: 21 }, (_, index) => ({ ...running, taskId: `task-${index}`, name: `任务 ${index}`, status: index ? 'completed' as const : 'failed' as const }));
    vi.mocked(essayQuantApi.tasks).mockResolvedValue({ items: tasks, total: 90 });
    render(fixture()); await flush();
    fireEvent.click(screen.getByRole('button', { name: '下一页' }));
    fireEvent.click(screen.getByRole('button', { name: /需要处理\s*1/ }));
    expect(screen.getByText('任务 0')).toBeInTheDocument();
    expect(screen.queryByRole('navigation', { name: '任务列表分页' })).not.toBeInTheDocument();
    fireEvent.change(screen.getByRole('combobox', { name: '任务类型' }), { target: { value: 'research' } });
    expect(screen.queryByText('任务 0')).not.toBeInTheDocument();
    expect(screen.getByText(/尚有历史任务未列入/)).toBeInTheDocument();
  });

  it('历史调研用真实offset继续读取，去重且保留精确结果入口', async () => {
    const projects: IndustryResearchProject[] = Array.from({ length: 31 }, (_, index) => ({ projectId: `research-${index}`, topic: `公司调研 ${index}`, researchType: 'company', objective: '公司基本面', lookbackDays: 180, status: 'completed', stage: 'completed', message: '报告已完成', progress: 100, createdAt: '2026-09-04T01:00:00Z', updatedAt: '2026-09-04T01:00:00Z', query: {} }));
    vi.mocked(essayQuantApi.tasks).mockResolvedValue({ items: [], total: 0 });
    vi.mocked(industryResearchApi.projects).mockImplementation(async options => ({ items: options?.offset ? [projects[29], projects[30]] : projects.slice(0, 30), total: 31 }));
    render(fixture()); await flush();
    const coverage = screen.getByText('读取范围与历史任务').closest('details')!;
    fireEvent.click(within(coverage).getByText('读取范围与历史任务'));
    fireEvent.click(screen.getByRole('button', { name: '再读取 30 条历史调研' })); await flush();
    expect(industryResearchApi.projects).toHaveBeenLastCalledWith({ limit: 30, offset: 30, force: true });
    expect(screen.getByText('已读取 31 / 31 条')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '再读取 30 条历史调研' })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '下一页' }));
    fireEvent.click(screen.getByRole('button', { name: /公司调研 30/ }));
    expect(screen.getByLabelText('当前路由')).toHaveTextContent('/industry-research?project=research-30');
  });
});
