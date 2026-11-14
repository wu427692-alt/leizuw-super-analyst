import { expect, test, type Page, type TestInfo } from '@playwright/test';

/**
 * 发布前 E2E 主流程（真实登录 + 真实渲染）。
 *
 * 与 `smoke.spec.ts` 的分工：
 * - `smoke.spec.ts`：逐页烟测「这一页能不能打开」；
 * - 本文件：只覆盖**跨页面、可回归的主流程**与**界面硬约束**（对齐 docs/testing.md 第 4/6 层）：
 *   1. 桌面端：登录 → 总览 `/home` → 切 Tab → 点快讯开抽屉，看到正文（且不跳路由）
 *   2. 桌面端：主要页面无横向溢出
 *   3. 桌面端：可见文本字号不小于 12px（`docs/ui-design-system.md` 最小档只留给角标）
 *   4. 手机端 390×844：`/meeting` 渲染团队条 + 对话输入框（唯一对话入口）
 *   5. 手机端：`/chat` 重定向到 `/meeting`，query 原样保留（旧深链不丢上下文）
 *   6. 手机端：主要可点热区不小于 44×44
 *
 * 没有 `DSA_WEB_SMOKE_PASSWORD` 时整份文件 skip（和 smoke 一致），不报失败。
 */
const smokePassword = process.env.DSA_WEB_SMOKE_PASSWORD;

if (!smokePassword) {
  test.skip(true, 'Set DSA_WEB_SMOKE_PASSWORD to run user flow E2E tests.');
}

const MOBILE_VIEWPORT = { width: 390, height: 844 };
const DESKTOP_VIEWPORT = { width: 1440, height: 900 };

/** 桌面端横向溢出 / 字号检查覆盖的主要页面（手机端另有专属检查）。 */
const DESKTOP_PAGES = [
  { path: '/home', name: '总览' },
  { path: '/app', name: '行情' },
  { path: '/super-watchlist', name: '自选看板' },
  { path: '/tasks', name: '任务中心' },
  { path: '/essay-radar', name: '纪要雷达' },
  { path: '/data-acquisition', name: '数据获取' },
];

async function login(page: Page) {
  test.skip(!smokePassword, 'Set DSA_WEB_SMOKE_PASSWORD to run authenticated E2E tests.');

  await page.goto('/login');
  await page.waitForLoadState('domcontentloaded');

  const passwordInput = page.locator('#password');
  const alreadyAuthenticated = !(await passwordInput.isVisible({ timeout: 4_000 }).catch(() => false));

  if (alreadyAuthenticated) {
    await page.waitForLoadState('domcontentloaded');
    return;
  }

  await passwordInput.fill(smokePassword!);
  const submitButton = page.getByRole('button', { name: /授权进入工作台|完成设置并登录/ });
  await expect(submitButton).toBeVisible();

  await Promise.all([
    page.waitForResponse(
      (response) => response.url().includes('/api/v1/auth/login') && response.status() === 200,
      { timeout: 15_000 },
    ),
    submitButton.click(),
  ]);

  await page.waitForLoadState('domcontentloaded');
  await page.waitForTimeout(800);
}

/** 打开页面并等首屏请求稳定：等不到 networkidle 也不阻塞，用固定窗口兜底。 */
async function gotoPage(page: Page, path: string) {
  await page.goto(path);
  await page.waitForLoadState('domcontentloaded');
  await page.waitForTimeout(1_200);
}

async function capture(page: Page, testInfo: Pick<TestInfo, 'outputPath' | 'attach'>, name: string) {
  const path = testInfo.outputPath(`${name}.png`);
  await page.screenshot({ path, fullPage: true });
  await testInfo.attach(name, { path, contentType: 'image/png' });
}

/** 可见且承载文本的元素里，字号 < 12px 的（角标/徽标类允许）。 */
async function smallFontViolations(page: Page) {
  return page.evaluate(() => {
    const offenders: string[] = [];

    const isVisible = (element: Element) => {
      const rect = element.getBoundingClientRect();
      if (rect.width < 1 || rect.height < 1) return false;
      const style = window.getComputedStyle(element);
      return style.visibility !== 'hidden' && style.display !== 'none' && style.opacity !== '0';
    };

    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
    let node = walker.nextNode();
    while (node) {
      const text = (node.textContent ?? '').trim();
      const element = node.parentElement;
      if (text.length >= 2 && element && isVisible(element)) {
        const className = typeof (element as HTMLElement).className === 'string'
          ? String((element as HTMLElement).className)
          : '';
        // 角标/徽标按设计令牌可以用 --fs-caption（12px）；这里只拦「正文被压小」。
        const allowedByDesign = /badge|chip|kbd|sup|sub|tag/i.test(className)
          || element.closest('[aria-hidden="true"]') !== null;
        const fontSize = Number.parseFloat(window.getComputedStyle(element).fontSize);
        if (!allowedByDesign && fontSize > 0 && fontSize < 12) {
          offenders.push(`${element.tagName.toLowerCase()}.${className.slice(0, 40)} fontSize=${fontSize} text="${text.slice(0, 18)}"`);
        }
      }
      node = walker.nextNode();
    }

    return offenders.slice(0, 10);
  });
}

