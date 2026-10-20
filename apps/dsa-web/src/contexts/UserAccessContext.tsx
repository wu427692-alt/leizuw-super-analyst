import type React from 'react';
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react';
import { getParsedApiError, type ParsedApiError } from '../api/error';
import { userAuthApi, type FrontUser, type SmsChallenge, type SmsVerification, type SmsPhoneBinding } from '../api/userAuth';
import { clearRequestCache } from '../api/requestCache';
import { getChatOwnerScope, useAgentChatStore } from '../stores/agentChatStore';

type ActionResult = { success: boolean; pending?: boolean; error?: ParsedApiError };
type SmsActionResult = ActionResult & { challenge?: SmsChallenge };

type UserAccessContextValue = {
  accessEnabled: boolean;
  loggedIn: boolean;
  user: FrontUser | null;
  authMethod: string | null;
  isLoading: boolean;
  statusUnavailable?: boolean;
  registrationMode: 'approval' | 'self_service';
  smsLoginEnabled: boolean;
  register: (name: string, password: string) => Promise<ActionResult>;
  login: (name: string, password: string) => Promise<ActionResult>;
  sendSms: (phone: string, purpose: 'login' | 'bind') => Promise<SmsActionResult>;
  smsLogin: (payload: SmsVerification & { name?: string }) => Promise<ActionResult>;
  bindPhone: (payload: SmsPhoneBinding) => Promise<ActionResult>;
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
  const [statusUnavailable, setStatusUnavailable] = useState(false);
  const [registrationMode, setRegistrationMode] = useState<'approval' | 'self_service'>('approval');
  const [smsLoginEnabled, setSmsLoginEnabled] = useState(false);
  const statusRequest = useRef(0);
  const invalidateStatus = useCallback(() => { statusRequest.current++; }, []);

  const refresh = useCallback(async () => {
    const request = ++statusRequest.current;
    try {
      const status = await userAuthApi.status();
      if (request !== statusRequest.current) return;
      // Reset private chat data before React publishes the next identity. This
      // also covers an active stream while the user is on another route.
      useAgentChatStore.getState().setOwnerScope(getChatOwnerScope(status));
      setAccessEnabled(status.accessEnabled);
      setLoggedIn(status.loggedIn);
      setUser(status.user);
      setAuthMethod(status.authMethod ?? null);
      setRegistrationMode(status.registrationMode ?? 'approval');
      setSmsLoginEnabled(status.smsLoginEnabled === true);
      setStatusUnavailable(false);
    } catch {
      if (request !== statusRequest.current) return;
      // A short backend restart or tunnel interruption should not create an
      // unhandled rejection or break the application shell.
      useAgentChatStore.getState().setOwnerScope(null);
      setLoggedIn(false);
      setUser(null);
      setAuthMethod(null);
      setSmsLoginEnabled(false);
      setStatusUnavailable(true);
    } finally {
      if (request === statusRequest.current) setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
    return invalidateStatus;
  }, [refresh, invalidateStatus]);

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
      statusRequest.current++;
      useAgentChatStore.getState().setOwnerScope(null);
      clearRequestCache();
      await refresh();
      return { success: true };
    } catch (error) {
      return { success: false, error: getParsedApiError(error) };
    }
  }, [refresh]);

  const sendSms = useCallback(async (phone: string, purpose: 'login' | 'bind'): Promise<SmsActionResult> => {
    try { return { success: true, challenge: await userAuthApi.sendSms(phone, purpose) }; }
    catch (error) { return { success: false, error: getParsedApiError(error) }; }
  }, []);

  const smsLogin = useCallback(async (payload: SmsVerification & { name?: string }): Promise<ActionResult> => {
    try {
      await userAuthApi.smsLogin(payload);
      statusRequest.current++;
      useAgentChatStore.getState().setOwnerScope(null);
      clearRequestCache();
      await refresh();
      return { success: true };
    } catch (error) { return { success: false, error: getParsedApiError(error) }; }
  }, [refresh]);

  const bindPhone = useCallback(async (payload: SmsPhoneBinding): Promise<ActionResult> => {
    try {
      await userAuthApi.bindPhone(payload);
      await refresh();
      return { success: true };
    } catch (error) { return { success: false, error: getParsedApiError(error) }; }
  }, [refresh]);

  const logout = useCallback(async () => {
    await userAuthApi.logout();
    statusRequest.current++;
    useAgentChatStore.getState().setOwnerScope(null);
    clearRequestCache();
    setLoggedIn(false);
    setUser(null);
    setAuthMethod(null);
    setIsLoading(false);
  }, []);

  const value = useMemo(() => ({
    accessEnabled, loggedIn, user, authMethod, isLoading, statusUnavailable, registrationMode, smsLoginEnabled,
    register, login, sendSms, smsLogin, bindPhone, logout, refresh,
  }), [accessEnabled, loggedIn, user, authMethod, isLoading, statusUnavailable, registrationMode, smsLoginEnabled, register, login, sendSms, smsLogin, bindPhone, logout, refresh]);

  return <UserAccessContext.Provider value={value}>{children}</UserAccessContext.Provider>;
}

// eslint-disable-next-line react-refresh/only-export-components -- hook is intentionally co-located with its provider
export function useUserAccess() {
  const value = useContext(UserAccessContext);
  if (!value) throw new Error('useUserAccess must be used inside UserAccessProvider');
  return value;
}
