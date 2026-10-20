import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import './index.css';
import './pages/JevStandaloneApp.css';

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <main><h1>Jev 功能暂时下线</h1><p>历史任务和记录已保留。</p><a href="/app">返回市场首页</a></main>
  </StrictMode>,
);
