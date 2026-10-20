# 🚀 部署指南

本文档介绍如何将 A股自选股智能分析系统部署到服务器。

## 📋 部署方案对比

| 方案 | 优点 | 缺点 | 推荐场景 |
|------|------|------|----------|
| **Docker Compose** ⭐ | 一键部署、环境隔离、易迁移、易升级 | 需要安装 Docker | **推荐**：大多数场景 |
| **直接部署** | 简单直接、无额外依赖 | 环境依赖、迁移麻烦 | 临时测试 |
| **Systemd 服务** | 系统级管理、开机自启 | 配置繁琐 | 长期稳定运行 |
| **Supervisor** | 进程管理、自动重启 | 需要额外安装 | 多进程管理 |

**结论：推荐使用 Docker Compose，迁移最快最方便！**

---

## 🐳 方案一：Docker Compose 部署（推荐）

### 1. 安装 Docker

```bash
# Ubuntu/Debian
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER

# CentOS
sudo yum install -y docker docker-compose
sudo systemctl start docker
sudo systemctl enable docker
```

### 2. 准备配置文件

```bash
# 克隆代码（或上传代码到服务器）
git clone <your-repo-url> /opt/stock-analyzer
cd /opt/stock-analyzer

# 复制并编辑配置文件
cp .env.example .env
vim .env  # 填入真实的 API Key 等配置
```

### 3. 一键启动

```bash
# 构建并启动（同时包含定时分析和 Web 界面服务）
docker-compose -f ./docker/docker-compose.yml up -d

# 查看日志
docker-compose -f ./docker/docker-compose.yml logs -f

# 查看运行状态
docker-compose -f ./docker/docker-compose.yml ps
```

启动成功后，在浏览器输入 `http://服务器公网IP:8000` 即可打开 Web 管理界面。如果打不开，记得先在云服务器控制台的「安全组」里放行 8000 端口。

> 不知道怎么访问？→ [云服务器 Web 界面访问指南](deploy-webui-cloud.md)

### 3.1 资源建议

默认 `docker/docker-compose.yml` 为每个服务设置 `limits.memory: 1G`、`reservations.memory: 512M`，这是完整分析场景的推荐起点。

- 最低可尝试：`512M`，仅适合轻量 Web/API、单股、低并发场景，建议设置 `MAX_WORKERS=1`。
- 推荐：`1G`，适合单独运行 `server` 或 `analyzer` 的常规分析。
- 高负载：`2G+`，适合同时启动 `server + analyzer`、多股票、默认 `MAX_WORKERS=3`、大盘复盘、新闻扩展、图片报告或 AlphaSift。

如果只能使用 `512M`，请避免同时启动 `server` 和 `analyzer`，并关闭非必要的大盘复盘、新闻扩展和图片报告能力。

### 4. 常用管理命令

```bash
# 停止服务
docker-compose -f ./docker/docker-compose.yml down

# 重启服务
docker-compose -f ./docker/docker-compose.yml restart

# 更新代码后重新部署
git pull
docker-compose -f ./docker/docker-compose.yml build --no-cache
docker-compose -f ./docker/docker-compose.yml up -d

# 进入容器调试
docker-compose -f ./docker/docker-compose.yml exec -u dsa stock-analyzer bash

# 手动执行一次分析
docker-compose -f ./docker/docker-compose.yml exec -u dsa stock-analyzer python main.py --no-notify
```

### 5. 数据持久化

数据自动保存在宿主机目录：
- `./data/` - 数据库文件
- `./logs/` - 日志文件
- `./reports/` - 分析报告

### 6. 权限说明

Docker 镜像启动入口会自动创建并修复 `./data`、`./logs`、`./reports` 对应挂载目录的权限，然后降权为非 root 用户 (`dsa`, UID 1000) 运行应用。普通部署不需要手动 `chown` / `chmod`。

如果你显式指定了 `--user` / Compose `user:`，或使用只读挂载、rootless Docker、NFS 等不允许容器修复属主的环境，请确保实际运行用户对这些目录具备写入权限。

---

## 🖥️ 方案二：直接部署

### 1. 安装 Python 环境

```bash
# 安装 Python 3.10+
sudo apt update
sudo apt install -y python3.10 python3.10-venv python3-pip

# 创建虚拟环境
python3.10 -m venv /opt/stock-analyzer/venv
source /opt/stock-analyzer/venv/bin/activate
```

### 2. 安装依赖

```bash
cd /opt/stock-analyzer
pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
```

### 3. 配置环境变量

