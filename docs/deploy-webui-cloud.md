# 云服务器 Web 界面访问指南

如果你已经把项目部署到云服务器，但不知道在浏览器里输入什么地址才能打开 Web 管理界面，这篇教程就是为你准备的。

> 其实就两步：让服务监听外网，再在浏览器里输入地址。

---

## 目录

- [方式一：直接部署（pip + python）](#方式一直接部署pip--python)
- [方式二：Docker Compose](#方式二docker-compose)
- [如何在浏览器里打开界面](#如何在浏览器里打开界面)
- [如何确认 Docker 重建已生效](#如何确认-docker-重建已生效)
- [访问不了？先检查这几项](#访问不了先检查这几项)
- [可选：Nginx 反向代理（绑定域名 / 80 端口）](#可选nginx-反向代理绑定域名--80-端口)
- [安全建议](#安全建议)
- [手机号自助注册与阿里云短信认证](#手机号自助注册与阿里云短信认证)

---

## 方式一：直接部署（pip + python）

### 第一步：修改 .env 中的监听地址

用编辑器打开 `.env`（在项目根目录，即包含 `main.py` 的目录），找到这一行：

```env
WEBUI_HOST=127.0.0.1
```

把 `127.0.0.1` 改成 `0.0.0.0`：

```env
WEBUI_HOST=0.0.0.0
```

> `127.0.0.1` 表示只有本机能访问，`0.0.0.0` 表示允许任何来源访问。云服务器必须改成 `0.0.0.0` 才能从外网打开界面。

> **注意**：当前 `python main.py` 启动逻辑会在 host 为默认 `0.0.0.0` 时读取 `.env` 里的 `WEBUI_HOST`；即使显式传入 `--host 0.0.0.0`，如果 `.env` 里仍是 `WEBUI_HOST=127.0.0.1`，最终也可能只监听本机。云服务器请务必先把 `.env` 改成 `WEBUI_HOST=0.0.0.0`。

### 第二步：启动服务

在项目根目录执行：

```bash
# 只启动 Web 界面（不自动执行分析）
python main.py --webui-only

# 或者：启动 Web 界面（启动时执行一次分析；需每日定时分析请加 --schedule 或设 SCHEDULE_ENABLED=true）
python main.py --webui
```

启动成功后，终端会输出类似：

```
FastAPI 服务已启动: http://0.0.0.0:8000
```

如果你想让服务在退出终端后继续运行，可以用 `nohup`：

```bash
nohup python main.py --webui-only > /dev/null 2>&1 &
```

> 日志文件会由程序自动写入 `logs/` 目录，用 `tail -f logs/stock_analysis_*.log` 查看。

### 修改端口（可选）

默认端口是 8000。如果想改用其他端口，在 `.env` 里设置：

```env
WEBUI_PORT=8888
```

然后重启服务。

---

## 方式二：Docker Compose

### 第一步：确认已有 .env 配置

项目的 `docker/docker-compose.yml` 在容器内部已经自动设置了 `WEBUI_HOST=0.0.0.0`，你不需要在 `.env` 里再改监听地址，Docker 会自动处理。

Docker Compose 中的 `env_file: ../.env` 只会把 `.env` 作为**启动环境变量**注入容器，不会在容器内创建 `/app/.env`，也不会让 WebUI 保存配置时回写宿主机 `.env`。新版 WebUI 会在活跃 `.env` 文件缺少某些键时展示启动注入的同名环境变量作为兜底，因此页面上能看到 Docker 启动时注入的配置；但“导出 `.env`”仍只导出当前活跃配置文件内容。

如果希望 WebUI 中保存的配置在容器删除、重建或升级后继续保留，请把活跃配置文件放到已挂载的数据卷中，例如在 Compose 的 `environment` 中增加：

```yaml
- ENV_FILE=/app/data/runtime.env
```

同时保留 `../data:/app/data` 挂载。注意：如果启动时的 `../.env`、`docker run -e` 或 Compose `environment:` 里还保留同名旧值，容器重启后这些启动环境变量仍可能覆盖运行时文件中的保存值；要让 WebUI 保存值接管，请同步更新或移除启动环境中的同名配置。

### 第二步：启动服务

在项目根目录执行：

```bash
# 同时启动定时分析 + Web 界面（推荐）
docker-compose -f ./docker/docker-compose.yml up -d

# 或者只启动 Web 界面服务
docker-compose -f ./docker/docker-compose.yml up -d server
```

启动后查看状态：

```bash
docker-compose -f ./docker/docker-compose.yml ps
```

看到 `server` 服务状态为 `running` 就说明 Web 界面已经在运行了。

### 修改端口（可选）

默认端口是 8000。如果想改用其他端口，在 `.env` 里设置：

```env
API_PORT=8888
```

然后重新启动容器：

```bash
docker-compose -f ./docker/docker-compose.yml down
docker-compose -f ./docker/docker-compose.yml up -d
```

---

## 如何在浏览器里打开界面

服务启动后，在浏览器地址栏输入：

```
http://你的服务器公网IP:8000
```

例如，如果你的服务器 IP 是 `1.2.3.4`，就输入：

```
http://1.2.3.4:8000
```

如果你的域名已经解析到这台服务器，也可以直接用域名访问：

```
http://your-domain.com:8000
```

> **在哪里查公网 IP？** 登录你的云服务器控制台（阿里云/腾讯云/AWS 等），在实例列表里可以看到「公网 IP」或「弹性 IP」。

---

## 如何确认 Docker 重建已生效

先区分两件事：

1. **Docker 镜像发布版本**：看你部署时使用的镜像 tag，例如 `ghcr.io/wu427692-alt/leizuw-super-analyst:v3.12.0`。仓库的 Docker 发布由 `.github/workflows/docker-publish.yml` 按 `v*.*.*` Git tag 触发，所以 Docker 版本应以镜像 tag / GitHub Releases 为准。
2. **当前页面加载的前端构建**：看 WebUI “系统设置”页里的版本信息卡片，用来确认浏览器拿到的静态资源是否已经更新。

也就是说，**“系统设置”里的版本信息更适合判断前端是否重建成功，不等同于 Docker 镜像发布版本**。

WebUI 现在会在“系统设置”页展示只读的“版本信息”卡片，包含：

- `WebUI 版本`
- `构建标识`
- `构建时间`

如果 `apps/dsa-web/package.json` 里的版本号仍是占位值 `0.0.0`，页面会自动回退展示本次前端构建生成的 `构建标识`，避免你误把占位版本当成真实发布版本。

当你重新执行 `docker-compose -f ./docker/docker-compose.yml up -d --build`，或者单独重新执行前端 `npm run build` 后，可以刷新浏览器并进入“系统设置”，优先确认“构建时间”是否已经变化；若变化，通常就说明当前加载的静态资源已经切换到最新构建。

如果你想确认“我现在到底部署的是哪个正式版本”，优先用下面这些方式：

```yaml
# 方式 1：看 docker-compose / 部署脚本里的 image tag
image: ghcr.io/wu427692-alt/leizuw-super-analyst:v3.12.0
```

```bash
# 方式 2：回看你的拉取命令
docker pull ghcr.io/wu427692-alt/leizuw-super-analyst:v3.12.0
```

如果你一直使用 `latest`，建议改成显式版本 tag；否则很难仅凭容器内页面信息判断自己是否已经重复更新到同一版本。

在确认本地前端打包链路时，建议执行以下命令作为最小验证闭环：

```bash
cd apps/dsa-web
npm ci
npm run lint
npm run build
```

其中 `build` 成功后，`static` 下生成的 `index.html`/JS/CSS 资源会包含本次构建时间与构建版本信息；刷新后在“版本信息”卡片中应能见到变化。

---

## 访问不了？先检查这几项

### 1. 安全组 / 防火墙没有放行端口

这是最常见的原因。云服务器默认只开放 22（SSH）端口，需要手动放行 8000（或你改的端口）。

**操作方法**（以阿里云为例）：
1. 登录阿里云控制台 → 云服务器 ECS → 找到你的实例
2. 点击「安全组」→「配置规则」→「添加安全组规则」
3. 方向选「入方向」，端口范围填 `8000/8000`，授权对象填 `0.0.0.0/0`，点击「确定」

腾讯云、AWS 等云厂商操作类似，找到「安全组」或「防火墙规则」，新增一条允许 TCP 8000 端口的入站规则即可。

### 2. 服务器系统防火墙拦截了

如果你的系统开启了 `ufw` 或 `firewalld`，也需要放行端口：

```bash
# Ubuntu / Debian（ufw）
sudo ufw allow 8000

# CentOS / RHEL（firewalld）
sudo firewall-cmd --permanent --add-port=8000/tcp
sudo firewall-cmd --reload
```

### 3. 直接部署时 .env 里的 WEBUI_HOST 没改

这是第二常见原因。`.env` 里默认是 `WEBUI_HOST=127.0.0.1`，这样服务只监听本机，外网根本连不上。

改法：打开 `.env`，把 `WEBUI_HOST=127.0.0.1` 改成 `WEBUI_HOST=0.0.0.0`，然后重启服务。

> Docker 方式不需要改这个，可以跳过。

### 4. 端口号对不上

检查访问地址里的端口是否和 `.env` / 启动命令里设置的端口一致。

- 直接部署：默认 8000，可通过 `WEBUI_PORT=xxxx` 修改
- Docker：默认 8000，可通过 `API_PORT=xxxx` 修改

### 5. 页面能打开，但 UI 元素异常变大 / 布局错乱

**症状**：浏览器能访问到 8000 端口，页面有内容，但文字、按钮、卡片尺寸异常大，没有正常布局与配色。

**根因**：`static/index.html` 存在但 CSS/JS 资源缺失（`static/assets/` 为空或不存在），浏览器加载了 HTML 框架但无法拿到样式与脚本，退化为裸 HTML 渲染。

可先用浏览器开发者工具（F12 → Network 标签页）检查是否有 `/assets/index-*.js`、`/assets/index-*.css` 的 **404** 错误。若有，按以下方式修复：

**Docker 用户**：

```bash
docker-compose -f ./docker/docker-compose.yml down
docker-compose -f ./docker/docker-compose.yml build --no-cache
docker-compose -f ./docker/docker-compose.yml up -d
```

重建完成后，用 `Ctrl+Shift+R` 强制刷新浏览器缓存，再访问页面。

**直接部署用户**：先确保已安装 Node.js 18+（推荐 20+），然后手动构建前端：

```bash
cd apps/dsa-web
npm ci
npm run build
cd ../..
python main.py --webui-only
```

---

## 可选：Nginx 反向代理（绑定域名 / 80 端口）

如果你有域名，或者不想在地址里带 `:8000`，可以用 Nginx 做反向代理，把 80/443 端口流量转发给后端服务。

### 安装 Nginx

```bash
# Ubuntu / Debian
sudo apt update && sudo apt install -y nginx

# CentOS
sudo yum install -y nginx
```

### 配置文件示例

新建文件 `/etc/nginx/conf.d/stock-analyzer.conf`，内容如下（把 `your-domain.com` 改成你的域名或 IP）：

```nginx
server {
    listen 80;
    server_name your-domain.com;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # 支持 WebSocket（Agent 对话页面需要）
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
    }
}
```

### 启用配置并重启 Nginx

```bash
sudo nginx -t            # 检查配置有没有语法错误
sudo systemctl reload nginx
```

配置成功后，直接用 `http://your-domain.com` 访问即可，不需要带端口号。

> **使用 Nginx 后的注意事项**：
> - 如果你开启了 Web 登录认证（`ADMIN_AUTH_ENABLED=true`），建议在 `.env` 中把 `TRUST_X_FORWARDED_FOR=true` 一并打开，否则系统可能无法正确识别真实 IP。该选项适用于**单层可信反向代理**（Nginx → App）部署；如果使用多级代理或 CDN（CDN → Nginx → App），登录限流的 key 可能退化为边缘代理 IP 而非真实客户端 IP，需根据实际拓扑评估。
> - 如需 HTTPS，可以用 [Certbot](https://certbot.eff.org/) 自动申请免费的 Let's Encrypt 证书。

---

## 安全建议

把 Web 界面暴露到公网之前，强烈建议开启登录密码保护：

在 `.env` 中设置：

```env
ADMIN_AUTH_ENABLED=true
```

重启服务后，第一次访问网页时会要求设置初始密码。设置完成后，每次打开设置页面都需要输入密码，可以防止 API Key 等敏感配置被他人看到。

> 如果忘了密码，可以在服务器上执行：`python -m src.auth reset_password`

---

## 手机号自助注册与阿里云短信认证

该功能使用阿里云号码认证服务 **PNVS** 的 `SendSmsVerifyCode` / `CheckSmsVerifyCode`，不是普通短信服务的 `SendSms`。个人账号也可按平台规则开通并使用赠送签名和模板；实际服务状态、收费与可用签名以控制台为准。参见[阿里云新手指引](https://help.aliyun.com/zh/pnvs/getting-started/sms-authentication-service-novice-guide)。

### 账号与权限

- `USER_ACCESS_ENABLED=true` 始终保留业务页面/API的账号认证。开放自助注册不等于匿名访问。
- `USER_REGISTRATION_MODE=self_service`：新用户使用已验证的手机号直接注册并登录，不进入管理员审批队列。姓名可作为显示名称，不作为手机号身份的合并依据。
- 旧账号继续使用原密码。绑定手机须先登录原账号、再次验证原密码并核验短信；绑定后仍使用原 `user_id`，自选股、会话和任务不迁移、不复制。绑定冲突不会自动合并。
- 已绑定的号码不能直接覆盖，更不能通过新注册恢复已停用或被拒绝账号。历史待审核账号也不会被批量批准。
- 管理员身份仍由独立的管理员认证保护；普通用户自助注册不会获得设置、密钥或管理权限。
- `USER_REGISTRATION_MODE=approval` 保持旧审批流程。短信未配置时旧密码登录仍可使用，不返回测试验证码或伪装已开通。

### 服务配置

1. 在阿里云号码认证控制台完成实名认证、服务开通，选择可用的**系统赠送签名与配套模板**。不购买服务器或迁移数据库。
2. 使用专用 RAM 身份，仅授权 `dypns:SendSmsVerifyCode` 和 `dypns:CheckSmsVerifyCode`；这两个接口不支持资源级授权，资源范围需按阿里云文档设置。不要配置主账号高权限密钥，也不要复用录音转写的密钥。
3. 把 `.env.example` 中的 `ALIYUN_SMS_*` 配置保存在服务器的受限环境文件中，不能提交 Git、放入浏览器或输出到日志。`ALIYUN_SMS_SCHEME_NAME` 同时用于发送和核验，最多20字符。`ALIYUN_SMS_TEMPLATE_CODE` 用于登录/注册；可选的 `ALIYUN_SMS_BIND_TEMPLATE_CODE` 用于原账号绑定手机号，空值沿用登录模板。两者都必须是控制台实际可用的赠送模板，不能凭示例编码假定可用。
4. 完成真实发送、接收、核验和费用限制检查后，才启用 `SMS_AUTH_ENABLED=true` 与 `USER_REGISTRATION_MODE=self_service`。未经开通、密钥未配置或上游报错时，不应宣称短信登录已上线。

### 防刷与费用限制

短信为付费调用。默认每号码60秒才能再发，验证码6位、5分钟有效、最多核验5次；发送请求按滚动24小时限制每手机号5次、每IP20次、全站100次，配置名分别为 `SMS_AUTH_PHONE_DAILY_LIMIT`、`SMS_AUTH_IP_DAILY_LIMIT`、`SMS_AUTH_GLOBAL_DAILY_LIMIT`。限额可以调整，但不建议无上限开放。

预占和消费记录保存在数据库，服务重启或多个进程不会清空配额。失败或超时的发送也保留额度，不自动重发收费请求。验证码由阿里云产生并验证，应用不保存验证码；手机号仅保存带密钥摘要与脱敏显示。浏览器随机凭证将验证码挑战绑定到发起浏览器和用途，已消费、过期、被替换或不属于当前账号的挑战无法复用。

### 发布、测试与回退

通用登录入口复用 `UserAccessPage`，可单独执行 `npx vite build --config vite.account-access.config.ts` 构建；生成的 `dist-account-access/account-access.html` 发布到后端 `static/account-access/account-access.html`，JS/CSS位于 `static/assets/account-access/`。后端发现此入口后，所有 `/access` 回调统一使用它；不存在时保留原入口。主站同时加载 `public/assets/account-access-navigation.js`，仅在旧SPA页内导航至 `/access` 时转为整页导航，让服务器选用统一入口，不影响其他模块路由。冻结的旧主站发布须把此脚本一并复制到 `static/assets/` 并在主HTML引入；必须同时验证页内导航进入新入口，不能只测试手输网址。

数据库只新增手机身份和验证码挑战表，不改动旧账号主键或旧数据。正式发布前采用 SQLite online backup 保留一致备份（不能仅复制运行中的主库而忽略WAL）。回退只恢复应用镜像和配置，保留新增表与新注册账号，**不回退整库、不删除账号数据**。停用短信后，手机专用账号没有原密码，因此不应把“关闭开关”当作没有影响的回退；需恢复上一可用短信版本或通知用户暂时不可用。

验收必须分开记录：离线单测、浏览器流程、生产匿名鉴权、真实阿里云发送/接收/核验。Mock接口或按钮展示不代表真实短信已接通。本文没有对应独立英文部署指南；已有 `full-guide_EN.md` 不改动与本功能无关的内容。

---

遇到其他问题？欢迎 [提交 Issue](https://github.com/wu427692-alt/leizuw-super-analyst/issues)。
