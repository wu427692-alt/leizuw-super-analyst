import { chromium } from 'playwright';
const BASE = process.argv[2] || 'http://127.0.0.1:8000';
const ROUTES = [['总览','/home'],['行情','/app'],['自选','/super-watchlist'],['题材','/concept-themes'],['任务','/tasks'],['纪要','/essay-radar'],['设置','/settings']];

const main = async () => {
  const CHROME='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
  let b; try { b=await chromium.launch({channel:'chrome'}); } catch { b=await chromium.launch({executablePath:CHROME}); }
  console.log(`${'页面'.padEnd(8)}${'请求数'.padEnd(8)}${'API耗时'.padEnd(12)}${'最慢接口'.padEnd(40)}总耗时`);
  for (const [name, route] of ROUTES) {
    const ctx = await b.newContext({viewport:{width:1440,height:900}});
    const page = await ctx.newPage();
    const api = [];
    page.on('response', async (resp) => {
      const u = resp.url();
      if (!u.includes('/api/')) return;
      const t = resp.request().timing();
      api.push({ url: u.replace(BASE,'').split('?')[0], ms: Math.round(resp.request().timing() ? 0 : 0) });
    });
    // 更可靠：用 requestfinished 事件拿 duration
    const durations = [];
    page.on('requestfinished', async (req) => {
      if (!req.url().includes('/api/')) return;
      try { const r = await req.response(); if (!r) return; const tm = req.timing(); durations.push({ url: req.url().replace(BASE,'').split('?')[0], ms: Math.round(tm.responseEnd) }); } catch {}
    });
    const t0 = Date.now();
    await page.goto(BASE+route, { waitUntil: 'domcontentloaded', timeout: 90000 });
    await page.waitForTimeout(9000);
    const total = Date.now() - t0;
    const apiTotal = durations.reduce((s,x)=>s+x.ms,0);
    const slowest = durations.slice().sort((a,b)=>b.ms-a.ms)[0];
    console.log(`${name.padEnd(8)}${String(durations.length).padEnd(8)}${(apiTotal+'ms').padEnd(12)}${(slowest ? slowest.url.slice(0,38):'-').padEnd(40)}${'~'+(total/1000).toFixed(1)+'s'}`);
    if (durations.length) {
      durations.sort((a,b)=>b.ms-a.ms);
      for (const d of durations.slice(0,4)) console.log(`    ${String(d.ms).padStart(6)}ms  ${d.url}`);
    }
    await ctx.close();
  }
  await b.close();
};
main().catch(e=>{console.error(String(e).slice(0,300));process.exit(1)});
