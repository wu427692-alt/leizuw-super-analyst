import { useEffect, useMemo, useState } from 'react';
import type React from 'react';
import { useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { ChevronLeft, ExternalLink } from 'lucide-react';
import { AppPage } from '../components/common';
import { PixelAvatar } from '../components/illustration/PixelAvatar';
import { industryResearchApi } from '../api/industryResearch';
import type { IndustryEvidence, IndustryResearchProject } from '../types/industryResearch';
import './ResearchReportPage.css';

const LEVEL_TEXT: Record<string, string> = {
  primary: '一手资料', secondary: '二手资料', tertiary: '衍生资料',
};

/**
 * 证据详情（乐小研 08）。
 *
 * 证据列表来自研究项目的 snapshot.evidence，全部真实入库资料；
 * `?index=N` 决定当前看第几条，可点上方切换。
 */
export const EvidenceDetailPage: React.FC = () => {
  const { projectId = '' } = useParams();
  const [params, setParams] = useSearchParams();
  const navigate = useNavigate();
  const [project, setProject] = useState<IndustryResearchProject | null>(null);
  const [error, setError] = useState('');

  useEffect(() => {
    let alive = true;
    industryResearchApi.project(projectId)
      .then((value) => { if (alive) setProject(value); })
      .catch((reason) => { if (alive) setError(reason instanceof Error ? reason.message : '读取失败'); });
    return () => { alive = false; };
  }, [projectId]);

  const evidence: IndustryEvidence[] = useMemo(() => project?.snapshot?.evidence ?? [], [project]);
  const rawIndex = Number(params.get('index') ?? '1');
  const index = Number.isFinite(rawIndex) ? Math.min(Math.max(1, rawIndex), Math.max(1, evidence.length)) : 1;
  const current = evidence[index - 1];

  const select = (next: number) => {
    const nextParams = new URLSearchParams(params);
    nextParams.set('index', String(next));
    setParams(nextParams, { replace: true });
  };

  return <AppPage className="lx-report">
    <div className="lx-report__bar">
      <button type="button" onClick={() => navigate(`/report/${projectId}`)} aria-label="返回研究报告"><ChevronLeft aria-hidden="true" /></button>
      <h1>证据详情</h1>
    </div>

    {error ? <p className="lx-list__empty" role="status">{error}</p> : null}
    {!project && !error ? <p className="lx-list__empty">正在读取证据…</p> : null}

    {project && !evidence.length ? <p className="lx-list__empty">这份研究还没有入库证据。</p> : null}

    {evidence.length ? <>
      <div className="lx-ev__tabs" role="tablist" aria-label="证据切换">
        {evidence.slice(0, 6).map((item, position) => (
          <button
            key={item.evidenceId}
            type="button"
            role="tab"
            aria-selected={position + 1 === index}
            className={position + 1 === index ? 'is-active' : undefined}
            onClick={() => select(position + 1)}
          >
            [{position + 1}] {item.title.slice(0, 8)}
          </button>
        ))}
      </div>

      {current ? <>
        <section className="lx-ev__source">
          <PixelAvatar seed={current.source || current.evidenceId} size={36} />
          <span className="lx-ev__source-main">
            <strong>{current.title}</strong>
            <small>来源：{current.source || '未标注'} ｜ 资料时间：{current.date || '未标注'}</small>
          </span>
        </section>

        <section className="lx-section" aria-label="引用观点">
          <header className="lx-section__head"><h2>引用内容</h2></header>
          <div className="lx-ev__quote">
            <PixelAvatar seed={current.evidenceId} size={28} />
            <p>{current.summary || '这条资料没有摘要，可打开原文查看。'}</p>
          </div>
        </section>

        <section className="lx-section" aria-label="资料信息">
          <header className="lx-section__head"><h2>资料信息</h2></header>
          <dl className="lx-ev__meta">
            <div><dt>资料类型</dt><dd>{current.kind || '未标注'}</dd></div>
            <div><dt>证据等级</dt><dd>{LEVEL_TEXT[current.evidenceLevel] ?? current.evidenceLevel ?? '未标注'}</dd></div>
            {current.company ? <div><dt>关联公司</dt><dd>{current.company}</dd></div> : null}
            {current.symbol ? <div><dt>证券代码</dt><dd>{current.symbol}</dd></div> : null}
            <div><dt>原文</dt><dd>{current.originalAvailable ? '可查看' : '未入库'}</dd></div>
          </dl>
        </section>

        {current.url ? (
          <a className="lx-outline-btn" href={current.url} target="_blank" rel="noreferrer noopener">
            <ExternalLink aria-hidden="true" />打开原文
          </a>
        ) : null}
      </> : null}

      <button type="button" className="lx-ev__back" onClick={() => navigate(`/report/${projectId}`)}>返回研究报告</button>
    </> : null}
  </AppPage>;
};

export default EvidenceDetailPage;