/** 文档级横向溢出：scrollWidth 不得超过视口宽度（1px 容差给滚动条/取整）。 */
async function horizontalOverflow(page: Page) {
  return page.evaluate(() => {
    const inner = window.innerWidth;
    const docScroll = document.documentElement.scrollWidth;
    const bodyScroll = document.body.scrollWidth;
    const widest = Math.max(docScroll, bodyScroll);
    if (widest <= inner + 1) return null;
    return { inner, docScroll, bodyScroll, overflow: widest - inner };
  });
}

/**
 * 手机端主要可点热区检查。
 *
 * 设计上允许「刻意紧凑」的控件（对话发送键 34px、技能 chip、图标工具、弹层把手等，
 * 见 `docs/ui-design-system.md` §15）；其余可点元素必须达到 44×44。
 * 底部主导航是最关键的一组，单独严格断言。
 */
async function smallTapTargetViolations(page: Page) {
  return page.evaluate(() => {
    const offenders: string[] = [];
    const compactByDesign = /chat-send|chat-stop|chat-skill|chat-session|chat-composer|composer|icon-only|__close|sheet__handle|chip|badge|badge-btn|inline-link|mobile-row__action|page-tabs__item/i;

    const isVisible = (element: Element) => {
      const rect = element.getBoundingClientRect();
      if (rect.width < 1 || rect.height < 1) return false;
      const style = window.getComputedStyle(element);
      return style.visibility !== 'hidden' && style.display !== 'none';
    };

    const nodes = Array.from(document.querySelectorAll('button, a[href], [role="button"]'));
    for (const element of nodes) {
      if (!isVisible(element)) continue;
      if (element.closest('[aria-hidden="true"]')) continue;
      const className = typeof (element as HTMLElement).className === 'string'
        ? String((element as HTMLElement).className)
        : '';
      const label = (element.getAttribute('aria-label') || element.textContent || '').trim().slice(0, 24);
      const rect = element.getBoundingClientRect();
      const isBottomNav = element.closest('nav[aria-label="手机主导航"]') !== null;
      const isCompact = compactByDesign.test(className);
      if (isBottomNav) {
        // 底部主导航不允许紧凑豁免：至少 44 宽、44 高。
        if (rect.width < 44 || rect.height < 44) {
          offenders.push(`[底部导航] ${label} ${Math.round(rect.width)}×${Math.round(rect.height)}`);
        }
        continue;
      }
      if (isCompact) continue;
      if (rect.width < 44 || rect.height < 44) {
        offenders.push(`${element.tagName.toLowerCase()}.${className.slice(0, 30)} "${label}" ${Math.round(rect.width)}×${Math.round(rect.height)}`);
      }
    }

    return offenders.slice(0, 12);
  });
}

