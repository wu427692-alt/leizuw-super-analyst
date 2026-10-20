import type React from 'react';
import { Component, Suspense, useEffect, useLayoutEffect, useRef, useState } from 'react';
import type { ErrorInfo } from 'react';
import { LoaderCircle } from 'lucide-react';
import { Outlet, useLocation } from 'react-router-dom';
import { useUiLanguage } from '../../contexts/UiLanguageContext';
import { WEB_BUILD_INFO } from '../../utils/constants';
import { clearChunkRetryMarkers, isChunkLoadError, reloadWithFreshFrontend } from '../../utils/chunkRecovery';
import { beginRouteLoad, finishRouteLoad, getRouteLoadSnapshot } from '../../utils/routeLoadTracker';

type PageLoadingFallbackProps = {
  fullPage?: boolean;
  status?: string;
  detail?: string;
};

export const PageLoadingFallback: React.FC<PageLoadingFallbackProps> = ({
  fullPage = true,
  status = '正在加载页面组件',
  detail = '页面打开后，各模块会分别读取数据。',
}) => (
  <div
    className={
      fullPage
        ? 'flex min-h-screen items-center justify-center bg-base'
        : 'flex min-h-[420px] items-center justify-center px-4'
    }
    role="status"
    aria-label={status}
  >
    <div className="flex w-full max-w-xl items-start gap-3 border border-border/70 bg-card/85 p-5 backdrop-blur-sm">
      <LoaderCircle className="mt-0.5 h-5 w-5 shrink-0 text-[hsl(var(--primary))] motion-safe:animate-spin" aria-hidden="true" />
      <div><p className="text-sm font-semibold text-foreground">{status}</p><p className="mt-1 text-xs leading-5 text-secondary-text">{detail}</p></div>
    </div>
  </div>
);

const RouteLoadCycle: React.FC<{ children: React.ReactNode; routeKey: string }> = ({ children, routeKey }) => {
  const [preparing, setPreparing] = useState(true);
  const [showActivity, setShowActivity] = useState(false);
  const sessionRef = useRef(0);

  useLayoutEffect(() => {
    const sessionId = beginRouteLoad(routeKey);
    sessionRef.current = sessionId;
    return () => finishRouteLoad(sessionId);
  }, [routeKey]);

  useEffect(() => {
    if (!preparing) return undefined;
    const tick = () => {
      const current = getRouteLoadSnapshot();
      if (!current.active || current.sessionId !== sessionRef.current) return;
      const now = Date.now();
      const elapsed = now - current.startedAt;
      const quietFor = now - current.lastActivityAt;
      const noRequestPageReady = current.started === 0 && elapsed >= 180;
      const requestPageReady = current.started > 0 && current.pending === 0 && quietFor >= 100 && elapsed >= 180;
      // This is a brief, indeterminate navigation hint, not total data progress.
      // After the hint expires, pages retain their own pending/error states;
      // no request is considered complete merely because 1.4 seconds passed.
      const initialHintExpired = elapsed >= 1_400;
      if (noRequestPageReady || requestPageReady || initialHintExpired) {
        finishRouteLoad(current.sessionId);
        setPreparing(false);
        return;
      }
      setShowActivity(elapsed >= 180 && current.pending > 0);
    };
    const interval = window.setInterval(tick, 100);
    const kickoff = window.setTimeout(tick, 0);
    return () => {
      window.clearInterval(interval);
      window.clearTimeout(kickoff);
    };
  }, [preparing, routeKey]);

  return <div className="relative min-h-[420px]">
    {children}
    {preparing && showActivity ? <div
      className="pointer-events-none absolute inset-x-0 top-0 z-40 h-0.5 overflow-hidden bg-transparent"
      role="progressbar"
      aria-label="正在读取初始模块"
    ><div className="h-full bg-[hsl(var(--primary))] motion-safe:animate-pulse" /></div> : null}
  </div>;
};

type RouteErrorBoundaryProps = {
  children: React.ReactNode;
  resetKey: string;
  fullPage: boolean;
  text: {
    title: string;
    description: string;
    reload: string;
    backHome: string;
  };
};

type RouteErrorBoundaryState = {
  hasError: boolean;
  automaticRecoveryUsed: boolean;
  chunkError: boolean;
};

