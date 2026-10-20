export const CHANNEL_LABELS: Record<string, string> = {
  company: '公司公告', news: '财经快讯', research: '券商研报', institution: '机构调研', capital: '资金席位',
  ownership: '股权事项', governance: '公司治理', fundamental: '财务业绩', enterprise: '企业风险', essay: '知识星球',
  market: '市场行情', technical: '技术因子', other: '其他',
};

export function sourceRecordUrl(value: unknown): string | null {
  if (typeof value !== 'string') return null;
  try {
    const url = new URL(value);
    return ['http:', 'https:'].includes(url.protocol) && !url.username && !url.password ? url.href : null;
  } catch { return null; }
}

export function eventTime(value?: string | null) {
  if (!value) return '—';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' });
}

// Research records use UTC in storage/API, unlike exchange quote timestamps.
export function researchInstant(value?: string | null) {
  if (!value) return NaN;
  const text = value.trim();
  const normalized = /^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?$/.test(text)
    ? `${text.replace(' ', 'T')}Z` : text;
  return Date.parse(normalized);
}

export function researchTime(value?: string | null) {
  if (!value) return '—';
  if (/^\d{4}-\d{2}-\d{2}$/.test(value.trim())) return value.trim();
  const instant = researchInstant(value);
  return Number.isFinite(instant) ? new Intl.DateTimeFormat('zh-CN', {
    timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', hourCycle: 'h23',
  }).format(new Date(instant)) : '未注明';
}

export function evidenceTime(event: MonitorEvent) {
  if (event.metrics.timePrecision === 'date') {
    const date = event.metrics.disclosureDate;
    if (typeof date === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(date)) return date;
    return researchTime(event.eventAt).split(' ')[0];
  }
  return researchTime(event.eventAt);
}
import type { MonitorEvent } from '../../types/investmentMonitor';
