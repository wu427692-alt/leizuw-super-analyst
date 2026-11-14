import React, { useState, useRef, useEffect, useCallback, useMemo } from 'react';
import { useLocation, useNavigate, useSearchParams } from 'react-router-dom';
import { useUserAccess } from '../contexts/UserAccessContext';
import Markdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { ChevronDown, Settings2, SlidersHorizontal } from 'lucide-react';
import { cn } from '../utils/cn';
import { agentApi } from '../api/agent';
import { systemConfigApi } from '../api/systemConfig';
import {
  ApiErrorAlert,
  Badge,
  Button,
  ConfirmDialog,
  EmptyState,
  InlineAlert,
  MetricRow,
  ScrollArea,
  ShareBar,
  Tooltip,
} from '../components/common';
import { getParsedApiError } from '../api/error';
import type { SkillInfo } from '../api/agent';
import { DashboardStateBlock } from '../components/dashboard';
import { PixelAvatar } from '../components/illustration/PixelAvatar';
import {
  useAgentChatStore,
  type Message,
  type ProgressStep,
} from '../stores/agentChatStore';
import { downloadSession, formatSessionAsMarkdown } from '../utils/chatExport';
import type { ChatFollowUpContext } from '../utils/chatFollowUp';
import {
  buildFollowUpPrompt,
  parseFollowUpRecordId,
  resolveChatFollowUpContext,
  sanitizeFollowUpStockCode,
  sanitizeFollowUpStockName,
} from '../utils/chatFollowUp';
import { isNearBottom } from '../utils/chatScroll';
import { getReportText } from '../utils/reportLanguage';
import { extractStockCodesFromMessage } from '../utils/chatStockCode';
import { findMatchingStockCode, includesStockCode, normalizeStockCode } from '../utils/stockCode';
import { findResearchSession } from '../stores/researchSessionStore';
import { useIsMobile } from '../hooks/useIsMobile';

// Quick question examples shown on empty state
const QUICK_QUESTIONS = [
  { label: '用缠论分析茅台', skill: 'chan_theory' },
  { label: '波浪理论看宁德时代', skill: 'wave_theory' },
  { label: '分析比亚迪趋势', skill: 'bull_trend' },
  { label: '箱体震荡技能看中芯国际', skill: 'box_oscillation' },
  { label: '分析腾讯 hk00700', skill: 'bull_trend' },
  { label: '用情绪周期分析东方财富', skill: 'emotion_cycle' },
];

const MAX_SELECTED_SKILLS = 3;

/**
 * 「研究议题 → 会话」带进 chat 的参数。切换 / 新开会话时必须清掉，
 * 否则页头会把上一个研究的议题串到别的会话上。
 */
const MEETING_QUERY_KEYS = ['from', 'topic', 'stock', 'name', 'cycle'] as const;
const CONTEXT_COMPRESSION_CONFIG_KEY = 'AGENT_CONTEXT_COMPRESSION_ENABLED';
const STRONG_COMPARE_STOCK_MESSAGE_RE = /比较|对比|\bvs\b|和[^，。,.!?！？]{0,40}比/i;
const WEAK_COMPARE_STOCK_MESSAGE_RE = /差异(?!化)|区别|不同|相比|对照|比一比/;
const CHOICE_COMPARE_STOCK_MESSAGE_RE = /哪个|哪只|哪一个|谁更|更值得|更适合|怎么选|选哪|二选一/;
const LINKED_COMPARE_STOCK_MESSAGE_RE = /(?:和|与|跟|同)[^，。,.!?！？]{0,40}(?:差异(?!化)|区别|不同|相比|对照|比一比)/;
const SWITCH_STOCK_MESSAGE_RE = /换成|改看|分析|看看|研究|诊断/;

type ActiveStockContext = Pick<ChatFollowUpContext, 'stock_code' | 'stock_name'>;
type ActiveStockResolution = {
  context: ActiveStockContext;
  useForCurrentSend: boolean;
};

/**
 * 取「当前真实地址」的 query。
 *
 * react-router 的 `useLocation()` / `useSearchParams()` 是**本次渲染的快照**：同一 tick 里
 * 别的组件刚 navigate 过时，浏览器地址已经变了、渲染快照还是旧的。回写 URL 时若拿快照
 * 重建参数，就会把别人刚写入的键（典型是 `/meeting` 发起研究时写入的 `session`）冲掉。
 * BrowserRouter 下以 `window.location` 为准；MemoryRouter / 非浏览器环境下路径对不上，
 * 回退到渲染快照。
 */
function readLatestLocationSearch(routerPathname: string, fallback: URLSearchParams): URLSearchParams {
  if (typeof window === 'undefined') return new URLSearchParams(fallback);
  const live = window.location;
  if (live.pathname !== routerPathname) return new URLSearchParams(fallback);
  return new URLSearchParams(live.search.replace(/^\?/, ''));
}

const getMessageSkillNames = (msg: Message): string[] => {
  if (msg.skillNames?.length) return msg.skillNames;
  if (msg.skillName) return [msg.skillName];
  if (msg.skills?.length) return msg.skills;
  if (msg.skill) return [msg.skill];
  return [];
};

const getMessageSkillLabel = (msg: Message): string => getMessageSkillNames(msg).join('、');

const isCompareStockMessage = (
  message: string,
  stockCodes: string[],
  currentStockCode?: string | null,
): boolean => {
  if (STRONG_COMPARE_STOCK_MESSAGE_RE.test(message)) {
    return true;
  }
  const current = currentStockCode ? normalizeStockCode(currentStockCode) : null;
  const newStockCodes = current
    ? stockCodes.filter((code) => code !== current)
    : stockCodes;
  if (newStockCodes.length >= 2) {
    return true;
  }
  if (CHOICE_COMPARE_STOCK_MESSAGE_RE.test(message) && stockCodes.length >= 2) {
    return true;
  }
  if (!WEAK_COMPARE_STOCK_MESSAGE_RE.test(message)) {
    return false;
  }
  if (stockCodes.length >= 2) {
    return true;
  }
  if (!currentStockCode) {
    return false;
  }
  const hasNewStock = stockCodes.some((code) => code !== current);
  return hasNewStock && LINKED_COMPARE_STOCK_MESSAGE_RE.test(message);
};

const resolveActiveStockContextFromMessage = (
  message: string,
  currentContext: ActiveStockContext | null,
): ActiveStockResolution | null => {
  const stockCodes = extractStockCodesFromMessage(message);
  const stockCode = stockCodes[0] ?? null;
  if (!stockCode) {
    return null;
  }

  const isCompare = isCompareStockMessage(message, stockCodes, currentContext?.stock_code);
  const isSwitch = SWITCH_STOCK_MESSAGE_RE.test(message);
  const currentStockCode = currentContext?.stock_code
    ? normalizeStockCode(currentContext.stock_code)
    : null;
  const newStockCodes = currentStockCode
    ? stockCodes.filter((code) => code !== currentStockCode)
    : stockCodes;
  // Explicit switches can mention the old stock; use the single new code when present.
  const targetStockCode = isSwitch && newStockCodes.length === 1
    ? newStockCodes[0]
    : stockCode;
  const isDifferentStock = currentStockCode !== targetStockCode;

  // Compare messages and implicit follow-ups must not rewrite the active stock context.
  if (isCompare || (currentContext && !isSwitch)) {
    return null;
  }

  return {
    context: {
      stock_code: targetStockCode,
      stock_name: currentContext && !isDifferentStock
        ? currentContext.stock_name
        : null,
    },
    // Only explicit switches should affect the context sent with the current request.
    useForCurrentSend: isSwitch && isDifferentStock,
  };
};

const restoreActiveStockContextFromMessages = (messages: Message[]): ActiveStockContext | null => {
  let restoredContext: ActiveStockContext | null = null;
  for (const message of messages) {
    if (message.role !== 'user') {
      continue;
    }
    const resolution = resolveActiveStockContextFromMessage(message.content, restoredContext);
    if (resolution) {
      restoredContext = resolution.context;
    }
  }
  return restoredContext;
};

