import { chromium } from 'playwright';
const main = async () => {
  const CHROME='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
  let b; try { b=await chromium.launch({channel:'chrome'}); } catch { b=await chromium.launch({executablePath:CHROME}); }
  const ctx = await b.newContext({viewport:{width:1440,height:900}});
  await ctx.addInitScript(()=>{try{localStorage.setItem('theme','light')}catch{}});
  const page = await ctx.newPage();
  await page.goto('http://127.0.0.1:5173/super-watchlist',{waitUntil:'domcontentloaded',timeout:60000});
  await page.waitForTimeout(14000);
  const info = await page.evaluate(() => {
    const out = [];
    // 找出所有含「经营与估值」的面板及其指标格
    document.querySelectorAll('section,div').forEach(el => {
      const t = (el.textContent||'').trim();
      if (!t.startsWith('经营与估值')) return;
      const r = el.getBoundingClientRect();
      if (r.width < 400) return;
      const cs = getComputedStyle(el);
      out.push({ cls:(el.className||'').toString().slice(0,60), w:Math.round(r.width), h:Math.round(r.height),
                 pad: cs.padding, gap: cs.gap, display: cs.display, gridCols: cs.gridTemplateColumns });
      // 指标格
      [...el.querySelectorAll('*')].slice(0,40).forEach(c => {
        const cc = getComputedStyle(c);
        if (cc.display === 'grid' && cc.gridTemplateColumns.split(' ').length >= 3) {
          const cr = c.getBoundingClientRect();
          out.push({ child:(c.className||'').toString().slice(0,50), grid: cc.gridTemplateColumns, gap: cc.gap, w: Math.round(cr.width), h: Math.round(cr.height) });
        }
      });
    });
    return out.slice(0, 8);
  });
  console.log(JSON.stringify(info, null, 2));
  await b.close();
};
main().catch(e=>{console.error(String(e).slice(0,200));process.exit(1)});
