import { useEffect, useState } from 'react';
import type React from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { ChevronLeft, FileSearch, ShieldAlert } from 'lucide-react';
import { AppPage } from '../components/common';
import { PixelAvatar } from '../components/illustration/PixelAvatar';
import { industryResearchApi } from '../api/industryResearch';
import type { IndustryResearchProject } from '../types/industryResearch';
import './ResearchReportPage.css';

/** 报告里的一条结论：正文 + 引用的证据编号。 */
const refNumbers = (ids?: string[]): number[] => (ids ?? []).map((id) => {
  const digits = String(id).match(/(\d+)$/);
  return digits ? Number(digits[1]) : 0;
}).filter(Boolean);

const splitRoles = (sides?: string[]): string[] => (sides ?? []).flatMap((side) => (
  String(side).split(/[：:]/)[0] ? [String(side)] : []
));

/**
 * 研究报告（乐小研 07）。
 *
 * 只读一个研究项目的真实报告：结论、分歧、风险、待核验，全部来自
 * `/api/v1/industry-research/projects/{id}` 的 report 字段，没有占位数据。
 */
export const ResearchReportPage: React.FC = () => {
  const { projectId = '' } = useParams();
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

  const report = project?.report;
  const subject = project?.snapshot?.subject;
  const evidence = project?.snapshot?.evidence ?? [];

  const conclusions = (report?.trends ?? [])
    .map((item) => ({ text: item.claim ?? '', refs: refNumbers(item.evidenceIds) }))
    .filter((item) => item.text);
  const disagreements = (report?.disagreements ?? []).filter((item) => item.question || item.sides?.length);
  const risks = [
    ...(report?.caveats ?? []),
    ...(report?.bottlenecks ?? []).map((item) => [item.issue, item.whyItMatters].filter(Boolean).join('：')),
  ].filter(Boolean);
  const pending = [
    ...(report?.openQuestions ?? []),
    ...(report?.interviewQuestions ?? []),
  ].filter(Boolean);

  return <AppPage className="lx-report">
    <div className="lx-report__bar">
      <button type="button" onClick={() => navigate(-1)} aria-label="返回"><ChevronLeft aria-hidden="true" /></button>
      <h1>研究报告</h1>
    </div>

    {error ? <p className="lx-list__empty" role="status">{error}</p> : null}
    {!project && !error ? <p className="lx-list__empty">正在读取报告…</p> : null}

    {project ? <>
      <section className="lx-report__head">
        <h2>{subject?.name ? `${subject.name}研究报告` : project.topic}</h2>
        <p>{subject?.symbol ? `${subject.symbol} · ` : ''}近 {project.lookbackDays} 天</p>
        <p>{String(project.completedAt ?? project.updatedAt ?? '').slice(5, 16).replace('T', ' ')} · AI 生成</p>
        <span className="lx-report__state">{project.status === 'completed' ? '已生成' : project.status === 'failed' ? '生成失败' : '进行中'}</span>
      </section>

      {report?.oneSentence ? <p className="lx-report__lead">{report.oneSentence}</p> : null}

      {conclusions.length ? <section className="lx-section" aria-label="主要结论">
        <header className="lx-section__head"><h2>主要结论</h2></header>
        <ol className="lx-report__conclusions">
          {conclusions.map((item, index) => (
            <li key={item.text}>
              <span className="lx-report__num">{String(index + 1).padStart(2, '0')}</span>
              <span>{item.text}</span>
              {item.refs.length ? <em>{item.refs.map((n) => `[${n}]`).join(' ')}</em> : null}
            </li>
          ))}
        </ol>
      </section> : null}

      {disagreements.length ? <section className="lx-section" aria-label="观点分歧">
        <header className="lx-section__head"><h2>观点分歧</h2></header>
        <div className="lx-list">
          {disagreements.map((item) => (
            <div key={item.question ?? item.sides?.join('|')} className="lx-report__side">
              <PixelAvatar seed={item.question ?? 'd'} size={28} />
              <span className="lx-list__main">
                <strong>{item.question ?? '分歧点'}</strong>
                <small>{splitRoles(item.sides).join(' / ')}</small>
              </span>
            </div>
          ))}
        </div>
      </section> : null}

      {risks.length ? <section className="lx-section" aria-label="风险因素">
        <header className="lx-section__head"><h2>风险因素</h2></header>
        <ul className="lx-report__bullets">
          {risks.slice(0, 6).map((item) => <li key={item}>{item}</li>)}
        </ul>
      </section> : null}

      {pending.length ? <section className="lx-section" aria-label="待核验事项">
        <header className="lx-section__head"><h2>待核验事项</h2></header>
        <ul className="lx-report__checks">
          {pending.slice(0, 8).map((item) => <li key={item}><i aria-hidden="true" />{item}</li>)}
        </ul>
      </section> : null}

      {!conclusions.length && !disagreements.length && !risks.length && !pending.length ? (
        <div className="lx-report__empty">
          <ShieldAlert aria-hidden="true" />
          <p>这份研究还没有生成结构化报告。</p>
        </div>
      ) : null}

      {evidence.length ? (
        <button type="button" className="lx-outline-btn" onClick={() => navigate(`/evidence/${projectId}?index=1`)}>
          <FileSearch aria-hidden="true" />查看证据（{evidence.length}）
        </button>
      ) : null}
    </> : null}
  </AppPage>;
};

export default ResearchReportPage;
