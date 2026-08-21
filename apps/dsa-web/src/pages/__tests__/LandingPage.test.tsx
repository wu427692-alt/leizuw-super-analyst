import { fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, expect, it } from 'vitest';
import LandingPage from '../LandingPage';

describe('LandingPage', () => {
  it('introduces the real platform and enters the dashboard', () => {
    render(<MemoryRouter><LandingPage /></MemoryRouter>);

    expect(screen.getByRole('heading', { name: /把市场噪声，\s*变成可以行动的证据。/ })).toBeInTheDocument();
    expect(screen.getByText('实时行情')).toBeInTheDocument();
    expect(screen.getByText('全渠道情报')).toBeInTheDocument();
    expect(screen.getByText('小作文洞察')).toBeInTheDocument();
    expect(screen.getByText('量化回测')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: /进入财经情报台/ })).toHaveAttribute('href', '/app');
  });

  it('updates the visual field without blocking the CTA', () => {
    const { container } = render(<MemoryRouter><LandingPage /></MemoryRouter>);
    const page = container.querySelector('.landing-page') as HTMLElement;
    Object.defineProperty(page, 'getBoundingClientRect', {
      value: () => ({ left: 0, top: 0, width: 1000, height: 800, right: 1000, bottom: 800, x: 0, y: 0, toJSON: () => ({}) }),
    });

    fireEvent.pointerMove(page, { clientX: 750, clientY: 200 });

    expect(page.style.getPropertyValue('--pointer-x')).toBe('6px');
    expect(screen.getByRole('link', { name: /进入财经情报台/ })).toBeVisible();
  });
});
