import type React from 'react';
import { ChevronRight, RefreshCw } from 'lucide-react';
import type { MarketIndexCard } from '../../types/homeDashboard';
import { MobileGroup, MobileListRow } from './MobilePrimitives';
import { SegmentedChips } from './SegmentedChips';
import { CHART_UP, CHART_DOWN } from '../../utils/chartTheme';

export type MobileHomeIndex = MarketIndexCard & {
  session: { label: string; canShowChange: boolean; isStale?: boolean };
  changePct?: number | null;
};

type MobileHomeProps = {
  indices: MobileHomeIndex[];
  activeCode: string;
  onSelectIndex: (code: string) => void;
  leadIndex?: MobileHomeIndex;
  mainValue: string;
  mainChange: string;
  mainTone: 'up' | 'down' | 'flat';
  changeLabel: string;
  watchlist: Array<{ symbol: string; name: string; price: string; change: string; tone: 'up' | 'down' | 'flat' }>;
  onOpenWatchlist: () => void;
  onOpenStock: (symbol: string) => void;
  breadth?: { up: number; down: number; flat: number; total: number } | null;
  breadthNote: string;
  sectors?: { leaders: Array<{ name: string; value: string }>; laggards: Array<{ name: string; value: string }> } | null;
  news: Array<{ id: string; title: string; time: string }>;
  onOpenNews: (id: string) => void;
  onOpenAllNews: () => void;
  quickActions: Array<{ label: string; icon: React.ComponentType<{ className?: string; 'aria-hidden'?: boolean | 'true' | 'false' }>; onClick: () => void }>;
  onRefresh: () => void;
  refreshing: boolean;
  loading: boolean;
  error?: string | null;
  updatedLabel?: string;
};

const TONE_VAR = { up: CHART_UP, down: CHART_DOWN, flat: 'var(--flat)' } as const;

/** 迷你走势线：只画形状，不带坐标轴；点不足 2 个时不渲染。 */
const Sparkline: React.FC<{ points: Array<{ value?: number | null }>; tone: 'up' | 'down' | 'flat' }> = ({ points, tone }) => {
  const values = points.map((point) => point.value).filter((value): value is number => typeof value === 'number' && Number.isFinite(value));
  if (values.length < 2) return null;
  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = max - min || 1;
  const width = 320;
  const height = 64;
  const step = width / (values.length - 1);
  const coords = values.map((value, index) => [index * step, height - 4 - ((value - min) / span) * (height - 8)] as const);
  const line = coords.map(([x, y], index) => `${index ? 'L' : 'M'}${x.toFixed(1)} ${y.toFixed(1)}`).join(' ');
  const area = `${line} L${width} ${height} L0 ${height} Z`;
  const color = TONE_VAR[tone];
  return (
    <svg className="mobile-home__spark" viewBox={`0 0 ${width} ${height}`} preserveAspectRatio="none" aria-hidden="true">
      <path d={area} fill={color} opacity="0.12" />
      <path d={line} fill="none" stroke={color} strokeWidth="1.75" vectorEffect="non-scaling-stroke" strokeLinejoin="round" />
    </svg>
  );
};

/**
 * 手机端首页：一屏只做一件事——先看指数，再看自选，再看广度与快讯。
 * 桌面端那套多列面板在手机上不再出现。
 */
