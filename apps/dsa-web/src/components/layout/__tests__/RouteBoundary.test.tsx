import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { lazy, useEffect } from 'react';
import type React from 'react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { RouteOutletBoundary } from '../RouteBoundary';
import { Shell } from '../Shell';
import { finishRouteRequest, resetRouteLoadTrackerForTests, startRouteRequest } from '../../../utils/routeLoadTracker';

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

  it('keeps a route hidden behind a determinate progress bar until initial requests settle', async () => {
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

    expect(screen.getByRole('progressbar', { name: '页面加载进度' })).toBeInTheDocument();
    expect(screen.getByTestId('data-page')).not.toBeVisible();
    await waitFor(() => expect(screen.getByTestId('data-page')).toBeVisible(), { timeout: 2_000 });
    expect(screen.queryByRole('progressbar', { name: '页面加载进度' })).not.toBeInTheDocument();
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
              <Route path="/portfolio" element={<div data-testid="portfolio-page">Portfolio</div>} />
            </Route>
          </Routes>
        </MemoryRouter>,
      );

      expect(screen.getByRole('navigation', { name: '主导航' })).toBeInTheDocument();
      expect(await screen.findByRole('heading', { name: '模块暂未完成加载' })).toBeInTheDocument();
      expect(screen.getByRole('button', { name: '重试当前模块' })).toBeInTheDocument();
      expect(screen.getByRole('button', { name: '返回首页' })).toBeInTheDocument();

      fireEvent.click(screen.getByRole('link', { name: '持仓' }));

      expect(await screen.findByTestId('portfolio-page')).toBeInTheDocument();
      expect(screen.queryByRole('heading', { name: '模块暂未完成加载' })).not.toBeInTheDocument();
    } finally {
      consoleError.mockRestore();
    }
  });
});
