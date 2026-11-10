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
    const link = screen.getByRole('link', { name: '投研团队' });
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

  it('exposes four primary destinations in the agreed order plus a More entry', () => {
    render(
      <MemoryRouter initialEntries={['/super-watchlist']}>
        <MobileBottomNav />
      </MemoryRouter>,
    );

    const links = screen.getAllByRole('link');
    expect(links).toHaveLength(4);
    expect(links.map((link) => link.getAttribute('href'))).toEqual(['/meeting', '/app', '/super-watchlist', '/home']);
    expect(links.map((link) => link.textContent)).toEqual(['团队', '行情', '自选', '总览']);
    expect(screen.getByRole('link', { name: '自选看板' })).toHaveClass('is-active');
    expect(screen.getByRole('button', { name: '全部模块' })).not.toHaveClass('is-active');
    expect(screen.queryByRole('link', { name: 'AI 问股' })).not.toBeInTheDocument();
  });

  it('highlights More when the current page is not a primary destination', () => {
    render(
      <MemoryRouter initialEntries={['/tasks']}>
        <MobileBottomNav />
      </MemoryRouter>,
    );

    expect(screen.getByRole('button', { name: '全部模块' })).toHaveClass('is-active');
    for (const link of screen.getAllByRole('link')) expect(link).not.toHaveClass('is-active');
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
