import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { BrowserRouter, useLocation } from 'react-router-dom';
import { UserAccessProvider } from './contexts/UserAccessContext';
import AiNewsPage from './pages/AiNewsPage';
import AiNewsAccessPage from './pages/AiNewsAccessPage';
import { Shell } from './components/layout/Shell';
import { ThemeProvider } from './components/theme/ThemeProvider';
import { UiLanguageProvider } from './contexts/UiLanguageContext';
import './index.css';
import './ai-news-standalone.css';

export function NewsApplication() {
  const location = useLocation();
  // Let the protected API enforce access for the news page itself. The access
  // context is only mounted on /access, so feed polling never invokes global
  // app loading, agent-chat state, or unrelated account status requests.
  return location.pathname === '/access'
    ? <UserAccessProvider><AiNewsAccessPage /></UserAccessProvider>
    : <Shell documentNavigation><AiNewsPage /></Shell>;
}

createRoot(document.getElementById('root')!).render(<StrictMode><ThemeProvider><UiLanguageProvider><BrowserRouter><NewsApplication /></BrowserRouter></UiLanguageProvider></ThemeProvider></StrictMode>);