```bash
cp .env.example .env
vim .env  # 填入配置
```

### 4. 运行

```bash
# 单次运行
python main.py

# 定时任务模式（前台运行）
python main.py --schedule

# 后台运行（使用 nohup）
nohup python main.py --schedule > /dev/null 2>&1 &

# 启动 Web 管理界面（云服务器需先在 .env 中设置 WEBUI_HOST=0.0.0.0）
python main.py --webui-only

# 启动 Web 界面（启动时执行一次分析；需每日定时请加 --schedule 或设 SCHEDULE_ENABLED=true）
python main.py --webui
```

> 不知道怎么访问？→ [云服务器 Web 界面访问指南](deploy-webui-cloud.md)

---

## 🔧 方案三：Systemd 服务

创建 systemd 服务文件实现开机自启和自动重启：

### 1. 创建服务文件

```bash
sudo vim /etc/systemd/system/stock-analyzer.service
```

内容：
```ini
[Unit]
Description=A股自选股智能分析系统
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/opt/stock-analyzer
Environment="PATH=/opt/stock-analyzer/venv/bin"
ExecStart=/opt/stock-analyzer/venv/bin/python main.py --schedule
Restart=always
RestartSec=30

[Install]
WantedBy=multi-user.target
```

### 2. 启动服务

```bash
# 重载配置
sudo systemctl daemon-reload

# 启动服务
sudo systemctl start stock-analyzer

# 开机自启
sudo systemctl enable stock-analyzer

# 查看状态
sudo systemctl status stock-analyzer

# 查看日志
journalctl -u stock-analyzer -f
```

---

## ⚙️ 配置说明

### 必须配置项

| 配置项 | 说明 | 获取方式 |
|--------|------|----------|
| `ANSPIRE_API_KEYS` / `AIHUBMIX_KEY` / `GEMINI_API_KEY` / `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` | AI 模型至少配置一个；推荐优先 Anspire 或 AIHubMix | 对应服务商控制台 |
| `STOCK_LIST` | 自选股列表 | 逗号分隔的股票代码 |
| 通知渠道 | 至少配置一个，如企业微信、飞书、Telegram 或邮件 | 对应通知平台 |

### 可选配置项

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `SCHEDULE_ENABLED` | `false` | 是否启用定时任务 |
| `SCHEDULE_TIME` | `18:00` | 每日执行时间 |
| `MARKET_REVIEW_ENABLED` | `true` | 是否启用大盘复盘 |
| `ANSPIRE_API_KEYS` | - | Anspire 大模型与新闻搜索（推荐） |
| `AIHUBMIX_KEY` | - | AIHubMix 一 Key 多模型（推荐） |
| `SERPAPI_API_KEYS` | - | SerpAPI 实时金融新闻搜索（推荐） |
| `TAVILY_API_KEYS` | - | Tavily 新闻搜索（可选） |
| `MINIMAX_API_KEYS` | - | MiniMax 搜索（可选） |

---

## 🌐 代理配置

如果服务器在国内，访问 Gemini API 需要代理：

### Docker 方式

编辑 `docker-compose.yml`：
```yaml
environment:
  - http_proxy=http://your-proxy:port
  - https_proxy=http://your-proxy:port
```

### 直接部署方式

编辑 `main.py` 顶部：
```python
os.environ["http_proxy"] = "http://your-proxy:port"
os.environ["https_proxy"] = "http://your-proxy:port"
```

---

## 📊 监控与维护

### 日志查看

```bash
# Docker 方式
docker-compose -f ./docker/docker-compose.yml logs -f --tail=100

# 直接部署
tail -f /opt/stock-analyzer/logs/stock_analysis_*.log
```

### 健康检查

```bash
# 检查进程
ps aux | grep main.py

# 检查最近的报告
ls -la /opt/stock-analyzer/reports/
```

### 定期维护

```bash
# 清理旧日志（保留7天）
find /opt/stock-analyzer/logs -mtime +7 -delete

# 清理旧报告（保留30天）
find /opt/stock-analyzer/reports -mtime +30 -delete
```

---

## ❓ 常见问题

### 1. Docker 构建失败

```bash
# 清理缓存重新构建
docker-compose -f ./docker/docker-compose.yml build --no-cache
```

### 2. API 访问超时

检查代理配置，确保服务器能访问 Gemini API。

### 3. 数据库锁定

```bash
# 停止服务后删除 lock 文件
rm /opt/stock-analyzer/data/*.lock
```

### 4. 内存不足

