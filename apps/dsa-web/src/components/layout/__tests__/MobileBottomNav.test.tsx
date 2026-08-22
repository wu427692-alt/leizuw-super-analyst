import { fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, expect, it, vi } from 'vitest';
import { MobileBottomNav } from '../MobileBottomNav';

describe('MobileBottomNav', () => {
  it('exposes the four primary mobile destinations and a more drawer trigger', () => {
    const onOpenMore = vi.fn();
    render(
      <MemoryRouter initialEntries={['/super-watchlist']}>
        <MobileBottomNav onOpenMore={onOpenMore} />
      </MemoryRouter>,
    );

    expect(screen.getByRole('link', { name: '首页' })).toHaveAttribute('href', '/app');
    expect(screen.getByRole('link', { name: '问股' })).toHaveAttribute('href', '/chat');
    expect(screen.getByRole('link', { name: '投资情报台' })).toHaveAttribute('href', '/investment-monitor');
    expect(screen.getByRole('link', { name: '自选股超级看板' })).toHaveClass('is-active');

    fireEvent.click(screen.getByRole('button', { name: '导航菜单' }));
    expect(onOpenMore).toHaveBeenCalledTimes(1);
  });

  it('marks more active for secondary research pages', () => {
    render(
      <MemoryRouter initialEntries={['/essay-quant']}>
        <MobileBottomNav onOpenMore={() => undefined} />
      </MemoryRouter>,
    );

    expect(screen.getByRole('button', { name: '导航菜单' })).toHaveClass('is-active');
  });
});
