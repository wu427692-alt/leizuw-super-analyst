import { chromium } from 'playwright';
const BASE = process.argv[2];
const ROUTES = [['home','/home'],['app','/app'],['super-watchlist','/super-watchlist'],['essay-radar','/essay-radar'],['concept-themes','/concept-themes']];
const main = async () => {
  const CHROME='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
  let b; try { b=await chromium.launch({channel:'chrome'}); } catch { b=await chromium.launch({executablePath:CHROME}); }
  const ctx = await b.newContext({viewport:{width:1440,height:900}});
  await ctx.addInitScript(()=>{try{localStorage.setItem('theme','light')}catch{}});
  for (const [name, route] of ROUTES) {
    const page = await ctx.newPage();
    await page.goto(BASE+route,{waitUntil:'domcontentloaded',timeout:60000});
    await page.waitForTimeout(12000);
    const m = await page.evaluate(() => {
      const main = document.querySelector('[data-research-workspace="true"]') || document.querySelector('main') || document.body;
      const r = main.getBoundingClientRect();
      // 统计 main 内可见文本节点数量与字符数
      let chars = 0, nodes = 0, boxes = 0;
      main.querySelectorAll('*').forEach(el => {
        const cs = getComputedStyle(el);
        if (cs.display === 'none' || cs.visibility === 'hidden') return;
        const er = el.getBoundingClientRect();
        if (er.width < 1 || er.height < 1) return;
        if (cs.borderTopWidth !== '0px' || cs.borderBottomWidth !== '0px' || cs.backgroundColor !== 'rgba(0, 0, 0, 0)') boxes++;
        if (el.children.length === 0) { const t=(el.textContent||'').trim(); if(t){ chars += t.length; nodes++; } }
      });
      // 空白率：main 高度内有多少是纯空隙（用元素覆盖估算）
      return { mainW: Math.round(r.width), mainH: Math.round(r.height), chars, nodes, boxes,
               charsPerScreen: Math.round(chars / Math.max(1, r.height/900)),
               nodesPerScreen: Math.round(nodes / Math.max(1, r.height/900)) };
    });
    console.log(`${name.padEnd(18)} 宽${m.mainW} 高${m.mainH} | 文本节点${m.nodes} 字符${m.chars} 有框元素${m.boxes} | 每屏字符${m.charsPerScreen} 每屏节点${m.nodesPerScreen}`);
    await page.close();
  }
  await b.close();
};
main().catch(e=>{console.error(String(e).slice(0,200));process.exit(1)});
