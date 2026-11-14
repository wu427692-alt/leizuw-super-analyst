import { chromium } from 'playwright';
const main = async () => {
  const CHROME='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
  let b; try { b=await chromium.launch({channel:'chrome'}); } catch { b=await chromium.launch({executablePath:CHROME}); }
  const ctx = await b.newContext({viewport:{width:1440,height:900}});
  await ctx.addInitScript(()=>{try{localStorage.setItem('theme','light')}catch{}});
  for (const [n,r] of [['super-watchlist','/super-watchlist'],['home','/home']]) {
    const page = await ctx.newPage();
    await page.goto('http://127.0.0.1:5173'+r,{waitUntil:'domcontentloaded',timeout:60000});
    await page.waitForTimeout(14000);
    const out = await page.evaluate(() => {
      const root = document.querySelector('[data-research-workspace="true"]');
      if (!root) return [];
      // 直接子级及其后代的主要面板，按 top 排序，算相邻垂直间隙
      const blocks = [...root.querySelectorAll('section, .super-market-band, [class*="panel"], [class*="card"]')]
        .map(el => { const b = el.getBoundingClientRect(); return { top: Math.round(b.top), bottom: Math.round(b.bottom), h: Math.round(b.height), cls: (el.className||'').toString().slice(0,44) }; })
        .filter(x => x.h > 60)
        .sort((a,b) => a.top - b.top);
      const gaps = [];
      for (let i=1;i<blocks.length;i++) {
        const g = blocks[i].top - blocks[i-1].bottom;
        if (g > 0 && g < 200) gaps.push({ gap: g, between: `${blocks[i-1].cls} -> ${blocks[i].cls}` });
      }
      const big = gaps.filter(x=>x.gap>=16);
      return { count: gaps.length, avgGap: gaps.length ? Math.round(gaps.reduce((s,x)=>s+x.gap,0)/gaps.length) : 0,
               bigGaps: big.slice(0,6) };
    });
    console.log(`\n=== ${n} ===`);
    console.log('  面板数间隙:', out.count, '平均间隙:', out.avgGap+'px');
    for (const g of out.bigGaps) console.log(`   ${g.gap}px  ${g.between}`);
    await page.close();
  }
  await b.close();
};
main().catch(e=>{console.error(String(e).slice(0,200));process.exit(1)});
