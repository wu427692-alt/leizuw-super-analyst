import apiClient from './index';
import { toCamelCase } from './utils';

export type EventContentStatus = 'ok' | 'empty' | 'failed';

/**
 * 单条快讯按需抓取的正文结果。
 *
 * 契约（后端按需抓原文抽正文任务）：`GET /api/v1/investment-monitor/events/{event_id}/content`
 * → `{ eventId, status: 'ok' | 'empty' | 'failed', content, source: 'db' | 'fetched', url, fetchedAt }`
 *
 * 只有「首页事件没有已保存正文、但有原文链接」时才调用，避免每条快讯都打一次网络。
 * 契约若调整，只改这一个函数，读报弹层不用动。
 */
export type EventContentResult = {
  eventId: number;
  status: EventContentStatus;
  content: string;
  source: 'db' | 'fetched' | null;
  url: string | null;
  fetchedAt: string | null;
};

const FAILED = (eventId: number): EventContentResult => ({
  eventId, status: 'failed', content: '', source: null, url: null, fetchedAt: null,
});

function isStatus(value: unknown): value is EventContentStatus {
  return value === 'ok' || value === 'empty' || value === 'failed';
}

export async function fetchEventContent(eventId: number): Promise<EventContentResult> {
  try {
    const response = await apiClient.get(`/api/v1/investment-monitor/events/${eventId}/content`, {
      timeout: 20_000,
    });
    const payload = toCamelCase<Partial<EventContentResult>>(response.data ?? {});
    return {
      eventId,
      // 未知状态一律按失败处理：宁可让用户点「阅读原文」，也不把空内容当正文。
      status: isStatus(payload.status) ? payload.status : 'failed',
      content: typeof payload.content === 'string' ? payload.content : '',
      source: payload.source === 'db' || payload.source === 'fetched' ? payload.source : null,
      url: typeof payload.url === 'string' && payload.url ? payload.url : null,
      fetchedAt: typeof payload.fetchedAt === 'string' && payload.fetchedAt ? payload.fetchedAt : null,
    };
  } catch {
    // 网络/404/超时都收敛成 failed，由界面给出「阅读原文」的出路。
    return FAILED(eventId);
  }
}