// ===========================================================================
// 桌面端 1440×900
// ===========================================================================
test.describe('桌面端主流程（1440×900）', () => {
  test.use({ viewport: DESKTOP_VIEWPORT, locale: 'zh-CN' });

  test('登录后进入总览，切 Tab 点快讯，抽屉里能看到正文且不跳路由', async ({ page }, testInfo) => {
    await login(page);
    await gotoPage(page, '/home');

    // 总览桌面端是 Tab 布局：市场概览 / 我的关注 / 快讯
    const tabs = page.getByRole('tablist', { name: '总览视图切换' });
    await expect(tabs).toBeVisible({ timeout: 15_000 });

    const newsTab = tabs.getByRole('tab', { name: /快讯/ });
    await newsTab.click();
    await expect(newsTab).toHaveAttribute('aria-selected', 'true');

    const newsItems = page.locator('button.workbench-news');
    const newsCount = await newsItems.count();
    test.skip(newsCount === 0, '当前账号/数据源没有快讯，跳过正文抽屉检查（不是门禁失败）');

    const first = newsItems.first();
    const firstLabel = (await first.getAttribute('aria-label')) ?? '';
    await expect(first).toBeVisible({ timeout: 10_000 });
    await first.click();

    // 抽屉是就地弹层，URL 必须仍然停在 /home
    await expect(page.getByRole('dialog')).toBeVisible({ timeout: 10_000 });
    expect(new URL(page.url()).pathname).toBe('/home');

    const article = page.locator('[role="dialog"] .news-sheet__article');
    await expect(article).toBeVisible({ timeout: 10_000 });
    await expect(article.locator('.news-sheet__headline')).toBeVisible();

    // 正文可能出现两种合法结果：① 有正文段落；② 该来源确实没有正文，给出空态 + 阅读原文。
    await expect
      .poll(async () => {
        const paragraphs = await article.locator('.news-sheet__content p').count();
        const emptyState = await article.locator('.news-sheet__empty').count();
        return paragraphs + emptyState;
      }, { timeout: 20_000, message: `快讯「${firstLabel}」的抽屉既没有正文也没有空态` })
      .toBeGreaterThan(0);

    const paragraphs = await article.locator('.news-sheet__content p').count();
    if (paragraphs === 0) {
      // 空态也算「看到了内容说明」，但必须给出阅读原文入口，不能是空白弹层。
      await expect(article.locator('.news-sheet__empty')).toContainText('该来源未提供正文');
      await expect(article.getByRole('link', { name: /阅读原文/ })).toBeVisible();
    }

    await capture(page, testInfo, 'desktop-workbench-news-sheet');
  });

  test('主要页面无横向溢出', async ({ page }) => {
    await login(page);

    const overflows: string[] = [];
    for (const { path, name } of DESKTOP_PAGES) {
      await gotoPage(page, path);
      const result = await horizontalOverflow(page);
      if (result) {
        overflows.push(`${name}（${path}）：文档宽 ${Math.max(result.docScroll, result.bodyScroll)}px > 视口 ${result.inner}px，溢出 ${result.overflow}px`);
      }
    }

    expect(overflows, `以下页面出现横向溢出：\n${overflows.join('\n')}`).toEqual([]);
  });

  test('主要页面可见文本字号不小于 12px', async ({ page }) => {
    await login(page);

    const violations: string[] = [];
    for (const { path, name } of DESKTOP_PAGES) {
      await gotoPage(page, path);
      const found = await smallFontViolations(page);
      for (const item of found) {
        violations.push(`${name}（${path}）：${item}`);
      }
    }

    expect(violations, `以下可见文本小于 12px：\n${violations.join('\n')}`).toEqual([]);
  });
});

// ===========================================================================
// 手机端 390×844
// ===========================================================================
test.describe('手机端主流程（390×844）', () => {
  test.use({ viewport: MOBILE_VIEWPORT, locale: 'zh-CN', isMobile: true, hasTouch: true });

  test('/meeting 渲染团队角色条与对话输入框', async ({ page }, testInfo) => {
    await login(page);
    await gotoPage(page, '/meeting');

    // 唯一对话入口：合并后的页面容器
    await expect(page.getByTestId('meeting-chat-page')).toBeVisible({ timeout: 15_000 });

    // 顶部团队条（AI 研究小组角色 chip 行）与议题动作
    await expect(page.getByRole('group', { name: 'AI 研究小组角色' })).toBeVisible({ timeout: 15_000 });
    await expect(
      page.getByRole('button', { name: /设置研究议题|修改研究议题/ }),
    ).toBeVisible();

    // 对话本体：输入框 + 发送键（复用 ChatPage）
    const composer = page.getByPlaceholder('输入研究问题');
    await expect(composer).toBeVisible({ timeout: 15_000 });
    await expect(page.getByRole('button', { name: '发送', exact: true })).toBeVisible();

    // 旧入口已收敛：手机端不应再出现独立的「AI 问股」导航项
    await expect(page.locator('nav[aria-label="手机主导航"] a[href="/chat"]')).toHaveCount(0);

    await capture(page, testInfo, 'mobile-meeting');
  });

  test('/chat 重定向到 /meeting 并保留 query（旧深链不丢上下文）', async ({ page }) => {
    await login(page);

    await page.goto('/chat?stock=600519&from=meeting&topic=%E5%85%89%E4%BC%8F');
    await page.waitForURL((url) => url.pathname === '/meeting', { timeout: 20_000 });

    const current = new URL(page.url());
    expect(current.pathname).toBe('/meeting');
    expect(current.searchParams.get('stock')).toBe('600519');
    expect(current.searchParams.get('topic')).toBe('光伏');

    await expect(page.getByTestId('meeting-chat-page')).toBeVisible({ timeout: 15_000 });
    await expect(page.getByPlaceholder('输入研究问题')).toBeVisible({ timeout: 15_000 });
  });

  test('主要可点热区不小于 44×44', async ({ page }) => {
    await login(page);

    const violations: string[] = [];
    for (const path of ['/meeting', '/home']) {
      await gotoPage(page, path);
      const found = await smallTapTargetViolations(page);
      for (const item of found) {
        violations.push(`${path}：${item}`);
      }
    }

    expect(violations, `以下可点热区小于 44×44：\n${violations.join('\n')}`).toEqual([]);
  });
});