class RouteErrorBoundary extends Component<RouteErrorBoundaryProps, RouteErrorBoundaryState> {
  override state: RouteErrorBoundaryState = {
    hasError: false,
    automaticRecoveryUsed: false,
    chunkError: false,
  };

  private recoveryTimer: number | null = null;

  static getDerivedStateFromError(error: unknown): Partial<RouteErrorBoundaryState> {
    return { hasError: true, chunkError: isChunkLoadError(error) };
  }

  override componentDidCatch(error: Error, errorInfo: ErrorInfo) {
    console.error('Route page failed to render or load', error, errorInfo);
    if (!this.state.automaticRecoveryUsed) {
      this.recoveryTimer = window.setTimeout(() => {
        this.setState({ hasError: false, automaticRecoveryUsed: true });
      }, 450);
    }
  }

  override componentDidUpdate(prevProps: RouteErrorBoundaryProps) {
    if (this.state.hasError && prevProps.resetKey !== this.props.resetKey) {
      this.setState({ hasError: false, automaticRecoveryUsed: false, chunkError: false });
    }
  }

  override componentWillUnmount() {
    if (this.recoveryTimer != null) window.clearTimeout(this.recoveryTimer);
  }

  override render() {
    if (!this.state.hasError) {
      return this.props.children;
    }

    if (!this.state.automaticRecoveryUsed) {
      return <PageLoadingFallback fullPage={this.props.fullPage} status="正在重试页面组件" detail="系统正在尝试重新载入当前模块，其他页面仍可使用。" />;
    }

    return (
      <div className={this.props.fullPage ? 'flex min-h-screen items-center justify-center bg-base px-4' : 'px-2 py-4'}>
        <div className="w-full rounded-xl border border-warning/25 bg-warning/5 p-4 text-left">
          <h1 className="text-sm font-semibold text-foreground">
            {this.state.automaticRecoveryUsed ? this.props.text.title : '正在恢复当前模块'}
          </h1>
          <p className="mt-1 text-xs leading-5 text-secondary-text">
            {this.props.text.description}
          </p>
          <div className="mt-3 flex flex-wrap gap-2">
            <button
              type="button"
              className="btn-secondary"
              onClick={() => {
                if (this.state.chunkError) {
                  clearChunkRetryMarkers();
                  reloadWithFreshFrontend(`${WEB_BUILD_INFO.buildId}-${Date.now()}`);
                  return;
                }
                this.setState({ hasError: false, automaticRecoveryUsed: true });
              }}
            >
              {this.props.text.reload}
            </button>
            <button
              type="button"
              className="rounded-lg border border-border/70 bg-card px-3 py-1.5 text-xs font-medium text-foreground transition-colors hover:bg-hover"
              onClick={() => window.location.assign('/app')}
            >
              {this.props.text.backHome}
            </button>
          </div>
        </div>
      </div>
    );
  }
}

export const RouteBoundary: React.FC<{ children: React.ReactNode; fullPage?: boolean }> = ({
  children,
  fullPage = true,
}) => {
  const location = useLocation();
  const { t } = useUiLanguage();
  const resetKey = `${location.pathname}${location.search}`;

  return (
    <RouteErrorBoundary
      resetKey={resetKey}
      fullPage={fullPage}
      text={{
        title: t('routeError.title'),
        description: t('routeError.description'),
        reload: t('routeError.reload'),
        backHome: t('routeError.backHome'),
      }}
    >
      <Suspense fallback={<PageLoadingFallback fullPage={fullPage} />}>{children}</Suspense>
    </RouteErrorBoundary>
  );
};

export const RouteOutletBoundary: React.FC = () => (
  <RouteBoundary fullPage={false}>
    <RouteLoadOutlet />
  </RouteBoundary>
);

const RouteLoadOutlet: React.FC = () => {
  const location = useLocation();
  return <RouteLoadCycle key={location.pathname} routeKey={location.pathname}><Outlet /></RouteLoadCycle>;
};

export const StandaloneRouteBoundary: React.FC<{ children: React.ReactNode }> = ({ children }) => (
  <RouteBoundary fullPage>
    {children}
  </RouteBoundary>
);
