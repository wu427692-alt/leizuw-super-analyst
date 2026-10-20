# 云端迁回家用 Mac mini：准备与切换手册

> **2026-09-15 数据事故核实：** 本会话执行日志证实，2026-09-14 18:58 删除了云端项目目录（包括目录内 data、reports、backups 等）；此前已验证的传输是明确排除业务数据的源码包，未找到云端完整业务交接验收成功证据。不能再将原目录缺失仅描述为“尚未找到”或将 MacBook 本地恢复等同云端最终恢复。公网现已由本地恢复库在 Mini 提供服务，但云端独有数据仍未恢复；进一步迁移与清理已停止，转为只读取证和恢复评估。下文保留历史现场记录，不代表数据迁移已经完整成功。完整时间线见 `docs/incidents/2026-09-15-migration-data-loss.md`。

## 当前恢复进展 · 2026-09-15

用户已明确选择 **Cloudflare Tunnel 直连 Mac mini，不保留本网站的阿里云转发**。公网入口已恢复：`app.leziwu.com/` 为 200，`/health` 返回 JSON `status=ok`；未登录 `/app` 为 307 跳转访问登录页，私有资料接口为 401。主域名与 `www` 为 301 跳转到 `app`。真实浏览器已打开当前介绍页与登录页。隧道运行于 Mini，四条 HTTP/2 连接就绪，指向仅本机开放的 `127.0.0.1:18000`。其他简历域名与云端简历站点不属于迁移对象。

本次使用用户批准的 **MacBook 本地 runtime 恢复源**，不是未找到的云端最终数据库：停写后通过 SQLite backup 取得一致副本，218 个文件、3 个数据库完成哈希与完整性验证；再恢复为专用 Colima 下七个 Linux named volume，应用 UID1000 权限验收通过。源端资料与恢复包均保留，不删除 WAL，不启动双写。初始恢复原文 107,216 条、研报 70,781 条、日线 1,534,844 条、用户 3 个；云端独有账号、任务和转写结果不能据此宣称已找回。

原生 ARM64 基础镜像 159 个依赖版本与 613 个文件通过冻结校验；r2 更新镜像 656 个文件核验通过。当前实际部署参数位于 Mini 私有 `LeziwuServer/deploy-20260915`，镜像 `leziwu-production:20260915-r2`，应用镜像 ID `sha256:224a7e3f4e07d4104b0e20856954751e8dd7e5694d850a058657fdb502c2e999`。性能验收见 [专题说明](macmini-performance.md)。公网恢复不等于全部历史已补齐，近两年资料另行登记持久任务并核对进度。

隧道当前使用用户登录后启动的 LaunchAgent；**未登录冷启动、FileVault 解锁、断电恢复以及手机关闭 VPN 的多运营商验证仍未完成**。不能承诺关机、休眠或家庭断网后仍可访问。下面保留故障追溯和通用迁移方法，历史“未完成/不可用”描述不是当前公网状态；不得照历史步骤重启已退役云端应用或覆盖 Mini 新写入。

## 历史故障与恢复依据（不代表当前状态）

2026-09-14 19:55（北京时间）现场确认：云端 `stock-server`、`stock-monitor-worker` 两个容器均已退出，原 `/opt/financial-intelligence-platform` 目录已不存在；`app.leziwu.com` 的 `/`、`/app`、`/health` 均返回 HTTP 502。2026-09-15 10:10 复查：应用首页仍为 HTTP 502、两个容器仍退出；其他站点 `wang-jingjing.com`、`wuyinle.com` 均为 HTTP 200。未启动旧容器、重建空库或修改这两个站点。

2026-09-15 10:16 用户重新登录成功，已复用 `10.65.148.94` 上的 SSH 会话确认 M4 / 16 GB 目标机。专用 Colima 的 Docker 在 guest 中正常，但宿主 socket 转发失效；三次确认后于 10:19 平滑重启该 profile，宿主 Docker ping 恢复。目标磁盘可用约 105 GiB，没有业务容器。仅存在一个非业务 migration-smoke 卷。

**当前主要阻断是原业务数据缺失，而非登录。** 已认证盘点显示 `LeziwuServer` 只有约 523 MiB 源码与安装材料，未收到业务数据库、配置/身份文件或最终迁移包。云端三个旧容器的 data 都是指向已不存在目录的 bind mount，15 个匿名卷均为空；限定云目录的相关归档只有代码。阿里云本实例快照页明确显示无快照。这些是已检查范围内未找到副本，不代表能断言永久不可恢复；不得创建空库冒充迁移，也不继续删除任何备份或残留文件。

