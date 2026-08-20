# macOS 登录自启动与应用入口

这套安装方式把后台运行代码部署到不受 macOS“文稿”隐私限制的 Application Support 目录。
当前项目的 `.env` 和 `data/` 会移动到运行目录，项目原位置建立符号链接，因此后台服务、命令行和后续开发仍使用同一份 SQLite 数据，不会产生两套知识库。

## 安装

```bash
bash scripts/install-macos-autostart.sh
```

安装完成后：

- 登录 macOS 时，LaunchAgent 会在后台启动 FastAPI 服务；服务异常退出时会自动重新拉起。
- “财经情报台.app”安装在系统“应用程序”目录；系统目录不可写时自动改用当前用户的“应用程序”目录。
- 安装器会把“财经情报台”加入 Dock；已存在时不会重复添加。
- 双击“财经情报台”会等待本地健康检查通过，然后使用默认浏览器打开前端。
- 后台只监听 `127.0.0.1`，不会直接暴露到局域网。
- Web 端口读取 `.env` 中的 `WEBUI_PORT`，未配置时使用项目默认值。
- 后台运行代码位于 `~/Library/Application Support/财经情报台/runtime`，避开 macOS 对登录进程读取 Documents 的限制。
- 修改后端源代码后重新运行安装脚本，即可把最新代码部署到后台运行目录；实时 `.env` 和数据库不会被覆盖。

后台日志：

- `logs/launchd.stdout.log`
- `logs/launchd.stderr.log`
- `logs/api_server_YYYYMMDD.log`

重建 `.venv` 或修改后端代码后，需要重新运行安装脚本更新后台运行副本。

## 卸载自启动

```bash
bash scripts/uninstall-macos-autostart.sh
```

卸载会把实时 `.env` 和 `data/` 从 Application Support 移回项目目录，然后移除 LaunchAgent 和 `.app` 入口；SQLite、附件和报告不会被删除。部署的代码副本和日志会保留，便于故障恢复。Dock 中的图标可直接右键选择“从程序坞中移除”。
