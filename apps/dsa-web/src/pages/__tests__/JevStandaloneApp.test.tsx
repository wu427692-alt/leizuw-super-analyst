import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, useLocation } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { JevStandaloneApp } from '../JevStandaloneApp';
import { getJevReturnTarget, jevDocumentNavigation } from '../../utils/jevAccessNavigation';

const access = { isLoading: false, loggedIn: false, login: vi.fn(), register: vi.fn() };
vi.mock('../../contexts/UserAccessContext', () => ({ useUserAccess: () => access }));
vi.mock('../JevBacktestPage', () => ({ default: () => <div>当前账号回测页</div> }));
function LocationProbe() { const location = useLocation(); return <div aria-label="当前页面地址">{location.pathname}{location.search}</div>; }
function Page({ url = '/jev-backtest?lab=convertible-t&round=sample&task=abc' }: { url?: string }) { return <MemoryRouter initialEntries={[url]}><JevStandaloneApp /><LocationProbe /></MemoryRouter>; }

describe('standalone Jev authentication boundary', () => {
  beforeEach(() => { vi.resetAllMocks(); access.isLoading = false; access.loggedIn = false; vi.spyOn(jevDocumentNavigation, 'replace').mockImplementation(() => {}); });
  afterEach(() => { cleanup(); vi.restoreAllMocks(); });
  it('does not mount the private page while authentication is loading', () => {
    access.isLoading = true; access.loggedIn = true;
    render(<Page />);
    expect(screen.getByRole('status')).toHaveTextContent('正在确认访问权限');
    expect(screen.queryByText('当前账号回测页')).not.toBeInTheDocument();
  });
  it('logs in within the standalone bundle and preserves lab, round and task without a SPA redirect', async () => {
    access.login.mockResolvedValue({ success: true });
    const page = render(<Page />);
    fireEvent.change(screen.getByLabelText('姓名'), { target: { value: '测试用户' } });
    fireEvent.change(screen.getByLabelText('密码'), { target: { value: 'test-password' } });
    fireEvent.click(screen.getByRole('button', { name: '进入工作台' }));
    await waitFor(() => expect(access.login).toHaveBeenCalledWith('测试用户', 'test-password'));
    expect(screen.queryByText('当前账号回测页')).not.toBeInTheDocument();
    access.loggedIn = true; page.rerender(<Page />);
    expect(screen.getByText('当前账号回测页')).toBeInTheDocument();
    expect(screen.getByLabelText('当前页面地址')).toHaveTextContent('/jev-backtest?lab=convertible-t&round=sample&task=abc');
    expect(screen.queryByRole('link', { name: '前往登录' })).not.toBeInTheDocument();
  });
  it('leaves the access document with a full document navigation after login and never mounts its private page', async () => {
    access.login.mockResolvedValue({ success: true });
    const target = '/jev-backtest?lab=convertible-t&round=sample&task=abc';
    const url = '/access?redirect=' + encodeURIComponent(target);
    const page = render(<Page url={url} />);
    fireEvent.change(screen.getByLabelText('姓名'), { target: { value: '测试用户' } });
    fireEvent.change(screen.getByLabelText('密码'), { target: { value: 'test-password' } });
    fireEvent.click(screen.getByRole('button', { name: '进入工作台' }));
    await waitFor(() => expect(access.login).toHaveBeenCalled());
    expect(jevDocumentNavigation.replace).not.toHaveBeenCalled();
    access.loggedIn = true; page.rerender(<Page url={url} />);
    expect(jevDocumentNavigation.replace).toHaveBeenCalledWith(target);
    expect(screen.queryByText('当前账号回测页')).not.toBeInTheDocument();
    expect(screen.getByRole('heading', { name: '正在打开 Jev 交易回测' })).toBeInTheDocument();
  });
  it('hands off an already authenticated access visit without mounting private API consumers', () => {
    access.loggedIn = true;
    render(<Page url={'/access?redirect=' + encodeURIComponent('/jev-backtest/?lab=convertible-t&round=saved')} />);
    expect(jevDocumentNavigation.replace).toHaveBeenCalledWith('/jev-backtest/?lab=convertible-t&round=saved');
    expect(screen.queryByText('当前账号回测页')).not.toBeInTheDocument();
  });
  it('rejects external, protocol-relative, escaped and unrelated redirect targets', () => {
    for (const target of ['https://example.com/jev-backtest', '//example.com/jev-backtest', '/app', '/jev-backtest-extra', '/jev-backtest/other', '/jev-backtest\\evil', '/jev-backtest?x=\nunsafe']) {
      expect(getJevReturnTarget('?redirect=' + encodeURIComponent(target))).toBe('/jev-backtest');
    }
    expect(getJevReturnTarget('?redirect=' + encodeURIComponent('/jev-backtest?round=a%2Fb'))).toBe('/jev-backtest?round=a%2Fb');
  });
  it('keeps rejected logins on the login form without mounting private data', async () => {
    access.login.mockResolvedValue({ success: false, error: { message: '测试会话未获批准' } });
    render(<Page />);
    fireEvent.change(screen.getByLabelText('姓名'), { target: { value: '测试用户' } });
    fireEvent.change(screen.getByLabelText('密码'), { target: { value: 'test-password' } });
    fireEvent.click(screen.getByRole('button', { name: '进入工作台' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('测试会话未获批准');
    expect(screen.queryByText('当前账号回测页')).not.toBeInTheDocument();
  });
  it('uses document links for destinations that belong to the main application bundle', () => {
    render(<Page />);
    const link = screen.getByRole('link', { name: '管理员入口' });
    expect(link).toHaveAttribute('href', '/admin');
    // A document link is not cancelled by React Router to perform client navigation.
    let intercepted: boolean | undefined;
    document.addEventListener('click', event => { intercepted = event.defaultPrevented; event.preventDefault(); }, { once: true });
    fireEvent.click(link);
    expect(intercepted).toBe(false);
    expect(screen.getByLabelText('当前页面地址')).toHaveTextContent('/jev-backtest?');
  });
  it('mounts the shared page only after a successful account check', () => {
    access.loggedIn = true;
    render(<Page />);
    expect(screen.getByText('当前账号回测页')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: '返回首页' })).toHaveAttribute('href', '/app');
  });
});
