import { useState } from 'react';
import type React from 'react';
import { ChevronRight, Newspaper, Search } from 'lucide-react';
import type { MarketIndexCard } from '../../types/homeDashboard';
import { SegmentedChips } from './SegmentedChips';
import { StockLogo } from '../common/StockLogo';
import { CHART_UP, CHART_DOWN } from '../../utils/chartTheme';

export type MobileHomeIndex = MarketIndexCard & {
  session: { label: string; canShowChange: boolean; isStale?: boolean };
  changePct?: number | null;
};

type Tone = 'up' | 'down' | 'flat';

type MobileHomeProps = {
  indices: MobileHomeIndex[];
  activeCode: string;
  onSelectIndex: (code: string) => void;
  leadIndex?: MobileHomeIndex;
  mainValue: string;
  mainChange: string;
  mainTone: Tone;
  changeLabel: string;
  watchlist: Array<{ symbol: string; name: string; price: string; change: string; tone: Tone }>;
  onOpenWatchlist: () => void;
  onOpenStock: (symbol: string) => void;
  breadth?: { up: number; down: number; flat: number; total: number } | null;
  breadthNote: string;
  sectors?: { leaders: Array<{ name: string; value: string }>; laggards: Array<{ name: string; value: string }> } | null;
  news: Array<{ id: string; title: string; time: string; source?: string }>;
  onOpenNews: (id: string) => void;
  onOpenAllNews: () => void;
  quickActions: Array<{ label: string; icon: React.ComponentType<{ className?: string; 'aria-hidden'?: boolean | 'true' | 'false' }>; onClick: () => void }>;
  onRefresh: () => void;
  refreshing: boolean;
  loading: boolean;
  error?: string | null;
  updatedLabel?: string;
  onSearch?: () => void;
  indexValue?: (item: MobileHomeIndex) => string;
  indexChange?: (item: MobileHomeIndex) => string;
};

const TONE_VAR = { up: CHART_UP, down: CHART_DOWN, flat: 'var(--flat)' } as const;
const toneOf = (value?: number | null): Tone => ((value ?? 0) > 0 ? 'up' : (value ?? 0) < 0 ? 'down' : 'flat');
const toneOfText = (text: string): Tone => (text.startsWith('+') ? 'up' : text.startsWith('-') || text.startsWith('−') ? 'down' : 'flat');

/** 模块标题：左标题，右侧可选的元信息或动作。全页同一种写法。 */
const Section: React.FC<{ title: string; meta?: React.ReactNode; children: React.ReactNode; label?: string }> = ({ title, meta, children, label }) => (
  <section className="lx-section" aria-label={label ?? title}>
    <header className="lx-section__head">
      <h2>{title}</h2>
      {meta ? <div className="lx-section__meta">{meta}</div> : null}
    </header>
    {children}
  </section>
);

/**
 * 手机端「行情」首页，按乐小研 v2 的版式：
 *   搜索 → 市场概览（三张指数卡）→ 热点观察（行业切换 + 表格）→ 我的自选 → 资讯选摘
 * 每个模块同一种标题，数字同一种字号，表格同一种行高。
 */
