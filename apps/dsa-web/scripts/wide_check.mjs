import { chromium } from 'playwright';
const main = async () => {
  const CHROME='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
  let b; try { b=await chromium.launch({channel:'chrome'}); } catch { b=await chromium.launch({executablePath:CHROME}); }
  const ctx = await b.newContext({viewport:{width:1638,height:1037}});
  await ctx.addInitScript(()=>{try{localStorage.setItem('theme','light')}catch{}});
  for (const [n,r] of [['super-watchlist','/super-watchlist'],['concept-themes','/concept-themes']]) {
    const page = await ctx.newPage();
    await page.goto('http://127.0.0.1:5173'+r,{waitUntil:'domcontentloaded',timeout:60000});
    await page.waitForTimeout(14000);
    const out = await page.evaluate(() => {
      const grid = document.querySelector('.super-brief-metrics, .concept-kpis > div.metric-row');
      const cols = grid ? getComputedStyle(grid).gridTemplateColumns : 'none';
      const nodes = grid ? grid.children.length : 0;
      return { cols, colCount: cols === 'none' ? 0 : cols.split(' ').length, nodes };
    });
    console.log(`${n}: 指标网格列数=${out.colCount} 格数=${out.nodes}`);
    console.log(`   grid-template-columns: ${out.cols}`);
    await page.screenshot({ path: `/tmp/wide-${n}.png` });
    await page.close();
  }
  await b.close();
};
main().catch(e=>{console.error(String(e).slice(0,200));process.exit(1)});
