import type React from 'react';
import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react';
import { getParsedApiError, type ParsedApiError } from '../api/error';
import { userAuthApi, type FrontUser } from '../api/userAuth';
import { clearRequestCache } from '../api/requestCache';

type ActionResult = { success: boolean; pending?: boolean; error?: ParsedApiError };

type UserAccessContextValue = {
  accessEnabled: boolean;
  loggedIn: boolean;
  user: FrontUser | null;
  authMethod: string | null;
  isLoading: boolean;
  register: (name: string, password: string) => Promise<ActionResult>;
  login: (name: string, password: string) => Promise<ActionResult>;
  logout: () => Promise<void>;
  refresh: () => Promise<void>;
};

const UserAccessContext = createContext<UserAccessContextValue | null>(null);

export function UserAccessProvider({ children }: { children: React.ReactNode }) {
  const [accessEnabled, setAccessEnabled] = useState(true);
  const [loggedIn, setLoggedIn] = useState(false);
  const [user, setUser] = useState<FrontUser | null>(null);
  const [authMethod, setAuthMethod] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);

  const refresh = useCallback(async () => {
    try {
      const status = await userAuthApi.status();
      setAccessEnabled(status.accessEnabled);
      setLoggedIn(status.loggedIn);
      setUser(status.user);
      setAuthMethod(status.authMethod ?? null);
    } catch {
      // A short backend restart or tunnel interruption should not create an
      // unhandled rejection or break the application shell.
      setLoggedIn(false);
      setUser(null);
      setAuthMethod(null);
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => { void refresh(); }, [refresh]);

  const register = useCallback(async (name: string, password: string): Promise<ActionResult> => {
    try {
      await userAuthApi.register(name, password);
      return { success: true, pending: true };
    } catch (error) {
      return { success: false, error: getParsedApiError(error) };
    }
  }, []);

  const login = useCallback(async (name: string, password: string): Promise<ActionResult> => {
    try {
      await userAuthApi.login(name, password);
      clearRequestCache();
      await refresh();
      return { success: true };
    } catch (error) {
      return { success: false, error: getParsedApiError(error) };
    }
  }, [refresh]);

  const logout = useCallback(async () => {
    await userAuthApi.logout();
    clearRequestCache();
    setLoggedIn(false);
    setUser(null);
    setAuthMethod(null);
  }, []);

  const value = useMemo(() => ({
    accessEnabled, loggedIn, user, authMethod, isLoading, register, login, logout, refresh,
  }), [accessEnabled, loggedIn, user, authMethod, isLoading, register, login, logout, refresh]);

  return <UserAccessContext.Provider value={value}>{children}</UserAccessContext.Provider>;
}

// eslint-disable-next-line react-refresh/only-export-components -- hook is intentionally co-located with its provider
export function useUserAccess() {
  const value = useContext(UserAccessContext);
  if (!value) throw new Error('useUserAccess must be used inside UserAccessProvider');
  return value;
}
