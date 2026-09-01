import type React from 'react';
import { lazy, useEffect } from 'react';
import { BrowserRouter as Router, Navigate, Route, Routes, useLocation } from 'react-router-dom';
import { Shell } from './components/common';
import { AdminShell } from './components/admin';
import {
  PageLoadingFallback,
  RouteOutletBoundary,
  StandaloneRouteBoundary,
} from './components/layout/RouteBoundary';
import { AuthProvider, useAuth } from './contexts/AuthContext';
import { UserAccessProvider, useUserAccess } from './contexts/UserAccessContext';
import { UiLanguageProvider } from './contexts/UiLanguageContext';
import { useAgentChatStore } from './stores/agentChatStore';
import { WEB_BUILD_INFO } from './utils/constants';
import { chunkRetryKey, isChunkLoadError, reloadWithFreshFrontend } from './utils/chunkRecovery';
import { canPreloadRoutes, preloadRoute } from './utils/routePreload';
import { preloadRouteData } from './utils/routeDataPreload';
import LandingPage from './pages/LandingPage';
import './App.css';

function lazyRoute(
  loader: () => Promise<{ default: React.ComponentType }>,
  key: string,
) {
  return lazy(async () => {
    const retryKey = chunkRetryKey(key, WEB_BUILD_INFO.buildId);
    try {
      let module: { default: React.ComponentType } | undefined;
      let lastError: unknown;
      for (let attempt = 0; attempt < 3; attempt += 1) {
        try {
          module = await loader();
          break;
        } catch (error) {
          lastError = error;
          if (!isChunkLoadError(error) || attempt === 2) throw error;
          await new Promise((resolve) => window.setTimeout(resolve, 180 * (attempt + 1)));
        }
      }
      if (!module) throw lastError;
      try { window.sessionStorage.removeItem(retryKey); } catch { /* storage is optional */ }
      return module;
    } catch (error) {
      try {
        if (isChunkLoadError(error) && !window.sessionStorage.getItem(retryKey)) {
          window.sessionStorage.setItem(retryKey, '1');
          // A full navigation with a build-specific query forces a fresh
          // no-store index.html instead of reusing the old entry module.
          reloadWithFreshFrontend(WEB_BUILD_INFO.buildId);
          return await new Promise<never>(() => undefined);
        }
      } catch {
        // Storage may be unavailable in hardened browsers; the route boundary
        // below still provides a manual recovery path.
      }
      throw error;
    }
  });
}

const HomePage = lazyRoute(() => import('./pages/MarketDashboardPage'), 'home');
const SettingsPage = lazyRoute(() => import('./pages/SettingsPage'), 'settings');
const LoginPage = lazyRoute(() => import('./pages/LoginPage'), 'login');
const UserAccessPage = lazyRoute(() => import('./pages/UserAccessPage'), 'user-access');
const NotFoundPage = lazyRoute(() => import('./pages/NotFoundPage'), 'not-found');
const ChatPage = lazyRoute(() => import('./pages/ChatPage'), 'chat');
const TokenUsagePage = lazyRoute(() => import('./pages/TokenUsagePage'), 'usage');
const StockScreeningPage = lazyRoute(() => import('./pages/StockScreeningPage'), 'screening');
const EssayRadarPage = lazyRoute(() => import('./pages/EssayRadarPage'), 'essay-radar');
const EssayQuantPage = lazyRoute(() => import('./pages/EssayQuantPage'), 'essay-quant');
const InvestmentMonitorPage = lazyRoute(() => import('./pages/InvestmentMonitorPage'), 'monitor-feed');
const SuperWatchlistPage = lazyRoute(() => import('./pages/SuperWatchlistPage'), 'super-watchlist');
const DataAcquisitionPage = lazyRoute(() => import('./pages/DataAcquisitionPage'), 'data-acquisition');
const IndustryResearchPage = lazyRoute(() => import('./pages/IndustryResearchPage'), 'industry-research');
const ConceptThemesPage = lazyRoute(() => import('./pages/ConceptThemesPage'), 'concept-themes');
const DragonTigerPage = lazyRoute(() => import('./pages/DragonTigerPage'), 'dragon-tiger');
const AdminConsolePage = lazyRoute(() => import('./pages/AdminConsolePage'), 'admin-console');
const UserGuidePage = lazyRoute(() => import('./pages/UserGuidePage'), 'user-guide');
const TasksHubPage = lazyRoute(() => import('./pages/TasksHubPage'), 'tasks-hub');