默认 Compose 已推荐 `1G`。如果仍出现 OOM 或平台杀掉容器，请提高 `docker-compose.yml` 中的内存限制；同时跑 `server + analyzer`、多股票、大盘复盘、图片报告或 AlphaSift 时建议 `2G+`：
```yaml
deploy:
  resources:
    limits:
      memory: 1G
    reservations:
      memory: 512M
```

低配环境只能使用 `512M` 时，建议设置 `MAX_WORKERS=1`，只启动 `server` 或 `analyzer` 其中一个服务，并减少非必要的大盘复盘、新闻扩展和图片报告任务。

### 5. WebUI 打开后 UI 元素异常变大 / 布局错乱

**症状**：能访问 8000 端口，但页面上的文字、按钮、卡片异常放大，没有正常布局。

**根因**：`static/index.html` 存在，但 CSS/JS 资源文件缺失（`static/assets/` 为空或不存在），浏览器无法加载样式与脚本，导致裸 HTML 渲染。

### 云端静态资源加速

阿里云小型主机应让 nginx 直接发送带内容哈希的 `/assets/*` 文件，避免页面切换时由 Python 服务处理 JS/CSS。网页 HTML 入口仍经应用服务和既有鉴权处理。每次仅更新后端并启动 `stock-server` 后，先执行只读一致性检查：

```bash
sudo ./scripts/publish-nginx-assets.sh --check
```

检查会读取运行容器 `/app/static` 与当前宿主发布目录，打印双方 `index.html` 的 SHA-256 和入口资源引用；入口失配、入口缺失、没有资源引用、直接引用的资源缺失或同名哈希资源内容冲突均以非零退出。它仅创建并清理临时快照，不切换或修改已发布目录。部署脚本应将非零结果视为发布失败，不要在检查失败后自动覆盖宿主前端。

首次安装可不带参数发布；已存在版本时，默认发布拒绝不同的 HTML 入口，防止重建的旧镜像覆盖已验收新版。只有明确发布已验收的新前端时才使用：

```bash
sudo ./scripts/publish-nginx-assets.sh --publish-new-version
sudo ./scripts/publish-nginx-assets.sh --check
sudo cp docker/nginx-leziwu.conf /etc/nginx/sites-available/leziwu
sudo nginx -t && sudo systemctl reload nginx
```

显式发布也必须通过资源完整性校验，不能用旧目录补齐新入口缺失的直接引用。脚本把容器内完整前端产物发布到新的版本目录，继承当前版本的旧 `/assets/*` 文件以兼容已打开页面的延迟加载，再原子切换 `/var/www/leziwu-static` 软链接；旧目录不修改、不删除。同一哈希文件名存在不同内容时拒绝发布。容器、发布根目录和活动目录继续分别使用 `LEZIWU_CONTAINER_NAME`、`LEZIWU_STATIC_RELEASE_ROOT`、`LEZIWU_STATIC_ROOT` 的现有配置。

前端单独上线后，必须把同一份已验收完整静态产物固化到后端部署所用镜像或已批准的持久挂载中，不能只热替换运行容器的 `index.html`。否则容器重建会恢复镜像内旧入口；宿主仍保留新版 JS/CSS 也不能阻止首页回退。不要直接全量发布含其他未验收业务修改的工作区构建，也不要通过放开 `/app` 的 nginx 静态回退绕过应用鉴权。

回滚需同时恢复先前镜像内的完整静态版本与对应宿主活动目录，再运行 `--check`，并验证公网首页、入口资源及未登录 `/app` 拦截；仅切换宿主软链接不足以回滚容器提供的 HTML。首次没有宿主静态目录时，仅 `/assets/*` 有应用容器回退；`/landing/*` 和 `/guide` 需要完整宿主发布目录。当前检查只验证入口直接引用和同名资源一致性，不替代动态分块、图片、真实浏览器或鉴权验收。

**解决方法**：

- **Docker 部署**：执行以下命令重新构建镜像（确保前端已正确打包进镜像）：
  ```bash
  docker-compose -f ./docker/docker-compose.yml down
  docker-compose -f ./docker/docker-compose.yml build --no-cache
  docker-compose -f ./docker/docker-compose.yml up -d
  ```
  构建完成后刷新浏览器缓存（`Ctrl+Shift+R`）再访问。

- **直接部署（pip + python）**：先构建前端，再启动服务：
  ```bash
  # 安装 Node.js 18+（推荐 20+，如尚未安装）
  # 构建前端
  cd apps/dsa-web
  npm ci
  npm run build
  cd ../..
  # 启动服务
  python main.py --webui-only
  ```

