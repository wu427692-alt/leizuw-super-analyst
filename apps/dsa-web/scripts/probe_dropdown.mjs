#!/usr/bin/env node
/**
 * 探针：复现自选看板搜索建议下拉框的布局问题。
 *
 * 该组件从 `/stocks.index.json` 加载股票索引后做纯本地搜索，
 * 本地库里没有索引，所以直接拦截这个请求注入假索引即可复现真实下拉。
 */
import { chromium } from 'playwright';

const BASE = process.argv[2] || 'http://127.0.0.1:8765';

// StockIndexItem：与 src/types/stockIndex.ts 对齐
// 压缩元组格式：字段顺序见 utils/stockIndexFields.ts 的 STOCK_INDEX_FIELDS
// [canonicalCode, displayCode, nameZh, pinyinFull, pinyinAbbr, aliases, market, assetType, active, popularity]
const INDEX = [
  ['300476.SZ', '300476', '胜宏科技', 'shenghongkeji', 'shkj', [], 'CN', 'stock', true, 90],
  ['603306.SH', '603306', '华懋科技', 'huamaokeji', 'hmkj', [], 'CN', 'stock', true, 80],
  ['300308.SZ', '300308', '中际旭创', 'zhongjixuchuang', 'zjxc', [], 'CN', 'stock', true, 85],
  ['300750.SZ', '300750', '宁德时代', 'ningdeshidai', 'ndsd', [], 'CN', 'stock', true, 99],
];

const main = async () => {
  const CHROME = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
  let browser;
  try { browser = await chromium.launch({ channel: 'chrome' }); }
  catch { browser = await chromium.launch({ executablePath: CHROME }); }

  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  await ctx.addInitScript(() => {
    try { window.localStorage.setItem('theme', 'light'); } catch { /* ignore */ }
  });

  const page = await ctx.newPage();
  await page.goto(`${BASE}/super-watchlist`, { waitUntil: 'domcontentloaded', timeout: 60000 });
  await page.waitForTimeout(15000);  // 3.3MB 索引 + 慢机器

  const input = page.locator('input[placeholder*="股票名称"], input[placeholder*="公司名称"]').first();
  const inputBox = await input.boundingBox();
  console.log('输入框:', JSON.stringify(inputBox));

  await input.click();
  await input.fill('');
  await input.type('胜', { delay: 120 });
  await page.waitForTimeout(8000);

  const probe = await page.evaluate(() => {
    const boxes = [];
    document.querySelectorAll('div,ul').forEach((el) => {
      const cs = getComputedStyle(el);
      if (cs.position !== 'fixed' && cs.position !== 'absolute') return;
      const r = el.getBoundingClientRect();
      if (r.width < 8) return;
      const txt = (el.textContent || '').trim();
      if (!txt.includes('胜宏科技')) return;
      boxes.push({
        tag: el.tagName.toLowerCase(),
        cls: (el.className || '').toString().slice(0, 70),
        pos: cs.position,
        rect: { x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height) },
        display: cs.display, flexDir: cs.flexDirection, overflow: cs.overflow,
        childCount: el.children.length,
        childWidths: [...el.children].slice(0, 4).map((c) => Math.round(c.getBoundingClientRect().width)),
      });
    });
    // 候选项内部结构
    const item = [...document.querySelectorAll('*')].find(
      (el) => el.children.length > 0 && (el.textContent || '').trim().startsWith('胜宏科技') && el.getBoundingClientRect().height < 100,
    );
    let itemInfo = null;
    if (item) {
      const r = item.getBoundingClientRect();
      const cs = getComputedStyle(item);
      itemInfo = {
        tag: item.tagName.toLowerCase(),
        cls: (item.className || '').toString().slice(0, 70),
        rect: { w: Math.round(r.width), h: Math.round(r.height) },
        flexDir: cs.flexDirection, flexWrap: cs.flexWrap, whiteSpace: cs.whiteSpace,
        grandChildren: [...item.children].map((c) => {
          const cr = c.getBoundingClientRect();
          return { cls: (c.className || '').toString().slice(0, 40), w: Math.round(cr.width), h: Math.round(cr.height) };
        }),
      };
    }
    return { boxes, itemInfo };
  });

  console.log('\n=== 含「胜宏科技」的浮层容器 ===');
  for (const b of probe.boxes) console.log(JSON.stringify(b));
  console.log('\n=== 候选项内部结构 ===');
  console.log(JSON.stringify(probe.itemInfo, null, 2));

  await page.screenshot({ path: '/tmp/probe-dropdown-real.png' });
  console.log('\n截图: /tmp/probe-dropdown-real.png');
  await browser.close();
};

main().catch((e) => { console.error(String(e).slice(0, 500)); process.exit(1); });
