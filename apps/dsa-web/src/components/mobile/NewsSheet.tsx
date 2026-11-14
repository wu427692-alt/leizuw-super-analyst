import type React from 'react';
import { useEffect, useState } from 'react';
import { ArrowLeft, ExternalLink, RefreshCw } from 'lucide-react';
import { fetchEventContent } from '../../api/eventContent';
import type { MonitorEvent } from '../../types/investmentMonitor';
import { newsHeadline, newsParagraphs } from '../../utils/newsText';
import { newsPublisher } from '../../utils/newsPublisher';
import { stripHtmlText } from '../../utils/plainText';
import { evidenceTime, sourceRecordUrl } from '../investmentMonitor/investmentMonitorMeta';
import { BottomSheet } from './BottomSheet';
import './NewsSheet.css';

/** 手机端快讯弹层的视图：列表（「更多」进入）或单条正文（点某条快讯进入）。 */
export type NewsSheetView =
  | { kind: 'list' }
  | { kind: 'detail'; eventId: number; fromList: boolean };

export type NewsSheetProps = {
  /** 为 null 时弹层关闭；组件内部不做路由跳转，只负责就地展示。 */
  view: NewsSheetView | null;
  /** 列表 / 正文预览数据，来自首页 dashboard 的 pinnedNews + latestNews。 */
  events: MonitorEvent[];
  loading?: boolean;
  error?: string | null;
  onRetry?: () => void;
  onSelect: (eventId: number, fromList: boolean) => void;
  onBack: () => void;
  onClose: () => void;
};

function sourceLabel(event?: MonitorEvent): string {
  return newsPublisher(event ?? { sourceName: '', sourceKey: '' }) || '来源未注明';
}

function newsTime(event: MonitorEvent): string {
  const label = evidenceTime(event);
  return label && label !== '—' ? label : '时间未注明';
}

/** 一次在线取正文的结果；attempt 用来区分「当前这一轮」和重试前的旧结果。 */
type FetchOutcome = { attempt: number; status: 'ok' | 'failed'; paragraphs: string[]; url: string | null };

/**
 * 单条快讯正文。
 *
 * 数据分两层：
 * - 首页精简事件里已带 `summary`（Tushare 快讯都有正文），有正文就直接渲染，不发请求；
 * - RSS / NewsNow 只有标题和原文链接时，才按需调正文接口（见 `api/eventContent.ts`）。
 *   抓不到就明确说「该来源未提供正文」，并把「阅读原文」做成主按钮。
 *
 * 上游正文一律清洗成纯文本用 React 文本节点渲染，不注入 HTML。
 */
const NewsArticle: React.FC<{ eventId: number; preview?: MonitorEvent; fromList: boolean; onBack: () => void }> = ({
  eventId, preview, fromList, onBack,
}) => {
  const fullTitle = stripHtmlText(preview?.title).trim();
  const headline = newsHeadline(preview?.title) || fullTitle;
  // 部分来源把摘要塞在标题的方括号后半段：只在没有原文链接、抓不到正文时兜底展示。
  const titleTail = fullTitle.replace(/^【[^】]{2,48}】/, '').trim();
  const savedParagraphs = newsParagraphs(preview?.summary).filter(paragraph => paragraph !== fullTitle);
  const fallbackParagraphs = newsParagraphs(titleTail !== fullTitle ? titleTail : '')
    .filter(paragraph => paragraph !== fullTitle);
  const eventUrl = sourceRecordUrl(preview?.url);
  const publisher = sourceLabel(preview);
  const [attempt, setAttempt] = useState(0);
  const [outcome, setOutcome] = useState<FetchOutcome | null>(null);
  // 有正文就直接用，不发请求；没有正文时交给后端判断能否取到。
  // 不能因为事件自身的 url 为空就跳过：`long_news` 的 url 列全为 NULL，
  // 真实链接只存在 raw_payload 里，后端会做三级回退自行找到原文。
  const needsFetch = savedParagraphs.length === 0;

  useEffect(() => {
    if (!needsFetch) return undefined;
    let cancelled = false;
    fetchEventContent(eventId)
      .then((result) => {
        if (cancelled) return;
        const paragraphs = result.eventId === eventId ? newsParagraphs(result.content) : [];
        setOutcome({
          attempt,
          status: paragraphs.length ? 'ok' : 'failed',
          paragraphs,
          url: result.url,
        });
      })
      .catch(() => { if (!cancelled) setOutcome({ attempt, status: 'failed', paragraphs: [], url: null }); });
    return () => { cancelled = true; };
  }, [eventId, needsFetch, attempt]);

  const current = needsFetch && outcome?.attempt === attempt ? outcome : null;
  // 后端会从 raw_payload 里回推真实原文地址，补上 url 列为 NULL 的那批（long_news）。
  const url = eventUrl ?? sourceRecordUrl(current?.url ?? undefined);
  const reading = needsFetch && !current;
  const paragraphs = !needsFetch
    ? (savedParagraphs.length ? savedParagraphs : fallbackParagraphs)
    : (current?.paragraphs ?? []);
  const missing = !reading && paragraphs.length === 0;

  return (
    <article className="news-sheet__article" aria-labelledby="news-sheet-headline">
      {fromList ? (
        <button type="button" className="news-sheet__back" onClick={onBack}>
          <ArrowLeft aria-hidden="true" />返回快讯列表
        </button>
      ) : null}
      <h3 id="news-sheet-headline" className="news-sheet__headline">{headline || '（无标题快讯）'}</h3>
      <p className="news-sheet__meta">
        <span>{publisher}</span>
        <time>{preview ? newsTime(preview) : '时间未注明'}</time>
      </p>
      {reading ? <p className="news-sheet__status" role="status">正在抓取原文正文…</p> : null}
      {paragraphs.length ? (
        <div className="news-sheet__content">{paragraphs.map((paragraph, index) => <p key={index}>{paragraph}</p>)}</div>
      ) : null}
      {missing ? (
        <div className="news-sheet__empty">
          <p>该来源未提供正文。</p>
          {url ? (
            <a className="news-sheet__read-origin" href={url} target="_blank" rel="noopener noreferrer">
              阅读原文<ExternalLink aria-hidden="true" />
            </a>
          ) : <span>该来源也没有可用的原文链接。</span>}
          {current?.status === 'failed' ? (
            <div className="news-sheet__retry" role="status">
              <span>正文在线抓取未完成。</span>
              <button type="button" onClick={() => setAttempt(value => value + 1)}>
                <RefreshCw aria-hidden="true" />重试读取
              </button>
            </div>
          ) : null}
        </div>
      ) : null}
      {url && paragraphs.length ? (
        <a className="news-sheet__origin" href={url} target="_blank" rel="noopener noreferrer">
          打开原文<ExternalLink aria-hidden="true" />
        </a>
      ) : null}
    </article>
  );
};

