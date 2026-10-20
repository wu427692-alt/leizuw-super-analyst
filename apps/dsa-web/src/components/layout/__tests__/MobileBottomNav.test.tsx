import { fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { MobileBottomNav } from '../MobileBottomNav';
import { preloadRoute } from '../../../utils/routePreload';
import { preloadRouteData } from '../../../utils/routeDataPreload';

vi.mock('../../../utils/routePreload', () => ({ preloadRoute: vi.fn().mockResolvedValue(undefined) }));
vi.mock('../../../utils/routeDataPreload', () => ({ preloadRouteData: vi.fn().mockResolvedValue(undefined) }));

describe('MobileBottomNav', () => {
  beforeEach(() => vi.clearAllMocks());

  it('uses native document links without prefetching unrelated modules in an isolated entry', () => {
    render(<MemoryRouter initialEntries={['/ai-news']}><MobileBottomNav documentNavigation /></MemoryRouter>);
    const link = screen.getByRole('link', { name: '市场总览' });
    fireEvent.focus(link);
    expect(preloadRoute).not.toHaveBeenCalled();
    expect(preloadRouteData).not.toHaveBeenCalled();
    // React Router's normal Link cancels this event; reloadDocument must not.
    const click = new MouseEvent('click', { bubbles: true, cancelable: true, button: 0 });
    link.dispatchEvent(click);
    expect(click.defaultPrevented).toBe(false);
  });

  it.each(['touchStart', 'focus'] as const)('starts page code and reusable data together on %s', event => {
    render(<MemoryRouter><MobileBottomNav /></MemoryRouter>);
    fireEvent[event](screen.getByRole('link', { name: '自选看板' }));
    expect(preloadRoute).toHaveBeenCalledExactlyOnceWith('/super-watchlist');
    expect(preloadRouteData).toHaveBeenCalledExactlyOnceWith('/super-watchlist');
  });

  it('exposes the five decision workspaces', () => {
    render(
      <MemoryRouter initialEntries={['/super-watchlist']}>
        <MobileBottomNav />
      </MemoryRouter>,
    );

    expect(screen.getByRole('link', { name: '市场总览' })).toHaveAttribute('href', '/app');
    expect(screen.getByRole('link', { name: '自选看板' })).toHaveClass('is-active');
    expect(screen.getByRole('link', { name: '题材图谱' })).toHaveAttribute('href', '/concept-themes');
    expect(screen.getByRole('link', { name: '深度研究' })).toHaveAttribute('href', '/industry-research');
    expect(screen.getByRole('link', { name: '任务中心' })).toHaveAttribute('href', '/tasks');
    expect(screen.getByText('题材')).toBeInTheDocument();
    expect(screen.getByText('自选')).toBeInTheDocument();
    expect(screen.getByText('任务')).toBeInTheDocument();
    expect(screen.queryByRole('link', { name: 'AI 问股' })).not.toBeInTheDocument();
    expect(screen.queryByRole('link', { name: '投资情报台' })).not.toBeInTheDocument();

    expect(screen.queryByRole('button', { name: '导航菜单' })).not.toBeInTheDocument();
  });

  it('marks tasks active', () => {
    render(
      <MemoryRouter initialEntries={['/tasks']}>
        <MobileBottomNav />
      </MemoryRouter>,
    );

    expect(screen.getByRole('link', { name: '任务中心' })).toHaveClass('is-active');
  });

  it('does not put utility pages in the primary mobile bar', () => {
    const { unmount } = render(
      <MemoryRouter initialEntries={['/essay-radar/feed']}>
        <MobileBottomNav />
      </MemoryRouter>,
    );

    expect(screen.queryByRole('link', { name: '机构纪要与录音' })).not.toBeInTheDocument();

    unmount();
    render(
      <MemoryRouter initialEntries={['/data-acquisition']}>
        <MobileBottomNav />
      </MemoryRouter>,
    );
    expect(screen.queryByRole('link', { name: '数据下载' })).not.toBeInTheDocument();
    expect(document.querySelector('a[href^="/jev-"]')).toBeNull();
  });
});