**验证**：用浏览器开发者工具（F12 → Network）检查是否有 `/assets/index-*.js` 和 `/assets/index-*.css` 的 404 错误；如有，说明资源缺失，按上述步骤重新构建即可。

### 6. 页面切换时仍在加载

加载提示区分三个阶段，不再用固定百分比表示未知进度：

- **确认访问权限 / 准备当前账号页面**：先完成既有鉴权与账号隔离，再挂载私有页面；这不是业务数据请求。不要通过取消登录拦截来缩短等待。
- **加载页面组件**：等待该页 JavaScript 分包；组件加载失败会先尝试恢复一次，仍失败才显示当前模块的重试入口。
- **读取各模块数据**：组件就绪后立即显示页面结构和已取得内容，不等待全部 API 一起结束。股票列表、工作区、行情图与任务渠道各自保留加载、失败或成功状态。

路径切换后的顶部细线只是非阻塞的初始请求活动提示，没有完成百分比；约 180 毫秒后仍有初始请求才出现，最迟约 1.4 秒结束提示。细线消失不表示所有数据就绪，更不会把仍在等待的请求记为成功。页内筛选、行情轮询和后台刷新由相应模块更新，不重新遮挡整页。

导航预加载只做有限准备：任务中心只预加载页面代码，不提前取五类任务；机构段子页面只预取共享状态，不连带读取检索页不用的研判聚合；自选股只预取列表和第一只股票的总览，不因悬停就请求行情历史或完整研究工作区。数据预加载在页面隐藏时跳过，并与分包预加载共用省流量、`2g` / `slow-2g` 判断；浏览器未提供网络信息时，不能据此保证弱网一定不预取。

若等待过久，应先核对提示阶段：权限阶段查会话接口；组件阶段查 `/assets/*` 状态、版本一致性及浏览器控制台；数据阶段查具体 pending / 失败的 API 与后端日志。422/404 可能是前后端版本不匹配，不应伪装成“数据准备中”。这些调整不会自行发布新代码，也不能代替部署后的真实浏览器、鉴权与接口验收。

---

## 🔄 快速迁移

从一台服务器迁移到另一台：

```bash
# 源服务器：打包
cd /opt/stock-analyzer
tar -czvf stock-analyzer-backup.tar.gz .env data/ logs/ reports/

# 目标服务器：部署
mkdir -p /opt/stock-analyzer
cd /opt/stock-analyzer
git clone <your-repo-url> .
tar -xzvf stock-analyzer-backup.tar.gz
docker-compose -f ./docker/docker-compose.yml up -d
```

---

## ☁️ 方案四：GitHub Actions 部署（免服务器）

**最简单的方案！** 无需服务器，利用 GitHub 免费计算资源。

### 优势
- ✅ **完全免费**（每月 2000 分钟）
- ✅ **无需服务器**
- ✅ **自动定时执行**
- ✅ **零维护成本**

### 限制
- ⚠️ 无状态（每次运行是新环境）
- ⚠️ 定时可能有几分钟延迟
- ⚠️ 无法提供 HTTP API

### 部署步骤

#### 1. 创建 GitHub 仓库

```bash
# 初始化 git（如果还没有）
cd /path/to/daily_stock_analysis
git init
git add .
git commit -m "Initial commit"

# 创建 GitHub 仓库并推送
# 在 GitHub 网页上创建新仓库后：
git remote add origin https://github.com/你的用户名/daily_stock_analysis.git
git branch -M main
git push -u origin main
```

#### 2. 配置 Secrets（重要！）

打开仓库页面 → **Settings** → **Secrets and variables** → **Actions** → **New repository secret**

添加以下 Secrets：

