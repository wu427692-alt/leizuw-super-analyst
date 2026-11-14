import { afterEach, describe, expect, it } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { UiLanguageProvider } from '../../../contexts/UiLanguageContext';
import { useMobileRouteMeta } from '../useMobileRouteMeta';

afterEach(cleanup);

const MetaProbe = () => {
  const { title, largeTitle } = useMobileRouteMeta();
  return (
    <div>
      <span data-testid="title">{title}</span>
      <span data-testid="large-title">{largeTitle ?? ''}</span>
    </div>
  );
};

const renderAt = (path: string) => render(
  <MemoryRouter initialEntries={[path]}>
    <UiLanguageProvider>
      <MetaProbe />
    </UiLanguageProvider>
  </MemoryRouter>,
);

describe('useMobileRouteMeta research topic', () => {
  it('uses the research topic as the app-bar title for meeting sessions', () => {
    renderAt('/chat?from=meeting&topic=%E5%AE%81%E5%BE%B7%E6%97%B6%E4%BB%A3%E4%B8%AD%E7%BA%BF%E6%80%8E%E4%B9%88%E7%9C%8B');
    expect(screen.getByTestId('title')).toHaveTextContent('宁德时代中线怎么看');
    expect(screen.getByTestId('title')).not.toHaveTextContent('AI 问股');
  });

  it('truncates a long topic instead of overflowing the app bar', () => {
    const longTopic = '储能业务在2027年海外需求与产能出清节奏下的中期机会与风险';
    renderAt(`/chat?from=meeting&topic=${encodeURIComponent(longTopic)}`);
    expect(screen.getByTestId('title').textContent).toBe(`${longTopic.slice(0, 16)}…`);
  });

  it('keeps the generic chat title for normal entries and the meeting title for /meeting', () => {
    renderAt('/chat');
    expect(['AI 问股', 'AI Stock Q&A']).toContain(screen.getByTestId('title').textContent);
    cleanup();
    renderAt('/meeting');
    expect(['投研团队', 'Research Team']).toContain(screen.getByTestId('title').textContent);
  });
});
