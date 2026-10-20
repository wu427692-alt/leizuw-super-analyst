import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, useLocation } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { useUserAccess } from '../../contexts/UserAccessContext';
import UserAccessPage from '../UserAccessPage';

vi.mock('../../contexts/UserAccessContext', () => ({ useUserAccess: vi.fn() }));
let access: ReturnType<typeof useUserAccess>;
function LocationProbe() { return <output aria-label="当前路由">{useLocation().search}</output>; }
function open(path = '/access?redirect=%2Fai-news') { render(<MemoryRouter initialEntries={[path]}><UserAccessPage /><LocationProbe /></MemoryRouter>); }
function enterPhone() { fireEvent.change(screen.getByLabelText('手机号'), { target: { value: '13900000000' } }); }
async function sendCode() {
  enterPhone(); fireEvent.click(screen.getByRole('button', { name: '获取验证码' }));
  await screen.findByText('验证码已发送，请查看手机短信。');
}
beforeEach(() => {
  localStorage.clear(); sessionStorage.clear();
  access = { accessEnabled: true, loggedIn: false, user: null, authMethod: null, isLoading: false,
    registrationMode: 'self_service', smsLoginEnabled: true,
    login: vi.fn().mockResolvedValue({ success: true }), register: vi.fn(),
    sendSms: vi.fn().mockResolvedValue({ success: true, challenge: { challengeId: 'opaque-test-challenge', expiresIn: 300, retryAfter: 60 } }),
    smsLogin: vi.fn().mockResolvedValue({ success: true }), bindPhone: vi.fn().mockResolvedValue({ success: true }),
    logout: vi.fn(), refresh: vi.fn(),
  };
  vi.mocked(useUserAccess).mockImplementation(() => access);
});
afterEach(() => { cleanup(); vi.useRealTimers(); });

