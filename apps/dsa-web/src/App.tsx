import type React from 'react';
import { lazy, useEffect } from 'react';
import { BrowserRouter as Router, Navigate, Route, Routes, useLocation } from 'react-router-dom';
import { Shell } from './components/common';
import {
  PageLoadingFallback,
  RouteOutletBoundary,
  StandaloneRouteBoundary,
} from './components/layout/RouteBoundary';
import { AuthProvider, useAuth } from './contexts/AuthContext';
import { UiLanguageProvider } from './contexts/UiLanguageContext';
import { useAgentChatStore } from './stores/agentChatStore';
import { WEB_BUILD_INFO } from './utils/constants';
import { chunkRetryKey, isChunkLoadError, reloadWithFreshFrontend } from './utils/chunkRecovery';
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
const BacktestPage = lazyRoute(() => import('./pages/BacktestPage'), 'backtest');
const SettingsPage = lazyRoute(() => import('./pages/SettingsPage'), 'settings');
const LoginPage = lazyRoute(() => import('./pages/LoginPage'), 'login');
const NotFoundPage = lazyRoute(() => import('./pages/NotFoundPage'), 'not-found');
const ChatPage = lazyRoute(() => import('./pages/ChatPage'), 'chat');
const PortfolioPage = lazyRoute(() => import('./pages/PortfolioPage'), 'portfolio');
const DecisionSignalsPage = lazyRoute(() => import('./pages/DecisionSignalsPage'), 'signals');
const AlertsPage = lazyRoute(() => import('./pages/AlertsPage'), 'alerts');
const TokenUsagePage = lazyRoute(() => import('./pages/TokenUsagePage'), 'usage');
const StockScreeningPage = lazyRoute(() => import('./pages/StockScreeningPage'), 'screening');
const EssayRadarPage = lazyRoute(() => import('./pages/EssayRadarPage'), 'essay-radar');
const EssayQuantPage = lazyRoute(() => import('./pages/EssayQuantPage'), 'essay-quant');
const InvestmentMonitorPage = lazyRoute(() => import('./pages/InvestmentMonitorPage'), 'monitor-feed');
const InvestmentMonitorOverviewPage = lazyRoute(() => import('./pages/InvestmentMonitorOverviewPage'), 'monitor-overview');
const InvestmentMonitorBIPage = lazyRoute(() => import('./pages/InvestmentMonitorBIPage'), 'monitor-bi');
const SuperWatchlistPage = lazyRoute(() => import('./pages/SuperWatchlistPage'), 'super-watchlist');
const DataAcquisitionPage = lazyRoute(() => import('./pages/DataAcquisitionPage'), 'data-acquisition');
const DragonTigerPage = lazyRoute(() => import('./pages/DragonTigerPage'), 'dragon-tiger');

// The desktop build serves these files locally. Loading route chunks after the
// first paint removes the fragile "click, then fetch component" gap without
// delaying the initial server health check or first screen.
const ROUTE_PRELOADERS = [
  () => import('./pages/MarketDashboardPage'),
  () => import('./pages/BacktestPage'),
  () => import('./pages/SettingsPage'),
  () => import('./pages/ChatPage'),
  () => import('./pages/PortfolioPage'),
  () => import('./pages/DecisionSignalsPage'),
  () => import('./pages/AlertsPage'),
  () => import('./pages/TokenUsagePage'),
  () => import('./pages/StockScreeningPage'),
  () => import('./pages/EssayRadarPage'),
  () => import('./pages/EssayQuantPage'),
  () => import('./pages/InvestmentMonitorPage'),
  () => import('./pages/InvestmentMonitorOverviewPage'),
  () => import('./pages/InvestmentMonitorBIPage'),
  () => import('./pages/SuperWatchlistPage'),
  () => import('./pages/DataAcquisitionPage'),
  () => import('./pages/DragonTigerPage'),
];