本机开发 runtime 是另一恢复候选：9 月 15 日只读快照约 107,216 条原文、70,781 份研报（研报截至 8 月 30 日）、153 万股票日线，但只有 3 个用户、1 条个人自选、4 个旧行业报告，录音转写输出目录为空，不能等同云端完整用户与任务状态。本地库仍在写入且有非空 WAL；须由用户确认采用这个来源后，再取得一致快照，不得直接忽略 WAL 拷贝主库。iCloud 滚动包标记为本地设备且仅含知识表，不是完整云业务备份。

10:20 尝试原生 ARM 构建时，在 AlphaSift 的本地 Git 镜像拉取阶段出现大量 `git-upload-pack` 进程及重复过滤警告，触发虚拟机 OOM，构建失败；没有运行网站或付费任务。Docker/containerd 自行恢复，但宿主转发再次失效。已停止重跑相同构建，待修正依赖获取链条后重验。10:24 再次三次确认后平滑恢复专用 profile，最终宿主 Docker ping 为 OK、服务版本 29.2.1/aarch64、无运行或退出的应用容器，原非业务测试卷保留。已校验的性能增量包传入 `LeziwuServer/installers/performance-update-20260915.tar.gz`，其 SHA256 为 `54259f765b38f885051a3e0279813d4c045f29605d47900229b39cfd266a6733`；文件已传输不等于应用已部署。

用户已指定 Mac mini 为今后的生产部署与优化目标。恢复目标连接后，须完成整套数据、鉴权、采集及域名访问验收，才将迁移标记为完成。下文记录的准备阶段检查属于历史证据，不能替代当前运行状态。本文面向本项目实际的双进程、SQLite、用户账号、AI 任务和文件资料，不是仅复制网页。

2026-09-14 本地已补充 [Mac mini 加载性能配置与验收](macmini-performance.md)：首屏预加载复用、首页事实短缓存、总览优先预热以及 16 GB 目标机的有界资源分配。配置和本地实验不代表已经在目标机生效；连接、数据恢复与正式上线阻断仍须先解决。

## 1. 没有公网 IP 怎么办

当前已按用户选择使用家中 Mac mini 主动连接 Cloudflare Tunnel，无须家用路由器开放入站端口，也不要求独立公网 IPv4。网站继续使用原域名；下文香港中继仅保留为历史候选说明，**本次不实施，不因线路测试擅自恢复该网站的阿里云转发**。[Cloudflare Tunnel 官方说明](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/)

```text
用户浏览器 → 原网站域名 / HTTPS → Cloudflare Tunnel
                                     ↑ Mac 主动建立连接
                         Mac mini 的 cloudflared
                                     ↓ 127.0.0.1:18000
                        Docker API + 独立采集进程
                                     ↓
                      本机工作数据库 / 附件 / 报告
```

这解决的是入口可达，不保证每个国内运营商都稳定。此前有不连 VPN 访问不稳定的情况，因此**必须用家中宽带、手机流量且关闭 VPN 测试**。若实测质量不够，暂不切换；保留香港服务器作中继不属于“完全撤掉云端”，用途、期限及费用须另行确认。家庭宽带的服务条款、面向公众服务的适用要求也要在正式开放前核实，隧道不等于豁免。

公网 IP 不影响后台主动访问 Tushare、知识星球 MCP、AI 等外部接口；但家庭网络对各上游的实际连通性、限流及账号授权需要逐个测试。服务器在家断电、断网或休眠时，网站仍会离线。

### 家中无公网 IP：未采用的历史候选

用户已确认计划将 Mac mini 放在家中；目标型号现已实测，最终家庭宽带运营商、上行带宽及公网地址情况仍需核对。价格不用于推定硬件；准备不依赖取得公网 IP，也不自动申请网络服务。

此前曾提出保留香港入口进行对照测试；用户已选择不采用。以下只解释该候选的结构，不是当前部署步骤或默认回退授权：

```text
朋友浏览器 → 原域名 HTTPS → 香港入口反向代理 → 加密反向隧道 → 家中 Mac mini
                                              ↑ Mac 主动连接
```

