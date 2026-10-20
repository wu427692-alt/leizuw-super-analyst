import type { MonitorEvent } from '../types/investmentMonitor';

// Provider routing stays in the record; readers see the actual source label.
export function newsPublisher(event: Pick<MonitorEvent, 'sourceName' | 'sourceKey'>): string {
  const name = (event.sourceName || '').trim();
  if (!name || name === event.sourceKey || /^tushare[._]/i.test(name)) return '';
  return name.replace(/\btushare\b/gi, '').replace(/^[\s·:：|—-]+|[\s·:：|—-]+$/g, '').trim();
}
