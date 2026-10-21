import type React from 'react';
import { useEffect, useState } from 'react';
import { BarChart3, Menu } from 'lucide-react';
import { Outlet } from 'react-router-dom';
import { Drawer } from '../common/Drawer';
import { SidebarNav } from './SidebarNav';
import { cn } from '../../utils/cn';
import { ThemeToggle } from '../theme/ThemeToggle';
import { UiLanguageToggle } from '../i18n/UiLanguageToggle';
import { useUiLanguage } from '../../contexts/UiLanguageContext';
import { MobileBottomNav } from './MobileBottomNav';
import './GlassWorkspace.css';

type ShellProps = {
  children?: React.ReactNode;
  /** Isolated entries share this shell but navigate other modules as documents. */
  documentNavigation?: boolean;
};

export const Shell: React.FC<ShellProps> = ({ children, documentNavigation = false }) => {
  const [mobileOpen, setMobileOpen] = useState(false);
  const collapsed = false;
  const { t } = useUiLanguage();

  useEffect(() => {
    if (!mobileOpen) {
      return undefined;
    }

    const handleResize = () => {
      if (window.innerWidth >= 1024) {
        setMobileOpen(false);
      }
    };

    window.addEventListener('resize', handleResize);
    return () => {
      window.removeEventListener('resize', handleResize);
    };
  }, [mobileOpen]);

  return (
    <div className="glass-workspace min-h-screen bg-background text-foreground">
      <header className="mobile-app-bar lg:hidden">
        <div className="mobile-app-bar__brand">
          <span><BarChart3 aria-hidden="true" /></span>
          <div><strong>乐子乌智能投研平台</strong><small>证据驱动的投资研究</small></div>
        </div>
        <div className="mobile-app-bar__actions">
          <button type="button" className="mobile-app-bar__menu" aria-label={t('layout.openNav')} onClick={() => setMobileOpen(true)}><Menu aria-hidden="true" /></button>
          <UiLanguageToggle />
          <ThemeToggle />
        </div>
      </header>

      <div className="mx-auto flex min-h-screen w-full max-w-[1920px] px-0 lg:px-4 lg:py-4">
        <aside
          className={cn(
            'research-sidebar sticky top-4 z-40 hidden shrink-0 overflow-visible border border-[var(--shell-sidebar-border)] bg-card/94 p-2.5 transition-[width] duration-200 lg:flex',
            'max-h-[calc(100vh-2rem)] self-start',
            collapsed ? 'w-[64px]' : 'w-[208px]'
          )}
          aria-label={t('layout.desktopSidebar')}
        >
          <div className="w-full min-w-0">
            <SidebarNav documentNavigation={documentNavigation} collapsed={collapsed} variant="rail" onNavigate={() => setMobileOpen(false)} />
          </div>
        </aside>

        <main className="mobile-shell-main min-h-0 min-w-0 flex-1 lg:pl-4 touch-pan-y">
          {children ?? <Outlet />}
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