这类反向代理可通过公网节点转发内网网站，家中不需要开放入站端口；例如 [frp 官方架构](https://gofrp.org/en/docs/overview/)。具体工具、鉴权和 TLS 配置待切换方案确认后实施，当前没有安装或启用中继。香港入口仅负责 HTTPS、访问控制和转发，数据库、采集、报告与 AI 任务在 Mac 上运行；只允许缓存公开版本化静态资源，不共享缓存登录、用户 API 或私有下载。继续保留香港入口仍有云资源费用，不属于完全撤云，也不能承诺绕过线路拥塞。

若希望最终不保留云服务器，可采用纯 Cloudflare Tunnel；访客通常只需浏览器，不需要安装隧道客户端。但它只解决内网入口问题，不能保证中国大陆各运营商的访问质量，也不改变家庭宽带或公网服务的适用要求。

两条测试入口应转到**同一个隔离测试实例**，不在两份数据库之间分流；生产仍保持云端单写。分别在家中宽带和关闭 VPN 的手机流量上，覆盖不同时段，测量登录、首屏、API、SSE、报告及录音大文件下载。家庭上行带宽是对外下载的瓶颈之一，换成 Mac 并不会自动消除网络延迟。记录请求失败、断线后隧道重连及断电恢复，再决定是否切域名或保留中继；在此之前不退订、降配或停用云端。

## 2. 硬件与容量，不根据购买价格猜配置

2026-09-14 通过已核对主机指纹的 SSH 在目标机实测：Apple M4、16 GiB 内存、macOS 26.2；数据卷当时可用约 112 GiB。不是使用开发用 MacBook Air 的配置代替。迁移时仍需重新检查空间，不把这次盘点当作持续容量保证：

```bash
python3 scripts/macmini_preflight.py --json
```

检查实际 macOS、CPU 架构、内存、剩余空间、Docker/Compose、本机 Docker 是否已运行、cloudflared、预定端口占用及旧登录服务。它不会安装软件、启动 Docker、连接云服务器、读取密钥内容或打开数据库；退出 0 只代表完成检查，**不代表上线验收通过**。

Docker 官方对 Mac 至少要求 4 GB 内存，但这不是本项目的容量承诺。Docker Desktop 需要区分 Apple Silicon 和 Intel 安装包；如果是 Apple Silicon，优先构建原生 Linux ARM64 镜像，不直接复制云端 AMD64 虚拟环境。[Mac 安装要求](https://docs.docker.com/desktop/setup/install/mac-install/)、[多架构镜像说明](https://docs.docker.com/build/building/multi-platform/)

### 本次采用 Colima 容器运行环境

目标机没有既有 Docker 服务，本次通过官方 Homebrew formula 安装 Colima、Docker CLI、Compose 与 Buildx，运行时选择 Colima 的 Docker 模式；不要求 Docker Desktop，也不改动已有 OpenClaw 服务。独立 `leziwu` profile 使用原生 ARM64、VZ、VirtioFS，初始分配 4 CPU、8 GiB 内存和 40 GiB 数据盘；另有 VM 系统盘，稀疏磁盘的上限不等于当前占用，也不是后续空间不会耗尽的承诺。[Colima 官方说明](https://github.com/abiosoft/colima)

VM 只挂载本次建立的私有服务器目录，不共享整个用户主目录，不转发 SSH agent，不修改默认 Docker context。操作时显式使用 `docker --context colima-leziwu`；运行预检时通过 `DOCKER_CONTEXT=colima-leziwu` 指定同一环境。若实际 context 名称不同，应先 `docker context ls` 核实，不能静默回退到远程或其他容器环境。Compose/Buildx 使用 Homebrew 安装的插件，不覆盖既有 Docker 登录配置。

目标机直连 GitHub Release 和 Docker Hub 曾发生超时。可在能访问官方来源的设备下载原始公开安装镜像，按官方发布摘要校验，再经已认证的局域网 SSH 传入目标机；使用 Colima 的 `--disk-image` 参数加载已校验镜像，不换不明镜像、不关闭 TLS、不把下载网络问题误报为应用故障。这只解决安装依赖，正式部署前仍须单独验证各业务上游的长期连通性。

VM 启动后须用非秘密临时文件验证 UID 1000 的读写、原子替换、VirtioFS 权限和隔离网络；数据库工作副本不能直接凭 macOS 文件可读就认定容器可写。暂不安装自动启动或发布公网端口；启动方案及故障守护在实际应用通过后配置并重启验收。

**当前机器的存储阻断项（2026-09-14 实测）：现有 bind-mount Compose 不能直接上线。** 私有测试目录在宿主机为 501:20、0700/0600，在当前 VirtioFS 容器内映射为 root:root；容器内 `chown 1000:1000` 后仍不能让应用 UID 1000 读取。没有扩大权限、改应用为 root 或放入真实密钥。相同测试在专用 Docker Linux named volume 内为真正的 1000:1000，私有文件读取和原子替换通过。

已补充 `docker/docker-compose.macmini-volumes.yml`，与原 Mac 配置叠加使用，只替换七个挂载，不复制或弱化鉴权、启动守卫、采集开关、健康检查和隔离网络。Compose 需支持 `!override`（2.24.4+）；目标机 5.1.1 实测合并后两个服务均没有残留 bind 字段，缺少卷前缀和缺少 external 卷时均拒绝，未创建业务容器。

工作副本放在 Colima 的 Linux 数据盘卷中，宿主机私有目录保存已校验备份。七个卷由操作者显式建立、导入并验证；`external: true` 不允许 Compose 自动创建空卷，`nocopy: true` 不把镜像旧内容复制进去，但已有空卷也不能算恢复成功。七卷全部要准备 UID/GID 1000，尤其 config/tyc/strategies 不由现有 entrypoint 修复；预览仍只读挂载这三个目录。不能把测试卷当业务卷，不设置 driver_opts 把卷重新指向 macOS 绑定路径。此轮尚未导入生产数据或完成应用镜像验收。

容量预算同时包括：停写原始副本、校验迁移包、可写运行副本、Docker VM/镜像/构建缓存，以及后续数据增长。不能仅看数据库大小。工具的空间估算仅覆盖额外迁移包；20 GiB 预警是运维提醒，不是“超过就够用”。上次云端数据目录约 2.7 GB、磁盘已偏紧，迁移当天重新盘点，**不要先在云端生成另一份大包**。

## 3. 已准备的文件

| 文件 | 用途 | 安全边界 |
| --- | --- | --- |
| `scripts/macmini_preflight.py` | 只读检查目标 Mac | 不安装、不启服务；远端 Docker context 不探测 |
| `scripts/migration_bundle.py` | `plan / export / verify` 数据清单与校验 | 新目标目录、停写确认、SQLite backup、SHA256、私有权限 |
| `docker/docker-compose.macmini.yml` | 独立 Mac 容器部署 | 默认隔离预览，不合并现有 cloud Compose |
| `docker/docker-compose.macmini-volumes.yml` | 当前 Colima 所需的 Linux 工作卷覆盖层 | 复用基础安全契约；七个 external 卷，缺卷拒绝，禁止自动复制镜像内容 |
| `docker/macmini.env.example` | 非秘密部署参数 | 镜像与运行路径必须显式填写；不填 API 密钥 |

这些配置不改变现有云端或原本的 Mac 自启动。**不要为迁移运行旧 `install-macos-autostart.sh`**：它会移动当前项目 `.env` 和 `data` 并创建软链接，可能碰到已有本地资料。使用新的隔离运行目录。

## 4. 迁移范围：不是只搬一个数据库

迁移前核对真实容器挂载、应用路径配置、定时任务、镜像与静态资源；以下是准备基线，不代替切换当天盘点。

| 云端资料 | 迁移包位置 | 必须保留的作用 |
| --- | --- | --- |
| `data/` | `runtime/data/` | SQLite、账号/自选、任务、逐字稿、资料包及缓存等实际内容 |
| `reports/` | `runtime/reports/` | 已生成报告及文档输出 |
| `strategies/` | `runtime/strategies/` | 既有策略文件 |
| 根 `.env` | `runtime/.env` | 根配置原件，敏感，不直接加载 |
| `cloud-config/` | `runtime/config/` | 持久设置，敏感；运行使用此处 `.env` |
| `tyc-config/` | `runtime/tyc/` | 天眼查配置/凭证 |
| `longbridge_tokens/` | `runtime/longbridge_tokens/` | 现有令牌资料，仅保留，不触发交易 |
| `logs/`、既有 `backups/` | 默认不装入迁移包 | 原处保留，不删除；需要历史存档时另行复制清单 |
| 实际生产代码、镜像、静态文件 | 单独版本归档 | 不包含在数据迁移包中 |

四个身份文件 `.admin_password_hash`、`.session_secret`、`.user_auth_secret`、`.llm_usage_hmac_secret` 也要随正式迁移保留；否则会话、认证或历史校验可能改变。工具默认排除，只有 `--include-secrets` 才纳入；数据库自身也包含私人数据，即便不含配置密钥，迁移包也不能公开。

任何自定义 `DATABASE_PATH`、外部挂载、对象存储、容器内部专有文件、Nginx/TLS、隧道凭证及远端定时任务需额外审计。脚本不会解析 `.env` 来自动发现外置目录，不能据此宣称“全量无遗漏”。不把活动 SQLite 放在 iCloud 同步目录。

### 保持当前网页版本

生产之前有基于镜像局部修复后端和独立发布磨砂玻璃前端的情况；本地工作区也有未提交变更。**不能把当前本地工作树或某个 Git HEAD 直接当成生产真源。**

迁移前记录实际容器镜像 ID、后端文件清单与哈希、对外静态入口哈希、所有入口引用的 JS/CSS，以及生产挂载。冻结可复现的生产代码基线，和现有 Dockerfile 对齐再构建目标架构。不得在迁移时夹带未验收功能或回退介绍首页。保留上一生产镜像及原云数据作为回退证据。

2026-09-14 18:20（北京时间）通过阿里云命令助手只读确认：两个生产容器镜像均为 `sha256:7148fda9610938921d5d35ca43f2ea751e83b4772c2a7306479b0d1716bf3888`，9 月 11 日启动、重启次数均为 0；两者实际挂载均与上表七个运行目录一致，只有 strategies 为只读。云端项目目录不是 Git 工作树。宿主 `static/index.html` 摘要为 `3b57913b8c28b6bf9f9b5edb4bb4d68b72fadb501eae0bd74b8519e0db5061e5`。这些只是盘点证据：**容器文件与宿主代码仍须交叉比对，不能认定生产源码已完整冻结或传输。** 本次未输出配置/密钥内容。

同次公网复核：首页 HTTP 200，未登录 `/app` 返回 307 到 `/access?redirect=%2Fapp`，`/health` 返回 `status=ok`。服务器 SSH 有成功连接记录，也多次在握手阶段超时；控制台只读命令可执行，不因此重启正常业务。正式数据复制仍需稳定的已认证传输通道。

当日后续仅对本次 SSH 连接指定 `curve25519-sha256` 密钥交换及已核验的 ED25519 主机密钥，成功建立并复用 ControlMaster；未改系统 SSH 配置、服务器密钥或防火墙。这是当前连接验证结果，不把原因归咎于用户 IP，也不保证长期链路稳定。

已从运行容器按白名单提取并传到 Mac mini 私有目录 `LeziwuServer/production-source-20260914-h3znxn63`：API 和 worker 各 400 个代码/资源文件，两份一致；另外保留实际 Nginx 静态目录 253 个文件、实际 entrypoint、37 个 tyc JS 文件及 159 个 Python 分发包版本。所有文件均在本地、源端提取后及 Mac mini 校验；未包含业务 data/config、令牌或密钥。真实公网、容器、Nginx 的首页摘要均为 `271f48fdd0788edb06ea5a2174857ac8aac8be5460e91ea736484c253a0de217`，与宿主项目目录里的旧 `static/index.html` 不同，不能拿后者覆盖线上版本。正式切换前还要重新核对是否出现新部署。

## 5. 数据准备：先检查，再在停写窗口导出

以下路径均为示意，在目标机选择真实绝对路径。不要放到 Git 目录或共享云盘；不要复用已有数据库目录。

```text
迁移私有目录/
  raw-cloud/          # 云端完全停写后，经 SSH 传输的原始快照
  bundle-final/       # export 新建；校验后不再修改
  working/runtime/   # 从 bundle/runtime 复制的可写运行副本
  macmini.env        # 非秘密 Compose 参数，与程序 API 密钥分开
  production-source/ # 核验后的生产代码及资源基线
```

准备阶段可在已取得的 staging 上只读列出范围：

```bash
python3 scripts/migration_bundle.py plan \
  --source /absolute/private/raw-cloud \
  --destination /absolute/private/bundle-final \
  --include-secrets
```

正式窗口由操作者先阻断新用户写入，等待或安全暂停在途付费任务，并停止**所有**写入端：API、独立采集、原生后台程序、定时任务及可能重新拉起容器的健康守护。检查实际进程和数据库连接后，保持停写，经核验主机身份的 SSH/SFTP 将文件送到 Mac 的 raw staging；不使用 `--delete`，不关闭 SSH 主机校验。若不能停写，改约窗口，不热拷贝整套目录冒充一致性快照。

```bash
python3 scripts/migration_bundle.py export \
  --source /absolute/private/raw-cloud \
  --destination /absolute/private/bundle-final \
  --writers-stopped --include-secrets

python3 scripts/migration_bundle.py verify \
  --bundle /absolute/private/bundle-final
```

`--writers-stopped` 是明确确认，不是脚本替你停止服务。脚本使用 SQLite backup 创建独立数据库，并校验所有选中文件；SHA256 防传输损坏，不提供来源签名。跨文件一致性依赖已停写的源。存在非空 WAL 时，普通可写源会被拒绝，需要干净关闭数据库，或另行取得含必要 SHM 的真正只读快照；**不手工删除 WAL，不对生产库执行 checkpoint 或修复来强行通关**。[SQLite 备份机制](https://www.sqlite.org/backup.html)

导出拒绝软链接、重叠路径及已存在目标；若中断保留部分结果用于诊断，不作为备份使用。产物目录 0700、文件 0600。`integrity_ok=true` 只证明产物完整性，`production_ready` 始终 false，还需应用恢复验收。verify 不创建工作 WAL。

## 6. 在 Mac 上预览：不接管采集，不重复花钱

1. 准备适合实际芯片的容器运行环境；本次目标机使用上文 Colima，Docker Desktop 是替代方案，不要并行启动两套生产运行时。公网通路验证后再安装所选隧道。保存经审计的生产版本，不复制 Linux `.venv`。
2. 用原 `docker/Dockerfile` 在目标机构建明确的本地镜像标签。首次 ARM 构建可能遇到 Python 原生依赖、PDF 工具、字体或 tyc CLI 架构问题，需实测解决，不能将 Compose 解析通过等同镜像可运行。
3. 保持校验迁移包原样，将 `runtime/` 独立复制成 working 副本，绝不使用硬链接。Docker entrypoint 会调整运行副本的数据/日志等权限，应用也会写入；**不能直接挂载 bundle-final/runtime**。
4. 在工作副本安全合并根 `.env` 和 `config/.env`：cloud 配置优先，输出为 `working/runtime/config/.env`；原件保留。该步骤在迁移窗口由操作者执行，工具目前只复制两份原件，不自动合并；不要通过 shell `source` 执行配置，不输出或提交密钥。审计所有旧 `/opt` 路径及 URL，使数据路径仍对应容器内 `/app/data` 等挂载。
5. 保持 `ADMIN_AUTH_ENABLED` 和 `USER_ACCESS_ENABLED` 均开启；校验恢复的登录密钥，不能以关闭鉴权解决启动问题。预览 config/tyc 挂载只读，仍需容器 UID 1000 可读；正式生产需要可写工作副本以保存管理员设置和凭证刷新，不能随意 `chmod 777`。
6. 从参数示例建立外置 `macmini.env`，填写 `DSA_MACMINI_RUNTIME_DIR`（working/runtime 的绝对路径）和已经构建的 `DSA_MACMINI_IMAGE`，保留默认预览开关。当前 Colima 必须叠加 Linux-volume 配置，并显式填写 `DSA_MACMINI_VOLUME_PREFIX`；七卷后缀为 `_data`、`_logs`、`_reports`、`_strategies`、`_config`、`_tyc`、`_longbridge_tokens`。Compose 每个文件先插值再合并，所以 base 仍要求 RUNTIME_DIR，但最终两个服务均不绑定该目录。
7. 在受控恢复步骤中将已核验 working/runtime 的七个目录分别导入对应新卷，核对卷身份、摘要、SQLite 完整性及非 root 读写；留存宿主备份，不能只备份 VM 磁盘。下方配置检查不导入数据，也不能代替该步骤。现阶段没有准备真实业务卷，直接启动应拒绝。

```bash
python3 scripts/macmini_preflight.py \
  --runtime-dir /absolute/private/working/runtime --image verified-local-image:tag --json

docker --context colima-leziwu compose --env-file /absolute/private/macmini.env \
  -f docker/docker-compose.macmini.yml \
  -f docker/docker-compose.macmini-volumes.yml config --quiet
```

确认后才启动隔离预览：

```bash
docker --context colima-leziwu compose --env-file /absolute/private/macmini.env \
  -f docker/docker-compose.macmini.yml \
  -f docker/docker-compose.macmini-volumes.yml up -d server
```

默认只发布 `127.0.0.1:18000`，不自动拉取镜像，`restart=no`、网络 `internal=true`、API `lifespan=off`，不开采集 profile。旧任务管理器可能在普通 API 启动时恢复队列，仅关闭几个 AUTO_START 开关不够，因此设置这层隔离。

预览不是只读 API，也不是完整功能演练；不要提交任务/修改用户资料。隔离网络的实际效果及 host 端口可达性须在实际采用的 Colima 或 Docker Desktop 环境验证。完成在线测试后正式迁移时重新取得最终快照，不能用演练工作副本直接接替生产。

## 7. 正式切换与验收

只有目标机器、公网测试入口及最终数据准备完成，且云端全部写入端已停止，才在参数文件切换：

```dotenv
DSA_MACMINI_NETWORK_INTERNAL=false
DSA_MACMINI_COLLECTOR_ENABLED=true
DSA_MACMINI_PRODUCTION_CONFIRMED=true
DSA_MACMINI_LIFESPAN=on
DSA_MACMINI_LIVE_JOBS=true
DSA_MACMINI_RESTART_POLICY=unless-stopped
DSA_MACMINI_CONFIG_READ_ONLY=false
DSA_MACMINI_TYC_READ_ONLY=false
```

重新创建服务并显式选择 `--profile collector`。恢复既有市场、知识星球、机构段子分析、日报、公告与同步看门狗；独立 worker 负责情报、研报库及概念题材。保留云端当前已关闭的自动任务策略，不因换机器开启全历史分析。`lifespan=on` 会恢复持久 AI/量化/回填队列，务必核对任务归属和在途状态，避免重付费。

使用单独测试入口和独立隧道核验，不让同一公网路由在两套独立数据库之间负载分流。cloudflared 连接 `http://127.0.0.1:18000`，只对外发布网站；不暴露 SSH、Docker socket、数据库、资料目录。正式切换原域名时再处理 TLS、代理头信任、真实客户端 IP 和 Cookie，不能因为经过隧道就默认信任任意转发 IP。

上线验收必须有证据：

- [ ] 公网介绍首页是当前玻璃版本；JS/CSS/图片和使用手册一致。
- [ ] 使用匿名、未命中 CDN 缓存的请求逐张核验首页 `/landing/` 图片：必须无登录跳转且返回 `image/*`，缺失图片必须 404。Tunnel 直连应用不经过旧 Nginx，必须由应用自身的受限静态路由提供图片；跟随登录跳转得到 HTML 200 不算图片通过。
- [ ] 未登录 `/app` 跳转登录，私有 API 拒绝访问；老账号登录、管理员和两名不同用户的数据隔离正常。
- [ ] `/health` 及容器健康探针为 HTTP 200 且 JSON `status=ok`；HTML/301 不算健康。
- [ ] 行情顶部、图表、昨收及日期一致；知识星球和公告等增量时间推进，非交易日口径正常。
- [ ] 机构段子检索、文件筛选、已有逐字稿查看/下载、资料包后台任务正常。
- [ ] 行业/公司调研、问股、量化后台任务、概念题材、自选股与一致预期均通过实际入口。
- [ ] 经授权选用小样本验证付费转写及 AI；已完成结果仍可查看，Word/PDF 中文字体及图片正常。
- [ ] SSE、长任务、批量下载、大文件中断恢复正常；不只测首页 200。
- [ ] 手机流量关闭 VPN、家庭宽带和另一网络连续访问正常，记录首屏/API 耗时和失败率。
- [ ] 重启后 Docker、API、采集、隧道、守护均恢复，停电和短时断网场景明确。

2026-09-20 首页图片故障已定向修复：仅在现有网站容器替换 `api/app.py`、`api/middlewares/auth.py` 并重启网站，采集容器与七个数据卷未变。五张首页展示图公网完整返回图片，源站缺图返回404，匿名业务页仍307、私有API仍401；浏览器已核验空白图恢复。旧代码备份与修复说明保存在 Mini 的 `LeziwuServer/landing-image-fix-20260920`。此次是现有容器文件层热修复，普通重启保留；**下次重新创建容器之前必须把该源码修复包含在新固定镜像中，不能直接从未修复的旧镜像重建**。未发布其他工作区改动或更新镜像身份。

## 8. 家用服务器的自启动、守护和备份

Docker Desktop 的设置是“用户登录后启动”，不等于无人登录的冷启动服务。FileVault 锁定、系统重启、睡眠及断电后恢复要实测；不要自动关闭 FileVault、启用免密登录或修改系统电源策略。保持有线网络、确认空闲不休眠；需要 UPS 时再由用户决定，不预先购买。[Docker Desktop 设置](https://docs.docker.com/desktop/settings-and-maintenance/settings/)

cloudflared 的 macOS 服务区分登录 LaunchAgent 与系统 LaunchDaemon，官方有安装方式，但隧道进程自启也不能代替 Docker VM 启动。[macOS 服务说明](https://developers.cloudflare.com/tunnel/advanced/local-management/as-a-service/macos/)

旧 Linux systemd 健康守护不能原样复制。Mac 守护待实际 Docker 启动方案确定后安装：只针对这一部署、严格 JSON 健康判断、三次失败确认、启动/部署宽限、记录动作；上游 403/429/504 不应引发不断重启。先检查 Docker 未启动或系统未解锁，不能盲目拉起第二份采集程序。公网监控需由 Mac 之外的设备/服务执行，否则 Mac 断电时自己无法报警。

正常运行后配置离机备份、磁盘容量预警、恢复演练及轮转保留策略；不以 iCloud 同步活动数据库代替一致性备份。现有云端数据库/附件/日志不删除，云资源是否保留及期限由用户确认。

本次只迁移“乐子乌超级价值”。2026-09-14 发现云端 Nginx 还配置了其他站点；不能因为本项目迁移完成就停止整台云服务器、卸载共用 Nginx 或移除其他站点配置。是否继续保留云机需另行核对其他用途。

## 9. 回退不能丢掉 Mac 上的新数据

- **Mac 尚未接受写入**：停止测试副本，恢复原入口和云端原版本/守护，验证原服务；保留迁移资料。
- **Mac 已有新用户操作、同步或 AI 结果**：先维护停写，停 Mac 所有写入端及守护，备份并验证最新数据；把新的完整数据状态安全迁回云端或经审计合并，再恢复入口。不能直接启动旧云数据库导致新增账号、任务和资料丢失，也不能双端同时写。

## 10. 准备阶段验证与仍待完成

原准备阶段 80 项离线测试通过，涵盖临时 SQLite/WAL 备份完整性、安全门槛、设备预检及真实配置管理器的原子保存；当时预览/生产两组 Compose CLI `config --quiet` 均通过，未操作生产、复制真实数据库或密钥。这些是工具契约验证，不是目标机业务验收。

2026-09-14 已在真实目标 Mac mini 执行只读预检，确认硬件、空间、Compose 可用、端口未占用，并传入非秘密迁移工具，校验脚本与本地 SHA256 一致。Colima 独立实例已启动，Docker 服务端实测为 Linux aarch64、4 CPU、约 8 GiB；预检返回 `daemon_reachable=true`，但 `production_ready=false`。安装后目标数据卷可用约 109 GiB，未启动任何业务容器或第二套采集。

另以 Alpine 官方 minirootfs 3.22.5 aarch64 的 SHA256 校验文件验证下载与传输，导入仅用于迁移检查的本地测试镜像；原生 ARM64、无网络、非 root 容器执行通过。宿主绑定目录权限未通过，Linux named-volume 的 UID 1000 私有读写及原子替换通过，结果如上。测试容器均已退出，没有常驻业务进程；测试镜像/非秘密样例卷保留供后续核对，不属于正式应用或生产备份。

当日连接测试中，云端 SSH 曾持续握手超时；重新登录阿里云控制台后，MacBook 直连曾成功，实测云端根文件系统 93%、剩余约 2.8 GiB，data 约 2.9 GiB、既有 backups 约 4.1 GiB，但后续连接仍间歇超时。目标机 Docker Hub 与 ECR 公共仓库的镜像拉取也发生超时。公网首页 HTTP 200 仅证明该页面传输可达，不代表维护通道或全部业务正常。没有据此重启生产、改防火墙、改密钥或停止写入；不在空间紧张的源机额外生成大包，需先稳定维护访问、核验目标网络。

新增卷覆盖层后合计 97 项离线测试通过，目标 Compose 5.1.1 实际合并确认两个服务除了挂载之外配置不变；缺前缀和缺卷均拒绝，未创建容器。这些测试使用明确标注的非秘密临时镜像和不存在的恢复卷，不使用真实生产配置。

完整生产 ARM 镜像构建、私有配置合并、最终数据一致性、全部业务与国内网络、自启动断电测试仍待实际迁移时完成；基础运行环境不能代替这些验收。

目标 Docker Hub 拉取超时后，已通过开发机从官方仓库按 ARM64 摘要取得 Python 3.11.16/bookworm 基础镜像，并校验归档 SHA256 后导入目标独立 Docker context；不是拷贝云端 AMD64 二进制。目标 `leziwu-python-base:3.11.16-arm64` 已以无网络、只读文件系统、UID 1000 验证 Python 3.11.16/aarch64。对应 Node 20.20.2 的官方 Linux ARM64 归档也已下载校验。原生应用镜像仍须完成全部依赖安装与验收，基础镜像成功不代表应用已部署。

本文是用户专属的中文迁移专题，暂无对应英文手册；公开配置示例和 CLI 均保留英文说明。准备完成不构成“下周自动切换”的定时承诺，需要用户确认目标机器和切换窗口后继续。
