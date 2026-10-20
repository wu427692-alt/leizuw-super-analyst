import { cleanup, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import AiNewsAccessPage from '../AiNewsAccessPage';

const { access, login, register } = vi.hoisted(() => ({
  access: { accessEnabled: true, loggedIn: false, isLoading: false, user: null },
  login: vi.fn(),
  register: vi.fn(),
}));

vi.mock('../../contexts/UserAccessContext', () => ({
  useUserAccess: () => ({ ...access, login, register }),
}));

const replace = vi.fn();

beforeEach(() => {
  access.accessEnabled = true;
  access.loggedIn = false;
  access.isLoading = false;
  replace.mockClear();
  login.mockClear();
  register.mockClear();
  // Keep jsdom's document and DOM methods, but substitute the navigation sink.
  // No login, cookie mutation or real navigation occurs in these tests.
  const testWindow = Object.create(window);
  Object.defineProperty(testWindow, 'location', { value: { origin: window.location.origin, replace } });
  vi.stubGlobal('window', testWindow);
});

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

function openAccess(redirect: string) {
  render(<MemoryRouter initialEntries={[`/access?redirect=${encodeURIComponent(redirect)}`]}><AiNewsAccessPage /></MemoryRouter>);
}

describe('AI news standalone access', () => {
  it('shows login for an anonymous/unapproved session without navigating', () => {
    openAccess('/ai-news');
    expect(screen.getByRole('heading', { name: '进入研究工作台' })).toBeInTheDocument();
    expect(screen.getByLabelText('姓名')).toBeInTheDocument();
    expect(replace).not.toHaveBeenCalled();
    expect(login).not.toHaveBeenCalled();
    expect(register).not.toHaveBeenCalled();
  });

  it('uses native replace after login and preserves the slash route and filters', () => {
    access.loggedIn = true;
    openAccess('/ai-news/?source=qbitai');
    expect(replace).toHaveBeenCalledExactlyOnceWith('/ai-news/?source=qbitai');
    expect(login).not.toHaveBeenCalled();
  });

  it('limits an external redirect to the local news root', () => {
    access.loggedIn = true;
    openAccess('https://external.invalid/ai-news?source=qbitai');
    expect(replace).toHaveBeenCalledExactlyOnceWith('/ai-news');
    expect(login).not.toHaveBeenCalled();
  });

  it('does not redirect an authenticated user away from explicit phone binding', () => {
    access.loggedIn = true;
    render(<MemoryRouter initialEntries={['/access?bind=phone&redirect=%2Fai-news']}><AiNewsAccessPage /></MemoryRouter>);
    expect(replace).not.toHaveBeenCalled();
  });
});