const AppContent: React.FC = () => {
  const location = useLocation();
  const { authEnabled, loggedIn, isLoading } = useAuth();

  useEffect(() => {
    useAgentChatStore.getState().setCurrentRoute(location.pathname);
  }, [location.pathname]);

  if (isLoading) {
    return <PageLoadingFallback />;
  }

  if (authEnabled && !loggedIn) {
    if (location.pathname === '/login') {
      return (
        <StandaloneRouteBoundary>
          <LoginPage />
        </StandaloneRouteBoundary>
      );
    }
    const redirect = encodeURIComponent(location.pathname + location.search);
    return <Navigate to={`/login?redirect=${redirect}`} replace />;
  }

  if (location.pathname === '/login') {
    return <Navigate to="/" replace />;
  }

  return (
    <Routes>
      <Route
        element={(
          <Shell>
            <RouteOutletBoundary />
          </Shell>
        )}
      >
        <Route path="/" element={<HomePage />} />
        <Route path="/chat" element={<ChatPage />} />
        <Route path="/portfolio" element={<PortfolioPage />} />
        <Route path="/decision-signals" element={<DecisionSignalsPage />} />
        <Route path="/screening" element={<StockScreeningPage />} />
        <Route path="/essay-radar" element={<EssayRadarPage />} />
        <Route path="/essay-radar/insights" element={<EssayRadarPage />} />
        <Route path="/essay-radar/feed" element={<EssayRadarPage />} />
        <Route path="/essay-radar/trends" element={<EssayRadarPage />} />
        <Route path="/essay-radar/reports" element={<EssayRadarPage />} />
        <Route path="/essay-radar/system" element={<EssayRadarPage />} />
        <Route path="/essay-quant/*" element={<EssayQuantPage />} />
        <Route path="/investment-monitor" element={<InvestmentMonitorOverviewPage />} />
        <Route path="/investment-monitor/bi" element={<InvestmentMonitorBIPage />} />
        <Route path="/investment-monitor/feed" element={<InvestmentMonitorPage />} />
        <Route path="/investment-monitor/dragon-tiger" element={<DragonTigerPage />} />
        <Route path="/investment-monitor/watchlist" element={<Navigate to="/super-watchlist" replace />} />
        <Route path="/super-watchlist" element={<SuperWatchlistPage />} />
        <Route path="/investment-monitor/market" element={<Navigate to="/investment-monitor" replace />} />
        <Route path="/investment-monitor/company" element={<Navigate to="/investment-monitor" replace />} />
        <Route path="/investment-monitor/analysis" element={<Navigate to="/investment-monitor" replace />} />
        <Route path="/data-acquisition" element={<DataAcquisitionPage />} />
        <Route path="/backtest" element={<BacktestPage />} />
        <Route path="/alerts" element={<AlertsPage />} />
        <Route path="/usage" element={<TokenUsagePage />} />
        <Route path="/settings" element={<SettingsPage />} />
        <Route path="*" element={<NotFoundPage />} />
      </Route>
    </Routes>
  );
};

const App: React.FC = () => {
  useEffect(() => {
    let cancelled = false;
    let interval: number | null = null;
    let cursor = 0;
    let inFlight = false;
    const preloadNext = async () => {
      if (cancelled || inFlight || cursor >= ROUTE_PRELOADERS.length) return;
      inFlight = true;
      const loader = ROUTE_PRELOADERS[cursor++];
      try { await loader?.(); } catch { /* lazyRoute performs recovery when the route is actually opened */ }
      finally { inFlight = false; }
    };
    const timer = window.setTimeout(() => {
      if (cancelled) return;
      // Warm one route at a time. A simultaneous burst of every route chunk
      // used to compete with the home dashboard and made first clicks fragile.
      void preloadNext();
      interval = window.setInterval(() => void preloadNext(), 450);
    }, 1_500);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
      if (interval != null) window.clearInterval(interval);
    };
  }, []);

  return (
    <UiLanguageProvider>
      <Router>
        <AuthProvider>
          <AppContent />
        </AuthProvider>
      </Router>
    </UiLanguageProvider>
  );
};

export default App;
