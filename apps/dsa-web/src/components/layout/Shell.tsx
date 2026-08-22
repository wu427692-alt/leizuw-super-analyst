import type React from 'react';
import { useEffect, useState } from 'react';
import { BarChart3 } from 'lucide-react';
import { Outlet } from 'react-router-dom';
import { Drawer } from '../common/Drawer';
import { SidebarNav } from './SidebarNav';
import { cn } from '../../utils/cn';
import { ThemeToggle } from '../theme/ThemeToggle';
import { UiLanguageToggle } from '../i18n/UiLanguageToggle';
import { useUiLanguage } from '../../contexts/UiLanguageContext';
import { MobileBottomNav } from './MobileBottomNav';

type ShellProps = {
  children?: React.ReactNode;
};

export const Shell: React.FC<ShellProps> = ({ children }) => {
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
    <div className="min-h-screen bg-background text-foreground">
      <header className="mobile-app-bar lg:hidden">
        <div className="mobile-app-bar__brand">
          <span><BarChart3 aria-hidden="true" /></span>
          <div><strong>乐子乌超级价值</strong><small>财经情报与研究</small></div>
        </div>
        <div className="mobile-app-bar__actions">
          <UiLanguageToggle />
          <ThemeToggle />
        </div>
      </header>

      <div className="mx-auto flex min-h-screen w-full max-w-[1680px] px-3 py-3 sm:px-4 sm:py-4 lg:px-5">
        <aside
          className={cn(
            'sticky top-3 z-40 hidden shrink-0 overflow-visible rounded-[1.5rem] border border-[var(--shell-sidebar-border)] bg-card/72 p-2.5 shadow-soft-card backdrop-blur-sm transition-[width] duration-200 lg:flex',
            'max-h-[calc(100vh-1.5rem)] self-start sm:top-4 sm:max-h-[calc(100vh-2rem)]',
            collapsed ? 'w-[64px]' : 'w-[192px]'
          )}
          aria-label={t('layout.desktopSidebar')}
        >
          <div className="w-full min-w-0">
            <SidebarNav collapsed={collapsed} variant="rail" onNavigate={() => setMobileOpen(false)} />
          </div>
        </aside>

        <main className="mobile-shell-main min-h-0 min-w-0 flex-1 lg:pl-3 touch-pan-y">
          {children ?? <Outlet />}
        </main>
      </div>

      <MobileBottomNav onOpenMore={() => setMobileOpen(true)} />

      <Drawer
        isOpen={mobileOpen}
        onClose={() => setMobileOpen(false)}
        title={t('layout.navMenu')}
        width="max-w-[88vw] sm:max-w-xs"
        zIndex={90}
        side="left"
      >
        <SidebarNav onNavigate={() => setMobileOpen(false)} />
      </Drawer>
    </div>
  );
};
