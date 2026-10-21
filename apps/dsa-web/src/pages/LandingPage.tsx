import { useEffect, useRef, useState, type KeyboardEvent } from 'react';
import { Link } from 'react-router-dom';
import {
  ArrowDown, ArrowRight, BookOpen, Building2, DatabaseZap, Download,
  FileText, LineChart, ListChecks, MessageCircleMore, MessagesSquare,
  Mic2, ShieldCheck, Waves,
} from 'lucide-react';
import './LandingPage.css';

const productTours = [
  {
    title: '智能投研团队', heading: '多角色协作，把研究拆成可执行的任务。', href: '/meeting', action: '进入智能投研团队',
    description: '主席、选股、数据、研究员、组合、调度六个角色围绕同一块黑板协作；每个任务独立上下文与工作区，可切换、搜索、并行。',
    data: '任务栏 · 黑板可视化 · 多源备源 · 文件交付 · 录音转写',
    output: '行情曲线、选股表、评分条、研报/财报/录音 ZIP 与文字稿',
    screenshot: '/landing/screens/meeting.jpg', alt: '智能投研团队实机界面',
  },
  {
    title: '市场总览', heading: '先看市场，再看机会。', href: '/app', action: '查看市场总览',
    description: '把核心指数、市场广度、行业分布和自选股放回同一幅画面，从重要新闻中找到值得继续研究的变化。',
    data: '指数行情 · 市场广度 · 行业分布 · 新闻',
    output: '市场环境、关注标的与新闻原文',
    screenshot: '/landing/screens/market-overview.jpg', alt: '市场总览历史界面',
  },
  {
    title: '概念题材', heading: '不止看标签，更要看关联。', href: '/concept-themes', action: '探索概念题材',
    description: '从分层题材与成分股出发，结合机构语料和行情联动，区分共同的板块驱动与个股独特线索。',
    data: '题材层级 · 成分股 · 行情联动 · 机构语料',
    output: '题材关联、权重、Beta / Alpha 归因与证据',
    screenshot: '/landing/screens/concept-themes.jpg', alt: '概念题材历史界面',
  },
  {
    title: '自选股', heading: '围绕一家公司，把信息看完整。', href: '/super-watchlist', action: '探索自选股',
    description: '行情、财务、公告、机构观点与一致预期，围绕同一个标的汇聚。少切几个页面，多留一点时间给判断。',
    data: '行情 · 财务 · 公告 · 研报 · 机构观点',
    output: '公司信息档案、事实时间线与一致预期',
    screenshot: '/landing/screens/super-watchlist-preview.png', alt: '自选股超级看板历史界面',
  },
  {
    title: '深度调研', heading: '带着问题，深入一家公司或行业。', href: '/industry-research', action: '开始深度调研',
    description: '组织研报、公告财报、机构段子、录音转写与互联网资料，后台生成研究报告，支持 Word 与 PDF 导出。',
    data: '研报 · 公告财报 · 录音转写 · 互联网资料',
    output: '研究报告、图表、证据清单与可下载文档',
    screenshot: '/landing/screens/industry-research.jpg', alt: '行业与公司调研历史界面',
  },
  {
    title: '量化验证', heading: '让研究假设，接受历史检验。', href: '/essay-quant', action: '进入量化工作台',
    description: '把机构观点与历史行情连接成研究任务，在后台运行，再回到结果中比较收益、回撤与样本边界。',
    data: '机构事件 · 历史行情 · 交易约束 · 研究参数',
    output: '收益回撤、置信区间与可复现结果',
    screenshot: '/landing/screens/quant-workbench.jpg', alt: '量化研究工作台历史界面',
  },
];

const utilities = [
  { title: '机构段子与录音', detail: '查原文、转写、下载纪要', href: '/essay-radar', icon: Mic2 },
  { title: '数据一站式获取', detail: '精细筛选，后台打包', href: '/data-acquisition', icon: Download },
  { title: '问股', detail: '带着数据上下文提问', href: '/chat', icon: MessagesSquare },
  { title: '任务与验证', detail: '离开页面，任务继续', href: '/tasks', icon: ListChecks },
];

