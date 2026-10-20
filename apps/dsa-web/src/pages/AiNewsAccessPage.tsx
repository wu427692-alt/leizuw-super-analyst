import { useEffect } from 'react';
import { useLocation } from 'react-router-dom';
import { useUserAccess } from '../contexts/UserAccessContext';
import UserAccessPage from './UserAccessPage';

export default function AiNewsAccessPage() {
  const { accessEnabled, loggedIn, isLoading } = useUserAccess();
  const location = useLocation();
  useEffect(() => {
    if (isLoading || (accessEnabled && !loggedIn)) return;
    // Binding is an explicit account action, not a new login. Keep this page
    // visible until the user has bound the existing identity or skips it.
    if (new URLSearchParams(location.search).get('bind') === 'phone') return;
    const requested = new URLSearchParams(location.search).get('redirect') || '/ai-news';
    // This standalone entry can finish login only on its own same-origin page.
    let target = '/ai-news';
    try {
      const parsed = new URL(requested, window.location.origin);
      if (parsed.origin === window.location.origin && ['/ai-news', '/ai-news/'].includes(parsed.pathname)) {
        target = parsed.pathname + parsed.search;
      }
    } catch { /* Invalid redirect values return to the news root. */ }
    window.location.replace(target);
  }, [accessEnabled, loggedIn, isLoading, location.search]);
  return <UserAccessPage documentNavigation />;
}
