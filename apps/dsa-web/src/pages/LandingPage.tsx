import { useCallback, useRef } from 'react';
import type { PointerEvent as ReactPointerEvent } from 'react';
import { Link } from 'react-router-dom';
import { ArrowRight, BarChart3, FlaskConical, Network, Radar } from 'lucide-react';
import './LandingPage.css';

const capabilities = [
  { label: '实时行情', icon: BarChart3 },
  { label: '全渠道情报', icon: Network },
  { label: '小作文洞察', icon: Radar },
  { label: '量化回测', icon: FlaskConical },
];

const leftSignals = [
  { label: '行情', y: 125, color: 'violet' },
  { label: '公告', y: 225, color: 'magenta' },
  { label: '研报', y: 325, color: 'violet' },
  { label: '知识星球', y: 425, color: 'cyan' },
  { label: '企业事实', y: 525, color: 'cyan' },
  { label: '公开股评', y: 625, color: 'magenta' },
] as const;

const rightSignals = [
  { label: '市场', y: 215, color: 'magenta' },
  { label: '公司', y: 345, color: 'cyan' },
  { label: '机构', y: 475, color: 'cyan' },
  { label: '证据链', y: 605, color: 'violet' },
] as const;

const pathForLeftSignal = (y: number, index: number) => (
  `M 30 ${y} C ${170 + index * 12} ${y - 72}, ${325 - index * 9} ${345 + (y - 360) * 0.22}, 488 360`
);

const pathForRightSignal = (y: number, index: number) => (
  `M 488 360 C ${650 + index * 11} ${350 + (y - 360) * 0.12}, ${720 - index * 9} ${y - 84}, 895 ${y}`
);

const SignalField = () => (
  <div className="landing-signal-field" aria-hidden="true">
    <svg viewBox="0 0 930 720" role="presentation" focusable="false">
      <defs>
        <linearGradient id="landing-flow-left" x1="0" x2="1">
          <stop offset="0" stopColor="#5d34d0" stopOpacity="0" />
          <stop offset="0.52" stopColor="#a855f7" stopOpacity="0.92" />
          <stop offset="1" stopColor="#ff006e" />
        </linearGradient>
        <linearGradient id="landing-flow-right" x1="0" x2="1">
          <stop offset="0" stopColor="#ff006e" />
          <stop offset="0.48" stopColor="#5d34d0" />
          <stop offset="1" stopColor="#00f0ff" stopOpacity="0" />
        </linearGradient>
        <radialGradient id="landing-core" cx="50%" cy="50%" r="50%">
          <stop offset="0" stopColor="#ffffff" />
          <stop offset="0.18" stopColor="#ff8ddb" />
          <stop offset="0.48" stopColor="#9f33ff" />
          <stop offset="1" stopColor="#00f0ff" stopOpacity="0" />
        </radialGradient>
        <filter id="landing-glow" x="-80%" y="-80%" width="260%" height="260%">
          <feGaussianBlur stdDeviation="8" result="blur" />
          <feMerge><feMergeNode in="blur" /><feMergeNode in="SourceGraphic" /></feMerge>
        </filter>
      </defs>

      <g className="landing-orbits">
        {[58, 92, 128, 166].map((radius) => (
          <circle key={radius} cx="488" cy="360" r={radius} />
        ))}
        <path d="M 310 360 H 666" />
        <path d="M 488 182 V 538" />
      </g>

      <g className="landing-flow landing-flow-left">
        {leftSignals.map((signal, index) => (
          <path key={signal.label} d={pathForLeftSignal(signal.y, index)} pathLength="1" />
        ))}
      </g>
      <g className="landing-flow landing-flow-right">
        {rightSignals.map((signal, index) => (
          <path key={signal.label} d={pathForRightSignal(signal.y, index)} pathLength="1" />
        ))}
      </g>

      <g className="landing-flow-nodes">
        {leftSignals.map((signal, index) => (
          <g key={signal.label} className={`landing-node landing-node-${signal.color}`}>
            <circle cx="92" cy={signal.y} r="22" />
            <circle cx="92" cy={signal.y} r="4" />
            <text x="57" y={signal.y + 5} textAnchor="end">{signal.label}</text>
            <circle className="landing-traveller" cx="0" cy="0" r="3.5">
              <animateMotion dur={`${4.5 + index * 0.37}s`} repeatCount="indefinite" path={pathForLeftSignal(signal.y, index)} />
            </circle>
          </g>
        ))}
        {rightSignals.map((signal, index) => (
          <g key={signal.label} className={`landing-node landing-node-${signal.color}`}>
            <circle cx="850" cy={signal.y} r="22" />
            <circle cx="850" cy={signal.y} r="4" />
            <text x="818" y={signal.y + 5} textAnchor="end">{signal.label}</text>
            <circle className="landing-traveller" cx="0" cy="0" r="3.5">
              <animateMotion dur={`${4.9 + index * 0.43}s`} repeatCount="indefinite" path={pathForRightSignal(signal.y, index)} />
            </circle>
          </g>
        ))}
      </g>

      <g className="landing-core" filter="url(#landing-glow)">
        <circle cx="488" cy="360" r="74" fill="url(#landing-core)" opacity="0.38" />
        <circle cx="488" cy="360" r="48" />
        <path d="M488 329C494 349 500 355 520 360C500 366 494 372 488 392C482 372 476 366 456 360C476 355 482 349 488 329Z" />
      </g>
    </svg>
  </div>
);