describe('SMS self-service access', () => {
  it('uses verified phone registration without the old approval or password registration path', async () => {
    open('/access?mode=register');
    expect(screen.queryByRole('button', { name: '申请注册' })).not.toBeInTheDocument();
    expect(screen.queryByText('管理员审批')).not.toBeInTheDocument();
    expect(screen.getByText(/未注册的手机号将自动注册/)).toBeInTheDocument();
    await sendCode();
    expect(access.sendSms).toHaveBeenCalledWith('13900000000', 'login');
    fireEvent.change(screen.getByLabelText('短信验证码'), { target: { value: '123456' } });
    fireEvent.change(screen.getByLabelText(/姓名/), { target: { value: '研究用户' } });
    fireEvent.submit(screen.getByRole('button', { name: '验证并登录 / 注册' }).closest('form')!);
    await waitFor(() => expect(access.smsLogin).toHaveBeenCalledWith({ phone: '13900000000', code: '123456', challengeId: 'opaque-test-challenge', name: '研究用户' }));
    expect(access.register).not.toHaveBeenCalled();
    expect(localStorage.length).toBe(0); expect(sessionStorage.length).toBe(0);
  });

  it('prevents duplicate sends and starts a server-driven resend countdown', async () => {
    vi.useFakeTimers();
    let resolve!: (value: Awaited<ReturnType<typeof access.sendSms>>) => void;
    vi.mocked(access.sendSms).mockReturnValue(new Promise(r => { resolve = r; }));
    open(); enterPhone();
    const button = screen.getByRole('button', { name: '获取验证码' });
    fireEvent.click(button); fireEvent.click(button);
    expect(access.sendSms).toHaveBeenCalledTimes(1);
    await act(async () => { resolve({ success: true, challenge: { challengeId: 'challenge', expiresIn: 300, retryAfter: 45 } }); });
    expect(screen.getByRole('button', { name: '45 秒后重发' })).toBeDisabled();
    await act(async () => { vi.advanceTimersByTime(45000); });
    expect(screen.getByRole('button', { name: '重新获取' })).toBeEnabled();
  });

  it('never claims a failed provider send succeeded', async () => {
    vi.mocked(access.sendSms).mockResolvedValue({ success: false, error: { message: '短信服务配置未完成', title: '服务暂不可用', rawMessage: '短信服务配置未完成', category: 'http_error', status: 503 } });
    open(); enterPhone(); fireEvent.click(screen.getByRole('button', { name: '获取验证码' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('短信服务配置未完成');
    expect(screen.queryByText('验证码已发送，请查看手机短信。')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: '验证并登录 / 注册' })).toBeDisabled();
  });

  it('truthfully disables an unconfigured provider while keeping existing password login', () => {
    access.smsLoginEnabled = false; open();
    expect(screen.getByText(/短信服务暂未就绪/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '获取验证码' })).toBeDisabled();
    fireEvent.click(screen.getByRole('button', { name: '原账号密码登录' }));
    expect(screen.getByLabelText('密码')).toBeEnabled();
    expect(screen.getByRole('checkbox')).not.toBeChecked();
    expect(access.register).not.toHaveBeenCalled();
  });

  it('invalidates a challenge after editing the phone and expires codes without sending again', async () => {
    vi.useFakeTimers(); open(); enterPhone(); fireEvent.click(screen.getByRole('button', { name: '获取验证码' }));
    await act(async () => { await Promise.resolve(); });
    await act(async () => { vi.advanceTimersByTime(300000); });
    expect(screen.getByText('验证码已过期，请重新获取。')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '验证并登录 / 注册' })).toBeDisabled();
    fireEvent.change(screen.getByLabelText('手机号'), { target: { value: '13900000001' } });
    expect(screen.queryByText('验证码已过期，请重新获取。')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: '验证并登录 / 注册' })).toBeDisabled();
    expect(access.sendSms).toHaveBeenCalledTimes(1);
  });

  it('lets an old account opt in to phone binding before authentication changes routing', async () => {
    open(); fireEvent.click(screen.getByRole('button', { name: '原账号密码登录' }));
    expect(screen.getByRole('checkbox')).not.toBeChecked();
    fireEvent.click(screen.getByRole('checkbox'));
    fireEvent.change(screen.getByLabelText('姓名'), { target: { value: '原用户' } });
    fireEvent.change(screen.getByLabelText('密码'), { target: { value: 'original-pass' } });
    fireEvent.submit(screen.getByRole('button', { name: '登录并绑定手机号' }).closest('form')!);
    await waitFor(() => expect(access.login).toHaveBeenCalledWith('原用户', 'original-pass'));
    expect(screen.getByLabelText('当前路由')).toHaveTextContent('bind=phone');
    expect(access.smsLogin).not.toHaveBeenCalled();
  });

  it('binds to the authenticated old identity with password re-verification and can skip binding', async () => {
    access.loggedIn = true; access.user = { id: 31, name: '原用户', status: 'approved', phoneBound: false };
    open('/access?bind=phone&redirect=%2Fai-news');
    expect(screen.getByRole('link', { name: '暂不绑定，继续使用原账号' })).toHaveAttribute('href', '/ai-news');
    expect(screen.getByRole('heading', { name: '绑定手机号' })).toBeInTheDocument();
    await sendCode(); expect(access.sendSms).toHaveBeenCalledWith('13900000000', 'bind');
    fireEvent.change(screen.getByLabelText('原账号密码'), { target: { value: 'original-pass' } });
    fireEvent.change(screen.getByLabelText('短信验证码'), { target: { value: '123456' } });
    fireEvent.submit(screen.getByRole('button', { name: '验证并绑定手机号' }).closest('form')!);
    await screen.findByRole('heading', { name: '手机号已绑定' });
    expect(access.bindPhone).toHaveBeenCalledWith({ phone: '13900000000', code: '123456', challengeId: 'opaque-test-challenge', currentPassword: 'original-pass' });
    expect(access.smsLogin).not.toHaveBeenCalled();
    expect(screen.queryByLabelText('原账号密码')).not.toBeInTheDocument();
  });

  it('does not offer changing an already-bound phone or allow an external return link', () => {
    access.loggedIn = true; access.user = { id: 31, name: '原用户', status: 'approved', phoneBound: true, phoneMasked: '139****0000' };
    open('/access?bind=phone&redirect=%2F%2Fevil.invalid');
    expect(screen.getByRole('heading', { name: '手机号已绑定' })).toBeInTheDocument();
    expect(screen.queryByLabelText('手机号')).not.toBeInTheDocument();
    expect(screen.getByRole('link', { name: '进入工作台' })).toHaveAttribute('href', '/app');
  });

  it('keeps the same challenge when the original password needs correcting', async () => {
    access.loggedIn = true; access.user = { id: 31, name: '原用户', status: 'approved', phoneBound: false };
    vi.mocked(access.bindPhone).mockResolvedValueOnce({ success: false, error: { message: '原账号密码不正确', title: '验证未完成', rawMessage: '原账号密码不正确', category: 'http_error', status: 401 } });
    open('/access?bind=phone');
    await sendCode();
    fireEvent.change(screen.getByLabelText('原账号密码'), { target: { value: 'mistyped-pass' } });
    fireEvent.change(screen.getByLabelText('短信验证码'), { target: { value: '123456' } });
    fireEvent.submit(screen.getByRole('button', { name: '验证并绑定手机号' }).closest('form')!);
    expect(await screen.findByRole('alert')).toHaveTextContent('原账号密码不正确');
    expect(screen.getByLabelText('短信验证码')).toHaveValue('123456');
    fireEvent.change(screen.getByLabelText('原账号密码'), { target: { value: 'corrected-pass' } });
    fireEvent.submit(screen.getByRole('button', { name: '验证并绑定手机号' }).closest('form')!);
    await screen.findByRole('heading', { name: '手机号已绑定' });
    expect(access.bindPhone).toHaveBeenLastCalledWith({ phone: '13900000000', code: '123456', challengeId: 'opaque-test-challenge', currentPassword: 'corrected-pass' });
    expect(access.sendSms).toHaveBeenCalledTimes(1);
    expect(localStorage.length).toBe(0); expect(sessionStorage.length).toBe(0);
  });
});
