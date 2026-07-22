# Mac mini 独立生产部署方案

目标：在另一台 Mac mini 上作为独立项目运行，不依赖 Codex、开发机、浏览器自动化或其他辅助软件。服务由 `launchd` 托管，异常退出自动拉起，单项目与批量队列产物都写入本机 `runtime/`。

## 1. 推荐机器准备

- macOS 13+，Apple Silicon 或 Intel 均可。
- 至少 16GB 内存；无字幕视频启用 Whisper 时建议 16GB+。
- 磁盘按业务量预留，建议从 100GB 起。
- 安装 Homebrew。
- 打开终端，确认：

```bash
python3 --version
brew --version
```

## 2. 一键安装方式 A：从公开 GitHub 下载项目本体

这是推荐方式：项目本体上传到公开 GitHub 仓库，Mac mini 只需要一条固定命令即可下载项目源码并完成本地部署。以后升级也执行同一条命令。

在 Mac mini 上：

```bash
bash -c "$(curl -fsSL https://raw.githubusercontent.com/a23200/ytube-xhs/main/update-macos.sh)"
```

默认安装到：

```text
/opt/ytube-xhs
```

默认启动端口：

```text
8012
```

如果要指定版本或分支：

```bash
export YTXHS_PORT="8012"
export YTXHS_APP_DIR="/opt/ytube-xhs"
YTXHS_REF=<发布页中的最新Tag> bash -c "$(curl -fsSL https://raw.githubusercontent.com/a23200/ytube-xhs/main/update-macos.sh)"
```

固定更新脚本会自动下载 GitHub 源码包，再调用项目内 `deploy/macos/install_macos.sh` 完成本地依赖、虚拟环境和 launchd 服务安装；已有 `.env` 与 `runtime/` 不会被覆盖。

如果以后仓库改回 private，脚本仍支持设置 `GH_TOKEN` 或使用已登录的 `gh` CLI 下载源码包。

## 3. 安装方式 B：手动传输部署包

在开发机生成部署包：

```bash
./scripts/package_macos_deploy.sh
```

把 `dist/ytube-xhs-macmini-*.tar.gz` 和 `.sha256` 传到 Mac mini，例如：

```bash
scp dist/ytube-xhs-macmini-*.tar.gz macmini.local:~/Downloads/
scp dist/ytube-xhs-macmini-*.sha256 macmini.local:~/Downloads/
```

在 Mac mini 校验：

```bash
cd ~/Downloads
shasum -a 256 -c ytube-xhs-macmini-*.sha256
tar -xzf ytube-xhs-macmini-*.tar.gz
cd ytube-xhs-macmini-*
```

## 4. 本地安装细节

推荐以 LaunchDaemon 安装到 `/opt/ytube-xhs`，开机自启：

```bash
sudo deploy/macos/install_macos.sh \
  --app-dir /opt/ytube-xhs \
  --port 8012 \
  --service-user "$USER"
```

安装脚本会：

- 复制项目文件到 `/opt/ytube-xhs`
- 创建 `.venv`
- 安装 Python 依赖，包括真实 Word `.docx` 导出所需的 `python-docx`
- 默认安装 `faster-whisper`，用于无字幕视频转录
- 安装/检查 `ffmpeg`、`tesseract`、`tesseract-lang`
- 创建 `/opt/ytube-xhs/.env`
- 创建 `runtime/logs`
- 创建并持久化 `runtime/projects` 与 `runtime/batches` 业务产物目录
- 注册主服务 LaunchDaemon：`com.ytube-xhs.service`，开机自启并异常退出自动拉起
- 注册启动自检/自恢复 LaunchDaemon：`com.ytube-xhs.bootcheck`，开机和每 5 分钟检查依赖、拉起服务、健康检查，失败时自动重启服务

默认 OCR 使用 Homebrew `tesseract`，不默认安装 PaddleOCR。若后续确实需要 PaddleOCR，可重新运行安装脚本时增加 `--with-paddleocr`，并按 PaddlePaddle 官方要求安装匹配 runtime。

## 5. 配置密钥

部署包不会包含 `.env` 和任何密钥。到目标机器上编辑：

```bash
sudo nano /opt/ytube-xhs/.env
```

