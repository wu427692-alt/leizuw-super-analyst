import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { useUserAccess } from '../../contexts/UserAccessContext';
import UserAccessPage from '../UserAccessPage';

vi.mock('../../contexts/UserAccessContext', () => ({
  useUserAccess: vi.fn(),
}));

describe('UserAccessPage', () => {
  beforeEach(() => {
    vi.mocked(useUserAccess).mockReturnValue({
      accessEnabled: true,
      loggedIn: false,
      user: null,
      authMethod: null,
      isLoading: false,
      login: vi.fn(),
      register: vi.fn(),
      logout: vi.fn(),
      refresh: vi.fn(),
    });
  });

  it('opens the registration form when linked with mode=register', () => {
    render(
      <MemoryRouter initialEntries={['/access?mode=register&redirect=%2Fapp']}>
        <UserAccessPage />
      </MemoryRouter>,
    );

    expect(screen.getByRole('heading', { name: '提交访问申请' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '申请注册' })).toHaveClass('is-active');
    expect(screen.getByPlaceholderText('输入姓名')).toBeVisible();
    expect(screen.getByRole('button', { name: /提交管理员审核/ })).toBeVisible();
  });
});
