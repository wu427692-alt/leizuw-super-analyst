import { fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, expect, it } from 'vitest';
import LandingPage from '../LandingPage';

describe('LandingPage', () => {
  it('introduces the real platform and enters the dashboard', () => {
    render(<MemoryRouter><LandingPage /></MemoryRouter>);

    expect(screen.getByRole('heading', { name: /把复杂市场，\s*变成可验证的投资线索。/ })).toBeInTheDocument();
    expect(screen.getByText('乐子乌超级价值')).toBeInTheDocument();
    expect(screen.getByText('实时行情')).toBeInTheDocument();
    expect(screen.getByText('全渠道情报')).toBeInTheDocument();
    expect(screen.getByText('小作文洞察')).toBeInTheDocument();
    expect(screen.getByText('量化研究')).toBeInTheDocument();
    expect(screen.getAllByRole('link', { name: /进入研究终端/ })[0]).toHaveAttribute('href', '/app');
    expect(screen.getByRole('link', { name: '管理员' })).toHaveAttribute('href', '/admin');
  });

  it('updates the visual field without blocking the CTA', () => {
    const { container } = render(<MemoryRouter><LandingPage /></MemoryRouter>);
    const page = container.querySelector('.landing-page') as HTMLElement;
    Object.defineProperty(page, 'getBoundingClientRect', {
      value: () => ({ left: 0, top: 0, width: 1000, height: 800, right: 1000, bottom: 800, x: 0, y: 0, toJSON: () => ({}) }),
    });

    fireEvent.pointerMove(page, { clientX: 750, clientY: 200 });

    expect(page.style.getPropertyValue('--pointer-x')).toBe('3.5px');
    expect(screen.getAllByRole('link', { name: /进入研究终端/ })[0]).toBeVisible();
  });
});