export const MobileHome: React.FC<MobileHomeProps> = ({
  indices,
  activeCode,
  onSelectIndex,
  leadIndex,
  mainValue,
  mainChange,
  mainTone,
  changeLabel,
  watchlist,
  onOpenWatchlist,
  onOpenStock,
  breadth,
  breadthNote,
  sectors,
  news,
  onOpenNews,
  onOpenAllNews,
  quickActions,
  onRefresh,
  refreshing,
  loading,
  error,
  updatedLabel,
}) => {
  const breadthTotal = Math.max(1, breadth?.total ?? 0);
  const upPct = ((breadth?.up ?? 0) / breadthTotal) * 100;
  const downPct = ((breadth?.down ?? 0) / breadthTotal) * 100;

  return (
    <div className="mobile-home">
      <section className="mobile-home__hero" aria-label="主指数">
        {indices.length > 1 ? (
          <SegmentedChips
            ariaLabel="切换主指数"
            items={indices.slice(0, 8).map((item) => ({ value: item.code, label: item.name }))}
            value={activeCode}
            onChange={onSelectIndex}
            className="mobile-home__index-chips"
          />
        ) : null}
        <div className="mobile-home__hero-head">
          <span>{leadIndex?.name ?? '上证指数'}</span>
          <span className="mobile-home__hero-code">{leadIndex?.code ?? '000001.SH'}</span>
        </div>
        <p className="mobile-home__hero-value" style={{ color: TONE_VAR[mainTone] }}>{mainValue}</p>
        <div className="mobile-home__hero-foot">
          <span style={{ color: TONE_VAR[mainTone] }}>{mainChange}</span>
          <span>{changeLabel}</span>
        </div>
        <Sparkline points={leadIndex?.history ?? []} tone={mainTone} />
      </section>

      {error ? <p className="mobile-home__error" role="status">{error}</p> : null}

      {loading && !watchlist.length ? (
        <p className="mobile-home__status" role="status">正在读取行情…</p>
      ) : null}

      <MobileGroup
        title="自选"
        action={<button type="button" onClick={onOpenWatchlist}>全部</button>}
      >
        {watchlist.length ? watchlist.slice(0, 5).map((item) => (
          <MobileListRow
            key={item.symbol}
            title={item.name}
            subtitle={item.symbol}
            value={item.price}
            valueNote={item.change}
            tone={item.tone}
            onClick={() => onOpenStock(item.symbol)}
          />
        )) : (
          <MobileListRow title="还没有自选股" subtitle="添加后这里会显示实时报价" onClick={onOpenWatchlist} />
        )}
      </MobileGroup>

      {breadth ? (
        <MobileGroup title="市场广度">
          <div className="mobile-breadth">
            <div className="mobile-breadth__bar" aria-hidden="true">
              <span style={{ width: `${upPct}%`, background: TONE_VAR.up }} />
              <span style={{ width: `${downPct}%`, background: TONE_VAR.down }} />
            </div>
            <div className="mobile-breadth__legend">
              <span style={{ color: TONE_VAR.up }}>涨 {breadth.up}</span>
              <span style={{ color: TONE_VAR.down }}>跌 {breadth.down}</span>
              <span>平 {breadth.flat}</span>
              <span className="mobile-breadth__note">{breadthNote}</span>
            </div>
          </div>
        </MobileGroup>
      ) : null}

      {sectors ? (
        <MobileGroup title="行业涨跌">
          <div className="mobile-sector">
            <div>
              <p style={{ color: TONE_VAR.up }}>领涨</p>
              {sectors.leaders.map((item) => (
                <span key={`up-${item.name}`}><b>{item.name}</b><em style={{ color: TONE_VAR.up }}>{item.value}</em></span>
              ))}
            </div>
            <div>
              <p style={{ color: TONE_VAR.down }}>领跌</p>
              {sectors.laggards.map((item) => (
                <span key={`down-${item.name}`}><b>{item.name}</b><em style={{ color: TONE_VAR.down }}>{item.value}</em></span>
              ))}
            </div>
          </div>
        </MobileGroup>
      ) : null}

      <MobileGroup
        title="快讯"
        action={<button type="button" onClick={onOpenAllNews}>更多</button>}
      >
        {news.length ? news.slice(0, 4).map((item) => (
          <button key={item.id} type="button" className="mobile-news-row" onClick={() => onOpenNews(item.id)}>
            <span className="mobile-news-row__title">{item.title}</span>
            <span className="mobile-news-row__meta">{item.time}<ChevronRight aria-hidden="true" /></span>
          </button>
        )) : (
          <div className="mobile-row"><span className="mobile-row__main"><strong>暂无快讯</strong><small>资讯同步后自动显示</small></span></div>
        )}
      </MobileGroup>

      <section className="mobile-quick" aria-label="分析入口">
        {quickActions.map(({ label, icon: Icon, onClick }) => (
          <button key={label} type="button" onClick={onClick}>
            <Icon aria-hidden="true" />
            <span>{label}</span>
          </button>
        ))}
      </section>

      <div className="mobile-home__footer">
        <button type="button" className="mobile-home__refresh" onClick={onRefresh} disabled={refreshing}>
          <RefreshCw aria-hidden="true" className={refreshing ? 'is-spinning' : undefined} />
          {refreshing ? '正在刷新' : '刷新行情'}
        </button>
        {updatedLabel ? <p>{updatedLabel}</p> : null}
        <p className="mobile-home__disclaimer">数据仅供参考，不构成投资建议</p>
      </div>
    </div>
  );
};
