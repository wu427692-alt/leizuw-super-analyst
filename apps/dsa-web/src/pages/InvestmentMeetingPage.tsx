import { useEffect, useMemo, useRef, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { MeetingChatHeader, type MeetingChatRole } from '../components/chat/MeetingChatHeader';
import { useIsMobile } from '../hooks/useIsMobile';
import { useAgentChatStore } from '../stores';
import {
  findResearchSession,
  saveResearchSession,
} from '../stores/researchSessionStore';
import { commandApi } from '../api/command';
import ChatPage from './ChatPage';
import './InvestmentMeetingPage.css';

/** 后端 AGENTS 的展示顺序：主席先落座，其余按研究流程排。 */
const ROLE_ORDER = ['general', 'screen', 'data', 'research', 'portfolio', 'schedule'];

/** 议题过长时只用于 document.title，页面标题由 ChatPage 自行截断。 */
const TITLE_TOPIC_LIMIT = 20;

/**
 * 05/06 研投团队。合并「AI 问股」与「投研团队」之后，这里就是**唯一**的对话入口：
 *
 * - 手机端（≤1023px）：本页就地渲染完整对话。团队角色收成一行 chip（高 40），
 *   研究议题作为这次对话的标题（可点开编辑），对话本体复用 ChatPage
 *   （消息区 / 输入区 / 历史会话 / 技能 / 导出都在里面，不另写一套 UI）。
 *   研究议题仍然是「一次研究 = 一个会话 = 首条消息就是议题」的旧语义，所以
 *   「开始研究」会新建会话并把议题作为首条消息发出，服务端据此生成会话标题。
 * - 桌面端：整页内容仍由 `/meeting-embed` 独立渲染（见 docs/smart-research-team.md），
 *   本页只负责 iframe 外层容器，`export_task` 白名单保持不变。
 *
 * `/chat` 在手机端由 App 路由重定向到本页（query 原样保留），因此 ChatPage 的
 * `from=meeting&topic=<议题>` 上下文契约（页头显示议题、返回胶囊条、document.title）
 * 在这条链路上继续生效。
 */
const InvestmentMeetingMobile = () => {
  const [searchParams, setSearchParams] = useSearchParams();
  const {
    ownerScope,
    sessionId,
    messages,
    messagesLoading,
    historyError,
    loading,
  } = useAgentChatStore();

  const [roles, setRoles] = useState<MeetingChatRole[] | null>(null);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState('');
  const [topicError, setTopicError] = useState('');

  useEffect(() => {
    let alive = true;
    commandApi
      .getAgentRoles()
      .then((agents) => {
        if (!alive) return;
        const ordered = ROLE_ORDER.filter((key) => agents[key]).map((key) => ({ ...agents[key], key }));
        const rest = Object.keys(agents)
          .filter((key) => !ROLE_ORDER.includes(key))
          .map((key) => ({ ...agents[key], key }));
        setRoles([...ordered, ...rest]);
      })
      .catch(() => {
        if (alive) setRoles([]);
      });
    return () => {
      alive = false;
    };
  }, []);

  const topicParam = searchParams.get('topic')?.trim() || '';
  const sessionParam = searchParams.get('session')?.trim() || '';
  const stockParam = searchParams.get('stock')?.trim() || '';
  const stockNameParam = searchParams.get('name')?.trim() || '';
  const cycleParam = searchParams.get('cycle')?.trim() || '';
  // ChatPage 会把 `stock` / `name` / `recordId` 从 URL 消费掉（预填追问）。
  // 记下首帧的标的上文，发起研究时仍能带上它，不因为 URL 被清空而丢标的。
  const entryContextRef = useRef({ stock: stockParam, stockName: stockNameParam, cycle: cycleParam });

  // 议题/标的优先认 URL；URL 没有时认本机研究记录（同一会话的研究上下文）。
  const researchRecord = useMemo(
    () => findResearchSession(ownerScope, sessionParam || sessionId),
    [ownerScope, sessionParam, sessionId],
  );
  const topic = searchParams.has('topic') ? topicParam : (researchRecord?.topic ?? '');
  const stock = stockParam || researchRecord?.stock || entryContextRef.current.stock;
  const stockName = stockNameParam || researchRecord?.stockName || entryContextRef.current.stockName;
  const cycle = cycleParam || researchRecord?.cycle || entryContextRef.current.cycle;

  // 与 ChatPage 的 researchContext 判定保持一致：满足时 ChatPage 自己会在页头
  // 展示「研究议题 · xxx」，顶部条就不再重复同一句话。
  // （这个页面跟着 store 每条流式消息重渲染，所以本机记录在这里缓存，不在渲染里读 localStorage。）
  const currentSessionRecord = useMemo(
    () => findResearchSession(ownerScope, sessionId),
    [ownerScope, sessionId],
  );
  const chatOwnsTitle = Boolean(topic) && (
    (searchParams.get('from') === 'meeting' && (!sessionParam || sessionParam === sessionId))
    || Boolean(currentSessionRecord)
  );

  const conversationEmpty = messages.length === 0 && !loading && !messagesLoading && !historyError;

  /** 写回研究上下文参数。`withStock` 只在发起新研究时用：已有对话改议题不动标的，
     避免把当前 URL 上已经过期/已被 ChatPage 消费的标的写回去。 */
  const writeResearchParams = (nextTopic: string, nextSession: string, withStock: boolean) => {
    setSearchParams((prev) => {
      const next = new URLSearchParams(prev);
      next.set('from', 'meeting');
      next.set('topic', nextTopic);
      if (nextSession) next.set('session', nextSession); else next.delete('session');
      if (withStock) {
        if (stock) next.set('stock', stock); else next.delete('stock');
        if (stockName) next.set('name', stockName); else next.delete('name');
        if (cycle) next.set('cycle', cycle); else next.delete('cycle');
      }
      return next;
    }, { replace: true });
  };

  const openEditor = () => {
    setDraft(topic);
    setTopicError('');
    setEditing(true);
  };

  const cancelEditor = () => {
    setEditing(false);
    setDraft('');
    setTopicError('');
  };

  const saveTopic = () => {
    const next = draft.trim();
    if (!next) {
      setTopicError('先写清这次想研究什么——它会成为这次研究的标题。');
      return;
    }
    const store = useAgentChatStore.getState();
    if (!store.ownerScope) {
      setTopicError('登录状态还没就绪，请稍后再试。');
      return;
    }
    if (conversationEmpty) {
      if (store.loading) {
        setTopicError('上一次研究还在进行中，先等它结束再发起。');
        return;
      }
      setTopicError('');
      setEditing(false);
      // 一次研究 = 一个全新会话；首条消息就是议题，服务端据此生成会话标题。
      store.startNewChat();
      const nextSessionId = useAgentChatStore.getState().sessionId;
      saveResearchSession(store.ownerScope, {
        sessionId: nextSessionId,
        topic: next,
        stock,
        stockName,
        cycle,
        at: new Date().toISOString(),
      });
      writeResearchParams(next, nextSessionId, true);
      void useAgentChatStore.getState().startStream({
        message: next,
        ...(stock ? { context: { stock_code: stock, stock_name: stockName } } : {}),
      });
      return;
    }
    // 已有对话时改议题只影响这次研究的标记与回看，不会重命名服务端会话标题。
    setTopicError('');
    setEditing(false);
    saveResearchSession(store.ownerScope, {
      sessionId,
      topic: next,
      stock,
      stockName,
      cycle,
      at: new Date().toISOString(),
    });
    writeResearchParams(next, sessionId, false);
  };

  // ChatPage 自己也会写 document.title；放到下一个任务里执行，让最终标题与议题一致。
  useEffect(() => {
    const title = topic
      ? `研究议题 · ${topic.slice(0, TITLE_TOPIC_LIMIT)} - 乐子乌智能投研平台`
      : '投研团队 - 乐子乌智能投研平台';
    const timer = window.setTimeout(() => {
      document.title = title;
    }, 0);
    return () => window.clearTimeout(timer);
  }, [topic]);

  return (
    <div className="meeting-chat-page" data-testid="meeting-chat-page">
      <MeetingChatHeader
        roles={roles}
        topic={topic}
        chatOwnsTitle={chatOwnsTitle}
        editing={editing}
        draft={draft}
        error={topicError}
        startsResearch={conversationEmpty}
        onEdit={openEditor}
        onDraftChange={setDraft}
        onSave={saveTopic}
        onCancel={cancelEditor}
      />
      <div className="meeting-chat-embed">
        <ChatPage />
      </div>
    </div>
  );
};

const InvestmentMeetingPage = () => {
  const [searchParams] = useSearchParams();
  const isMobile = useIsMobile();
  const taskId = searchParams.get('export_task') || '';
  const embedParams = new URLSearchParams({ embed: '1' });
  if (/^essay-export-[a-zA-Z0-9-]{1,100}$/.test(taskId)) embedParams.set('export_task', taskId);

  useEffect(() => {
    document.title = '投研团队 - 乐子乌智能投研平台';
  }, []);

  if (isMobile) {
    return <InvestmentMeetingMobile />;
  }

  // 桌面端整页内容由 /meeting-embed 独立渲染（见 docs/smart-research-team.md），
  // 本页只负责外层容器：按应用外壳的 padding 与免责声明高度收敛，避免 iframe 把页面顶出视口。
  return (
    <div className="meeting-frame meeting-frame--desktop w-full overflow-hidden">
      <iframe
        src={`/meeting-embed?${embedParams}`}
        title="投研团队"
        className="h-full w-full border-0"
      />
    </div>
  );
};

export default InvestmentMeetingPage;