const sources = [
  { title: 'Tushare', detail: '行情、财务与研报', icon: LineChart },
  { title: '巨潮资讯', detail: '公告与 PDF 原文', icon: FileText },
  { title: '知识星球 MCP', detail: '机构观点与录音', icon: Waves },
  { title: '天眼查', detail: '企业信息与风险', icon: Building2 },
  { title: '公开股评', detail: '公开讨论与线索', icon: MessageCircleMore },
  { title: '共享数据库', detail: '统一索引，增量更新', icon: DatabaseZap },
];

const LandingPage = () => {
  const [activeProduct, setActiveProduct] = useState(0);
  const tabRefs = useRef<Array<HTMLButtonElement | null>>([]);
  const product = productTours[activeProduct];

  useEffect(() => {
    document.title = '乐子乌智能投研平台 · 证据驱动的投资研究工作台';
  }, []);

  const handleTabKey = (event: KeyboardEvent<HTMLButtonElement>, index: number) => {
    let next: number;
    if (event.key === 'ArrowRight') next = (index + 1) % productTours.length;
    else if (event.key === 'ArrowLeft') next = (index - 1 + productTours.length) % productTours.length;
    else if (event.key === 'Home') next = 0;
    else if (event.key === 'End') next = productTours.length - 1;
    else return;
    event.preventDefault();
    setActiveProduct(next);
    tabRefs.current[next]?.focus({ preventScroll: true });
    tabRefs.current[next]?.scrollIntoView?.({ block: 'nearest', inline: 'nearest', behavior: 'instant' });
  };

  return (
    <main className="landing-page">
      <a className="landing-skip" href="#landing-content">跳至主要内容</a>
      <header className="landing-header landing-glass">
        <Link className="landing-brand" to="/" aria-label="乐子乌智能投研平台首页">
          <span><DatabaseZap aria-hidden="true" /></span><b>乐子乌智能投研平台</b>
        </Link>
        <nav aria-label="介绍页导航">
          <a href="#real-product">产品体验</a>
          <a href="#data-sources">数据来源</a>
          <Link to="/guide">使用手册</Link>
          <a href="https://leowu.leziwu.com" target="_blank" rel="noopener noreferrer">作者介绍</a>
        </nav>
        <div className="landing-header-actions">
          <Link className="landing-register" to="/access?mode=register&redirect=%2Fapp">注册</Link>
          <Link className="landing-button landing-button--primary" to="/app">进入平台<ArrowRight aria-hidden="true" /></Link>
        </div>
      </header>

      <section className="landing-hero landing-wrap" id="landing-content" aria-labelledby="landing-title">
        <div className="landing-hero-copy">
          <h1 id="landing-title"><span>让信息沉淀，</span><span>让判断清晰。</span></h1>
          <p>行情、公告、研报与机构观点，<br className="landing-desktop-break" />在同一个研究工作台连接。</p>
          <div className="landing-actions">
            <Link className="landing-button landing-button--primary" to="/app">进入研究平台<ArrowRight aria-hidden="true" /></Link>
            <a className="landing-button landing-button--glass" href="#real-product">查看产品体验<ArrowDown aria-hidden="true" /></a>
          </div>
        </div>
        <div className="landing-hero-visual">
          <figure className="landing-hero-screen landing-glass">
            <div className="landing-window-bar"><span aria-hidden="true"><i /><i /><i /></span><b>产品界面展示</b><span aria-hidden="true" /></div>
            <img src="/landing/screens/market-overview.jpg" alt="乐子乌智能投研平台市场总览历史界面" width="1440" height="900" fetchPriority="high" />
          </figure>
          <p className="landing-image-caption">历史界面示意 · 非实时行情</p>
        </div>
      </section>

      <section className="landing-platform landing-wrap landing-glass" id="real-product" aria-labelledby="landing-product-title">
        <div className="landing-section-heading">
          <h2 id="landing-product-title">每一个问题，都有研究入口。</h2>
          <p>从发现变化到核验假设，让信息真正进入你的研究过程。</p>
        </div>
        <div className="landing-product-tabs" role="tablist" aria-label="产品功能展示">
          {productTours.map((item, index) => (
            <button
              key={item.title}
              id={`landing-tab-${index}`}
              ref={(node) => { tabRefs.current[index] = node; }}
              type="button"
              role="tab"
              aria-selected={index === activeProduct}
              aria-controls="landing-product-panel"
              tabIndex={index === activeProduct ? 0 : -1}
              className={index === activeProduct ? 'is-active' : ''}
              onClick={() => setActiveProduct(index)}
              onKeyDown={(event) => handleTabKey(event, index)}
            >{item.title}</button>
          ))}
        </div>
        <div className="landing-product-stage" id="landing-product-panel" role="tabpanel" aria-labelledby={`landing-tab-${activeProduct}`} tabIndex={0}>
          <figure className="landing-product-screen">
            <img key={product.screenshot} src={product.screenshot} alt={product.alt} width="1440" height="900" loading="lazy" decoding="async" />
            <figcaption><span>实机页面截图 · 数据与界面以登录后为准</span><a href={product.screenshot} target="_blank" rel="noreferrer" aria-label={`查看${product.title}界面大图`}>查看大图<ArrowRight aria-hidden="true" /></a></figcaption>
          </figure>
          <div className="landing-product-copy" key={product.title}>
            <h3>{product.heading}</h3>
            <p>{product.description}</p>
            <dl>
              <div><dt>使用数据</dt><dd>{product.data}</dd></div>
              <div><dt>研究输出</dt><dd>{product.output}</dd></div>
            </dl>
            <Link className="landing-button landing-button--primary" to={product.href}>{product.action}<ArrowRight aria-hidden="true" /></Link>
          </div>
        </div>
        <div className="landing-utilities" aria-label="更多研究工具">
          {utilities.map(({ title, detail, href, icon: Icon }) => (
            <Link to={href} key={title}><Icon aria-hidden="true" /><span><strong>{title}</strong><small>{detail}</small></span><ArrowRight aria-hidden="true" /></Link>
          ))}
        </div>
      </section>

      <section className="landing-foundation landing-wrap" id="data-sources" aria-labelledby="landing-data-title">
        <div className="landing-data-intro">
          <h2 id="landing-data-title">多源信息，<br />保留每一条来路。</h2>
          <p>把分散的信息组织起来。看结论，也能继续查看来源、时间与原文。</p>
        </div>
        <div className="landing-source-grid">
          {sources.map(({ title, detail, icon: Icon }) => (
            <article key={title}><Icon aria-hidden="true" /><h3>{title}</h3><p>{detail}</p></article>
          ))}
        </div>
      </section>

      <section className="landing-final landing-wrap landing-glass" aria-labelledby="landing-final-title">
        <div><h2 id="landing-final-title">开始你的下一次研究。</h2><p>登录或注册，开启你的个人研究空间。</p></div>
        <div className="landing-final-actions">
          <Link className="landing-button landing-button--primary" to="/access?mode=register&redirect=%2Fapp">注册账号<ArrowRight aria-hidden="true" /></Link>
          <Link className="landing-button landing-button--glass" to="/guide">使用手册<BookOpen aria-hidden="true" /></Link>
        </div>
        <p className="landing-account-note"><ShieldCheck aria-hidden="true" />个人自选股、问股记录与研究任务按账号独立保存。</p>
      </section>

      <footer className="landing-footer landing-wrap">
        <Link className="landing-footer-brand" to="/">乐子乌智能投研平台</Link>
        <p>AI 辅助研究，不替代原文核验。投资有风险，研究结论不构成收益承诺。</p>
        <a href="https://leowu.leziwu.com" target="_blank" rel="noopener noreferrer">作者介绍</a>
        <Link to="/admin">管理员</Link>
      </footer>
    </main>
  );
};

export default LandingPage;