const APP_ROUTE_PRELOAD_PATHS = [
  '/super-watchlist',
  '/concept-themes',
  '/industry-research',
  '/tasks',
  '/essay-radar',
];

/**
 * Warm only the next useful pages after the current screen is fully usable.
 *
 * The old global preloader fetched every route (including the large admin and
 * settings bundles) from 1.5 seconds after first paint. On a Cloudflare Tunnel
 * that burst competed with live quotes and dashboard APIs. The public landing
 * page now warms only the dashboard; application pages warm a small priority
 * set slowly and only on a healthy foreground connection.
 */
const RoutePreloadController = () => {
  const location = useLocation();

  useEffect(() => {
    if (!canPreloadRoutes()) return undefined;

    let cancelled = false;
    let interval: number | null = null;
    let cursor = 0;
    let inFlight = false;
    const preloadPaths = location.pathname === '/'
      ? ['/app']
      : location.pathname === '/app'
        ? APP_ROUTE_PRELOAD_PATHS
        : ['/app'];
    const preloadNext = async () => {
      if (cancelled || inFlight || cursor >= preloadPaths.length || document.visibilityState === 'hidden') return;
      inFlight = true;
      const path = preloadPaths[cursor++];
      try { await preloadRoute(path ?? '/app'); } catch { /* lazyRoute performs recovery when the route is actually opened */ }
      finally { inFlight = false; }
    };
    const timer = window.setTimeout(() => {
      if (cancelled) return;
      void preloadNext();
      if (preloadPaths.length > 1) {
        interval = window.setInterval(() => void preloadNext(), 2_500);
      }
    }, location.pathname === '/' ? 2_500 : 2_000);

    return () => {
      cancelled = true;
      window.clearTimeout(timer);
      if (interval != null) window.clearInterval(interval);
    };
  }, [location.pathname]);

  return null;
};

/** Warm one page at a time after authentication and first paint. */
const RouteDataWarmupController = () => {
  useEffect(() => {
    if (!canPreloadRoutes()) return undefined;
    const candidates = APP_ROUTE_PRELOAD_PATHS;
    let cancelled = false;
    let cursor = 0;
    let timer: number | undefined;

    const warmNext = async () => {
      if (cancelled || document.visibilityState === 'hidden' || cursor >= candidates.length) return;
      const path = candidates[cursor++];
      try { await preloadRouteData(path ?? '/app'); } catch { /* the page owns its retry UI */ }
      if (!cancelled && cursor < candidates.length) timer = window.setTimeout(() => void warmNext(), 3_500);
    };
    timer = window.setTimeout(() => void warmNext(), 3_500);
    return () => {
      cancelled = true;
      if (timer !== undefined) window.clearTimeout(timer);
    };
  }, []);

  return null;
};

