import { useEffect } from 'react';
import { useLocation } from 'react-router-dom';
import { getJevReturnTarget, jevDocumentNavigation } from '../utils/jevAccessNavigation';
import { ArrowLeft } from 'lucide-react';
import { useUserAccess } from '../contexts/UserAccessContext';
import JevBacktestPage from './JevBacktestPage';
import UserAccessPage from './UserAccessPage';

/** Isolated deployment entry; the normal application reuses the same page. */
export function JevStandaloneApp() {
  const access = useUserAccess();
  const location = useLocation();
  const accessDocument = location.pathname === '/access';
  const returnTarget = getJevReturnTarget(location.search);
  useEffect(() => {
    if (accessDocument && access.loggedIn && !access.isLoading) jevDocumentNavigation.replace(returnTarget);
  }, [accessDocument, access.loggedIn, access.isLoading, returnTarget]);
  return <div className="jev-standalone">
    <header className="jev-standalone-nav"><a href="/app" className="jev-site-name">乐子乌超级价值</a><a href="/app"><ArrowLeft size={14} />返回首页</a></header>
    {access.isLoading ? <main className="jev-standalone-gate" role="status"><h1>正在确认访问权限</h1><p>验证完成后读取当前账号的回测任务。</p></main>
      : access.loggedIn && accessDocument ? <main className="jev-standalone-gate" role="status"><h1>正在打开 Jev 交易回测</h1><p>正在载入独立回测页面。</p></main>
      : access.loggedIn ? <JevBacktestPage />
        // The /access handoff completes with a document load, so the old main
        // SPA never needs to recognize this independently deployed route.
        : <UserAccessPage documentNavigation />}
  </div>;
}
