import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { BrowserRouter } from 'react-router-dom';
import { ThemeProvider } from './components/theme/ThemeProvider';
import { UserAccessProvider } from './contexts/UserAccessContext';
import { UiLanguageProvider } from './contexts/UiLanguageContext';
import AccountAccessEntry from './pages/AccountAccessEntry';
import './index.css';
import './components/layout/GlassWorkspace.css';
import './account-access.css';

createRoot(document.getElementById('root')!).render(
  <StrictMode><ThemeProvider><UiLanguageProvider><BrowserRouter>
    <UserAccessProvider><AccountAccessEntry /></UserAccessProvider>
  </BrowserRouter></UiLanguageProvider></ThemeProvider></StrictMode>,
);
