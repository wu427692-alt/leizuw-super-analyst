import apiClient from './index';

export type FrontUser = {
  id: number;
  name: string;
  status: string;
  authMethod?: string;
  phoneBound?: boolean;
  phoneMasked?: string | null;
};

export type UserAccessStatus = {
  accessEnabled: boolean;
  loggedIn: boolean;
  user: FrontUser | null;
  authMethod?: string | null;
  registrationMode?: 'approval' | 'self_service';
  smsLoginEnabled?: boolean;
};

export type SmsChallenge = { challengeId: string; expiresIn: number; retryAfter: number };
export type SmsVerification = { phone: string; code: string; challengeId: string };
export type SmsPhoneBinding = SmsVerification & { currentPassword: string };

export const userAuthApi = {
  status: async () => (await apiClient.get<UserAccessStatus>('/api/v1/user-auth/status')).data,
  register: async (name: string, password: string) => (
    await apiClient.post('/api/v1/user-auth/register', { name, password })
  ).data,
  login: async (name: string, password: string) => (
    await apiClient.post('/api/v1/user-auth/login', { name, password })
  ).data,
  sendSms: async (phone: string, purpose: 'login' | 'bind') => (
    await apiClient.post<SmsChallenge>('/api/v1/user-auth/sms/send', { phone, purpose })
  ).data,
  smsLogin: async (payload: SmsVerification & { name?: string }) => (
    await apiClient.post('/api/v1/user-auth/sms/login', payload)
  ).data,
  bindPhone: async (payload: SmsPhoneBinding) => (
    await apiClient.post('/api/v1/user-auth/sms/bind', payload)
  ).data,
  logout: async () => apiClient.post('/api/v1/user-auth/logout'),
};
