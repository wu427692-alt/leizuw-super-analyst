import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import AdminConsolePage from '../AdminConsolePage';

const { get, post } = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
}));

vi.mock('../../api', () => ({
  default: { get, post },
}));

const users = [
  {
    id: 1,
    name: '待审用户',
    status: 'pending',
    createdAt: '2026-06-01T10:00:00',
    registrationIp: '10.0.0.8',
    trustedIpCount: 0,
  },
  {
    id: 2,
    name: '已批准用户',
    status: 'approved',
    createdAt: '2026-05-01T10:00:00',
    approvedAt: '2026-05-02T10:00:00',
    registrationIp: '10.0.0.9',
    lastLoginAt: '2026-06-02T09:00:00',
    lastLoginIp: '10.0.0.9',
    trustedIpCount: 2,
  },
];

function LocationProbe() {
  const location = useLocation();
  return <output aria-label="当前路由">{location.pathname}</output>;
}

function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        {['/admin', '/admin/data-sources', '/admin/sync', '/admin/access'].map((route) => (
          <Route key={route} path={route} element={<AdminConsolePage />} />
        ))}
      </Routes>
      <LocationProbe />
    </MemoryRouter>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  get.mockImplementation((url: string) => {
    if (url === '/api/v1/user-auth/admin/users') {
      return Promise.resolve({ data: { users } });
    }
    return Promise.resolve({ data: { status: 'ok', updated_at: '2026-06-03T08:00:00' } });
  });
  post.mockResolvedValue({ data: { ok: true } });
});

describe('AdminConsolePage 四个管理入口', () => {
  it('运行概览保留运行状态与常用入口，统计收进指标行', async () => {
    renderAt('/admin');

    expect(await screen.findByRole('heading', { name: '运行概览' })).toBeInTheDocument();
    await waitFor(() => expect(screen.getByLabelText('在线服务')).toHaveTextContent('4/4'));
    expect(screen.getByLabelText('待审申请')).toHaveTextContent('1');
    expect(screen.getByText('服务状态')).toBeInTheDocument();
    // 四个探针的接口都被真实探测。
    for (const endpoint of [
      '/api/v1/stocks/market-data/status',
      '/api/v1/investment-monitor/status',
      '/api/v1/essay-radar/status',
      '/api/v1/system/scheduler/status',
    ]) expect(get).toHaveBeenCalledWith(endpoint);
    expect(screen.getByRole('link', { name: /API 与模型/ })).toHaveAttribute('href', '/admin/api-models');
  });

  it('主 Tab 与路由一致：数据源、同步、访问各自渲染独立视图', async () => {
    renderAt('/admin');

    await screen.findByRole('heading', { name: '运行概览' });
    fireEvent.click(screen.getByRole('tab', { name: '数据源' }));
    expect(await screen.findByRole('heading', { name: '数据源与渠道' })).toBeInTheDocument();
    expect(screen.getByLabelText('当前路由')).toHaveTextContent('/admin/data-sources');
    expect(screen.getByText('本轮探测耗时')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('tab', { name: '同步任务' }));
    expect(await screen.findByRole('heading', { name: '同步任务' })).toBeInTheDocument();
    expect(screen.getByLabelText('当前路由')).toHaveTextContent('/admin/sync');
    expect(screen.getAllByText('任务调度器').length).toBeGreaterThan(0);
    expect(screen.getByText('同步任务边界')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('tab', { name: '用户与访问' }));
    expect(await screen.findByRole('heading', { name: '用户与访问' })).toBeInTheDocument();
    expect(screen.getByLabelText('当前路由')).toHaveTextContent('/admin/access');
  });

  it('访问入口直接打开审批队列，权限边界在第二层 Tab', async () => {
    renderAt('/admin/access');

    expect(await screen.findByText('已批准用户')).toBeInTheDocument();
    const queue = screen.getByRole('tab', { name: /审批队列/ });
    expect(queue).toHaveAttribute('aria-selected', 'true');
    expect(screen.getByLabelText('待审申请')).toHaveTextContent('1');

    fireEvent.click(screen.getByRole('tab', { name: '权限边界' }));
    expect(screen.getByText('访问权限矩阵')).toBeInTheDocument();
    expect(screen.getByText('账号会话强制验证')).toBeInTheDocument();
    expect(screen.queryByText('已批准用户')).not.toBeInTheDocument();
  });

  it('审批申请后刷新账号列表', async () => {
    renderAt('/admin/access');

    const approve = await screen.findByRole('button', { name: '批准' });
    await act(async () => {
      fireEvent.click(approve);
    });
    expect(post).toHaveBeenCalledWith('/api/v1/user-auth/admin/users/1/approve');
    await waitFor(() => expect(get.mock.calls.filter(([url]) => url === '/api/v1/user-auth/admin/users').length).toBeGreaterThan(1));
  });

  it('直接访问深层路由也渲染同一控制台的对应视图', async () => {
    renderAt('/admin/data-sources');

    expect(await screen.findByRole('heading', { name: '数据源与渠道' })).toBeInTheDocument();
    const section = screen.getByRole('tab', { name: '数据源' });
    expect(section).toHaveAttribute('aria-selected', 'true');
    const table = screen.getByRole('table');
    expect(within(table).getAllByRole('row')).toHaveLength(4);
  });
});
