import type React from 'react';
import { useEffect, useState } from 'react';
import { Moon, Sun } from 'lucide-react';
import { Outlet, useLocation, useNavigate } from 'react-router-dom';
import { useTheme } from 'next-themes';
import { Drawer } from '../common/Drawer';
import { SidebarNav } from './SidebarNav';
import { cn } from '../../utils/cn';
import { useUiLanguage } from '../../contexts/UiLanguageContext';
import { MobileBottomNav } from './MobileBottomNav';
import { MobileAppBar } from '../mobile/MobileAppBar';
import { useMobileRouteMeta } from './useMobileRouteMeta';
import './GlassWorkspace.css';

type ShellProps = {
  children?: React.ReactNode;
  /** Isolated entries share this shell but navigate other modules as documents. */
  documentNavigation?: boolean;
};

/** 浏览器历史里是否还有上一页；用于决定手机应用栏是否显示返回。 */
function hasHistoryBack(): boolean {
  const state = window.history.state as { idx?: number } | null;
  return typeof state?.idx === 'number' && state.idx > 0;
}

export const Shell: React.FC<ShellProps> = ({ children, documentNavigation = false }) => {
  const [mobileOpen, setMobileOpen] = useState(false);
  const collapsed = false;
  const { t } = useUiLanguage();
  const { resolvedTheme, setTheme } = useTheme();
  const location = useLocation();
  const navigate = useNavigate();
  const { title, largeTitle, nested } = useMobileRouteMeta();

  const canGoBack = hasHistoryBack();

  // 路由切换回到顶部，避免手机上停在上一页的滚动位置
  useEffect(() => {
    window.scrollTo({ top: 0 });
  }, [location.pathname]);

  const toggleTheme = () => setTheme(resolvedTheme === 'dark' ? 'light' : 'dark');

  const mobileActions = [
    {
      key: 'theme',
      label: resolvedTheme === 'dark' ? '切换到浅色' : '切换到深色',
      icon: resolvedTheme === 'dark' ? Sun : Moon,
      onClick: toggleTheme,
    },
  ];

  return (
    <div className="glass-workspace min-h-[100svh] bg-bg-0 text-text-1">
      <MobileAppBar
          title={title}
          largeTitle={largeTitle}
          onBack={nested || canGoBack ? () => navigate(-1) : undefined}
          actions={mobileActions}
        />

      <div className="flex min-h-[100svh] w-full">
        <aside
          className={cn(
            'research-sidebar sticky top-0 z-40 hidden h-[100dvh] shrink-0 overflow-y-auto border-r border-line-1 bg-bg-1 lg:flex',
            collapsed ? 'w-16' : 'w-60'
          )}
          aria-label={t('layout.desktopSidebar')}
        >
          <div className="w-full min-w-0">
            <SidebarNav documentNavigation={documentNavigation} collapsed={collapsed} variant="rail" onNavigate={() => setMobileOpen(false)} />
          </div>
        </aside>

        <main className="mobile-shell-main min-h-0 min-w-0 flex-1 touch-pan-y">
          <div className="shell-content mx-auto w-full min-w-0 max-w-[1680px] lg:px-6 lg:py-6">
            {children ?? <Outlet />}
          </div>
        </main>
      </div>

      <MobileBottomNav documentNavigation={documentNavigation} />

      <Drawer
        isOpen={mobileOpen}
        onClose={() => setMobileOpen(false)}
        title={t('layout.navMenu')}
        width="max-w-[88vw] sm:max-w-xs"
        zIndex={90}
        side="left"
      >
        <SidebarNav documentNavigation={documentNavigation} onNavigate={() => setMobileOpen(false)} />
      </Drawer>
    </div>
  );
};
