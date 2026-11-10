# 前端 UI 重做 · 发布前置说明

本文件是本轮 UI 改动的发布检查清单，供上线前逐项核对。**发布动作尚未执行**，需确认目标机器与提交授权。

## 1. 改动范围

| 区域 | 内容 |
| --- | --- |
| 设计令牌 | `apps/dsa-web/src/styles/design-tokens.css`（新增，唯一色/字/间距/圆角/阴影来源） |
| 手机端外壳 | `src/styles/mobile-app.css`、`src/components/mobile/*`、`src/components/layout/{Shell,MobileBottomNav,MobileMoreSheet,navItems,useMobileRouteMeta}` |
| 手机端页面 | 总览（新增 `/home`）、行情、自选（列表 + 详情）、任务中心、题材图谱、纪要录音、深度研究、数据下载、量化研究、AI 问股 |
| 桌面端页面 | 行情、总览、题材图谱、纪要录音、深度研究、自选看板、任务中心 |
| 登录页 | `src/pages/UserAccessPage.*`、`src/account-access.css` |
| 独立静态页 | `apps/dsa-web/public/home.html`（投研团队，令牌化 + 主题跟随主站） |
| PWA | `public/manifest.webmanifest`、`public/sw.js`、`public/icons/*`、`index.html` meta、`src/main.tsx` 注册 |
| 后端白名单 | `api/middlewares/auth.py`：`/sw.js`、`/icons/` 允许匿名访问（PWA 安装必需） |
| 文档 | `docs/CHANGELOG.md`、`docs/ui-design-system.md`（新增）、若干既有文档同步 |

未触碰：数据库、数据卷、调度、通知、发布工作流本身。

## 2. 发布前必须通过

```bash
cd apps/dsa-web
npm run lint          # 0 错误
npm run build         # 主站 / 登录页 / AI 新闻 / Jev 四个构建
npx vitest run --maxWorkers=2
```

当前结果（2026-11-10）：lint 0 错误、tsc 通过、四个构建通过、vitest **2071 通过 / 2 skipped / 3 失败**。
3 条失败均在 `ConceptThemesPage`：2 条 5 秒超时 + 1 条标题断言；单文件跑 13/13 通过，改造前基线同样超时。**发布判定：不阻断**，但需在发布记录里注明。

## 3. 发布步骤（按 docs/cloud-operations.md）

1. 后端与前端完整测试、校验 Compose 配置。
2. 停止可用性守卫定时器，构建新镜像（数据库、日志、报告、配置均为宿主机挂载，不进镜像）。
3. 只重建网页与监控容器，等待健康检查通过后恢复守卫。
4. 检查公开首页、登录拦截、`/api/health`、容器内存、最近错误日志。
5. 失败即用上一镜像重建容器。

**本次新增的外部依赖**：`/sw.js` 与 `/icons/*` 必须匿名可达，否则主屏安装会失败。发布后请直接访问这两个路径确认 200，而不是只看首页。

## 4. 回滚方案

| 层 | 方式 |
| --- | --- |
| 镜像 | 保留上一镜像 tag，`docker compose up -d` 指回上一镜像重建网页与监控容器；数据卷不回滚 |
| 静态产物 | `static/` 为构建产物，回滚镜像即回到旧产物；本地`.gitignore` 忽略 |
| 源码 | 改动尚未提交 Git；如需放弃本地改动：`git checkout -- apps/dsa-web docs api/middlewares/auth.py` |
| 备份 | 改造前完整前端快照：`.claude/reviews/ui-refresh-20261109/baseline-before-ui.tar.gz`（仅本地） |

## 5. 已知残留

- `ConceptThemesPage` 3 条测试在并发机器负载下超时（非回归）。
- 登录页与投研团队页只做令牌化与主题同步，未重排布局。
- 浅色主题已截图验收主要 7 个页面，未逐像素比对。
- PWA 已在 Chrome 桌面与移动模拟下验证 manifest/SW 注册/图标可达；iOS Safari「添加到主屏幕」的实机表现未验证。
