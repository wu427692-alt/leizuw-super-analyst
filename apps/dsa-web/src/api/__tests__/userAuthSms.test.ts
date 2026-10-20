import { beforeEach, describe, expect, it, vi } from 'vitest';
import apiClient from '../index';
import { userAuthApi } from '../userAuth';

vi.mock('../index', () => ({ default: { get: vi.fn(), post: vi.fn() } }));
beforeEach(() => { vi.clearAllMocks(); vi.mocked(apiClient.post).mockResolvedValue({ data: { success: true } }); });
describe('SMS account API request contract', () => {
  it('sends phone and explicit purpose without legacy registration', async () => {
    await userAuthApi.sendSms('13900000000', 'bind');
    expect(apiClient.post).toHaveBeenCalledExactlyOnceWith('/api/v1/user-auth/sms/send', { phone: '13900000000', purpose: 'bind' });
  });
  it('keeps the agreed camelCase challenge field for SMS login', async () => {
    const payload = { phone: '13900000000', code: '123456', challengeId: 'challenge', name: '测试用户' };
    await userAuthApi.smsLogin(payload);
    expect(apiClient.post).toHaveBeenCalledExactlyOnceWith('/api/v1/user-auth/sms/login', payload);
  });
  it('requires the original password as well as the SMS challenge to bind a phone', async () => {
    const payload = { phone: '13900000000', code: '123456', challengeId: 'challenge', currentPassword: 'test-password' };
    await userAuthApi.bindPhone(payload);
    expect(apiClient.post).toHaveBeenCalledExactlyOnceWith('/api/v1/user-auth/sms/bind', payload);
  });
});
