import { chromium } from 'playwright';
import { mkdir } from 'node:fs/promises';
import path from 'node:path';

const baseUrl = process.env.DSA_SCREENSHOT_BASE_URL ?? 'http://127.0.0.1:4173';
const outputDir = path.resolve('public/landing/screens');

const captures = [
  ['market-overview', '/app', 8_000],
  ['super-watchlist', '/super-watchlist', 20_000],
  ['essay-radar', '/essay-radar/insights', 12_000],
  ['investment-monitor', '/investment-monitor', 8_000],
  ['quant-workbench', '/essay-quant', 10_000],
  ['data-acquisition', '/data-acquisition', 10_000],
  ['industry-research', '/industry-research', 8_000],
];
const requestedCapture = process.env.DSA_SCREENSHOT_NAME;
const selectedCaptures = requestedCapture
  ? captures.filter(([name]) => name === requestedCapture)
  : captures;

await mkdir(outputDir, { recursive: true });
const executablePath = process.env.DSA_SCREENSHOT_BROWSER_PATH
  ?? '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
const browser = await chromium.launch({ headless: true, executablePath });

try {
  const context = await browser.newContext({
    viewport: { width: 1440, height: 900 },
    deviceScaleFactor: 1,
    locale: 'zh-CN',
    colorScheme: 'dark',
    reducedMotion: 'reduce',
  });

  for (const [name, route, settleMs] of selectedCaptures) {
    const page = await context.newPage();
    await page.goto(`${baseUrl}${route}`, { waitUntil: 'domcontentloaded', timeout: 45_000 });
    await page.waitForTimeout(settleMs);
    await page.screenshot({
      path: path.join(outputDir, `${name}.jpg`),
      type: 'jpeg',
      quality: 86,
      fullPage: false,
    });
    await page.close();
  }
} finally {
  await browser.close();
}