至少配置：

```env
XHS_LLM_API_KEY=你的文案模型key
XHS_LLM_BASE_URL=https://你的-openai-compatible-endpoint/v1
XHS_LLM_MODEL=你的模型名
XHS_LLM_REQUIRE_API_KEY=auto
XHS_LLM_TIMEOUT_MS=180000
XHS_LLM_MAX_CHARS=120000
XHS_LLM_MAX_TOKENS=6000
XHS_LLM_RETRY_ATTEMPTS=3
YTXHS_MAX_ANALYZE_WORKERS=1
YTXHS_MAX_PRODUCE_WORKERS=3
```

默认并发适合资源保守的 Mac mini：本地媒体/Whisper 分析 1 路、远程 LLM 文章生成 3 路。超过上限的任务显示排队位置；不要盲目提高分析并发，否则多个 Whisper/ffmpeg 任务会争抢内存和 CPU。

使用官方 DeepSeek 时可直接配置：

```env
XHS_LLM_BASE_URL=https://api.deepseek.com
XHS_LLM_MODEL=deepseek-chat
XHS_LLM_REQUIRE_API_KEY=true
XHS_LLM_API_KEY=你的官方DeepSeekKey
```

LLM 故障会区分鉴权、限流、超时、网络、HTTP 和响应结构问题；项目日志只保留脱敏摘要。`XHS_LLM_RETRY_ATTEMPTS` 范围为 1 至 10。

如果使用外部生图 API：

```env
XHS_IMAGE_ENABLED=true
XHS_IMAGE_API_KEY=你的生图key
XHS_IMAGE_BASE_URL=https://你的-image-endpoint/v1
XHS_IMAGE_MODEL=你的生图模型
XHS_IMAGE_REQUIRE_API_KEY=auto
```

平台 Cookie：抖音以及部分 YouTube、哔哩哔哩公开视频也可能要求最新浏览器 Cookie。无人值守服务不建议依赖 Chrome profile，优先从能正常打开目标视频的浏览器导出最新 `cookies.txt` 到：

```bash
sudo mkdir -p /opt/ytube-xhs/secrets
sudo cp cookies.txt /opt/ytube-xhs/secrets/cookies.txt
sudo chown "$USER":staff /opt/ytube-xhs/secrets/cookies.txt
sudo chmod 600 /opt/ytube-xhs/secrets/cookies.txt
```

并设置：

```env
XHS_YTDLP_COOKIES_FILE=/opt/ytube-xhs/secrets/cookies.txt
XHS_YTDLP_COOKIES_FROM_BROWSER=
```

如果服务以当前已登录的 macOS 用户运行，并且该用户的 Chrome 能正常打开目标视频，也可不使用 Cookie 文件，改为：

```env
XHS_YTDLP_COOKIES_FILE=
XHS_YTDLP_COOKIES_FROM_BROWSER=chrome
```

`Fresh cookies (not necessarily logged in) are needed` 表示平台需要新的匿名或登录态浏览器 Cookie，不代表公开视频已失效。配置过期 Cookie 时会返回 `yt_dlp_cookies_invalid`；完全未配置但平台明确要求时会返回 `yt_dlp_cookies_required`。

抖音公开视频另有同平台公开分享页回退：当 yt-dlp 明确要求 fresh cookies，且 `iesdouyin.com/share/video/{id}/` 公开返回匹配作品 ID 的结构化数据时，系统直接下载公开 MP4，不依赖第三方解析服务。未公开、图文或受限内容仍需要合法 Cookie/授权。

配置后重启：

```bash
sudo /opt/ytube-xhs/deploy/macos/manage.sh restart
```

## 6. 验收

基础健康检查：

```bash
/opt/ytube-xhs/start.sh
/opt/ytube-xhs/deploy/macos/manage.sh health
```

完整业务检查，包含 LLM 和 image 配置级自检：

```bash
/opt/ytube-xhs/deploy/macos/manage.sh self-test
```

运行诊断：

```bash
/opt/ytube-xhs/deploy/macos/manage.sh doctor
```

浏览器打开：

```text
http://<Mac-mini-IP>:8012
```

