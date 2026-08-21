import apiClient from './index';

export type FrontUser = {
  id: number;
  name: string;
  status: string;
  authMethod?: string;
};

export type UserAccessStatus = {
  accessEnabled: boolean;
  loggedIn: boolean;
  user: FrontUser | null;
  authMethod?: string | null;
};

export const userAuthApi = {
  status: async () => (await apiClient.get<UserAccessStatus>('/api/v1/user-auth/status')).data,
  register: async (name: string, password: string) => (
    await apiClient.post('/api/v1/user-auth/register', { name, password })
  ).data,
  login: async (name: string, password: string) => (
    await apiClient.post('/api/v1/user-auth/login', { name, password })
  ).data,
  logout: async () => apiClient.post('/api/v1/user-auth/logout'),
};
