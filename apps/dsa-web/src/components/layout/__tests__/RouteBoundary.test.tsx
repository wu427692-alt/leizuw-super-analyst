import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { lazy, useEffect, useState } from 'react';
import type React from 'react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { PageLoadingFallback, RouteOutletBoundary } from '../RouteBoundary';
import { Shell } from '../Shell';
import { finishRouteRequest, getRouteLoadSnapshot, resetRouteLoadTrackerForTests, startRouteRequest } from '../../../utils/routeLoadTracker';

vi.mock('../../../contexts/AuthContext', () => ({
  useAuth: () => ({
    authEnabled: false,
    logout: vi.fn().mockResolvedValue(undefined),
  }),
}));

vi.mock('../../../stores/agentChatStore', () => {
  const state = { completionBadge: false };

  return {
    useAgentChatStore: (selector?: (value: typeof state) => unknown) => (
      selector ? selector(state) : state
    ),
  };
});

describe('RouteOutletBoundary', () => {
  beforeEach(() => resetRouteLoadTrackerForTests());

  it('describes an unresolved lazy component without claiming any data percentage', async () => {
    let finishImport!: (module: { default: React.ComponentType }) => void;
    const LazyPage = lazy(() => new Promise<{ default: React.ComponentType }>(resolve => { finishImport = resolve; }));
    render(<MemoryRouter initialEntries={['/lazy']}><Routes>
      <Route element={<RouteOutletBoundary />}><Route path="/lazy" element={<LazyPage />} /></Route>
    </Routes></MemoryRouter>);

    const status = screen.getByRole('status', { name: '正在加载页面组件' });
    expect(status).toHaveTextContent('各模块会分别读取数据');
    expect(status).not.toHaveTextContent('%');
    expect(screen.queryByRole('progressbar')).not.toBeInTheDocument();
    expect(screen.queryByText(/页面数据完整后/)).not.toBeInTheDocument();
    await act(async () => finishImport({ default: () => <div>页面组件已打开</div> }));
    expect(await screen.findByText('页面组件已打开')).toBeVisible();
    expect(screen.queryByRole('status', { name: '正在加载页面组件' })).not.toBeInTheDocument();
  });

  it('exposes the supplied authorization stage as readable status, without simulated progress', () => {
    render(<PageLoadingFallback status="正在确认访问权限" detail="验证完成后进入页面，个人数据不会提前显示。" />);
    const status = screen.getByRole('status', { name: '正在确认访问权限' });
    expect(status).toHaveTextContent('个人数据不会提前显示');
    expect(status).not.toHaveTextContent('%');
    expect(screen.queryByRole('progressbar')).not.toBeInTheDocument();
    expect(status.querySelector('svg')).toHaveAttribute('aria-hidden', 'true');
    expect(status.querySelector('svg')).toHaveClass('motion-safe:animate-spin');
  });

  it('shows the route immediately while initial requests settle in the background', async () => {
    const DataPage = () => {
      useEffect(() => {
        const token = startRouteRequest();
        const timer = window.setTimeout(() => finishRouteRequest(token), 120);
        return () => window.clearTimeout(timer);
      }, []);
      return <div data-testid="data-page">完整页面数据</div>;
    };

    render(
      <MemoryRouter initialEntries={['/portfolio']}>
        <Routes>
          <Route element={<RouteOutletBoundary />}>
            <Route path="/portfolio" element={<DataPage />} />
          </Route>
        </Routes>
      </MemoryRouter>,
    );

    expect(screen.getByTestId('data-page')).toBeVisible();
    await waitFor(() => expect(getRouteLoadSnapshot().active).toBe(false));
    expect(screen.queryByRole('progressbar', { name: '正在读取初始模块' })).not.toBeInTheDocument();
  });

  it('keeps controls usable and a slow module pending after the indeterminate initial hint expires', () => {
    vi.useFakeTimers();
    const SlowPage = () => {
      const [clicked, setClicked] = useState(false);
      useEffect(() => { startRouteRequest(); }, []);
      return <div data-testid="slow-page"><p>该模块仍在读取</p><button onClick={() => setClicked(true)}>已可使用的操作</button>{clicked ? <p>操作已完成</p> : null}</div>;
    };
    const view = render(
      <MemoryRouter initialEntries={['/slow']}>
        <Routes>
          <Route element={<RouteOutletBoundary />}>
            <Route path="/slow" element={<SlowPage />} />
          </Route>
        </Routes>
      </MemoryRouter>,
    );

    try {
      expect(screen.getByTestId('slow-page')).toBeVisible();
      act(() => vi.advanceTimersByTime(250));
      const hint = screen.getByRole('progressbar', { name: '正在读取初始模块' });
      expect(hint).not.toHaveAttribute('aria-valuenow');
      expect(hint).not.toHaveAttribute('aria-valuemax');
      fireEvent.click(screen.getByRole('button', { name: '已可使用的操作' }));
      expect(screen.getByText('操作已完成')).toBeVisible();
      act(() => vi.advanceTimersByTime(1_500));
      expect(screen.queryByRole('progressbar')).not.toBeInTheDocument();
      expect(screen.getByText('该模块仍在读取')).toBeVisible();
      expect(getRouteLoadSnapshot()).toMatchObject({ active: false, completed: 0, pending: 1 });
      expect(screen.queryByText(/100%/)).not.toBeInTheDocument();
    } finally {
      view.unmount();
      vi.useRealTimers();
    }
  });

  it('catches rejected lazy route imports inside the shell and resets on navigation', async () => {
    const consoleError = vi.spyOn(console, 'error').mockImplementation(() => undefined);
    const BrokenLazyRoute = lazy(() => (
      Promise.reject(new Error('chunk load failed')) as Promise<{ default: React.ComponentType }>
    ));

    try {
      render(
        <MemoryRouter initialEntries={['/chat']}>
          <Routes>
            <Route
              element={(
                <Shell>
                  <RouteOutletBoundary />
                </Shell>
              )}
            >
              <Route path="/chat" element={<BrokenLazyRoute />} />
              <Route path="/super-watchlist" element={<div data-testid="watchlist-page">Watchlist</div>} />
            </Route>
          </Routes>
        </MemoryRouter>,
      );

      expect(screen.getByRole('navigation', { name: '主导航' })).toBeInTheDocument();
      expect(await screen.findByRole('heading', { name: '模块暂未完成加载' })).toBeInTheDocument();
      expect(screen.getByRole('button', { name: '重试当前模块' })).toBeInTheDocument();
      expect(screen.getByRole('button', { name: '返回首页' })).toBeInTheDocument();

      fireEvent.click(within(screen.getByRole('navigation', { name: '主导航' })).getByRole('link', {
        name: '自选股超级看板',
      }));

      expect(await screen.findByTestId('watchlist-page')).toBeInTheDocument();
      expect(screen.queryByRole('heading', { name: '模块暂未完成加载' })).not.toBeInTheDocument();
    } finally {
      consoleError.mockRestore();
    }
  });
});
