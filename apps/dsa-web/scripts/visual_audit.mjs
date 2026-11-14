#!/usr/bin/env node
/**
 * 桌面端视觉审计：逐页截图 + 采集可度量的布局问题。
 *
 * 这是「不靠肉眼猜、也不靠读代码猜」的检查方式——真实浏览器渲染后测量：
 * 主题是否一致、能否滚动、有无横向溢出、字号/行高、卡片密度、重影面板。
 *
 * 用法：
 *   node scripts/visual_audit.mjs [baseUrl] [outDir]
 * 默认 http://127.0.0.1:8765 与 /tmp/visual-audit
 */
import { chromium } from 'playwright';
import fs from 'node:fs/promises';
import path from 'node:path';

const BASE = process.argv[2] || 'http://127.0.0.1:8765';
const OUT = process.argv[3] || '/tmp/visual-audit';
/** 主题：dark / light。用户报的「团队是黑的、其它是白的」只在浅色主题下成立，两个都要测。 */
const THEME = process.env.AUDIT_THEME || 'dark';

/** 桌面端要过一遍的全部路由（含子路由与之前被忽略的页面）。 */
const ROUTES = [
  ['home', '/home'],
  ['app', '/app'],
  ['meeting', '/meeting'],
  ['chat', '/chat'],
  ['concept-themes', '/concept-themes'],
  ['super-watchlist', '/super-watchlist'],
  ['industry-research', '/industry-research'],
  ['tasks', '/tasks'],
  ['ai-news', '/ai-news'],
  ['essay-radar', '/essay-radar'],
  ['essay-radar-insights', '/essay-radar/insights'],
  ['essay-quant', '/essay-quant'],
  ['data-acquisition', '/data-acquisition'],
  ['screening', '/screening'],
  ['guide', '/guide'],
  ['admin', '/admin'],
  ['admin-data-sources', '/admin/data-sources'],
  ['admin-usage', '/admin/usage'],
  ['me', '/me'],
  ['dragon-tiger', '/investment-monitor/dragon-tiger'],
  ['feed', '/investment-monitor/feed'],
];

const probe = () => {
  const doc = document.documentElement;
  const body = document.body;

  // 竖向滚动能力：整页可滚内容是否超过视口，且没有被某层挡住
  const scrollHeight = Math.max(doc.scrollHeight, body.scrollHeight);
  const viewport = window.innerHeight;
  const canScrollDoc = scrollHeight > viewport + 4;

  // 找出把滚动锁死的容器：overflow 为 hidden/auto 且内容超高的元素
  const lockers = [];
  document.querySelectorAll('*').forEach((el) => {
    const cs = getComputedStyle(el);
    if (cs.overflowY === 'hidden' && el.scrollHeight > el.clientHeight + 8 && el.clientHeight > 200) {
      const r = el.getBoundingClientRect();
      if (r.height > viewport * 0.4) {
        lockers.push({
          tag: el.tagName.toLowerCase(),
          cls: (el.className || '').toString().slice(0, 90),
          clientH: el.clientHeight,
          scrollH: el.scrollHeight,
        });
      }
    }
  });

  // 主题一致性：统计背景色，检查是否有大面积与页面底不同的深色块
  const bgCount = {};
  document.querySelectorAll('body *').forEach((el) => {
    const r = el.getBoundingClientRect();
    const area = r.width * r.height;
    if (area < window.innerWidth * window.innerHeight * 0.18) return;
    const bg = getComputedStyle(el).backgroundColor;
    if (!bg || bg === 'rgba(0, 0, 0, 0)' || bg === 'transparent') return;
    bgCount[bg] = (bgCount[bg] || 0) + area;
  });
  const dominantBgs = Object.entries(bgCount)
    .sort((a, b) => b[1] - a[1])
    .slice(0, 4)
    .map(([bg, area]) => ({ bg, pct: Math.round((area / (window.innerWidth * window.innerHeight)) * 100) }));

  // iframe：数量与主题（iframe 内文档无法跨源读，但能报存在与尺寸）
  const iframes = [...document.querySelectorAll('iframe')].map((f) => ({
    src: (f.getAttribute('src') || '').slice(0, 60),
    h: Math.round(f.getBoundingClientRect().height),
  }));

  // 字号过小的可见文本
  const tiny = [];
  document.querySelectorAll('body *').forEach((el) => {
    if (el.children.length) return;
    const text = (el.textContent || '').trim();
    if (!text || text.length < 2) return;
    const r = el.getBoundingClientRect();
    if (r.width === 0 || r.height === 0) return;
    const fs = parseFloat(getComputedStyle(el).fontSize);
    if (fs && fs < 12) tiny.push({ text: text.slice(0, 24), fs });
  });

  // 横向溢出
  const overflowX = doc.scrollWidth > window.innerWidth + 2;

  // 卡片/面板数量（粗口径：有边框或背景的独立块）
  let panels = 0;
  document.querySelectorAll('section, article, [class*="card"], [class*="panel"], [class*="Card"], [class*="Panel"]').forEach((el) => {
    const r = el.getBoundingClientRect();
    if (r.width > 120 && r.height > 60) panels += 1;
  });

  return {
    title: document.title,
    theme: doc.className.includes('dark') || document.body.className.includes('dark') ? 'dark' : 'light',
    bodyBg: getComputedStyle(body).backgroundColor,
    scrollHeight,
    viewport,
    canScrollDoc,
    lockers,
    dominantBgs,
    iframes,
    tinyCount: tiny.length,
    tinySamples: tiny.slice(0, 5),
    overflowX,
    panels,
    mainCount: document.querySelectorAll('main').length,
  };
};

