import { fireEvent, render, screen, waitFor } from '@testing-library/react';
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
      registrationMode: 'approval',
      smsLoginEnabled: false,
      login: vi.fn(),
      register: vi.fn(),
      sendSms: vi.fn(),
      smsLogin: vi.fn(),
      bindPhone: vi.fn(),
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

  it.each(['approval', 'self_service'] as const)('waits for confirmed policy without promising %s registration while loading', registrationMode => {
    vi.mocked(useUserAccess).mockReturnValue({
      ...vi.mocked(useUserAccess).getMockImplementation()!(),
      isLoading: true,
      registrationMode,
    });
    render(<MemoryRouter initialEntries={['/access?mode=register']}><UserAccessPage /></MemoryRouter>);
    expect(screen.getByRole('heading', { name: '正在准备登录' })).toBeInTheDocument();
    expect(screen.getByRole('status')).toHaveTextContent('正在读取登录方式');
    expect(screen.queryByText('管理员审批')).not.toBeInTheDocument();
    expect(screen.queryByText('手机号验证，即刻使用')).not.toBeInTheDocument();
    expect(screen.queryByRole('heading', { name: '提交访问申请' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /提交管理员审核|验证并登录/ })).not.toBeInTheDocument();
  });

  it.each(['approval', 'self_service'] as const)('does not guess a %s policy when reading status fails, and keeps password login and retry', async registrationMode => {
    const login = vi.fn().mockResolvedValue({ success: false, error: { message: '请核对账号密码' } });
    const refresh = vi.fn().mockResolvedValue(undefined);
    vi.mocked(useUserAccess).mockReturnValue({
      ...vi.mocked(useUserAccess).getMockImplementation()!(),
      statusUnavailable: true, registrationMode, smsLoginEnabled: false, login, refresh,
    });
    render(<MemoryRouter initialEntries={['/access?mode=register']}><UserAccessPage /></MemoryRouter>);
    expect(screen.getByRole('status')).toHaveTextContent('暂时无法读取登录方式');
    expect(screen.queryByText('管理员审批')).not.toBeInTheDocument();
    expect(screen.queryByText('手机号验证，即刻使用')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '申请注册' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /提交管理员审核|验证并登录/ })).not.toBeInTheDocument();
    expect(screen.getByLabelText('密码')).toBeEnabled();
    fireEvent.change(screen.getByLabelText('姓名'), { target: { value: '原用户' } });
    fireEvent.change(screen.getByLabelText('密码'), { target: { value: 'old-test-password' } });
    fireEvent.submit(screen.getByRole('button', { name: '进入工作台' }).closest('form')!);
    await waitFor(() => expect(login).toHaveBeenCalledWith('原用户', 'old-test-password'));
    await screen.findByRole('alert');
    fireEvent.click(screen.getByRole('button', { name: '重试读取登录方式' }));
    await waitFor(() => expect(refresh).toHaveBeenCalledOnce());
  });
});
