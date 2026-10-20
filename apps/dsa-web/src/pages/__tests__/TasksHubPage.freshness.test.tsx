import { act, cleanup, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import apiClient from '../../api';
import { dataAcquisitionApi } from '../../api/dataAcquisition';
import { essayQuantApi } from '../../api/essayQuant';
import { clearRequestCache } from '../../api/requestCache';
import TasksHubPage from '../TasksHubPage';

vi.mock('../../api', () => ({ default: { get: vi.fn() } }));
vi.mock('../../api/industryResearch', () => ({ industryResearchApi: { projects: async () => ({ items: [], total: 0 }) } }));
vi.mock('../../api/essayRadar', () => ({ essayRadarApi: {
  audioAnalysisTasks: async () => ({ items: [], total: 0 }),
  audioBatchTasks: async () => ({ items: [], total: 0 }),
} }));
vi.mock('../../contexts/UserAccessContext', () => ({ useUserAccess: () => ({
  user: { id: 1, name: '测试用户', status: 'approved' }, loggedIn: true,
  isLoading: false, authMethod: 'password', accessEnabled: true,
}) }));

const cases = [
  { name: '量化', path: '/api/v1/essay-quant/tasks', query: () => essayQuantApi.tasks() },
  { name: '取数', path: '/api/v1/data-acquisition/tasks', query: () => dataAcquisitionApi.tasks() },
];
const task = (status: string) => ({
  task_id: `task-${status}`, name: `${status} 研究任务`, title: `${status} 研究任务`,
  request: `${status} 研究任务`, status, progress: status === 'running' ? 25 : 100,
  tasks: [{ label: `${status} 研究任务` }],
  message: '读取测试', created_at: '2026-09-01T01:00:00Z', updated_at: '2026-09-01T01:00:00Z',
});

describe.each(cases)('$name task lists deliver refreshed results to the actual task page', ({ path, query }) => {
  beforeEach(() => {
    vi.useFakeTimers(); vi.resetAllMocks(); clearRequestCache();
    Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'visible' });
  });
  afterEach(() => { cleanup(); clearRequestCache(); vi.useRealTimers(); });

  it.each(['empty', 'completed'])('replaces a cached %s list when entering after its fresh window', async initial => {
    const previous = { items: initial === 'empty' ? [] : [task('completed')], total: initial === 'empty' ? 0 : 1 };
    vi.mocked(apiClient.get).mockResolvedValue({ data: previous });
    await query();
    await vi.advanceTimersByTimeAsync(2_000);

    let finish!: (response: { data: { items: ReturnType<typeof task>[]; total: number } }) => void;
    const pending = new Promise<{ data: { items: ReturnType<typeof task>[]; total: number } }>(resolve => { finish = resolve; });
    vi.mocked(apiClient.get).mockImplementation(async url => url === path ? pending : { data: { items: [], total: 0 } });
    render(<MemoryRouter><TasksHubPage /></MemoryRouter>);
    await act(async () => { await Promise.resolve(); });
    // Another caller must share this network request instead of getting the
    // previously completed/empty value or issuing a second request.
    const alsoWaiting = query();
    expect(vi.mocked(apiClient.get).mock.calls.filter(([url]) => url === path)).toHaveLength(2);
    await act(async () => {
      finish({ data: { items: [task('running')], total: 1 } });
      await alsoWaiting;
    });
    expect(screen.getByRole('button', { name: /running 研究任务/ })).toBeVisible();
    expect(screen.queryByRole('button', { name: /completed 研究任务/ })).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: /^正在处理\s*1$/ })).toBeVisible();
    await act(async () => { await vi.advanceTimersByTimeAsync(10_000); });
    expect(vi.mocked(apiClient.get).mock.calls.filter(([url]) => url === path)).toHaveLength(3);
  });

  it('propagates refresh failure instead of presenting cached success', async () => {
    vi.mocked(apiClient.get).mockResolvedValue({ data: { items: [], total: 0 } });
    await query();
    await vi.advanceTimersByTimeAsync(2_000);
    vi.mocked(apiClient.get).mockRejectedValue(new Error('temporary failure'));
    await expect(query()).rejects.toThrow('temporary failure');
  });
});
