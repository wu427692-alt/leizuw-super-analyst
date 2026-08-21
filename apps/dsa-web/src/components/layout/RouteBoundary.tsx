import type React from 'react';
import { Component, Suspense, useEffect, useLayoutEffect, useRef, useState } from 'react';
import type { ErrorInfo } from 'react';
import { Outlet, useLocation } from 'react-router-dom';
import { useUiLanguage } from '../../contexts/UiLanguageContext';
import { WEB_BUILD_INFO } from '../../utils/constants';
import { clearChunkRetryMarkers, isChunkLoadError, reloadWithFreshFrontend } from '../../utils/chunkRecovery';
import { beginRouteLoad, finishRouteLoad, getRouteLoadSnapshot } from '../../utils/routeLoadTracker';

type PageLoadingFallbackProps = {
  fullPage?: boolean;
  progress?: number;
  status?: string;
  detail?: string;
};

export const PageLoadingFallback: React.FC<PageLoadingFallbackProps> = ({
  fullPage = true,
  progress = 18,
  status = '正在准备当前页面',
  detail = '页面数据完整后会一次性显示，不会先露出半成品。',
}) => (
  <div
    className={
      fullPage
        ? 'flex min-h-screen items-center justify-center bg-base'
        : 'flex min-h-[420px] items-center justify-center px-4'
    }
    role="status"
    aria-label="页面正在准备"
  >
    <div className="w-full max-w-xl border border-border/70 bg-card/85 p-5 shadow-soft-card backdrop-blur-sm">
      <div className="flex items-center justify-between gap-4">
        <div><p className="text-sm font-semibold text-foreground">{status}</p><p className="mt-1 text-xs leading-5 text-secondary-text">{detail}</p></div>
        <span className="shrink-0 font-mono text-sm font-semibold text-cyan">{Math.round(progress)}%</span>
      </div>
      <div
        className="mt-4 h-1.5 overflow-hidden bg-border/60"
        role="progressbar"
        aria-label="页面加载进度"
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={Math.round(progress)}
      >
        <div className="h-full bg-cyan transition-[width] duration-200 ease-out" style={{ width: `${Math.max(3, Math.min(100, progress))}%` }} />
      </div>
    </div>
  </div>
);

const RouteLoadCycle: React.FC<{ children: React.ReactNode; routeKey: string }> = ({ children, routeKey }) => {
  const [preparing, setPreparing] = useState(true);
  const [progress, setProgress] = useState(8);
  const [status, setStatus] = useState('正在初始化页面组件');
  const sessionRef = useRef(0);
  const finishingRef = useRef(false);

  useLayoutEffect(() => {
    const sessionId = beginRouteLoad(routeKey);
    sessionRef.current = sessionId;
    return () => finishRouteLoad(sessionId);
  }, [routeKey]);

  useEffect(() => {
    if (!preparing) return undefined;
    let revealTimer: number | null = null;
    const tick = () => {
      const current = getRouteLoadSnapshot();
      if (!current.active || current.sessionId !== sessionRef.current) return;
      const now = Date.now();
      const elapsed = now - current.startedAt;
      const quietFor = now - current.lastActivityAt;
      const noRequestPageReady = current.started === 0 && elapsed >= 900;
      const requestPageReady = current.started > 0 && current.pending === 0 && quietFor >= 420 && elapsed >= 520;
      if (noRequestPageReady || requestPageReady) {
        if (finishingRef.current) return;
        finishingRef.current = true;
        setProgress(100);
        setStatus('本页数据已完成');
        revealTimer = window.setTimeout(() => {
          finishRouteLoad(current.sessionId);
          setPreparing(false);
        }, 160);
        return;
      }

      if (current.started === 0) {
        setProgress(Math.min(24, 8 + elapsed / 90));
        setStatus('正在初始化页面组件');
        return;
      }
      const completedRatio = current.completed / Math.max(1, current.started);
      const nextProgress = Math.min(94, 24 + completedRatio * 62 + Math.min(8, elapsed / 1_500));
      setProgress(nextProgress);
      setStatus(current.pending > 0
        ? `正在加载本页数据（${current.completed}/${current.started}）`
        : '正在整理并校验本页数据');
    };
    const interval = window.setInterval(tick, 90);
    const kickoff = window.setTimeout(tick, 0);
    return () => {
      window.clearInterval(interval);
      window.clearTimeout(kickoff);
      if (revealTimer != null) window.clearTimeout(revealTimer);
    };
  }, [preparing, routeKey]);

  return <div className="relative min-h-[420px]">
    <div className={preparing ? 'pointer-events-none' : ''} style={preparing ? { visibility: 'hidden' } : undefined} aria-hidden={preparing || undefined}>{children}</div>
    {preparing ? <div className="absolute inset-0 z-30 bg-background"><PageLoadingFallback fullPage={false} progress={progress} status={status} detail="正在等待当前页面的核心接口全部返回；页内刷新会保留现有数据，不再反复遮挡或弹出失败层。" /></div> : null}
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
      return <PageLoadingFallback fullPage={this.props.fullPage} progress={64} status="当前模块正在自动恢复" detail="系统正在重新载入页面组件，不需要手动刷新。" />;
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
              onClick={() => window.location.assign('/')}
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