const ChatWorkspace: React.FC<{ canManageGlobals: boolean }> = ({ canManageGlobals }) => {
  const navigate = useNavigate();
  const location = useLocation();
  const [searchParams, setSearchParams] = useSearchParams();
  // 手机/平板（≤1023px，与 index.css 的 chat 版式断点一致）：对话区改为
  // 「AI 回答直接铺在页面底色上、只有用户消息有气泡、不显示头像」。
  const isMobile = useIsMobile();
  // 从「研究议题」进入的会话：`from=meeting` + `topic` 只影响页头与「返回研究议题」，
  // 不改变 chat 原有的 question/report 追问参数契约。
  const isMeetingEntry = searchParams.get('from') === 'meeting';
  const meetingTopicParam = searchParams.get('topic')?.trim() || '';
  const [input, setInput] = useState('');
  const [skills, setSkills] = useState<SkillInfo[]>([]);
  const [skillsLoading, setSkillsLoading] = useState(false);
  const [skillsError, setSkillsError] = useState<string | null>(null);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const [deletingSessionId, setDeletingSessionId] = useState<string | null>(null);
  const [selectedSkillIds, setSelectedSkillIds] = useState<string[]>([]);
  const [showSkillDesc, setShowSkillDesc] = useState<string | null>(null);
  const [mobileSkillPickerOpen, setMobileSkillPickerOpen] = useState(false);
  // 会话级设置（全局上下文压缩等）默认收起：它不属于「提问」这条主流程，
  // 展开入口放在输入区右侧，避免把设置项平铺在输入框上方。规范 §10。
  const [sessionSettingsOpen, setSessionSettingsOpen] = useState(false);
  const [expandedThinking, setExpandedThinking] = useState<Set<string>>(new Set());
  const [deleteConfirmId, setDeleteConfirmId] = useState<string | null>(null);
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [sending, setSending] = useState(false);
  const [isFollowUpContextLoading, setIsFollowUpContextLoading] = useState(false);
  const [sendToast, setSendToast] = useState<{
    type: 'success' | 'error';
    message: string;
  } | null>(null);
  const [contextCompressionEnabled, setContextCompressionEnabled] = useState(false);
  const [contextCompressionLoaded, setContextCompressionLoaded] = useState(false);
  const [contextCompressionSaving, setContextCompressionSaving] = useState(false);
  const [contextCompressionConfigVersion, setContextCompressionConfigVersion] = useState('');
  const [contextCompressionMaskToken, setContextCompressionMaskToken] = useState('******');
  const [contextCompressionError, setContextCompressionError] = useState<string | null>(null);
  const [copiedMessages, setCopiedMessages] = useState<Set<string>>(new Set());
  const [showJumpToBottom, setShowJumpToBottom] = useState(false);
  const [watchlistCodes, setWatchlistCodes] = useState<string[]>([]);
  const [isWatchlistActioning, setIsWatchlistActioning] = useState(false);
  const [watchlistMessage, setWatchlistMessage] = useState<string | null>(null);
  const [activeStockCode, setActiveStockCode] = useState<string | null>(null);
  const [activeStockContext, setActiveStockContext] = useState<ActiveStockContext | null>(null);
  const watchlistMessageTimerRef = useRef<number | null>(null);
  const copyResetTimerRef = useRef<Partial<Record<string, number>>>({});
  const messagesViewportRef = useRef<HTMLDivElement>(null);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const isMountedRef = useRef(true);
  const skillsRequestRef = useRef(0);
  const deleteRequestRef = useRef(false);
  const observedSessionParamRef = useRef<string | null | undefined>(undefined);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const sendToastTimerRef = useRef<number | null>(null);
  const followUpHydrationTokenRef = useRef(0);
  const followUpContextRef = useRef<ChatFollowUpContext | null>(null);
  const shouldStickToBottomRef = useRef(true);
  const pendingScrollBehaviorRef = useRef<ScrollBehavior>('auto');

  // Get localized text (default to Chinese)
  const text = getReportText('zh');

  // Cleanup timers on unmount
  useEffect(() => {
    const timers = copyResetTimerRef.current;
    return () => {
      if (sendToastTimerRef.current !== null) {
        window.clearTimeout(sendToastTimerRef.current);
      }
      Object.values(timers).forEach((timerId) => {
        if (timerId !== undefined) {
          window.clearTimeout(timerId);
        }
      });
    };
  }, []);

  // 页面标题在下面随「研究议题上下文」变化：从会议进来是议题，普通进入仍是「问股」。

  useEffect(() => {
    isMountedRef.current = true;
    return () => { isMountedRef.current = false; };
  }, []);

  const loadWatchlist = useCallback(async () => {
    try {
      const codes = await systemConfigApi.getWatchlist();
      if (isMountedRef.current) {
        setWatchlistCodes(codes);
      }
    } catch {
      // ignore error silently
    }
  }, []);

  useEffect(() => {
    void loadWatchlist();
  }, [loadWatchlist]);

  const stockInWatchlist = useCallback(
    (stockCode: string) => includesStockCode(watchlistCodes, stockCode),
    [watchlistCodes],
  );

  const handleToggleWatchlist = useCallback(
    async (stockCode: string) => {
      if (!stockCode || isWatchlistActioning) return;
      setIsWatchlistActioning(true);
      setWatchlistMessage(null);
      try {
        const existingStockCode = findMatchingStockCode(watchlistCodes, stockCode);
        if (existingStockCode) {
          const codes = await systemConfigApi.removeFromWatchlist(existingStockCode);
          if (isMountedRef.current) {
            setWatchlistCodes(codes);
            setWatchlistMessage(`已从自选中移除 ${stockCode}`);
          }
        } else {
          const codes = await systemConfigApi.addToWatchlist(stockCode);
          if (isMountedRef.current) {
            setWatchlistCodes(codes);
            setWatchlistMessage(`已加入自选 ${stockCode}`);
          }
        }
      } catch {
        if (isMountedRef.current) {
          setWatchlistMessage('操作失败，请重试');
        }
      } finally {
        if (isMountedRef.current) {
          setIsWatchlistActioning(false);
          if (watchlistMessageTimerRef.current !== null) {
            window.clearTimeout(watchlistMessageTimerRef.current);
          }
          watchlistMessageTimerRef.current = window.setTimeout(() => {
            if (isMountedRef.current) {
              setWatchlistMessage(null);
            }
          }, 3000);
        }
      }
    },
    [isWatchlistActioning, watchlistCodes],
  );

  const {
    ownerScope,
    messages,
    loading,
    progressSteps,
    sessionId,
    sessions,
    sessionsLoading,
    messagesLoading,
    historyError,
    sessionsError,
    chatError,
    loadSessions,
    loadInitialSession,
    switchSession,
    startStream,
    retrySession,
    stopStream,
    clearCompletionBadge,
  } = useAgentChatStore();

  /**
   * 研究议题上下文：本次 chat 会话是否来自「投研团队 → 研究议题」。
   *
   * - 从会议进来：URL 带 from/topic/session（会话 id 对得上才算，避免切换会话后串题）；
   * - 从侧栏回看这次研究：本机研究记录里能按 sessionId 找回议题。
   * 命中时页头、文档标题、AI 角色名都改用研究语境，并给出返回「研究议题」的入口。
   */
  const meetingSessionParam = searchParams.get('session')?.trim() || '';
  const researchContext = useMemo(() => {
    if (isMeetingEntry && meetingTopicParam && (!meetingSessionParam || meetingSessionParam === sessionId)) {
      return {
        sessionId: meetingSessionParam || sessionId,
        topic: meetingTopicParam,
        stock: searchParams.get('stock')?.trim() || '',
        stockName: searchParams.get('name')?.trim() || '',
        cycle: searchParams.get('cycle')?.trim() || '',
      };
    }
    const stored = findResearchSession(ownerScope, sessionId);
    return stored ? { ...stored } : null;
  }, [isMeetingEntry, meetingTopicParam, meetingSessionParam, sessionId, ownerScope, searchParams]);

  // 页面标题跟着会话走：研究工作会话显示议题，普通会话仍是「问股」。
  useEffect(() => {
    document.title = researchContext
      ? `研究议题 · ${researchContext.topic.slice(0, 20)} - 乐子乌智能投研平台`
      : '问股 - 乐子乌智能投研平台';
  }, [researchContext]);

  const goBackToMeeting = useCallback(() => {
    if (!researchContext) return;
    const params = new URLSearchParams({
      topic: researchContext.topic,
      session: researchContext.sessionId,
    });
    if (researchContext.stock) params.set('stock', researchContext.stock);
    if (researchContext.stockName) params.set('name', researchContext.stockName);
    if (researchContext.cycle) params.set('cycle', researchContext.cycle);
    navigate(`/meeting?${params.toString()}`);
  }, [navigate, researchContext]);

  // 从侧栏回看研究会话时把议题写回 URL：应用栏标题、「返回研究议题」才不会丢上下文。
  //
  // 注意：react-router 的 `setSearchParams(prev => ...)` 里 `prev` 是**本次渲染的快照**，
  // 不是已经排队但还没提交的地址。`/meeting` 发起研究时刚写入 `from/topic/session`，
  // 同一 tick 里这里读到的渲染快照还是旧的，拿它重建参数就会把别人写入的 `session` 冲掉
  //（实测：两次连续 replaceState，第一次带 session，第二次没有）。所以回写前先取
  // 「当前真实地址」合并，只增补自己负责的键，不删别人写的键。
  useEffect(() => {
    if (!researchContext || isMeetingEntry) return;
    const latestSearch = readLatestLocationSearch(location.pathname, searchParams);
    if (latestSearch.get('from') === 'meeting' && latestSearch.get('topic')) return;
    setSearchParams(() => {
      const next = readLatestLocationSearch(location.pathname, searchParams);
      next.set('from', 'meeting');
      next.set('topic', researchContext.topic);
      // 议题属于某次研究会话：深链必须带上它，刷新/回看才能对回同一个会话。
      if (researchContext.sessionId) next.set('session', researchContext.sessionId);
      if (researchContext.stock) next.set('stock', researchContext.stock);
      if (researchContext.stockName) next.set('name', researchContext.stockName);
      if (researchContext.cycle) next.set('cycle', researchContext.cycle);
      return next;
    }, { replace: true });
  }, [researchContext, isMeetingEntry, location.pathname, searchParams, setSearchParams]);

  useEffect(() => {
    if (activeStockContext || messages.length === 0) {
      return;
    }
    const restoredContext = restoreActiveStockContextFromMessages(messages);
    if (!restoredContext) {
      return;
    }
    setActiveStockContext(restoredContext);
    setActiveStockCode(restoredContext.stock_code);
  }, [activeStockContext, messages, sessionId]);

  const syncScrollState = useCallback(() => {
    const viewport = messagesViewportRef.current;
    if (!viewport) return;
    const nearBottom = isNearBottom({
      scrollTop: viewport.scrollTop,
      clientHeight: viewport.clientHeight,
      scrollHeight: viewport.scrollHeight,
    });
    shouldStickToBottomRef.current = nearBottom;
    setShowJumpToBottom((prev) => (nearBottom ? false : prev));
  }, []);

  const scrollToBottom = useCallback((behavior: ScrollBehavior = 'auto') => {
    messagesEndRef.current?.scrollIntoView({ behavior });
  }, []);

  const requestScrollToBottom = useCallback((behavior: ScrollBehavior = 'auto') => {
    shouldStickToBottomRef.current = true;
    pendingScrollBehaviorRef.current = behavior;
    setShowJumpToBottom(false);
  }, []);

  const handleMessagesScroll = useCallback(() => {
    syncScrollState();
  }, [syncScrollState]);

  useEffect(() => {
    syncScrollState();
  }, [syncScrollState, sessionId]);

  useEffect(() => {
    const behavior = pendingScrollBehaviorRef.current;
    const shouldAutoScroll = shouldStickToBottomRef.current;
    if (!shouldAutoScroll) {
      if (messages.length > 0 || progressSteps.length > 0 || loading) {
        setShowJumpToBottom(true);
      }
      return;
    }

    const frame = window.requestAnimationFrame(() => {
      scrollToBottom(behavior);
      pendingScrollBehaviorRef.current = loading ? 'auto' : 'smooth';
    });

    return () => window.cancelAnimationFrame(frame);
  }, [messages, progressSteps, loading, sessionId, scrollToBottom]);

  useEffect(() => {
    if (!loading) {
      pendingScrollBehaviorRef.current = 'smooth';
    }
  }, [loading]);

  useEffect(() => {
    clearCompletionBadge();
  }, [clearCompletionBadge]);

  const requestedSessionId = searchParams.get('session')?.trim() || null;
  useEffect(() => {
    if (observedSessionParamRef.current === undefined) {
      observedSessionParamRef.current = requestedSessionId;
      void loadInitialSession(requestedSessionId ?? undefined);
    } else if (observedSessionParamRef.current !== requestedSessionId) {
      observedSessionParamRef.current = requestedSessionId;
      if (requestedSessionId && requestedSessionId !== sessionId) {
        followUpHydrationTokenRef.current += 1;
        followUpContextRef.current = null;
        setActiveStockContext(null);
        setActiveStockCode(null);
        setInput('');
        void switchSession(requestedSessionId);
      }
    }
  }, [requestedSessionId, sessionId, loadInitialSession, switchSession]);

  const loadSkills = useCallback(async () => {
    const requestId = ++skillsRequestRef.current;
    setSkillsLoading(true);
    setSkillsError(null);
    try {
      const res = await agentApi.getSkills();
      if (isMountedRef.current && requestId === skillsRequestRef.current) {
        setSkills(res.skills);
        const defaultId =
          res.default_skill_id ||
          res.skills[0]?.id ||
          '';
        setSelectedSkillIds(defaultId ? [defaultId] : []);
      }
    } catch (error) {
      if (isMountedRef.current && requestId === skillsRequestRef.current) {
        setSkillsError(getParsedApiError(error).message || '分析策略暂未加载');
      }
    } finally {
      if (isMountedRef.current && requestId === skillsRequestRef.current) setSkillsLoading(false);
    }
  }, []);

  useEffect(() => { void loadSkills(); }, [loadSkills]);

  useEffect(() => {
    if (!canManageGlobals) return;
    let active = true;

    void systemConfigApi.getConfig(false)
      .then((config) => {
        if (!active) {
          return;
        }
        const enabledItem = config.items.find((item) => item.key === CONTEXT_COMPRESSION_CONFIG_KEY);
        setContextCompressionEnabled(String(enabledItem?.value ?? '').trim().toLowerCase() === 'true');
        setContextCompressionConfigVersion(config.configVersion);
        setContextCompressionMaskToken(config.maskToken || '******');
        setContextCompressionLoaded(true);
        setContextCompressionError(null);
      })
      .catch((error) => {
        if (!active) {
          return;
        }
        const parsed = getParsedApiError(error);
        setContextCompressionLoaded(false);
        setContextCompressionError(parsed.message || '无法读取上下文压缩配置');
        console.error('Failed to load context compression setting:', error);
      });

    return () => {
      active = false;
    };
  }, [canManageGlobals]);

  const updateContextCompressionEnabled = useCallback(
    async (nextEnabled: boolean) => {
      if (!canManageGlobals || !contextCompressionLoaded || contextCompressionSaving) {
        return;
      }

      const previousEnabled = contextCompressionEnabled;
      setContextCompressionEnabled(nextEnabled);
      setContextCompressionSaving(true);
      setContextCompressionError(null);

      try {
        const result = await systemConfigApi.update({
          configVersion: contextCompressionConfigVersion,
          maskToken: contextCompressionMaskToken,
          reloadNow: true,
          items: [
            {
              key: CONTEXT_COMPRESSION_CONFIG_KEY,
              value: nextEnabled ? 'true' : 'false',
            },
          ],
        });
        setContextCompressionConfigVersion(result.configVersion || contextCompressionConfigVersion);
      } catch (error) {
        const parsed = getParsedApiError(error);
        setContextCompressionEnabled(previousEnabled);
        setContextCompressionError(parsed.message || '上下文压缩设置保存失败');
      } finally {
        setContextCompressionSaving(false);
      }
    },
    [
      canManageGlobals,
      contextCompressionConfigVersion,
      contextCompressionEnabled,
      contextCompressionLoaded,
      contextCompressionMaskToken,
      contextCompressionSaving,
    ],
  );

  const availableSkillIds = new Set(skills.map((skill) => skill.id));
  const quickQuestions = QUICK_QUESTIONS.filter((question) => availableSkillIds.has(question.skill));
  const selectedSkillIdSet = new Set(selectedSkillIds);
  const skillLimitReached = selectedSkillIds.length >= MAX_SELECTED_SKILLS;

  const getSkillNames = useCallback(
    (skillIds: string[]) => skillIds.map((id) => skills.find((s) => s.id === id)?.name || id),
    [skills],
  );

  const normalizeSelectedSkillIds = useCallback((skillIds: string[]) => {
    const normalized: string[] = [];
    for (const skillId of skillIds) {
      const cleaned = skillId.trim();
      if (cleaned && !normalized.includes(cleaned)) {
        normalized.push(cleaned);
      }
    }
    return normalized.slice(0, MAX_SELECTED_SKILLS);
  }, []);

  const toggleSkillSelection = useCallback((skillId: string) => {
    setSelectedSkillIds((prev) => {
      if (prev.includes(skillId)) {
        return prev.filter((id) => id !== skillId);
      }
      if (prev.length >= MAX_SELECTED_SKILLS) {
        return prev;
      }
      return [...prev, skillId];
    });
  }, []);

  const handleStartNewChat = useCallback(() => {
    followUpHydrationTokenRef.current += 1;
    followUpContextRef.current = null;
    setIsFollowUpContextLoading(false);
    setActiveStockContext(null);
    setActiveStockCode(null);
    setInput('');
    setDeleteError(null);
    requestScrollToBottom('auto');
    useAgentChatStore.getState().startNewChat();
    observedSessionParamRef.current = null;
    setSearchParams((prev) => {
      const next = new URLSearchParams(prev);
      next.delete('session');
      MEETING_QUERY_KEYS.forEach((key) => next.delete(key));
      return next;
    }, { replace: true });
    setSidebarOpen(false);
  }, [requestScrollToBottom, setSearchParams]);

  const handleSwitchSession = useCallback((targetSessionId: string) => {
    if (targetSessionId === sessionId) {
      setSidebarOpen(false);
      return;
    }
    followUpHydrationTokenRef.current += 1;
    followUpContextRef.current = null;
    setIsFollowUpContextLoading(false);
    setActiveStockContext(null);
    setActiveStockCode(null);
    setInput('');
    requestScrollToBottom('auto');
    observedSessionParamRef.current = targetSessionId;
    setSearchParams((prev) => {
      const next = new URLSearchParams(prev);
      next.set('session', targetSessionId);
      // 研究议题只属于发起它的那个会话：切走就不再展示。
      MEETING_QUERY_KEYS.forEach((key) => next.delete(key));
      return next;
    });
    void switchSession(targetSessionId);
    setSidebarOpen(false);
  }, [requestScrollToBottom, sessionId, switchSession, setSearchParams]);

  const confirmDelete = useCallback(() => {
    if (!deleteConfirmId || deleteRequestRef.current) return;
    const targetSessionId = deleteConfirmId;
    deleteRequestRef.current = true;
    setDeletingSessionId(targetSessionId);
    setDeleteError(null);
    agentApi.deleteChatSession(targetSessionId)
      .then(() => {
        if (!isMountedRef.current) return;
        void loadSessions();
        if (targetSessionId === useAgentChatStore.getState().sessionId) {
          handleStartNewChat();
        }
      })
      .catch((error) => {
        if (isMountedRef.current) setDeleteError(getParsedApiError(error).message || '删除失败，请重试');
      })
      .finally(() => {
        deleteRequestRef.current = false;
        if (isMountedRef.current) setDeletingSessionId(null);
      });
    setDeleteConfirmId(null);
  }, [deleteConfirmId, loadSessions, handleStartNewChat]);

  // Prepared research questions carry an explicit stock identity; report
  // follow-ups may additionally hydrate their saved analysis by recordId.
  useEffect(() => {
    // 研究议题发起的会话已经把议题作为首条消息发出，URL 上的 stock / name / cycle
    // 只用于页头展示和「返回研究议题」，不能在这里预填成一句追问。
    if (searchParams.get('from') === 'meeting') return;
    const preparedPrompt = searchParams.get('prompt')?.trim();
    const stock = sanitizeFollowUpStockCode(searchParams.get('stock'));
    const name = sanitizeFollowUpStockName(searchParams.get('name'));
    if (preparedPrompt) {
      if (stock) {
        // Do not infer a route's company from prose: comparing facts with
        // expectations is not a request to retain the previous session stock.
        // A prepared question is not a request to attach an older report.
        followUpHydrationTokenRef.current += 1;
        const context = { stock_code: stock, stock_name: name };
        followUpContextRef.current = context;
        setActiveStockCode(stock);
        setActiveStockContext(context);
        setIsFollowUpContextLoading(false);
      }
      setInput(preparedPrompt.slice(0, 12000));
      setSearchParams((prev) => {
        const next = new URLSearchParams(prev);
        for (const key of ['prompt', 'stock', 'name', 'recordId']) next.delete(key);
        return next;
      }, { replace: true });
      return;
    }
    const recordId = parseFollowUpRecordId(searchParams.get('recordId'));

    if (!stock) return;

    const hydrationToken = ++followUpHydrationTokenRef.current;
    setInput(buildFollowUpPrompt(stock, name));
    setActiveStockCode(stock);
    setActiveStockContext({
      stock_code: stock,
      stock_name: name,
    });
    followUpContextRef.current = {
      stock_code: stock,
      stock_name: name,
    };
    if (recordId !== undefined) {
      setIsFollowUpContextLoading(true);
    }
    void resolveChatFollowUpContext({
      stockCode: stock,
      stockName: name,
      recordId,
    }).then((context) => {
      if (!isMountedRef.current || followUpHydrationTokenRef.current !== hydrationToken) {
        return;
      }
      followUpContextRef.current = context;
    }).finally(() => {
      if (isMountedRef.current && followUpHydrationTokenRef.current === hydrationToken) {
        setIsFollowUpContextLoading(false);
      }
    });
    setSearchParams((prev) => {
      const next = new URLSearchParams(prev);
      for (const key of ['stock', 'name', 'recordId']) next.delete(key);
      return next;
    }, { replace: true });
  }, [searchParams, setSearchParams]);

  const handleSend = useCallback(
    async (overrideMessage?: string, overrideSkillIds?: string[]) => {
      const msgText = (overrideMessage ?? input).trim();
      if (!msgText || loading || messagesLoading || historyError) return;
      const usedSkillIds = normalizeSelectedSkillIds(overrideSkillIds ?? selectedSkillIds);
      const usedSkillNames = usedSkillIds.length > 0 ? getSkillNames(usedSkillIds) : ['通用'];

      let nextActiveStockContext = activeStockContext;
      let useActiveContextForThisSend = false;
      const stockResolution = resolveActiveStockContextFromMessage(msgText, activeStockContext);
      if (stockResolution) {
        nextActiveStockContext = stockResolution.context;
        useActiveContextForThisSend = stockResolution.useForCurrentSend;
        setActiveStockContext(nextActiveStockContext);
        setActiveStockCode(nextActiveStockContext.stock_code);
      }
      const contextForSend = useActiveContextForThisSend
        ? nextActiveStockContext
        : followUpContextRef.current ?? nextActiveStockContext ?? undefined;

      const payload = {
        message: msgText,
        session_id: sessionId,
        ...(usedSkillIds.length > 0 ? { skills: usedSkillIds } : {}),
        context: contextForSend ?? undefined,
      };
      followUpHydrationTokenRef.current += 1;
      followUpContextRef.current = null;
      setIsFollowUpContextLoading(false);

      setInput('');
      setMobileSkillPickerOpen(false);
      observedSessionParamRef.current = sessionId;
      setSearchParams((prev) => { const next = new URLSearchParams(prev); next.set('session', sessionId); return next; }, { replace: true });
      requestScrollToBottom('smooth');
      await startStream(payload, {
        skillNames: usedSkillNames,
        skillName: usedSkillNames.join('、'),
      });
    },
    [activeStockContext, getSkillNames, input, loading, messagesLoading, historyError, normalizeSelectedSkillIds, requestScrollToBottom, selectedSkillIds, sessionId, startStream, setSearchParams],
  );

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing && e.keyCode !== 229) {
      e.preventDefault();
      handleSend();
    }
  };

  const handleQuickQuestion = (q: (typeof QUICK_QUESTIONS)[0]) => {
    setSelectedSkillIds([q.skill]);
    handleSend(q.label, [q.skill]);
  };

  const showSendFeedback = useCallback((nextToast: { type: 'success' | 'error'; message: string }, durationMs: number) => {
    if (sendToastTimerRef.current !== null) {
      window.clearTimeout(sendToastTimerRef.current);
    }
    setSendToast(nextToast);
    sendToastTimerRef.current = window.setTimeout(() => {
      setSendToast(null);
      sendToastTimerRef.current = null;
    }, durationMs);
  }, []);

  const toggleThinking = (msgId: string) => {
    setExpandedThinking((prev) => {
      const next = new Set(prev);
      if (next.has(msgId)) next.delete(msgId);
      else next.add(msgId);
      return next;
    });
  };

  const copyMessageToClipboard = async (msgId: string, content: string) => {
    try {
      await navigator.clipboard.writeText(content);
      setCopiedMessages((prev) => new Set(prev).add(msgId));
      const existingTimer = copyResetTimerRef.current[msgId];
      if (existingTimer !== undefined) {
        window.clearTimeout(existingTimer);
      }
      copyResetTimerRef.current[msgId] = window.setTimeout(() => {
        setCopiedMessages((prev) => {
          const next = new Set(prev);
          next.delete(msgId);
          return next;
        });
        delete copyResetTimerRef.current[msgId];
      }, 2000);
    } catch (err) {
      console.error('Copy failed:', err);
    }
  };

  const downloadMessageAsMarkdown = useCallback((msg: Message) => {
    const skillLabel = getMessageSkillLabel(msg);
    const heading = msg.role === 'user' ? '# 用户消息' : `# AI 回复${skillLabel ? ` · ${skillLabel}` : ''}`;
    const content = [heading, '', msg.content].join('\n');
    const blob = new Blob([content], { type: 'text/markdown;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement('a');
    anchor.href = url;
    anchor.download = `${msg.role === 'user' ? 'user' : 'assistant'}-message-${msg.id}.md`;
    document.body.appendChild(anchor);
    anchor.click();
    document.body.removeChild(anchor);
    URL.revokeObjectURL(url);
  }, []);

  const getCurrentStage = (steps: ProgressStep[]): string => {
    if (steps.length === 0) return '正在连接...';
    const last = steps[steps.length - 1];
    if (last.type === 'thinking') return last.message || 'AI 正在思考...';
    if (last.type === 'tool_start')
      return `${last.display_name || last.tool}...`;
    if (last.type === 'tool_done')
      return `${last.display_name || last.tool} 完成`;
    if (last.type === 'generating')
      return last.message || '正在生成最终分析...';
    return '处理中...';
  };

  const renderThinkingBlock = (msg: Message) => {
    if (!msg.thinkingSteps || msg.thinkingSteps.length === 0) return null;
    const isExpanded = expandedThinking.has(msg.id);
    const toolSteps = msg.thinkingSteps.filter((s) => s.type === 'tool_done');
    const totalDuration = toolSteps.reduce(
      (sum, s) => sum + (s.duration || 0),
      0,
    );
    const summary = `${toolSteps.length} 个工具调用 · ${totalDuration.toFixed(1)}s`;

    return (
      <button
        onClick={() => toggleThinking(msg.id)}
        className="flex items-center gap-2 text-xs text-muted-text hover:text-secondary-text transition-colors mb-2 w-full text-left"
      >
        <svg
          className={`w-3 h-3 transition-transform flex-shrink-0 ${isExpanded ? 'rotate-90' : ''}`}
          fill="none"
          stroke="currentColor"
          viewBox="0 0 24 24"
        >
          <path
            strokeLinecap="round"
            strokeLinejoin="round"
            strokeWidth={2}
            d="M9 5l7 7-7 7"
          />
        </svg>
        <span className="flex items-center gap-1.5">
          <span className="opacity-60">思考过程</span>
          <span className="text-muted-text/50">·</span>
          <span className="opacity-50">{summary}</span>
        </span>
      </button>
    );
  };

  const renderThinkingDetails = (steps: ProgressStep[]) => (
    <div className="mb-3 pl-5 border-l border-border/40 space-y-1.5 animate-fade-in">
      {steps.map((step, idx) => {
        let statusClass = 'chat-progress-item-muted';
        let iconClass = 'chat-progress-dot-muted';
        let text = '';
        if (step.type === 'thinking') {
          text = step.message || `第 ${step.step} 步：思考`;
          statusClass = 'chat-progress-item-thinking';
          iconClass = 'chat-progress-dot-thinking';
        } else if (step.type === 'tool_start') {
          text = `${step.display_name || step.tool}...`;
          statusClass = 'chat-progress-item-tool';
          iconClass = 'chat-progress-dot-tool';
        } else if (step.type === 'tool_done') {
          text = `${step.display_name || step.tool} (${step.duration}s)`;
          statusClass = step.success ? 'chat-progress-item-success' : 'chat-progress-item-danger';
          iconClass = step.success ? 'chat-progress-dot-success' : 'chat-progress-dot-danger';
        } else if (step.type === 'generating') {
          text = step.message || '生成分析';
          statusClass = 'chat-progress-item-generating';
          iconClass = 'chat-progress-dot-generating';
        }
        return (
          <div
            key={idx}
            className={cn('chat-progress-item', statusClass)}
          >
            <span className={cn('chat-progress-dot', iconClass)} />
            <span className="leading-relaxed">{text}</span>
          </div>
        );
      })}
    </div>
  );

  const sidebarContent = (
    <>
      <div className="flex items-center justify-between border-b border-line-1 px-3 py-2">
        <h2 className="text-sm font-semibold text-cyan uppercase tracking-normal flex items-center gap-2">
          <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z" />
          </svg>
          历史对话
        </h2>
        <button
          onClick={handleStartNewChat}
          className="rounded-lg p-1.5 text-muted-text transition-colors hover:bg-bg-3 hover:text-foreground"
          aria-label="开启新对话"
        >
          <svg
            className="w-4 h-4"
            fill="none"
            stroke="currentColor"
            viewBox="0 0 24 24"
          >
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              strokeWidth={2}
              d="M12 4v16m8-8H4"
            />
          </svg>
        </button>
      </div>
      <ScrollArea testId="chat-session-list-scroll" viewportClassName="p-3">
        {sessionsError && (
          <div className="mb-3 space-y-2 text-xs" role="status">
            <p className="text-secondary-text">对话列表暂未更新：{sessionsError.message}</p>
            <Button variant="secondary" size="sm" disabled={sessionsLoading} onClick={() => void loadSessions()}>重试对话列表</Button>
          </div>
        )}
        {sessionsLoading && sessions.length === 0 ? (
          <DashboardStateBlock
            loading
            compact
            title="加载对话中..."
            className="rounded-lg border border-dashed border-line-2 bg-bg-1"
          />
        ) : sessions.length === 0 && !sessionsError ? (
          <DashboardStateBlock
            compact
            title="暂无历史对话"
            description="开始提问后，这里会保留会话记录。"
            className="rounded-lg border border-dashed border-line-2 bg-bg-1"
          />
        ) : (
          <div className="space-y-2">
            {sessions.map((s) => (
              <div key={s.session_id} className="session-item-row">
                <button
                  type="button"
                  onClick={() => handleSwitchSession(s.session_id)}
                  className={`session-item ${s.session_id === sessionId ? 'active' : ''}`}
                  aria-label={`切换到对话 ${s.title}`}
                  aria-current={s.session_id === sessionId ? 'page' : undefined}
                >
                  <div className="indicator" />
                  <div className="content">
                    <span className="title">{s.title}</span>
                    <div className="mt-0.5 flex items-center gap-2">
                      <span className="meta">
                        {s.message_count} 条对话
                      </span>
                      {s.last_active && (
                        <>
                          <span className="separator" />
                          <span className="meta">
                            {new Date(s.last_active).toLocaleDateString('zh-CN', { month: 'short', day: 'numeric' })}
                          </span>
                        </>
                      )}
                    </div>
                  </div>
                </button>
                <button
                  type="button"
                  className="delete-btn"
                  onClick={() => {
                    setDeleteConfirmId(s.session_id);
                  }}
                  aria-label={`删除对话 ${s.title}`}
                  disabled={deletingSessionId !== null}
                >
                  <svg
                    className="w-3.5 h-3.5"
                    fill="none"
                    stroke="currentColor"
                    viewBox="0 0 24 24"
                  >
                    <path
                      strokeLinecap="round"
                      strokeLinejoin="round"
                      strokeWidth={2}
                      d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16"
                    />
                  </svg>
                </button>
              </div>
            ))}
          </div>
        )}
      </ScrollArea>
    </>
  );

  const selectedSkillSummary = selectedSkillIds.length > 0
    ? getSkillNames(selectedSkillIds).join('、')
    : '通用分析';

  /**
   * 会话统计进标题栏（§11）：消息数 / 工具调用次数 / 工具耗时合计，
   * 不再额外占一行统计条。数字一律 tabular-nums 由 Metric 保证。
   */
  const sessionStats = useMemo(() => {
    const toolSteps = messages.flatMap((message) => (
      (message.thinkingSteps || []).filter((step) => step.type === 'tool_done')
    ));
    const toolDuration = toolSteps.reduce((sum, step) => sum + (step.duration || 0), 0);
    return {
      messages: messages.length,
      tools: toolSteps.length,
      duration: toolDuration,
    };
  }, [messages]);

  /** 分析进度用构成占比条表达（§11）：已完成 / 进行中 / 等待。 */
  const progressSegments = useMemo(() => {
    if (!progressSteps.length) return [];
    const done = progressSteps.filter((step) => step.success === true || step.type === 'tool_done').length;
    const pending = Math.max(0, progressSteps.length - done);
    return [
      { key: 'done', value: done, color: 'var(--chart-1)', label: '已完成' },
      { key: 'pending', value: pending, color: 'var(--line-2)', label: '进行中' },
    ].filter((segment) => segment.value > 0);
  }, [progressSteps]);

  return (
    <div
      data-testid="chat-workspace"
      className="chat-workspace flex w-full min-w-0 gap-4 overflow-hidden"
    >
      {/* Desktop sidebar */}
      <div className={cn(
        'h-full w-64 flex-shrink-0 flex-col overflow-hidden rounded-xl border border-line-1 bg-bg-1',
        isMobile ? 'hidden' : 'flex',
      )}>
        {sidebarContent}
      </div>

      {/* Mobile sidebar overlay */}
      {sidebarOpen && (
        <div
          className={cn('chat-sidebar-overlay fixed inset-0 z-40', isMobile ? '' : 'hidden')}
          onClick={() => setSidebarOpen(false)}
        >
          <div className="page-drawer-overlay absolute inset-0" />
          <div
            className="absolute left-0 top-0 bottom-0 w-72 flex flex-col glass-card overflow-hidden border-r border-white/10 bg-card/90 shadow-2xl"
            onClick={(e) => e.stopPropagation()}
          >
            {sidebarContent}
          </div>
        </div>
      )}

      {/* Delete confirmation dialog */}
      <ConfirmDialog
        isOpen={Boolean(deleteConfirmId)}
        title="删除对话"
        message="删除后，该对话将不可恢复，确认删除吗？"
        confirmText="删除"
        cancelText="取消"
        isDanger
        onConfirm={confirmDelete}
        onCancel={() => setDeleteConfirmId(null)}
      />

      {/* Main chat area */}
      <div className="flex h-full min-w-0 flex-1 flex-col overflow-hidden">
        <header className="chat-page-header mb-3 flex-shrink-0 space-y-2">
          <div className="flex items-start justify-between gap-4">
            <h1 className="flex items-center gap-2 text-2xl font-semibold tracking-tight text-foreground">
              <button
                onClick={() => setSidebarOpen(true)}
                className={cn(
                  'p-1.5 -ml-1 rounded-lg hover:bg-hover transition-colors text-secondary-text hover:text-foreground',
                  isMobile ? '' : 'hidden',
                )}
                aria-label="历史对话"
              >
                <svg
                  className="w-5 h-5"
                  fill="none"
                  stroke="currentColor"
                  viewBox="0 0 24 24"
                >
                  <path
                    strokeLinecap="round"
                    strokeLinejoin="round"
                    strokeWidth={2}
                    d="M4 6h16M4 12h16M4 18h16"
                  />
                </svg>
              </button>
              <svg
                className={cn('chat-header-brand w-6 h-6 text-cyan', isMobile ? 'hidden' : 'block')}
                fill="none"
                stroke="currentColor"
                viewBox="0 0 24 24"
              >
                <path
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  strokeWidth={2}
                  d="M9.663 17h4.673M12 3v1m6.364 1.636l-.707.707M21 12h-1M4 12H3m3.343-5.657l-.707-.707m2.828 9.9a5 5 0 117.072 0l-.548.547A3.374 3.374 0 0014 18.469V19a2 2 0 11-4 0v-.531c0-.895-.356-1.754-.988-2.386l-.548-.547z"
                />
              </svg>
              {researchContext ? (
                <span className="flex min-w-0 items-baseline gap-1.5">
                  <span className="shrink-0 text-base font-semibold text-secondary-text md:text-lg">研究议题</span>
                  <span className="truncate text-base font-semibold md:text-2xl">
                    · {researchContext.topic}
                  </span>
                </span>
              ) : (
                <span className={isMobile ? 'hidden' : 'inline'}>问股</span>
              )}
            </h1>
            {messages.length > 0 && (
              <div className="chat-header-actions flex flex-shrink-0 flex-wrap items-center justify-end gap-2">
                <Tooltip content="导出会话为 Markdown 文件">
                  <span className="inline-flex">
                    <Button
                      variant="action-primary"
                      size="sm"
                      onClick={() => downloadSession(messages)}
                      aria-label="导出会话为 Markdown 文件"
                    >
                      <svg
                        className="w-4 h-4"
                        fill="none"
                        stroke="currentColor"
                        viewBox="0 0 24 24"
                      >
                        <path
                          strokeLinecap="round"
                          strokeLinejoin="round"
                          strokeWidth={2}
                          d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4"
                        />
                      </svg>
                      导出会话
                    </Button>
                  </span>
                </Tooltip>
                {canManageGlobals && <Tooltip content="发送到已配置的通知机器人/邮箱">
                  <span className="inline-flex">
                    <Button
                      variant="action-primary"
                      size="sm"
                      disabled={sending}
                      onClick={async () => {
                        if (sending) return;
                        setSending(true);
                        setSendToast(null);
                        try {
                          const content = formatSessionAsMarkdown(messages);
                          await agentApi.sendChat(content);
                          showSendFeedback({ type: 'success', message: '已发送到通知渠道' }, 3000);
                        } catch (err) {
                          const parsed = getParsedApiError(err);
                          showSendFeedback({
                            type: 'error',
                            message: parsed.message || '发送失败',
                          }, 5000);
                        } finally {
                          setSending(false);
                        }
                      }}
                      aria-label="发送到已配置的通知机器人/邮箱"
                    >
                      {sending ? (
                        <svg
                          className="w-4 h-4 animate-spin"
                          fill="none"
                          viewBox="0 0 24 24"
                        >
                          <circle
                            className="opacity-25"
                            cx="12"
                            cy="12"
                            r="10"
                            stroke="currentColor"
                            strokeWidth="4"
                          />
                          <path
                            className="opacity-75"
                            fill="currentColor"
                            d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"
                          />
                        </svg>
                      ) : (
                        <svg
                          className="w-4 h-4"
                          fill="none"
                          stroke="currentColor"
                          viewBox="0 0 24 24"
                        >
                          <path
                            strokeLinecap="round"
                            strokeLinejoin="round"
                            strokeWidth={2}
                            d="M12 19l9 2-9-18-9 18 9-2zm0 0v-8"
                          />
                        </svg>
                      )}
                      发送
                    </Button>
                  </span>
                </Tooltip>}
              </div>
            )}
          </div>
          <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
            <p className="text-secondary-text text-sm">
              {researchContext
                ? '这是围绕本次研究议题展开的会话，可以继续追问；研究结果也保存在这个会话里。'
                : '向 AI 询问个股分析，获取基于技能视角的交易建议与实时决策报告。'}
            </p>
            {sessionStats.messages > 0 ? (
              <div className="chat-header-metrics shrink-0">
              <MetricRow
                columns={3}
                items={[
                  { label: '消息', value: sessionStats.messages },
                  { label: '工具调用', value: sessionStats.tools },
                  {
                    label: '工具耗时',
                    value: sessionStats.duration > 0 ? `${sessionStats.duration.toFixed(1)}s` : null,
                  },
                ]}
              />
              </div>
            ) : null}
          </div>
          {deleteError && <InlineAlert variant="danger" title="对话未删除" message={deleteError} />}
          {sendToast ? (
            <InlineAlert
              variant={sendToast.type === 'success' ? 'success' : 'danger'}
              title={sendToast.type === 'success' ? '发送成功' : '发送失败'}
              message={sendToast.message}
              className="max-w-md rounded-xl px-3 py-2 text-xs shadow-none"
            />
          ) : null}
        </header>

        {/* 研究议题与当前标的合成一条上下文条：同一批上下文只占一行，不再各铺一条。 */}
        {researchContext || activeStockCode ? (
          <div
            data-testid={researchContext ? 'chat-meeting-topic' : undefined}
            className="chat-context-bar mb-2 flex flex-wrap items-center gap-x-4 gap-y-2 rounded-lg border border-line-1 bg-bg-1 px-3 py-2"
          >
            {researchContext ? (
              <>
                <div className="flex min-w-0 flex-1 flex-col gap-0.5">
                  <span className="text-[11px] leading-none text-secondary-text">本次研究议题</span>
                  <strong className="truncate text-sm font-medium text-foreground">
                    {researchContext.topic}
                  </strong>
                  {[researchContext.stockName, researchContext.stock, researchContext.cycle].filter(Boolean).length > 0 ? (
                    <span className="truncate text-[11px] text-muted-text">
                      {[researchContext.stockName, researchContext.stock, researchContext.cycle].filter(Boolean).join(' · ')}
                    </span>
                  ) : null}
                </div>
                <Button variant="secondary" size="xsm" onClick={goBackToMeeting} aria-label="返回研究议题">
                  返回研究议题
                </Button>
              </>
            ) : null}
            {activeStockCode ? (
              <div
                className={cn(
                  'flex min-w-0 items-center gap-2',
                  researchContext && 'shrink-0 border-line-1 pt-1',
                  researchContext && !isMobile && 'md:border-l md:pl-4 md:pt-0',
                )}
              >
                <PixelAvatar seed={activeStockCode} size={24} className="chat-stock-row__avatar" />
                <span className="flex min-w-0 flex-col leading-tight">
                  <span className="truncate text-sm font-medium text-foreground">
                    {activeStockContext?.stock_name?.trim() || activeStockCode}
                  </span>
                  {activeStockContext?.stock_name ? (
                    <span className="font-mono text-[11px] tabular-nums text-muted-text">{activeStockCode}</span>
                  ) : null}
                </span>
                <Button
                  variant="secondary"
                  size="xsm"
                  isLoading={isWatchlistActioning}
                  onClick={() => void handleToggleWatchlist(activeStockCode)}
                >
                  {stockInWatchlist(activeStockCode) ? '从自选删除' : '加入自选'}
                </Button>
                {watchlistMessage ? (
                  <span className="text-[11px] text-muted-text" role="status">{watchlistMessage}</span>
                ) : null}
              </div>
            ) : null}
          </div>
        ) : null}

        <div className="chat-main-pane relative z-10 flex min-h-0 flex-1 flex-col overflow-hidden rounded-xl border border-line-1 bg-bg-1">
          {/* Messages */}
          <ScrollArea
            className="relative z-10 flex-1"
            viewportRef={messagesViewportRef}
            onScroll={handleMessagesScroll}
            viewportClassName={isMobile ? 'chat-message-list--mobile' : 'space-y-4 p-3 md:p-4'}
            testId="chat-message-scroll"
          >
            {messagesLoading && (
              <DashboardStateBlock loading compact title="正在读取历史会话…" description="读取完成后可继续提问，不会重新发起 AI 分析。" />
            )}
            {historyError && (
              <div className="space-y-2" role="status">
                <ApiErrorAlert error={historyError} />
                <Button variant="secondary" size="sm" disabled={messagesLoading || loading} onClick={() => void retrySession()}>重新读取历史会话</Button>
              </div>
            )}
            {messages.length === 0 && !loading && !messagesLoading && !historyError ? (
              <div className="chat-empty-wrap flex h-full items-center justify-center">
                <EmptyState
                  title="开始问股"
                  description="输入「分析 600519」或「茅台现在能买吗」，AI 将调用实时数据工具为您生成决策报告。"
                  className="chat-empty-state max-w-2xl border-dashed bg-card/55"
                  icon={(
                    <svg
                      className="h-8 w-8"
                      fill="none"
                      stroke="currentColor"
                      viewBox="0 0 24 24"
                    >
                      <path
                        strokeLinecap="round"
                        strokeLinejoin="round"
                        strokeWidth={1.5}
                        d="M9.663 17h4.673M12 3v1m6.364 1.636l-.707.707M21 12h-1M4 12H3m3.343-5.657l-.707-.707m2.828 9.9a5 5 0 117.072 0l-.548.547A3.374 3.374 0 0014 18.469V19a2 2 0 11-4 0v-.531c0-.895-.356-1.754-.988-2.386l-.548-.547z"
                      />
                    </svg>
                  )}
                  action={(
                    <div className="flex max-w-lg flex-wrap justify-center gap-2">
                      {quickQuestions.map((q, i) => (
                        <button
                          key={i}
                          onClick={() => handleQuickQuestion(q)}
                          className="quick-question-btn"
                        >
                          {q.label}
                        </button>
                      ))}
                    </div>
                  )}
                />
              </div>
            ) : (
              <>
                {/* 手机端把「AI 问股 / AI 研究小组」这个角色标签从每条消息上方收走，
                    整段会话只在顶部出现一次，把纵向空间还给正文。 */}
                {isMobile ? (
                  <div className="chat-conversation-context" data-testid="chat-conversation-context">
                    <span className="chat-conversation-context__label">
                      {researchContext ? 'AI 研究小组' : 'AI 问股'}
                    </span>
                    {researchContext?.topic ? (
                      <span className="chat-conversation-context__topic">· {researchContext.topic}</span>
                    ) : null}
                  </div>
                ) : null}
                {messages.map((msg) => {
                  const skillLabel = getMessageSkillLabel(msg);
                  const isUser = msg.role === 'user';
                  return (
                  <div
                    key={msg.id}
                    data-chat-row="message"
                    data-role={msg.role}
                    className={cn(
                      'chat-message-row flex',
                      !isMobile && 'gap-4',
                      isUser ? 'flex-row-reverse' : ''
                    )}
                  >
                    {/* 手机端去掉头像：横向空间全部让给正文（桌面端保留）。 */}
                    {!isMobile ? (
                      <div
                        className={cn(
                          'chat-message-avatar flex h-8 w-8 shrink-0 items-center justify-center rounded-full text-[12px] font-bold',
                          isUser ? 'chat-avatar-user' : 'chat-avatar-ai'
                        )}
                      >
                        {isUser ? 'U' : 'AI'}
                      </div>
                    ) : null}
                    <div
                      className={cn(
                        'chat-message-main flex min-w-0 flex-col',
                        isUser ? 'chat-message-main--user' : 'chat-message-main--assistant'
                      )}
                    >
                      <div
                        className={cn(
                          'chat-message-bubble group/message min-w-0 overflow-hidden transition-colors',
                          isUser
                            ? 'chat-bubble-user w-fit'
                            : (isMobile ? 'chat-message-plain' : 'chat-bubble-ai'),
                          isMobile ? 'chat-message-bubble--mobile' : 'w-fit max-w-[min(100%,48rem)] px-4 py-3'
                        )}
                      >
                        {msg.role === 'assistant' && skillLabel && (
                          <div className="mb-2">
                            <Badge variant="info" className="chat-skill-badge shadow-none" aria-label={`技能 ${skillLabel}`}>
                              <svg
                                className="w-3 h-3"
                                fill="none"
                                stroke="currentColor"
                                viewBox="0 0 24 24"
                              >
                                <path
                                  strokeLinecap="round"
                                  strokeLinejoin="round"
                                  strokeWidth={2}
                                  d="M13 10V3L4 14h7v7l9-11h-7z"
                                />
                              </svg>
                              {skillLabel}
                            </Badge>
                          </div>
                        )}
                        {msg.role === 'assistant' && renderThinkingBlock(msg)}
                        {msg.role === 'assistant' &&
                          expandedThinking.has(msg.id) &&
                          msg.thinkingSteps &&
                          renderThinkingDetails(msg.thinkingSteps)}
                        {msg.role === 'assistant' ? (
                          <div className="relative">
                            <div className="chat-message-actions">
                              <button
                                type="button"
                                onClick={() => copyMessageToClipboard(msg.id, msg.content)}
                                className="chat-copy-btn"
                                aria-label={copiedMessages.has(msg.id) ? text.copied : text.copy}
                              >
                                {copiedMessages.has(msg.id) ? text.copied : text.copy}
                              </button>
                              <button
                                type="button"
                                onClick={() => downloadMessageAsMarkdown(msg)}
                                className="chat-copy-btn"
                                aria-label="导出此条消息为 Markdown"
                              >
                                导出
                              </button>
                            </div>
                            <div className="chat-prose pr-20 sm:pr-24">
                              <Markdown remarkPlugins={[remarkGfm]}>
                                {msg.content}
                              </Markdown>
                            </div>
                          </div>
                        ) : (
                          msg.content
                            .split('\n')
                            .map((line, i) => (
                              <p
                                key={i}
                                className="mb-1 last:mb-0 leading-relaxed"
                              >
                                {line || '\u00A0'}
                              </p>
                            ))
                        )}
                      </div>
                    </div>
                  </div>
                  );
                })}
              </>
            )}

            {loading && (
              <div className={cn('chat-streaming-row flex', !isMobile && 'gap-4')}>
                {!isMobile ? (
                  <div className="chat-message-avatar w-8 h-8 rounded-full bg-elevated text-foreground flex items-center justify-center flex-shrink-0 text-xs font-bold">
                    AI
                  </div>
                ) : null}
                <div className={cn(
                  'chat-streaming-card overflow-hidden',
                  isMobile
                    ? 'chat-streaming-card--mobile'
                    : 'min-w-[200px] max-w-[min(100%,48rem)] rounded-xl rounded-tl-sm border border-line-1 bg-bg-1 px-4 py-3',
                )}>
                  <div className="flex items-center gap-2.5 text-sm text-secondary-text">
                    <div className="relative w-4 h-4 flex-shrink-0">
                      <div className="absolute inset-0 rounded-full border-2 border-cyan/20" />
                      <div className="absolute inset-0 rounded-full border-2 border-cyan border-t-transparent animate-spin" />
                    </div>
                    <span className="text-secondary-text">
                      {getCurrentStage(progressSteps)}
                    </span>
                  </div>
                  {progressSegments.length > 0 ? (
                    <div className="mt-2 flex items-center gap-3">
                      <ShareBar
                        segments={progressSegments}
                        className="max-w-48 flex-1"
                        ariaLabel="分析步骤进度"
                      />
                      <span className="shrink-0 text-[11px] tabular-nums text-muted-text">
                        步骤 {progressSteps.filter((step) => step.success === true || step.type === 'tool_done').length}/{progressSteps.length}
                      </span>
                    </div>
                  ) : null}
                </div>
              </div>
            )}

            <div ref={messagesEndRef} />
          </ScrollArea>

          {showJumpToBottom && (
            <div className="chat-jump-to-bottom pointer-events-none absolute bottom-[5.75rem] right-4 z-20 md:bottom-24 md:right-6">
              <button
                type="button"
                className="pointer-events-auto chat-copy-btn shadow-soft-card"
                onClick={() => {
                  requestScrollToBottom('smooth');
                  scrollToBottom('smooth');
                }}
                aria-label="查看最新消息"
              >
                <svg
                  className="h-3.5 w-3.5"
                  fill="none"
                  stroke="currentColor"
                  viewBox="0 0 24 24"
                >
                  <path
                    strokeLinecap="round"
                    strokeLinejoin="round"
                    strokeWidth={2}
                    d="M19 14l-7 7m0 0l-7-7m7 7V3"
                  />
                </svg>
                有新消息
              </button>
            </div>
          )}

          {/* Input area */}
          <div className="chat-composer relative z-20 border-t border-line-1 bg-bg-1 p-3 md:p-4">
            <div className="space-y-2">
              {chatError ? (
                <div className="space-y-2">
                  <ApiErrorAlert error={chatError} />
                  <div className="flex flex-wrap gap-2">
                    <Button variant="secondary" size="sm" disabled={messagesLoading || loading} onClick={() => void retrySession()}>重新读取会话</Button>
                    <Button variant="secondary" size="sm" disabled={loading || !messages.some((message) => message.role === 'user')} onClick={() => {
                      const previous = [...messages].reverse().find((message) => message.role === 'user');
                      if (previous) { setInput(previous.content); inputRef.current?.focus(); }
                    }}>重新编辑上次问题</Button>
                  </div>
                  {progressSteps.length > 0 && (
                    <details className="text-xs text-secondary-text">
                      <summary className="cursor-pointer">查看断开前的处理进度</summary>
                      <div className="mt-2 max-h-32 overflow-y-auto">{renderThinkingDetails(progressSteps)}</div>
                    </details>
                  )}
                </div>
              ) : null}
              {isFollowUpContextLoading ? (
                <InlineAlert
                  variant="info"
                  title="追问上下文加载中"
                  message="正在加载历史分析上下文；现在可直接发送追问。"
                  className="rounded-xl px-3 py-2 text-xs shadow-none"
                />
              ) : null}
              {skillsError && (
                <div className="flex flex-wrap items-center gap-2 text-xs" role="status">
                  <span className="text-secondary-text">分析策略暂未加载，可直接使用通用分析。{skillsError}</span>
                  <Button variant="secondary" size="sm" disabled={skillsLoading} onClick={() => void loadSkills()}>重新加载策略</Button>
                </div>
              )}
              {canManageGlobals && contextCompressionError ? (
                <InlineAlert
                  variant="danger"
                  title="上下文压缩设置未保存"
                  message={contextCompressionError}
                  className="rounded-lg px-3 py-2 text-xs shadow-none"
                />
              ) : null}

              {/* 策略与会话设置都是「默认不需要看、需要能就地展开」的面板（§10）：
                  收成一行触发条，正文区不再被整排复选框占掉。 */}
              {skills.length > 0 || canManageGlobals ? (
                <div className="chat-composer-meta flex flex-wrap items-center gap-2">
                  {skills.length > 0 ? (
                    <button
                      type="button"
                      className="flex h-8 min-w-0 flex-1 items-center justify-between gap-2 rounded-lg border border-line-1 px-2.5 text-left text-xs text-secondary-text hover:text-foreground"
                      aria-label={mobileSkillPickerOpen ? '收起策略选择' : '展开策略选择'}
                      aria-expanded={mobileSkillPickerOpen}
                      aria-controls="chat-skill-picker-panel"
                      onClick={() => setMobileSkillPickerOpen((open) => !open)}
                    >
                      <span className="flex min-w-0 items-center gap-1.5">
                        <SlidersHorizontal className="h-3.5 w-3.5 flex-shrink-0" aria-hidden="true" />
                        <span className="flex-shrink-0 font-medium">策略</span>
                        <span className="truncate text-muted-text">{selectedSkillSummary}</span>
                      </span>
                      <ChevronDown
                        className={cn(
                          'h-3.5 w-3.5 flex-shrink-0 text-muted-text transition-transform',
                          mobileSkillPickerOpen ? 'rotate-180' : '',
                        )}
                        aria-hidden="true"
                      />
                    </button>
                  ) : null}
                  {canManageGlobals ? (
                    <button
                      type="button"
                      className="inline-flex h-8 flex-shrink-0 items-center gap-1.5 rounded-lg border border-line-1 px-2.5 text-xs text-secondary-text hover:text-foreground"
                      aria-label={sessionSettingsOpen ? '收起会话设置' : '会话设置'}
                      aria-expanded={sessionSettingsOpen}
                      aria-controls="chat-session-settings"
                      onClick={() => setSessionSettingsOpen((open) => !open)}
                    >
                      <Settings2 className="h-3.5 w-3.5" aria-hidden="true" />
                      会话设置
                    </button>
                  ) : null}
                </div>
              ) : null}
              {skills.length > 0 ? (
                <div
                  id="chat-skill-picker-panel"
                  data-testid="chat-skill-picker-panel"
                  className={cn(
                    mobileSkillPickerOpen ? 'flex' : 'hidden',
                    'max-h-40 flex-wrap items-start gap-x-5 gap-y-2 overflow-y-auto rounded-lg border border-line-1 bg-bg-1 px-3 py-2',
                  )}
                >
                  <span className="mt-1 flex-shrink-0 text-xs font-medium text-muted-text">策略</span>
                  <label className="mt-0.5 flex cursor-pointer items-center gap-1.5 text-sm">
                    <input
                      type="checkbox"
                      name="general-analysis"
                      value=""
                      checked={selectedSkillIds.length === 0}
                      onChange={() => setSelectedSkillIds([])}
                      className="chat-skill-checkbox"
                    />
                    <span
                      className={`text-sm transition-colors ${selectedSkillIds.length === 0 ? 'font-medium text-foreground' : 'text-secondary-text hover:text-foreground'}`}
                    >
                      通用分析
                    </span>
                  </label>
                  {skills.map((s) => {
                    const checked = selectedSkillIdSet.has(s.id);
                    const disabled = !checked && skillLimitReached;
                    return (
                      <label
                        key={s.id}
                        className={`relative mt-0.5 flex cursor-pointer items-center gap-1.5 ${disabled ? 'cursor-not-allowed opacity-60' : ''}`}
                        onMouseEnter={() => setShowSkillDesc(s.id)}
                        onMouseLeave={() => setShowSkillDesc(null)}
                      >
                        <input
                          type="checkbox"
                          name="skills"
                          value={s.id}
                          checked={checked}
                          disabled={disabled}
                          onChange={() => toggleSkillSelection(s.id)}
                          className="chat-skill-checkbox"
                        />
                        <span
                          className={`text-sm transition-colors ${checked ? 'font-medium text-foreground' : 'text-secondary-text hover:text-foreground'}`}
                        >
                          {s.name}
                        </span>
                        {showSkillDesc === s.id && s.description && (
                          <div className="skill-desc-tooltip">
                            <p className="skill-title">{s.name}</p>
                            <p>{s.description}</p>
                          </div>
                        )}
                      </label>
                    );
                  })}
                </div>
              ) : null}
              {canManageGlobals ? (
                <div
                  id="chat-session-settings"
                  data-testid="chat-session-settings"
                  className={sessionSettingsOpen ? 'block' : 'hidden'}
                >
                  <div className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-line-1 bg-bg-1 px-3 py-2">
                    <label
                      className={cn(
                        'inline-flex items-center gap-2 text-sm',
                        contextCompressionLoaded && !contextCompressionSaving
                          ? 'cursor-pointer text-foreground'
                          : 'cursor-not-allowed text-muted-text',
                      )}
                    >
                      <input
                        type="checkbox"
                        checked={contextCompressionEnabled}
                        disabled={!contextCompressionLoaded || contextCompressionSaving}
                        onChange={(event) => void updateContextCompressionEnabled(event.target.checked)}
                        className="chat-skill-checkbox"
                      />
                      <span className="font-medium">上下文压缩</span>
                    </label>
                    <span className="text-xs text-muted-text">
                      {contextCompressionSaving
                        ? '保存中...'
                        : contextCompressionEnabled
                          ? '已启用'
                          : '未启用'}
                    </span>
                  </div>
                </div>
              ) : null}

              <div className="chat-composer-row flex items-end gap-3">
                <textarea
                  ref={inputRef}
                  value={input}
                  onChange={(e) => setInput(e.target.value)}
                  onKeyDown={handleKeyDown}
                  placeholder="输入研究问题"
                  disabled={loading || messagesLoading || Boolean(historyError)}
                  rows={1}
                  className="chat-composer-input input-surface input-focus-glow flex-1 min-h-[44px] max-h-[200px] rounded-xl border bg-transparent px-4 py-2.5 text-sm transition-all focus:outline-none resize-none disabled:cursor-not-allowed disabled:opacity-60"
                  style={{ height: 'auto' }}
                  onInput={(e) => {
                    const t = e.target as HTMLTextAreaElement;
                    t.style.height = 'auto';
                    t.style.height = `${Math.min(t.scrollHeight, 200)}px`;
                  }}
                />
                {loading ? <Button variant="secondary" onClick={stopStream} className="chat-stop-btn flex-shrink-0">停止等待</Button> : <Button
                  variant="primary"
                  onClick={() => handleSend()}
                  disabled={!input.trim() || messagesLoading || Boolean(historyError)}
                  className="chat-send-btn btn-primary flex-shrink-0"
                >
                  发送
                </Button>}
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
};

const ChatPage: React.FC = () => {
  const { accessEnabled, loggedIn, user, authMethod } = useUserAccess();
  const identity = `${accessEnabled}:${loggedIn}:${user?.id ?? 'none'}:${user?.status ?? ''}:${authMethod ?? ''}`;
  return <ChatWorkspace key={identity} canManageGlobals={!accessEnabled || (loggedIn && authMethod === 'admin')} />;
};

export default ChatPage;
