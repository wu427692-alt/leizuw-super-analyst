import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { StrictMode } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { useUserAccess } from '../../contexts/UserAccessContext';
import { loadTurnstile } from '../../utils/turnstile';
import type { TurnstileApi, TurnstileOptions } from '../../utils/turnstile';
import UserAccessPage from '../UserAccessPage';

vi.mock('../../contexts/UserAccessContext', () => ({ useUserAccess: vi.fn() }));
vi.mock('../../utils/turnstile', () => ({ loadTurnstile: vi.fn() }));
let access: ReturnType<typeof useUserAccess>;
let api: TurnstileApi;
let renders: TurnstileOptions[];
function open(path = '/access') { render(<MemoryRouter initialEntries={[path]}><UserAccessPage /></MemoryRouter>); }
function phone(value = '13900000000') { fireEvent.change(screen.getByLabelText('手机号'), { target: { value } }); }
async function verify(token = 'test-only-verification-token') {
  await waitFor(() => expect(renders.length).toBeGreaterThan(0));
  act(() => renders.at(-1)!.callback(token));
}
beforeEach(() => {
  vi.clearAllMocks();
  localStorage.clear(); sessionStorage.clear(); renders = [];
  api = {
    render: vi.fn((_element, options) => { renders.push(options); return `widget-${renders.length}`; }),
    remove: vi.fn(),
  };
  vi.mocked(loadTurnstile).mockResolvedValue(api);
  access = { accessEnabled: true, loggedIn: false, user: null, authMethod: null, isLoading: false,
    registrationMode: 'self_service', smsLoginEnabled: true,
    captcha: { provider: 'turnstile', required: true, siteKey: 'public-test-site-key' },
    login: vi.fn().mockResolvedValue({ success: true }), register: vi.fn().mockResolvedValue({ success: true, pending: true }),
    sendSms: vi.fn().mockResolvedValue({ success: true, challenge: { challengeId: 'test-challenge', expiresIn: 300, retryAfter: 60 } }),
    smsLogin: vi.fn().mockResolvedValue({ success: true }), bindPhone: vi.fn().mockResolvedValue({ success: true }),
    logout: vi.fn(), refresh: vi.fn(),
  };
  vi.mocked(useUserAccess).mockImplementation(() => access);
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

describe('visible login verification', () => {
  it('shows how to load verification and prevents sending without a token', async () => {
    open();
    expect(screen.getByText('输入完整手机号后，将在此显示人机验证。')).toBeVisible();
    expect(loadTurnstile).not.toHaveBeenCalled();
    phone();
    await screen.findByRole('region', { name: '人机验证' });
    await waitFor(() => expect(renders.length).toBe(1));
    expect(renders[0]).toMatchObject({ size: 'flexible', appearance: 'always', 'response-field': false });
    const button = screen.getByRole('button', { name: '获取验证码' });
    expect(button).toBeDisabled(); fireEvent.click(button);
    expect(access.sendSms).not.toHaveBeenCalled();
    expect(access.smsLogin).not.toHaveBeenCalled();
  });

  it('requires an explicit send click after verification and passes the token only once', async () => {
    open(); phone(); await verify();
    expect(access.sendSms).not.toHaveBeenCalled();
    const send = screen.getByRole('button', { name: '获取验证码' });
    expect(send).toBeEnabled(); fireEvent.click(send); fireEvent.click(send);
    await screen.findByText('验证码已发送，请查看手机短信。');
    expect(access.sendSms).toHaveBeenCalledExactlyOnceWith('13900000000', 'login', 'test-only-verification-token');
    expect(screen.getByRole('button', { name: '60 秒后重发' })).toBeDisabled();
    expect(screen.queryByRole('region', { name: '人机验证' })).not.toBeInTheDocument();
    expect(localStorage.length).toBe(0); expect(sessionStorage.length).toBe(0);
  });

  it('does not require a second CAPTCHA to enter an already issued SMS code', async () => {
    open(); phone(); await verify();
    fireEvent.click(screen.getByRole('button', { name: '获取验证码' }));
    await screen.findByText('验证码已发送，请查看手机短信。');
    fireEvent.change(screen.getByLabelText('短信验证码'), { target: { value: '123456' } });
    fireEvent.submit(screen.getByRole('button', { name: '验证并登录 / 注册' }).closest('form')!);
    await waitFor(() => expect(access.smsLogin).toHaveBeenCalledExactlyOnceWith({ phone: '13900000000', code: '123456', challengeId: 'test-challenge' }));
    expect(access.sendSms).toHaveBeenCalledTimes(1);
  });

  it('renders a single live widget under the production entry StrictMode lifecycle', async () => {
    render(<StrictMode><MemoryRouter initialEntries={['/access']}><UserAccessPage /></MemoryRouter></StrictMode>);
    phone(); await verify();
    expect(renders.length).toBe(1);
    expect(screen.getByRole('button', { name: '获取验证码' })).toBeEnabled();
    expect(access.sendSms).not.toHaveBeenCalled();
  });

  it('consumes a token even when the SMS provider rejects the send and requires a fresh verification', async () => {
    vi.mocked(access.sendSms).mockResolvedValue({ success: false, error: { category: 'http_error', title: '未发送', message: '短信服务暂不可用', rawMessage: '短信服务暂不可用' } });
    open(); phone(); await verify();
    fireEvent.click(screen.getByRole('button', { name: '获取验证码' }));
    await screen.findByText('短信服务暂不可用');
    await waitFor(() => expect(renders.length).toBe(2));
    expect(screen.getByRole('button', { name: '获取验证码' })).toBeDisabled();
    expect(api.remove).toHaveBeenCalledWith('widget-1');
    expect(access.sendSms).toHaveBeenCalledTimes(1);
  });

  it.each(['expired-callback', 'timeout-callback', 'error-callback'] as const)('clears a token after %s and offers a visible retry', async callback => {
    open(); phone(); await verify();
    act(() => { if (callback === 'error-callback') renders[0][callback]('200500'); else renders[0][callback](); });
    expect(screen.getByRole('button', { name: '获取验证码' })).toBeDisabled();
    expect(screen.getByRole('alert')).toHaveTextContent(callback === 'error-callback' ? '未能加载或未通过' : '已过期');
    fireEvent.click(screen.getByRole('button', { name: '重新加载人机验证' }));
    await waitFor(() => expect(renders.length).toBe(2));
    expect(api.remove).toHaveBeenCalledWith('widget-1');
    expect(access.sendSms).not.toHaveBeenCalled();
  });

  it('invalidates a previous phone token and ignores callbacks from a removed widget', async () => {
    open(); phone(); await verify('old-phone-token');
    const old = renders[0];
    phone('13900000001');
    await waitFor(() => expect(renders.length).toBe(2));
    act(() => old.callback('late-old-token'));
    expect(screen.getByRole('button', { name: '获取验证码' })).toBeDisabled();
    await verify('new-phone-token');
    fireEvent.click(screen.getByRole('button', { name: '获取验证码' }));
    await screen.findByText('验证码已发送，请查看手机短信。');
    expect(access.sendSms).toHaveBeenCalledExactlyOnceWith('13900000001', 'login', 'new-phone-token');
  });

  it('keeps password login usable without CAPTCHA and unmounts the SMS widget', async () => {
    open(); phone(); await verify();
    fireEvent.click(screen.getByRole('button', { name: '原账号密码登录' }));
    expect(screen.queryByRole('region', { name: '人机验证' })).not.toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('姓名'), { target: { value: '原用户' } });
    fireEvent.change(screen.getByLabelText('密码'), { target: { value: 'test-password' } });
    fireEvent.submit(screen.getByRole('button', { name: '进入工作台' }).closest('form')!);
    await waitFor(() => expect(access.login).toHaveBeenCalledExactlyOnceWith('原用户', 'test-password'));
    expect(access.sendSms).not.toHaveBeenCalled();
  });

  it('protects binding sends without creating a new account', async () => {
    access.loggedIn = true; access.user = { id: 31, name: '原用户', status: 'approved', phoneBound: false };
    open('/access?bind=phone'); phone(); await verify();
    fireEvent.click(screen.getByRole('button', { name: '获取验证码' }));
    await screen.findByText('验证码已发送，请查看手机短信。');
    expect(access.sendSms).toHaveBeenCalledExactlyOnceWith('13900000000', 'bind', 'test-only-verification-token');
    expect(access.register).not.toHaveBeenCalled(); expect(access.smsLogin).not.toHaveBeenCalled();
  });

  it('protects approval registration with the same visible widget', async () => {
    access.registrationMode = 'approval'; open('/access?mode=register');
    expect(screen.getByRole('button', { name: '提交管理员审核' })).toBeDisabled();
    fireEvent.change(screen.getByLabelText('姓名'), { target: { value: '研究用户' } });
    fireEvent.change(screen.getByLabelText('密码'), { target: { value: 'test-password' } });
    await verify();
    fireEvent.submit(screen.getByRole('button', { name: '提交管理员审核' }).closest('form')!);
    await waitFor(() => expect(access.register).toHaveBeenCalledExactlyOnceWith('研究用户', 'test-password', 'test-only-verification-token'));
    expect(access.sendSms).not.toHaveBeenCalled();
  });

  it('fails closed with an explicit missing-site-key message instead of hiding the widget', () => {
    access.captcha!.siteKey = null; open(); phone();
    expect(screen.getByRole('alert')).toHaveTextContent('本站人机验证配置暂未就绪');
    expect(screen.getByRole('button', { name: '获取验证码' })).toBeDisabled();
    expect(loadTurnstile).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: '原账号密码登录' }));
    expect(screen.getByRole('button', { name: '进入工作台' })).toBeEnabled();
  });

  it('supports explicitly disabled verification without loading the external script', async () => {
    access.captcha!.required = false; open(); phone();
    fireEvent.click(screen.getByRole('button', { name: '获取验证码' }));
    await screen.findByText('验证码已发送，请查看手机短信。');
    expect(access.sendSms).toHaveBeenCalledExactlyOnceWith('13900000000', 'login');
    expect(loadTurnstile).not.toHaveBeenCalled();
  });

  it('uses a compact widget when a narrow phone cannot fit the 300px minimum', async () => {
    vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockReturnValue({ width: 280, height: 65, top: 0, bottom: 65, left: 0, right: 280, x: 0, y: 0, toJSON: () => ({}) });
    open(); phone(); await waitFor(() => expect(renders.length).toBe(1));
    expect(renders[0].size).toBe('compact');
  });

  it('reports a blocked or failed script with a usable retry and does not send SMS', async () => {
    vi.mocked(loadTurnstile).mockRejectedValueOnce(new Error('script blocked'));
    open(); phone();
    expect(await screen.findByRole('alert')).toHaveTextContent('人机验证未能加载或未通过');
    expect(screen.getByRole('button', { name: '获取验证码' })).toBeDisabled();
    fireEvent.click(screen.getByRole('button', { name: '重新加载人机验证' }));
    await waitFor(() => expect(renders.length).toBe(1));
    expect(access.sendSms).not.toHaveBeenCalled();
  });
});
