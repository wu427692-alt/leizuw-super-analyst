import { useEffect, useState } from 'react';
import type React from 'react';
import { FileText, Plus, Trash2 } from 'lucide-react';
import { useNavigate } from 'react-router-dom';
import { industryResearchApi } from '../../api/industryResearch';
import type { IndustryResearchProject } from '../../types/industryResearch';
import { StockLogo } from '../common/StockLogo';
import { CHART_UP, CHART_DOWN } from '../../utils/chartTheme';

type Tone = 'up' | 'down' | 'flat';
const TONE_VAR = { up: CHART_UP, down: CHART_DOWN, flat: 'var(--flat)' } as const;

const STATUS_TEXT: Record<IndustryResearchProject['status'], string> = {
  queued: '排队中', collecting: '收集资料', analyzing: '分析中', completed: '已生成', limited: '资料有限', failed: '失败',
};

export type MobileWatchStock = { symbol: string; name: string; price: string; change: string; tone: Tone };

type Props = {
  stocks: MobileWatchStock[];
  ready: boolean;
  editing: boolean;
  onToggleEdit: () => void;
  onOpen: (symbol: string) => void;
  onDelete: (symbol: string) => void;
  deleteDisabled?: boolean;
  /** 搜索框 + 添加，由页面传入（复用现有自动补全与提交逻辑） */
  search: React.ReactNode;
  addOpen: boolean;
  onToggleAdd: () => void;
  updatedLabel?: string;
  error?: React.ReactNode;
};

/**
 * 手机端「自选」，按乐小研 v2 的版式：
 *   标题行（管理）→ 搜索 → 全部自选 N · 时间 → 表格（头像/名称/代码 · 最新价 · 涨跌幅）→ 添加自选 → 最近研究
 */
export const MobileWatchlist: React.FC<Props> = ({
  stocks, ready, editing, onToggleEdit, onOpen, onDelete, deleteDisabled, search, addOpen, onToggleAdd, updatedLabel, error,
}) => {
  const navigate = useNavigate();
  const [recent, setRecent] = useState<IndustryResearchProject[]>([]);

  useEffect(() => {
    let alive = true;
    industryResearchApi.projects({ limit: 3 })
      .then((list) => { if (alive) setRecent(list.items ?? []); })
      .catch(() => undefined);
    return () => { alive = false; };
  }, []);

  return (
    <div className="lx-home">
      {stocks.length ? (
        <div className="lx-titlebar-action">
          <button type="button" onClick={onToggleEdit}>{editing ? '完成' : '管理'}</button>
        </div>
      ) : null}

      {search}
      {error}

      <section className="lx-section" aria-label="我的自选">
        <header className="lx-section__head">
          <h2>全部自选 <span className="lx-count">{stocks.length}</span></h2>
          {updatedLabel ? <div className="lx-section__meta">{updatedLabel}</div> : null}
        </header>
        <div className="lx-table">
          <div className="lx-table__head is-3"><span>公司</span><span>最新价</span><span>涨跌幅</span></div>
          {!ready ? (
            Array.from({ length: 4 }).map((_, i) => <div key={i} className="lx-table__row is-3 is-stock is-skeleton" />)
          ) : stocks.length ? stocks.map((stock) => (
            <div key={stock.symbol} className="lx-table__row is-3 is-stock">
              <button type="button" className="lx-stock" onClick={() => onOpen(stock.symbol)} disabled={editing}>
                <StockLogo symbol={stock.symbol} name={stock.name} size={26} />
                <span><strong>{stock.name}</strong><small>{stock.symbol}</small></span>
              </button>
              <b className="lx-num">{stock.price}</b>
              {editing ? (
                <button type="button" className="lx-delete" onClick={() => onDelete(stock.symbol)} disabled={deleteDisabled} aria-label={`删除自选股 ${stock.name}`}>
                  <Trash2 aria-hidden="true" />
                </button>
              ) : (
                <b className="lx-num" style={{ color: TONE_VAR[stock.tone] }}>{stock.change}</b>
              )}
            </div>
          )) : (
            <p className="lx-list__empty">暂无自选证券</p>
          )}
        </div>
        <button type="button" className="lx-outline-btn" onClick={onToggleAdd} aria-expanded={addOpen}>
          <Plus aria-hidden="true" />添加自选
        </button>
      </section>

      {recent.length ? (
        <section className="lx-section" aria-label="最近研究">
          <header className="lx-section__head">
            <h2>最近研究</h2>
            <div className="lx-section__meta"><button type="button" onClick={() => navigate('/industry-research')}>全部</button></div>
          </header>
          <div className="lx-list">
            {recent.map((item) => (
              <button key={item.projectId} type="button" className="lx-list__row" onClick={() => navigate(item.status === 'completed' ? `/report/${item.projectId}` : `/industry-research?project=${encodeURIComponent(item.projectId)}`)}>
                <span className="lx-list__icon"><FileText aria-hidden="true" /></span>
                <span className="lx-list__main">
                  <strong>{item.topic}</strong>
                  <small>{String(item.updatedAt || item.createdAt).slice(5, 16).replace('T', ' ')} · {STATUS_TEXT[item.status]}</small>
                </span>
                <span />
              </button>
            ))}
          </div>
        </section>
      ) : null}
    </div>
  );
};