const NewsList: React.FC<{
  events: MonitorEvent[];
  loading: boolean;
  error?: string | null;
  onRetry?: () => void;
  onSelect: (eventId: number) => void;
}> = ({ events, loading, error, onRetry, onSelect }) => {
  if (loading && !events.length) {
    return <p className="news-sheet__status" role="status">正在读取快讯…</p>;
  }
  if (error && !events.length) {
    return (
      <div className="news-sheet__error" role="status">
        <p>{error}</p>
        {onRetry ? <button type="button" onClick={onRetry}><RefreshCw aria-hidden="true" />重试读取</button> : null}
      </div>
    );
  }
  if (!events.length) {
    return (
      <div className="news-sheet__empty">
        <p>暂无快讯</p>
        <span>财经快讯同步后会自动出现在这里。</span>
      </div>
    );
  }
  return (
    <>
      {error ? <p className="news-sheet__stale" role="status">{error}</p> : null}
      <ol className="news-sheet__list">
        {events.map((event) => {
          const headline = newsHeadline(event.title) || stripHtmlText(event.summary);
          const meta = [sourceLabel(event), newsTime(event)].filter(Boolean).join(' · ');
          return (
            <li key={event.id}>
              <button
                type="button"
                className="news-sheet__row"
                aria-label={`查看快讯全文：${headline || '（无标题快讯）'}`}
                onClick={() => onSelect(event.id)}
              >
                <strong>{headline || '（无标题快讯）'}</strong>
                <small>{meta}</small>
              </button>
            </li>
          );
        })}
      </ol>
    </>
  );
};

/**
 * 手机端快讯读报弹层：复用现有 BottomSheet（底部抽屉 / 可下拉关闭 / 已处理安全区）。
 * 「更多」打开列表，点单条切到正文；正文只在本组件内滚动，不跳走当前页面。
 */
export const NewsSheet: React.FC<NewsSheetProps> = ({
  view, events, loading = false, error = null, onRetry, onSelect, onBack, onClose,
}) => {
  const isOpen = view !== null;
  const detail = view?.kind === 'detail' ? events.find(event => event.id === view.eventId) : undefined;
  const title = view?.kind === 'detail' ? '快讯详情' : '财经快讯';
  const description = view?.kind === 'list'
    ? (events.length ? `${events.length} 条快讯，点开可看正文` : undefined)
    : undefined;

  return (
    <BottomSheet isOpen={isOpen} onClose={onClose} title={title} description={description}>
      {view?.kind === 'detail' ? (
        <NewsArticle
          key={view.eventId}
          eventId={view.eventId}
          preview={detail}
          fromList={view.fromList}
          onBack={onBack}
        />
      ) : (
        <NewsList events={events} loading={loading} error={error} onRetry={onRetry} onSelect={id => onSelect(id, true)} />
      )}
    </BottomSheet>
  );
};