const main = async () => {
  await fs.mkdir(OUT, { recursive: true });
  // 优先用本机 Chrome，避免依赖 playwright 自带浏览器下载
  const CHROME = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
  let browser;
  try {
    browser = await chromium.launch({ channel: 'chrome' });
  } catch {
    browser = await chromium.launch({ executablePath: CHROME });
  }
  const ctx = await browser.newContext({
    viewport: { width: 1440, height: 900 },
    deviceScaleFactor: 1,
  });
  // 用 localStorage 固定主题，避免跟随系统
  await ctx.addInitScript((theme) => {
    try {
      window.localStorage.setItem('theme', theme);
      window.localStorage.setItem('dsa-theme', theme);
    } catch { /* ignore */ }
    // next-themes 读 'theme'；同时给 html 预置类，首帧即正确
    document.addEventListener('DOMContentLoaded', () => {
      document.documentElement.classList.toggle('dark', theme === 'dark');
      document.documentElement.style.colorScheme = theme;
    });
  }, THEME);

  const results = [];

  for (const [name, route] of ROUTES) {
    const page = await ctx.newPage();
    const consoleErrors = [];
    page.on('console', (m) => { if (m.type() === 'error') consoleErrors.push(m.text().slice(0, 160)); });
    page.on('pageerror', (e) => consoleErrors.push(`PAGEERROR ${String(e).slice(0, 160)}`));
    try {
      await page.goto(`${BASE}${route}`, { waitUntil: 'domcontentloaded', timeout: 45000 });
      await page.waitForTimeout(4500);
      const data = await page.evaluate(probe);

      // 能不能真的滚动。
      // 注意：本应用多数页面是「外壳固定高度 + 内部容器滚动」，只测 window.scrollY
      // 会全部误判成「不能滚动」。这里先找真正承载内容的滚动容器再测它。
      const scrollTest = await page.evaluate(() => {
        const canScroll = (el) => el && el.scrollHeight > el.clientHeight + 8;
        const tryScroll = (el) => {
          if (!canScroll(el)) return 0;
          const before = el.scrollTop;
          el.scrollTop = el.scrollHeight;
          const moved = el.scrollTop - before;
          el.scrollTop = before;
          return moved;
        };

        // 1) document 滚动
        const docBefore = window.scrollY;
        window.scrollTo(0, document.documentElement.scrollHeight);
        const docMoved = window.scrollY - docBefore;
        window.scrollTo(0, docBefore);
        if (docMoved > 0) return { mode: 'document', moved: docMoved };

        // 2) 找到所有可滚动容器，取内容最多的那个
        let best = null;
        document.querySelectorAll('main, [class*="main"], [class*="content"], [class*="page"], div, section').forEach((el) => {
          const cs = getComputedStyle(el);
          if (cs.overflowY !== 'auto' && cs.overflowY !== 'scroll' && cs.overflowY !== 'overlay') return;
          if (!canScroll(el)) return;
          if (!best || el.scrollHeight - el.clientHeight > best.scrollHeight - best.clientHeight) best = el;
        });
        if (best) {
          const moved = tryScroll(best);
          return { mode: 'inner', moved, cls: (best.className || '').toString().slice(0, 70) };
        }
        return { mode: 'none', moved: 0 };
      });

      await page.screenshot({ path: path.join(OUT, `${name}.png`), fullPage: false });
      await page.screenshot({ path: path.join(OUT, `${name}-full.png`), fullPage: true }).catch(() => {});
      results.push({ name, route, ok: true, ...data, scrollTest, consoleErrors: consoleErrors.slice(0, 4) });
      console.log(`✓ ${name.padEnd(22)} theme=${data.theme} scroll=${scrollTest.mode}/${scrollTest.moved}px overflowX=${data.overflowX} tiny=${data.tinyCount} panels=${data.panels} main=${data.mainCount}`);
    } catch (e) {
      results.push({ name, route, ok: false, error: String(e).slice(0, 200), consoleErrors });
      console.log(`✗ ${name.padEnd(22)} ${String(e).slice(0, 90)}`);
    } finally {
      await page.close();
    }
  }

  await browser.close();
  await fs.writeFile(path.join(OUT, 'report.json'), JSON.stringify(results, null, 2));

  // 汇总
  console.log('\n================ 汇总 ================');
  // 真正的「不能滚动」= 内容超出视口，但 document 和内部容器都动不了
  const badScroll = results.filter((r) => {
    if (!r.ok) return false;
    const contentTaller = r.scrollHeight > r.viewport + 8;
    const innerMoved = (r.scrollTest?.moved || 0) > 0;
    return contentTaller && !innerMoved;
  });
  const darkOnLight = results.filter((r) => r.ok && r.theme === 'light' && r.dominantBgs.some((b) => /rgb\((\d+), (\d+), (\d+)\)/.test(b.bg) && b.pct > 25 && (() => { const m = b.bg.match(/rgb\((\d+), (\d+), (\d+)\)/); return Number(m[1]) + Number(m[2]) + Number(m[3]) < 180; })()));
  console.log(`无法滚动: ${badScroll.length} -> ${badScroll.map((r) => r.name).join(', ') || '无'}`);
  console.log(`深色块出现在浅色主题: ${darkOnLight.length} -> ${darkOnLight.map((r) => r.name).join(', ') || '无'}`);
  console.log(`横向溢出: ${results.filter((r) => r.ok && r.overflowX).map((r) => r.name).join(', ') || '无'}`);
  console.log(`含 iframe 的页: ${results.filter((r) => r.ok && r.iframes.length).map((r) => `${r.name}(${r.iframes.map((f) => f.h + 'px:' + f.src).join('|')})`).join(', ') || '无'}`);
  console.log(`多个 main 的页: ${results.filter((r) => r.ok && r.mainCount > 1).map((r) => `${r.name}:${r.mainCount}`).join(', ') || '无'}`);
  console.log(`控制台报错页: ${results.filter((r) => r.consoleErrors.length).map((r) => r.name).join(', ') || '无'}`);
  console.log(`截图目录: ${OUT}（主题 ${THEME}）`);
};

main().catch((e) => { console.error(e); process.exit(1); });
