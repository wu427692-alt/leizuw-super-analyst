import type React from 'react';
import { Pencil } from 'lucide-react';
import { PixelAvatar } from '../illustration/PixelAvatar';

/** 团队角色：来自 `/api/v1/command/catalog`，只用于展示，不参与对话路由。 */
export type MeetingChatRole = {
  key: string;
  name: string;
  /** 职责文案（例如「选股 / 策略 / 回测」）。窄屏 chip 只显示名字，职责交给读屏。 */
  title?: string;
};

export type MeetingChatHeaderProps = {
  /** null = 角色还在读取；空数组 = 读不到角色（仍可直接对话，不挡住主流程）。 */
  roles: MeetingChatRole[] | null;
  /** 当前研究议题；空串表示这次对话还没有议题。 */
  topic: string;
  /**
   * ChatPage 的页头已经展示议题（`from=meeting&topic` 或本机研究记录）时，
   * 这里不再重复一遍标题，避免同一句话在一屏里出现两次。
   */
  chatOwnsTitle: boolean;
  editing: boolean;
  draft: string;
  error: string;
  /** 会话还是空的：保存议题就等于发起一次研究（首条消息 = 议题）。 */
  startsResearch: boolean;
  onEdit: () => void;
  onDraftChange: (value: string) => void;
  onSave: () => void;
  onCancel: () => void;
};

/**
 * 合并「AI 问股」与「投研团队」之后，`/meeting` 的顶部条：
 * 一行紧凑的团队角色 chip（高 40，不占对话空间）+ 议题标题/编辑区。
 *
 * 对话本体由 ChatPage 渲染（消息区、输入区、历史会话都在里面），这里只补两件
 * ChatPage 不知道的事：这次对话属于哪个团队、这次研究的议题是什么。
 */
export const MeetingChatHeader: React.FC<MeetingChatHeaderProps> = ({
  roles,
  topic,
  chatOwnsTitle,
  editing,
  draft,
  error,
  startsResearch,
  onEdit,
  onDraftChange,
  onSave,
  onCancel,
}) => {
  const showTopic = !chatOwnsTitle && Boolean(topic);

  return (
    <div className="meeting-chat-head">
      <div className="meeting-chat-team" role="group" aria-label="AI 研究小组角色">
        <span className="meeting-chat-team__label">AI 研究小组</span>
        {roles === null ? (
          <span className="meeting-chat-team__note">正在载入团队…</span>
        ) : roles.length === 0 ? (
          <span className="meeting-chat-team__note">暂时读不到团队角色，可以直接提问</span>
        ) : (
          <ul className="meeting-chat-team__list">
            {roles.map((role) => (
              <li key={role.key} className="meeting-chat-team__chip">
                <PixelAvatar seed={role.key} size={18} />
                <span>{role.name}</span>
                {role.title ? <span className="sr-only">{role.title}</span> : null}
              </li>
            ))}
          </ul>
        )}
        <button
          type="button"
          className="meeting-chat-team__action"
          onClick={onEdit}
          aria-label={topic ? '修改研究议题' : '设置研究议题'}
          aria-expanded={editing}
        >
          <Pencil aria-hidden="true" />
          <span aria-hidden="true">{topic ? '议题' : '定议题'}</span>
        </button>
      </div>

      {showTopic ? (
        <p className="meeting-chat-topic" data-testid="meeting-chat-topic">
          <span>研究议题</span>
          <strong>{topic}</strong>
        </p>
      ) : null}

      {editing ? (
        <form
          className="meeting-chat-editor"
          onSubmit={(event) => {
            event.preventDefault();
            onSave();
          }}
        >
          <textarea
            className="meeting-chat-editor__input"
            value={draft}
            autoFocus
            rows={2}
            aria-label="研究议题"
            placeholder="写清这次要研究什么"
            onChange={(event) => onDraftChange(event.target.value)}
          />
          <div className="meeting-chat-editor__acts">
            <button type="button" className="meeting-chat-editor__cancel" onClick={onCancel}>取消</button>
            <button type="submit" className="meeting-chat-editor__save">{startsResearch ? '开始研究' : '保存议题'}</button>
          </div>
          {error ? <p className="meeting-chat-editor__error" role="alert">{error}</p> : null}
        </form>
      ) : null}
    </div>
  );
};

export default MeetingChatHeader;