const AppContent: React.FC = () => {
  const location = useLocation();
  const { authEnabled, loggedIn, isLoading } = useAuth();
  const userAccess = useUserAccess();
  const isAdminPath = location.pathname === '/admin' || location.pathname.startsWith('/admin/');
  const isAdminLogin = location.pathname === '/admin/login';

  useEffect(() => {
    useAgentChatStore.getState().setCurrentRoute(location.pathname);
  }, [location.pathname]);

  if (isAdminPath && isLoading) {
    return <PageLoadingFallback />;
  }

  if (!isAdminPath && userAccess.isLoading) {
    return <PageLoadingFallback />;
  }

  if (!isAdminPath && location.pathname !== '/access' && !userAccess.loggedIn) {
    const redirect = encodeURIComponent(location.pathname + location.search);
    return <Navigate to={`/access?redirect=${redirect}`} replace />;
  }

  if (location.pathname === '/access') {
    if (userAccess.loggedIn) {
      const redirect = new URLSearchParams(location.search).get('redirect');
      return <Navigate to={redirect?.startsWith('/') ? redirect : '/app'} replace />;
    }
    return <StandaloneRouteBoundary><UserAccessPage /></StandaloneRouteBoundary>;
  }

  if (isAdminPath && !isAdminLogin && (!authEnabled || !loggedIn)) {
    const redirect = encodeURIComponent(location.pathname + location.search);
    return <Navigate to={`/admin/login?redirect=${redirect}`} replace />;
  }

  if (isAdminLogin) {
    if (authEnabled && loggedIn) {
      return <Navigate to="/admin" replace />;
    }
    return (
      <StandaloneRouteBoundary>
        <LoginPage />
      </StandaloneRouteBoundary>
    );
  }

  if (location.pathname === '/login') {
    if (!authEnabled || !loggedIn) {
      return (
        <Navigate to="/admin/login" replace />
      );
    }
    return <Navigate to="/admin" replace />;
  }

  return (
    <>
      <RouteDataWarmupController />
      <Routes>
      <Route
        element={(
          <Shell>
            <RouteOutletBoundary />
          </Shell>
        )}
      >
        <Route path="/app" element={<HomePage />} />
        <Route path="/dashboard" element={<Navigate to="/app" replace />} />
        <Route path="/chat" element={<ChatPage />} />
        <Route path="/research-center" element={<Navigate to="/super-watchlist?view=decision" replace />} />
        <Route path="/industry-research" element={<IndustryResearchPage />} />
        <Route path="/concept-themes" element={<ConceptThemesPage />} />
        <Route path="/tasks" element={<TasksHubPage />} />
        <Route path="/portfolio" element={<Navigate to="/app" replace />} />
        <Route path="/decision-signals" element={<Navigate to="/app" replace />} />
        <Route path="/screening" element={<StockScreeningPage />} />
        <Route path="/essay-radar" element={<EssayRadarPage />} />
        <Route path="/essay-radar/insights" element={<EssayRadarPage />} />
        <Route path="/essay-radar/feed" element={<EssayRadarPage />} />
        <Route path="/essay-radar/trends" element={<EssayRadarPage />} />
        <Route path="/essay-radar/reports" element={<EssayRadarPage />} />
        <Route path="/essay-radar/system" element={<EssayRadarPage />} />
        <Route path="/essay-quant/*" element={<EssayQuantPage />} />
        <Route path="/investment-monitor" element={<Navigate to="/admin/data-sources" replace />} />
        <Route path="/investment-monitor/bi" element={<Navigate to="/admin/data-sources" replace />} />
        <Route path="/investment-monitor/feed" element={<InvestmentMonitorPage />} />
        <Route path="/investment-monitor/dragon-tiger" element={<DragonTigerPage />} />
        <Route path="/investment-monitor/watchlist" element={<Navigate to="/super-watchlist" replace />} />
        <Route path="/super-watchlist" element={<SuperWatchlistPage />} />
        <Route path="/investment-monitor/market" element={<Navigate to="/investment-monitor" replace />} />
        <Route path="/investment-monitor/company" element={<Navigate to="/investment-monitor" replace />} />
        <Route path="/investment-monitor/analysis" element={<Navigate to="/investment-monitor" replace />} />
        <Route path="/data-acquisition" element={<DataAcquisitionPage />} />
        <Route path="/backtest" element={<Navigate to="/app" replace />} />
        <Route path="/alerts" element={<Navigate to="/app" replace />} />
        <Route path="/usage" element={<Navigate to="/admin/usage" replace />} />
        <Route path="/settings" element={<Navigate to="/admin/settings" replace />} />
        <Route path="*" element={<NotFoundPage />} />
      </Route>
      <Route
        element={(
          <AdminShell>
            <RouteOutletBoundary />
          </AdminShell>
        )}
      >
        <Route path="/admin" element={<AdminConsolePage />} />
        <Route path="/admin/data-sources" element={<AdminConsolePage />} />
        <Route path="/admin/sync" element={<AdminConsolePage />} />
        <Route path="/admin/access" element={<AdminConsolePage />} />
        <Route path="/admin/api-models" element={<SettingsPage />} />
        <Route path="/admin/usage" element={<TokenUsagePage />} />
        <Route path="/admin/settings" element={<SettingsPage />} />
      </Route>
      </Routes>
    </>
  );
};

const RoutedApplication: React.FC = () => {
  const location = useLocation();
  const needsAdminAuth = location.pathname === '/admin' || location.pathname.startsWith('/admin/');

  return (
    <>
      <RoutePreloadController />
      {location.pathname === '/' || location.pathname === '/guide' ? (
        <StandaloneRouteBoundary>
          {location.pathname === '/guide' ? <UserGuidePage /> : <LandingPage />}
        </StandaloneRouteBoundary>
      ) : (
        <AuthProvider initialize={needsAdminAuth}>
          <UserAccessProvider><AppContent /></UserAccessProvider>
        </AuthProvider>
      )}
    </>
  );
};

const App: React.FC = () => {
  return (
    <UiLanguageProvider>
      <Router>
        <RoutedApplication />
      </Router>
    </UiLanguageProvider>
  );
};

export default App;
