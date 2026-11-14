import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import { MeetingChatHeader, type MeetingChatHeaderProps } from '../MeetingChatHeader';

const baseProps = (overrides: Partial<MeetingChatHeaderProps> = {}): MeetingChatHeaderProps => ({
  roles: [
    { key: 'general', name: '主席', title: '统筹 / 决策' },
    { key: 'research', name: '研究员', title: '研究 / 报告' },
  ],
  topic: '',
  chatOwnsTitle: false,
  editing: false,
  draft: '',
  error: '',
  startsResearch: true,
  onEdit: vi.fn(),
  onDraftChange: vi.fn(),
  onSave: vi.fn(),
  onCancel: vi.fn(),
  ...overrides,
});

describe('MeetingChatHeader', () => {
  it('renders the team as one compact chip row instead of a grid of avatars', () => {
    render(<MeetingChatHeader {...baseProps()} />);

    expect(screen.getByRole('group', { name: 'AI 研究小组角色' })).toBeInTheDocument();
    expect(screen.getByText('主席')).toBeInTheDocument();
    expect(screen.getByText('研究员')).toBeInTheDocument();
    // 读屏仍能拿到职责，视觉上只保留角色名，保证角色条只有一行高。
    expect(screen.getByText('研究 / 报告')).toHaveClass('sr-only');
  });

  it('explains loading and unavailable teams without blocking the conversation', () => {
    const { rerender } = render(<MeetingChatHeader {...baseProps({ roles: null })} />);
    expect(screen.getByText('正在载入团队…')).toBeInTheDocument();

    rerender(<MeetingChatHeader {...baseProps({ roles: [] })} />);
    expect(screen.getByText('暂时读不到团队角色，可以直接提问')).toBeInTheDocument();
  });

  it('shows the research topic once when ChatPage does not render it', () => {
    const { rerender } = render(<MeetingChatHeader {...baseProps({ topic: '光伏出清到哪一步了' })} />);
    expect(screen.getByTestId('meeting-chat-topic')).toHaveTextContent('光伏出清到哪一步了');

    rerender(<MeetingChatHeader {...baseProps({ topic: '光伏出清到哪一步了', chatOwnsTitle: true })} />);
    expect(screen.queryByTestId('meeting-chat-topic')).not.toBeInTheDocument();
  });

  it('opens a topic editor whose primary action depends on whether the conversation is empty', () => {
    const props = baseProps({ editing: true, draft: '宁德时代储能怎么看', startsResearch: true });
    const { rerender } = render(<MeetingChatHeader {...props} />);

    expect(screen.getByRole('button', { name: '开始研究' })).toBeInTheDocument();
    fireEvent.change(screen.getByRole('textbox', { name: '研究议题' }), { target: { value: '新议题' } });
    expect(props.onDraftChange).toHaveBeenCalledWith('新议题');

    fireEvent.submit(screen.getByRole('textbox', { name: '研究议题' }).closest('form') as HTMLFormElement);
    expect(props.onSave).toHaveBeenCalledTimes(1);

    rerender(<MeetingChatHeader {...baseProps({ editing: true, draft: '旧对话', startsResearch: false })} />);
    expect(screen.getByRole('button', { name: '保存议题' })).toBeInTheDocument();
  });

  it('surfaces the topic error inside the editor and wires cancel', () => {
    const props = baseProps({ editing: true, error: '先写清这次想研究什么', startsResearch: true });
    render(<MeetingChatHeader {...props} />);

    expect(screen.getByRole('alert')).toHaveTextContent('先写清这次想研究什么');
    fireEvent.click(screen.getByRole('button', { name: '取消' }));
    expect(props.onCancel).toHaveBeenCalledTimes(1);
  });

  it('exposes an edit action labelled by whether a topic already exists', () => {
    const onEdit = vi.fn();
    const { rerender } = render(<MeetingChatHeader {...baseProps({ onEdit })} />);
    fireEvent.click(screen.getByRole('button', { name: '设置研究议题' }));
    expect(onEdit).toHaveBeenCalledTimes(1);

    rerender(<MeetingChatHeader {...baseProps({ onEdit, topic: '已有议题' })} />);
    expect(screen.getByRole('button', { name: '修改研究议题' })).toBeInTheDocument();
  });
});