业务验收还应确认：四个平台均可选择；文章正文无小标题；生成结果可下载 JSON、Markdown、Word 和质量报告；从两个浏览器窗口同时提交不同平台文章时，任务状态互不覆盖；停止其中一个任务后另一个继续执行。

## 7. 日常运维

```bash
/opt/ytube-xhs/start.sh              # 服务正常则输出访问地址；不正常则自动启动
/opt/ytube-xhs/start.sh restart      # 强制重启并等待健康检查
/opt/ytube-xhs/start.sh status       # 查看 launchd 状态、HTTP 健康和访问地址
/opt/ytube-xhs/start.sh bootcheck    # 手动执行开机同款依赖/健康/自恢复检查
sudo /opt/ytube-xhs/deploy/macos/manage.sh status
sudo /opt/ytube-xhs/deploy/macos/manage.sh restart
sudo /opt/ytube-xhs/deploy/macos/manage.sh logs
sudo /opt/ytube-xhs/deploy/macos/manage.sh bootcheck
sudo /opt/ytube-xhs/deploy/macos/manage.sh recover
```

如果确实要临时停服，使用 `sudo /opt/ytube-xhs/deploy/macos/manage.sh stop`；它会暂停 bootcheck 自动拉起，直到再次执行 `start` 或 `restart`。

日志位置：

```text
/opt/ytube-xhs/runtime/logs/uvicorn.out.log
/opt/ytube-xhs/runtime/logs/uvicorn.err.log
/opt/ytube-xhs/runtime/logs/bootcheck.out.log
/opt/ytube-xhs/runtime/logs/bootcheck.err.log
```

项目产物：

```text
/opt/ytube-xhs/runtime/projects/
```

批量队列会按输入顺序逐条生成 Word，集中保存到：

```text
/opt/ytube-xhs/runtime/batches/{batch_id}/documents/
```

每个批次同时保存 `batch.json` 和 `documents/batch-summary.json`。Web 的“批量队列”页面可停止当前批次、查看每条视频对应项目，并下载包含全部成功 Word 的 ZIP。更新脚本保留整个 `runtime/`，不会删除未完成批次或已生成文档；服务重启后会重新排入未完成批次，保留已成功文档，中断中的单项标记失败后继续后续链接。

## 8. 防故障建议

1. 用 `launchd` 托管，不用手动终端常驻；默认同时安装主服务和 bootcheck 自恢复守护。
2. 定期执行：

```bash
/opt/ytube-xhs/deploy/macos/healthcheck.sh --base-url http://127.0.0.1:8012 --llm
```

3. 服务启动时会立即把上次进程遗留的排队/运行任务恢复成带 partial package 的结构化失败，避免永久卡住。也可定期执行额外的陈旧任务巡检：

```bash
/opt/ytube-xhs/.venv/bin/python /opt/ytube-xhs/scripts/recover_stale_projects.py --older-than-seconds 3600
```

4. 定期备份：

```bash
tar -czf ~/ytube-xhs-runtime-$(date +%Y%m%d).tar.gz -C /opt/ytube-xhs runtime
```

5. 大量视频任务时，建议把 `/opt/ytube-xhs/runtime` 放到大容量磁盘，并在 `.env` 设置绝对路径。

## 9. 升级流程

GitHub 方式：

```bash
bash -c "$(curl -fsSL https://raw.githubusercontent.com/a23200/ytube-xhs/main/update-macos.sh)"
sudo /opt/ytube-xhs/deploy/macos/manage.sh self-test
```

部署包方式：

1. 在开发机重新生成部署包。
2. 传到 Mac mini。
3. 解压后重新运行安装脚本；脚本不会覆盖已有 `.env` 和 `runtime/`。
4. 重启服务并自检：

```bash
sudo deploy/macos/install_macos.sh --app-dir /opt/ytube-xhs --port 8012 --service-user "$USER"
sudo /opt/ytube-xhs/deploy/macos/manage.sh self-test
```

`scripts/package_macos_deploy.sh` 在生成归档后会执行 `tar -tzf` 内容审计和 SHA256 自检；任何本地 `.env`、runtime 数据、虚拟环境、Git/缓存、Cookie 或疑似密钥文件进入包内都会令打包失败。
