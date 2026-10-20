import { useEffect } from 'react';
import { useLocation } from 'react-router-dom';
import { useUserAccess } from '../contexts/UserAccessContext';
import { safeUserAccessRedirect } from '../utils/userAccessRedirect';
import UserAccessPage from './UserAccessPage';

/** Reuses the same account form without loading unrelated research workspaces. */
export default function AccountAccessEntry() {
  const { loggedIn, isLoading } = useUserAccess();
  const location = useLocation();
  useEffect(() => {
    const parameters = new URLSearchParams(location.search);
    if (isLoading || !loggedIn || parameters.get('bind') === 'phone') return;
    window.location.replace(safeUserAccessRedirect(parameters.get('redirect')));
  }, [loggedIn, isLoading, location.search]);
  return <UserAccessPage documentNavigation />;
}