| Secret 名称 | 说明 | 必填 |
|------------|------|------|
| `ANSPIRE_API_KEYS` | Anspire Open API Key（一 Key 启用大模型与搜索） | 推荐 |
| `AIHUBMIX_KEY` | AIHubMix API Key（一 Key 多模型） | 推荐 |
| `ANTHROPIC_API_KEY` | Anthropic API Key | 可选 |
| `GEMINI_API_KEY` | Gemini AI API Key | 可选 |
| `OPENAI_API_KEY` | OpenAI 兼容 API Key | 可选 |
| `WECHAT_WEBHOOK_URL` | 企业微信机器人 Webhook | 可选* |
| `FEISHU_WEBHOOK_URL` | 飞书机器人 Webhook | 可选* |
| `TELEGRAM_BOT_TOKEN` | Telegram Bot Token | 可选* |
| `TELEGRAM_CHAT_ID` | Telegram Chat ID | 可选* |
| `TELEGRAM_MESSAGE_THREAD_ID` | Telegram Topic ID | 可选* |
| `EMAIL_SENDER` | 发件人邮箱 | 可选* |
| `EMAIL_PASSWORD` | 邮箱授权码 | 可选* |
| `SERVERCHAN3_SENDKEY` | Server酱³ Sendkey | 可选* |
| `CUSTOM_WEBHOOK_URLS` | 自定义 Webhook（多个逗号分隔） | 可选* |
| `STOCK_LIST` | 自选股列表，如 `600519,300750` | ✅ |
| `SERPAPI_API_KEYS` | SerpAPI Key | 推荐 |
| `TAVILY_API_KEYS` | Tavily 搜索 API Key | 可选 |
| `BOCHA_API_KEYS` | 博查搜索 API Key | 可选 |
| `BRAVE_API_KEYS` | Brave Search API Key | 可选 |
| `MINIMAX_API_KEYS` | MiniMax Coding Plan Web Search | 可选 |
| `SEARXNG_BASE_URLS` | SearXNG 自建实例（无配额兜底，需在 settings.yml 启用 format: json）；留空时默认自动发现公共实例 | 可选 |
| `SEARXNG_PUBLIC_INSTANCES_ENABLED` | 是否在 `SEARXNG_BASE_URLS` 为空时自动从 `searx.space` 获取公共实例（默认 `true`） | 可选 |
| `TUSHARE_TOKEN` | Tushare Token | 可选 |
| `GEMINI_MODEL` | 模型名称（默认 gemini-2.0-flash） | 可选 |

> *注：通知渠道至少配置一个，支持多渠道同时推送

#### 3. 验证 Workflow 文件

确保 `.github/workflows/00-daily-analysis.yml` 文件存在且已提交：

```bash
git add .github/workflows/00-daily-analysis.yml
git commit -m "Add GitHub Actions workflow"
git push
```

#### 4. 手动测试运行

1. 打开仓库页面 → **Actions** 标签
2. 选择 **"每日股票分析"** workflow
3. 点击 **"Run workflow"** 按钮
4. 选择运行模式：
   - `full` - 完整分析（股票+大盘）
   - `market-only` - 仅大盘复盘
   - `stocks-only` - 仅股票分析
5. 点击绿色 **"Run workflow"** 按钮

#### 5. 查看执行日志

- Actions 页面可以看到运行历史
- 点击具体的运行记录查看详细日志
- 分析报告会作为 Artifact 保存 30 天

### 定时说明

默认配置：**周一到周五，北京时间 18:00** 自动执行

修改时间：编辑 `.github/workflows/00-daily-analysis.yml` 中的 cron 表达式：

```yaml
schedule:
  - cron: '0 10 * * 1-5'  # UTC 时间，+8 = 北京时间
```

常用 cron 示例：
| 表达式 | 说明 |
|--------|------|
| `'0 10 * * 1-5'` | 周一到周五 18:00（北京时间） |
| `'30 7 * * 1-5'` | 周一到周五 15:30（北京时间） |
| `'0 10 * * *'` | 每天 18:00（北京时间） |
| `'0 2 * * 1-5'` | 周一到周五 10:00（北京时间） |

### 修改自选股

方法一：修改仓库 Secret `STOCK_LIST`

方法二：直接修改代码后推送：
```bash
# 修改 .env.example 或在代码中设置默认值
git commit -am "Update stock list"
git push
```

### 常见问题

**Q: 为什么定时任务没有执行？**
A: GitHub Actions 定时任务可能有 5-15 分钟延迟，且仅在仓库有活动时才触发。长时间无 commit 可能导致 workflow 被禁用。

**Q: 如何查看历史报告？**
A: Actions → 选择运行记录 → Artifacts → 下载 `analysis-reports-xxx`

**Q: 免费额度够用吗？**
A: 每次运行约 2-5 分钟，一个月 22 个工作日 = 44-110 分钟，远低于 2000 分钟限制。

---

## 🌐 云服务器上部署了，但不知道怎么用浏览器访问？

详见 → [云服务器 Web 界面访问指南](deploy-webui-cloud.md)

涵盖：直接部署和 Docker 两种方式的启动与访问、安全组/防火墙配置、常见问题排查、Nginx 反向代理（可选）。

---

**祝部署顺利！🎉**
