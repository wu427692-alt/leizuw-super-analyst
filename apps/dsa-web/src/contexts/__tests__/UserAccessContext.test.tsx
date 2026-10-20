import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { UserAccessProvider, useUserAccess } from '../UserAccessContext';
import { getChatOwnerScope, useAgentChatStore } from '../../stores/agentChatStore';
import type { UserAccessStatus } from '../../api/userAuth';
import { clearRequestCache } from '../../api/requestCache';

const { status, login, logout, smsLogin, sendSms, bindPhone } = vi.hoisted(() => ({ status: vi.fn(), login: vi.fn(), logout: vi.fn(), smsLogin: vi.fn(), sendSms: vi.fn(), bindPhone: vi.fn() }));
vi.mock('../../api/userAuth', () => ({ userAuthApi: { status, login, logout, smsLogin, sendSms, bindPhone, register: vi.fn() } }));
vi.mock('../../api/requestCache', () => ({ clearRequestCache: vi.fn() }));
vi.mock('../../api/agent', () => ({ agentApi: { getChatSessions: vi.fn(async () => []) } }));
const approved = (id: number): UserAccessStatus => ({ accessEnabled: true, loggedIn: true,
  user: { id, name: `用户${id}`, status: 'approved' }, authMethod: 'session' });
function Probe() {
  const access = useUserAccess();
  const messages = useAgentChatStore(state => state.messages);
  return <><span>{access.user?.name || '未登录'}:{messages.map(m => m.content).join(',')}</span>
    <button onClick={() => void access.refresh()}>refresh</button>
    <button onClick={() => void access.login('next', 'test-password')}>login</button>
    <button onClick={() => void access.smsLogin({ phone: '13900000000', code: '123456', challengeId: 'challenge' })}>smsLogin</button>
    <button onClick={() => void access.bindPhone({ phone: '13900000000', code: '123456', challengeId: 'challenge', currentPassword: 'test-password' })}>bindPhone</button>
    <span>{access.registrationMode}:{String(access.smsLoginEnabled)}</span>
    <span aria-label="登录方式读取状态">{access.isLoading ? 'loading' : access.statusUnavailable ? 'unavailable' : 'ready'}</span>
    <button onClick={() => void access.logout()}>logout</button></>;
}
beforeEach(() => {
  vi.resetAllMocks(); localStorage.clear();
  useAgentChatStore.getState().setOwnerScope(null);
});
describe('UserAccessProvider chat identity integration', () => {
  it('reports failed policy reads separately from approval and clears that state after a successful retry', async () => {
    status.mockRejectedValueOnce(new Error('network unavailable')).mockResolvedValueOnce({ accessEnabled: true, loggedIn: false, user: null, registrationMode: 'self_service', smsLoginEnabled: true });
    render(<UserAccessProvider><Probe /></UserAccessProvider>);
    await waitFor(() => expect(screen.getByLabelText('登录方式读取状态')).toHaveTextContent('unavailable'));
    expect(screen.getByText('approval:false')).toBeInTheDocument();
    fireEvent.click(screen.getByText('refresh'));
    await waitFor(() => expect(screen.getByLabelText('登录方式读取状态')).toHaveTextContent('ready'));
    expect(screen.getByText('self_service:true')).toBeInTheDocument();
  });
  it('clears private messages and aborts the active stream before publishing another user', async () => {
    status.mockResolvedValueOnce(approved(1)).mockResolvedValueOnce(approved(2));
    render(<UserAccessProvider><Probe /></UserAccessProvider>);
    await screen.findByText('用户1:');
    const controller = new AbortController();
    act(() => useAgentChatStore.setState({ messages: [{ id: 'secret', role: 'assistant', content: 'private-1' }], loading: true, abortController: controller }));
    fireEvent.click(screen.getByText('refresh'));
    await screen.findByText('用户2:');
    expect(controller.signal.aborted).toBe(true);
    expect(screen.queryByText(/private-1/)).not.toBeInTheDocument();
    expect(useAgentChatStore.getState().ownerScope).toBe('user:2');
  });
  it('does not let a pre-login status overwrite a newer authenticated identity', async () => {
    let resolve!: (value: UserAccessStatus) => void;
    status.mockReturnValueOnce(new Promise<UserAccessStatus>(r => { resolve = r; })).mockResolvedValueOnce(approved(2));
    login.mockResolvedValue({ success: true });
    render(<UserAccessProvider><Probe /></UserAccessProvider>);
    fireEvent.click(screen.getByText('login'));
    await screen.findByText('用户2:');
    await act(async () => resolve(approved(1)));
    expect(screen.getByText('用户2:')).toBeInTheDocument();
    expect(useAgentChatStore.getState().ownerScope).toBe('user:2');
  });
  it('clears chat after logout while preserving only separately scoped opaque handles', async () => {
    status.mockResolvedValue(approved(1)); logout.mockResolvedValue({});
    render(<UserAccessProvider><Probe /></UserAccessProvider>);
    await screen.findByText('用户1:');
    act(() => useAgentChatStore.getState().startNewChat());
    const handle = useAgentChatStore.getState().sessionId;
    fireEvent.click(screen.getByText('logout'));
    await waitFor(() => expect(useAgentChatStore.getState().ownerScope).toBeNull());
    expect(localStorage.getItem('dsa:chat:v2:user:1:session')).toBe(handle);
    expect(useAgentChatStore.getState().messages).toEqual([]);
  });
  it('separates local/admin/approved users and never trusts a pending identity', () => {
    expect(getChatOwnerScope({ accessEnabled: false, loggedIn: false, user: null })).toBe('local');
    expect(getChatOwnerScope({ ...approved(0), authMethod: 'admin' })).toBe('admin');
    expect(getChatOwnerScope({ ...approved(1), user: { id: 1, name: 'pending', status: 'pending' } })).toBeNull();
    expect(getChatOwnerScope({ ...approved(1), loggedIn: false })).toBeNull();
  });

  it('resets private chat and request caches on SMS login just like password login', async () => {
    status.mockResolvedValueOnce(approved(1)).mockResolvedValueOnce({ ...approved(2), registrationMode: 'self_service', smsLoginEnabled: true });
    smsLogin.mockResolvedValue({ success: true });
    render(<UserAccessProvider><Probe /></UserAccessProvider>);
    await screen.findByText('用户1:');
    const controller = new AbortController();
    act(() => useAgentChatStore.setState({ messages: [{ id: 'old', role: 'assistant', content: 'private-old' }], loading: true, abortController: controller }));
    fireEvent.click(screen.getByText('smsLogin'));
    await screen.findByText('用户2:');
    expect(smsLogin).toHaveBeenCalledWith({ phone: '13900000000', code: '123456', challengeId: 'challenge' });
    expect(controller.signal.aborted).toBe(true);
    expect(clearRequestCache).toHaveBeenCalledOnce();
    expect(screen.getByText('self_service:true')).toBeInTheDocument();
    expect(useAgentChatStore.getState().ownerScope).toBe('user:2');
  });

  it('binding refreshes the same identity without discarding its private conversation', async () => {
    status.mockResolvedValueOnce(approved(1)).mockResolvedValueOnce({ ...approved(1), user: { ...approved(1).user!, phoneBound: true, phoneMasked: '139****0000' } });
    bindPhone.mockResolvedValue({ success: true });
    render(<UserAccessProvider><Probe /></UserAccessProvider>);
    await screen.findByText('用户1:');
    act(() => useAgentChatStore.setState({ messages: [{ id: 'old', role: 'assistant', content: 'keep-own-notes' }] }));
    fireEvent.click(screen.getByText('bindPhone'));
    await waitFor(() => expect(status).toHaveBeenCalledTimes(2));
    expect(useAgentChatStore.getState().ownerScope).toBe('user:1');
    expect(screen.getByText('用户1:keep-own-notes')).toBeInTheDocument();
    expect(clearRequestCache).not.toHaveBeenCalled();
  });
});