const LandingPage = () => {
  const pageRef = useRef<HTMLElement>(null);

  const handlePointerMove = useCallback((event: ReactPointerEvent<HTMLElement>) => {
    const bounds = event.currentTarget.getBoundingClientRect();
    const x = (event.clientX - bounds.left) / bounds.width;
    const y = (event.clientY - bounds.top) / bounds.height;
    pageRef.current?.style.setProperty('--pointer-x', `${(x - 0.5) * 24}px`);
    pageRef.current?.style.setProperty('--pointer-y', `${(y - 0.5) * 18}px`);
    pageRef.current?.style.setProperty('--pointer-glow-x', `${x * 100}%`);
    pageRef.current?.style.setProperty('--pointer-glow-y', `${y * 100}%`);
  }, []);

  const resetPointer = useCallback(() => {
    pageRef.current?.style.setProperty('--pointer-x', '0px');
    pageRef.current?.style.setProperty('--pointer-y', '0px');
    pageRef.current?.style.setProperty('--pointer-glow-x', '62%');
    pageRef.current?.style.setProperty('--pointer-glow-y', '42%');
  }, []);

  return (
    <main
      ref={pageRef}
      className="landing-page"
      onPointerMove={handlePointerMove}
      onPointerLeave={resetPointer}
    >
      <div className="landing-mesh" aria-hidden="true" />
      <div className="landing-noise" aria-hidden="true" />

      <header className="landing-header">
        <Link className="landing-brand" to="/" aria-label="DSA 财经情报台简介页">
          DSA
        </Link>
      </header>

      <section className="landing-hero" aria-labelledby="landing-title">
        <div className="landing-copy">
          <h1 id="landing-title">
            <span>把市场噪声，</span>
            变成可以行动的证据。
          </h1>
          <p>
            聚合行情、公告、研报、知识星球、企业事实与公开股评，
            以统一时间线连接市场、公司与机构。
          </p>
          <Link className="landing-enter" to="/app">
            <span>进入财经情报台</span>
            <ArrowRight aria-hidden="true" />
          </Link>
        </div>

        <SignalField />
      </section>

      <section className="landing-capabilities" aria-label="平台核心能力">
        {capabilities.map(({ label, icon: Icon }) => (
          <div className="landing-capability" key={label}>
            <span className="landing-capability-icon"><Icon aria-hidden="true" /></span>
            <strong>{label}</strong>
          </div>
        ))}
      </section>
    </main>
  );
};

export default LandingPage;
