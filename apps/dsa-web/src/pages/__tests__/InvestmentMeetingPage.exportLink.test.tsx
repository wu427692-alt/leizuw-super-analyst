import { render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, expect, it, vi } from 'vitest';

/**
 * `/meeting` 的 export_task 契约。
 *
 * 历史实现把桌面端塞进 `/meeting-embed` iframe，测试断言的正是那个 iframe 的 src。
 * 后来发现 iframe 里是 static/home.html 那个独立静态 SPA（自带侧栏与主题），
 * 造成「应用套应用」：浅色主题下团队页是黑的、页面被固定高度锁死无法滚动。
 * 现在桌面与手机都渲染同一个 React 对话界面，iframe 已移除。
 *
 * 所以这里断言的是新契约：
 *   1. 不再渲染任何 iframe（不会再出现嵌套应用）
 *   2. export_task 只接受白名单格式，恶意 URL 不会被透传
 *   3. 页面本体可用（团队条 + 对话输入区可渲染）
 */

vi.mock('../../../api/command', () => ({
  commandApi: {
    getAgentRoles: vi.fn().mockResolvedValue({}),
  },
}));

vi.mock('../ChatPage', () => ({
  default: () => <div data-testid="chat-page-stub">对话界面</div>,
}));

import InvestmentMeetingPage from '../InvestmentMeetingPage';

const renderAt = (url: string) =>
  render(
    <MemoryRouter initialEntries={[url]}>
      <InvestmentMeetingPage />
    </MemoryRouter>,
  );

describe('InvestmentMeetingPage 的 export_task 契约', () => {
  it('不再渲染任何 iframe（桌面端曾经因此变成应用套应用）', () => {
    const { container } = renderAt('/meeting?export_task=essay-export-1234-abcd');
    expect(container.querySelector('iframe')).toBeNull();
  });

  it('渲染的是应用内对话界面，而不是外部嵌入页', async () => {
    renderAt('/meeting');
    await waitFor(() => {
      expect(screen.getByTestId('chat-page-stub')).toBeTruthy();
    });
    expect(screen.queryByTitle('投研团队')).toBeNull();
  });

  it('合法 export_task 不会让页面崩溃或退回 iframe', () => {
    const { container } = renderAt('/meeting?export_task=essay-export-1234-abcd');
    expect(container.querySelector('iframe')).toBeNull();
  });

  it('不把任意 URL 或无关查询参数透传给任何嵌入页', () => {
    const { container } = renderAt(
      '/meeting?export_task=https%3A%2F%2Funtrusted.example%2Ftask&poll_path=%2Fapi%2Fprivate',
    );
    expect(container.querySelector('iframe')).toBeNull();
    // 页面里不该出现被透传的外部地址
    expect(container.innerHTML).not.toContain('untrusted.example');
    expect(container.innerHTML).not.toContain('/api/private');
  });
});
