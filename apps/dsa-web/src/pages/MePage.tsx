import { useEffect, useState } from 'react';
import type React from 'react';
import { useNavigate } from 'react-router-dom';
import { ChevronRight, CircleCheck, ClipboardCheck, HelpCircle, Languages, LogOut, Moon, Star, Sun } from 'lucide-react';
import { useTheme } from 'next-themes';
import { AppPage, MetricRow, ShareBar } from '../components/common';
import { PixelAvatar } from '../components/illustration/PixelAvatar';
import { useUserAccess } from '../contexts/UserAccessContext';
import { useUiLanguage } from '../contexts/UiLanguageContext';
import { industryResearchApi } from '../api/industryResearch';
import { systemConfigApi } from '../api/systemConfig';
import './MePage.css';

const RUNNING = new Set(['queued', 'collecting', 'analyzing']);

/**
 * 「我的」：手机端的账号与设置入口。
 *
 * 只放三件事：我是谁、数据同步到哪一步、能去哪里配置。
 * 三个计数（自选 / 报告 / 进行中）来自各自模块的真实接口，没有就显示 —。
 */
export const MePage: React.FC = () => {
  const navigate = useNavigate();
  const { user, loggedIn, logout } = useUserAccess();
  const { language, setLanguage } = useUiLanguage();
  const { resolvedTheme, setTheme } = useTheme();
  const [counts, setCounts] = useState<{ watchlist: number | null; reports: number | null; running: number | null }>({
    watchlist: null, reports: null, running: null,
  });
  const [syncedAt, setSyncedAt] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    void (async () => {
      const [watch, projects] = await Promise.allSettled([
        systemConfigApi.getWatchlist(),
        industryResearchApi.projects({ limit: 50 }),
      ]);
      if (!alive) return;
      const symbols = watch.status === 'fulfilled' ? (watch.value ?? []) : [];
      const items = projects.status === 'fulfilled' ? (projects.value.items ?? []) : [];
      setCounts({
        watchlist: watch.status === 'fulfilled' ? symbols.length : null,
        reports: projects.status === 'fulfilled' ? items.filter((item) => !RUNNING.has(item.status)).length : null,
        running: projects.status === 'fulfilled' ? items.filter((item) => RUNNING.has(item.status)).length : null,
      });
      const latest = items.map((item) => item.updatedAt || item.createdAt).filter(Boolean).sort().pop();
      setSyncedAt(latest ? String(latest).slice(5, 16).replace('T', ' ') : null);
    })();
    return () => { alive = false; };
  }, []);

  return <AppPage className="me-page">
    {/* 桌面端没有应用栏，标题由页面自己给；手机端应用栏已有「我的」。 */}
    <h1 className="me-page__title">我的</h1>
    <button type="button" className="me-account" onClick={() => navigate('/access')}>
      <PixelAvatar seed={user?.name || 'guest'} size={40} />
      <span className="me-account__main">
        <strong>{user?.name || (loggedIn ? '演示账号' : '未登录')}</strong>
        <small>{loggedIn ? '已登录' : '点击登录'}</small>
      </span>
      <ChevronRight aria-hidden="true" className="me-chev" />
    </button>

    <section className="lx-section" aria-label="数据同步">
      <header className="lx-section__head"><h2>数据同步</h2></header>
      <div className="me-sync">
        <span className="me-sync__icon"><CircleCheck aria-hidden="true" /></span>
        <span className="me-sync__main">
          <strong>同步完成</strong>
          <small>{syncedAt ? `最近同步 ${syncedAt}` : '本地数据已就绪'}</small>
        </span>
      </div>
      <MetricRow
        columns={3}
        items={[
          { label: '自选', value: counts.watchlist, hint: '关注股票' },
          { label: '报告', value: counts.reports, hint: '已完成研究' },
          { label: '进行中', value: counts.running, hint: '后台任务' },
        ]}
      />
      {(counts.reports ?? 0) + (counts.running ?? 0) > 0 ? (
        <div className="flex items-center gap-[var(--gap-sm)]">
          <span className="shrink-0 text-[length:var(--fs-caption)] text-[color:var(--text-3)]">研究记录构成</span>
          <ShareBar
            className="max-w-[200px]"
            ariaLabel="研究记录构成"
            segments={[
              { key: 'reports', value: counts.reports ?? 0, label: '已完成' },
              { key: 'running', value: counts.running ?? 0, color: 'var(--brand)', label: '进行中' },
            ]}
          />
        </div>
      ) : null}
    </section>

    <div className="me-links">
      <button type="button" onClick={() => navigate('/tasks')}>
        <ClipboardCheck aria-hidden="true" />
        <span><strong>研究记录</strong><small>任务与已归档报告</small></span>
        <ChevronRight aria-hidden="true" className="me-chev" />
      </button>
      <button type="button" onClick={() => navigate('/guide')}>
        <HelpCircle aria-hidden="true" />
        <span><strong>使用帮助</strong><small>功能说明与常见问题</small></span>
        <ChevronRight aria-hidden="true" className="me-chev" />
      </button>
      <button type="button" onClick={() => setTheme(resolvedTheme === 'dark' ? 'light' : 'dark')}>
        {resolvedTheme === 'dark' ? <Sun aria-hidden="true" /> : <Moon aria-hidden="true" />}
        <span><strong>外观</strong><small>{resolvedTheme === 'dark' ? '深色' : '浅色'}</small></span>
        <ChevronRight aria-hidden="true" className="me-chev" />
      </button>
      <button type="button" onClick={() => setLanguage(language === 'zh' ? 'en' : 'zh')}>
        <Languages aria-hidden="true" />
        <span><strong>界面语言</strong><small>{language === 'zh' ? '中文' : 'English'}</small></span>
        <ChevronRight aria-hidden="true" className="me-chev" />
      </button>
      {loggedIn ? (
        <button type="button" onClick={() => void logout()}>
          <LogOut aria-hidden="true" />
          <span><strong>退出登录</strong><small>下次需要重新登录</small></span>
          <ChevronRight aria-hidden="true" className="me-chev" />
        </button>
      ) : null}
    </div>

    <footer className="me-brand">
      <Star aria-hidden="true" className="h-5 w-5 text-[color:var(--brand)]" />
      <strong>乐子乌智能投研</strong>
      <small>v2.0</small>
    </footer>
  </AppPage>;
};

export default MePage;
