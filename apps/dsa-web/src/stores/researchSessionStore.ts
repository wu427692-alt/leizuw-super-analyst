/**
 * 研投会议 ↔ 会话 的本地对应关系。
 *
 * 会议每发起一次研究都会新建一个 chat 会话，服务端用首条用户消息（研究议题）
 * 生成会话标题；前端额外记住「这个会话是从研究议题发起的」，用于：
 * 1. 在 chat 里显示「本次研究议题：xxx」页头，并给出返回「研究议题」的入口；
 * 2. 手机端「团队」页在本机回看你最近发起的研究。
 *
 * 只落 localStorage（按登录身份隔离），不新增后端契约；存储受限（隐私模式等）
 * 时静默降级，不影响会议发起。
 */
export interface ResearchSessionRecord {
  sessionId: string;
  topic: string;
  stock: string;
  stockName: string;
  cycle: string;
  at: string;
}

export const RESEARCH_SESSION_LIMIT = 8;

const STORAGE_PREFIX = 'dsa:research-sessions:v1:';

const storageKey = (owner: string | null) => `${STORAGE_PREFIX}${owner ?? 'anon'}`;

const isResearchSessionRecord = (value: unknown): value is ResearchSessionRecord => {
  if (!value || typeof value !== 'object') return false;
  const item = value as Record<string, unknown>;
  return (
    typeof item.sessionId === 'string'
    && item.sessionId.length > 0
    && typeof item.topic === 'string'
    && typeof item.stock === 'string'
    && typeof item.stockName === 'string'
    && typeof item.cycle === 'string'
    && typeof item.at === 'string'
  );
};

/** 读取当前身份下最近发起的研究，新记录在前。 */
export function readResearchSessions(owner: string | null): ResearchSessionRecord[] {
  try {
    if (typeof localStorage === 'undefined') return [];
    const raw = localStorage.getItem(storageKey(owner));
    if (!raw) return [];
    const parsed: unknown = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    return parsed.filter(isResearchSessionRecord).slice(0, RESEARCH_SESSION_LIMIT);
  } catch {
    return [];
  }
}

/** 记录一次研究（同一会话重复记录时去重并置顶），返回更新后的列表。 */
export function saveResearchSession(owner: string | null, record: ResearchSessionRecord): ResearchSessionRecord[] {
  const rest = readResearchSessions(owner).filter((item) => item.sessionId !== record.sessionId);
  const next = [record, ...rest].slice(0, RESEARCH_SESSION_LIMIT);
  try {
    if (typeof localStorage !== 'undefined') {
      localStorage.setItem(storageKey(owner), JSON.stringify(next));
    }
  } catch {
    /* 存储不可用时只影响本机记忆，会议本身照常发起。 */
  }
  return next;
}

/** 按会话 id 找回研究记录；不是研究发起的会话返回 null。 */
export function findResearchSession(owner: string | null, sessionId: string): ResearchSessionRecord | null {
  if (!sessionId) return null;
  return readResearchSessions(owner).find((item) => item.sessionId === sessionId) ?? null;
}
