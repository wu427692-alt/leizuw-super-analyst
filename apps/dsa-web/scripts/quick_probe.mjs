import { chromium } from 'playwright';
const BASE = process.argv[2];
const main = async () => {
  const CHROME = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
  let b; try { b = await chromium.launch({ channel: 'chrome' }); } catch { b = await chromium.launch({ executablePath: CHROME }); }
  const ctx = await b.newContext({ viewport: { width: 1440, height: 900 } });
  await ctx.addInitScript(() => { try { localStorage.setItem('theme','light'); } catch {} });
  const page = await ctx.newPage();
  const errs = [];
  page.on('console', m => { if (m.type()==='error') errs.push(m.text().slice(0,120)); });
  await page.goto(`${BASE}/super-watchlist`, { waitUntil: 'domcontentloaded', timeout: 60000 });
  await page.waitForTimeout(12000);
  // 索引是否加载
  const idx = await page.evaluate(() => {
    const keys = Object.keys(localStorage);
    return { lsKeys: keys.slice(0,6) };
  });
  console.log('localStorage keys:', JSON.stringify(idx.lsKeys));
  const input = page.locator('input[placeholder*="股票名称"], input[placeholder*="公司名称"]').first();
  await input.click(); await input.fill(''); await input.type('宁德', { delay: 150 });
  await page.waitForTimeout(6000);
  const state = await page.evaluate(() => {
    const ul = document.getElementById('suggestions-list');
    return {
      hasList: Boolean(ul),
      listRect: ul ? (() => { const r = ul.getBoundingClientRect(); return {x:Math.round(r.x),y:Math.round(r.y),w:Math.round(r.width),h:Math.round(r.height)}; })() : null,
      itemCount: ul ? ul.children.length : 0,
      firstItemText: ul && ul.children[0] ? (ul.children[0].textContent||'').trim().slice(0,40) : null,
      inputValue: document.querySelector('input[placeholder*="股票名称"], input[placeholder*="公司名称"]')?.value,
    };
  });
  console.log('下拉状态:', JSON.stringify(state, null, 2));
  console.log('控制台错误:', errs.slice(0,3));
  await page.screenshot({ path: '/tmp/quick-dropdown.png' });
  await b.close();
};
main().catch(e => { console.error(String(e).slice(0,300)); process.exit(1); });