export const MobileHome: React.FC<MobileHomeProps> = ({
  indices,
  onSelectIndex,
  watchlist,
  onOpenWatchlist,
  onOpenStock,
  sectors,
  news,
  onOpenNews,
  onOpenAllNews,
  loading,
  error,
  updatedLabel,
  onSearch,
  indexValue,
  indexChange,
  activeCode,
}) => {
  const [sectorSide, setSectorSide] = useState<'leaders' | 'laggards'>('leaders');
  // 与参考版式一致：上证 / 深证 / 创业板；缺哪个就用列表里的下一个补位。
  const PREFERRED = ['000001.SH', '399001.SZ', '399006.SZ'];
  const preferred = PREFERRED.map((code) => indices.find((item) => item.code.toUpperCase() === code)).filter(Boolean) as MobileHomeIndex[];
  const tiles = [...preferred, ...indices.filter((item) => !preferred.includes(item))].slice(0, 3);
  const sectorRows = (sectors?.[sectorSide] ?? []).slice(0, 5);

  return (
    <div className="lx-home">
      {onSearch ? (
        <button type="button" className="lx-search" onClick={onSearch}>
          <Search aria-hidden="true" />
          <span>搜索公司名称或证券代码</span>
        </button>
      ) : null}

      {error ? <p className="lx-error" role="status">{error}</p> : null}

      <Section title="市场概览" meta={updatedLabel} label="主指数">
        <div className="lx-tiles">
          {tiles.map((item) => {
            const tone = item.session.canShowChange ? toneOf(item.changePct) : 'flat';
            return (
              <button
                key={item.code}
                type="button"
                className={`lx-tile${item.code === activeCode ? ' is-active' : ''}`}
                onClick={() => onSelectIndex(item.code)}
              >
                <span className="lx-tile__name">{item.name}</span>
                <b className="lx-tile__value">{indexValue ? indexValue(item) : item.close ?? '—'}</b>
                <em className="lx-tile__change" style={{ color: TONE_VAR[tone] }}>
                  {indexChange ? indexChange(item) : ''}
                </em>
              </button>
            );
          })}
          {loading && !tiles.length ? Array.from({ length: 3 }).map((_, i) => <div key={i} className="lx-tile is-skeleton" />) : null}
        </div>
      </Section>

      {sectors ? (
        <Section title="热点观察" label="行业涨跌">
          <SegmentedChips
            ariaLabel="切换领涨领跌"
            items={[{ value: 'leaders', label: '领涨行业' }, { value: 'laggards', label: '领跌行业' }]}
            value={sectorSide}
            onChange={(value) => setSectorSide(value as 'leaders' | 'laggards')}
            className="lx-chips"
          />
          <div className="lx-table">
            <div className="lx-table__head"><span>行业</span><span>涨跌幅</span></div>
            {sectorRows.map((item) => (
              <div key={item.name} className="lx-table__row">
                <span className="lx-table__name">{item.name}</span>
                <b style={{ color: TONE_VAR[toneOfText(item.value)] }}>{item.value}</b>
              </div>
            ))}
          </div>
        </Section>
      ) : null}

      <Section
        title="我的自选"
        meta={<button type="button" onClick={onOpenWatchlist}>全部</button>}
        label="自选"
      >
        <div className="lx-table">
          <div className="lx-table__head is-3"><span>公司</span><span>最新价</span><span>涨跌幅</span></div>
          {watchlist.length ? watchlist.slice(0, 5).map((item) => (
            <button key={item.symbol} type="button" className="lx-table__row is-3 is-stock" onClick={() => onOpenStock(item.symbol)}>
              <span className="lx-stock">
                <StockLogo symbol={item.symbol} name={item.name} size={26} />
                <span><strong>{item.name}</strong><small>{item.symbol}</small></span>
              </span>
              <b className="lx-num">{item.price}</b>
              <b className="lx-num" style={{ color: TONE_VAR[item.tone] }}>{item.change}</b>
            </button>
          )) : (
            <button type="button" className="lx-table__empty" onClick={onOpenWatchlist}>添加自选股</button>
          )}
        </div>
      </Section>

      <Section
        title="资讯选摘"
        meta={<button type="button" onClick={onOpenAllNews}>更多</button>}
        label="快讯"
      >
        <div className="lx-list">
          {news.length ? news.slice(0, 4).map((item) => (
            <button key={item.id} type="button" className="lx-list__row" onClick={() => onOpenNews(item.id)}>
              <span className="lx-list__icon"><Newspaper aria-hidden="true" /></span>
              <span className="lx-list__main"><strong>{item.title}</strong><small>{item.source ? `${item.source} · ${item.time}` : item.time}</small></span>
              <ChevronRight aria-hidden="true" className="lx-list__chev" />
            </button>
          )) : <p className="lx-list__empty">暂无快讯</p>}
        </div>
      </Section>
    </div>
  );
};
