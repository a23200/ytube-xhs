# Mac mini 独立生产部署方案

目标：在另一台 Mac mini 上作为独立项目运行，不依赖 Codex、开发机、浏览器自动化或其他辅助软件。服务由 `launchd` 托管，异常退出自动拉起，产物写入本机 `runtime/`。

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

这是推荐方式：项目本体上传到公开 GitHub 仓库，Mac mini 只需要一条命令即可下载项目源码并完成本地部署。

在 Mac mini 上：

```bash
YTXHS_REF=macmini-v20260704.1 bash -c "$(curl -fsSL https://raw.githubusercontent.com/a23200/ytube-xhs/macmini-v20260704.1/install-from-github-macos.sh)"
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
export YTXHS_REF="main"
export YTXHS_PORT="8012"
export YTXHS_APP_DIR="/opt/ytube-xhs"
YTXHS_REF=macmini-v20260704.1 bash -c "$(curl -fsSL https://raw.githubusercontent.com/a23200/ytube-xhs/macmini-v20260704.1/install-from-github-macos.sh)"
```

脚本会自动下载 GitHub 源码包，再调用项目内 `deploy/macos/install_macos.sh` 完成本地依赖、虚拟环境和 launchd 服务安装。

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
- 安装 Python 依赖
- 默认安装 `faster-whisper`，用于无字幕视频转录
- 安装/检查 `ffmpeg`、`tesseract`、`tesseract-lang`
- 创建 `/opt/ytube-xhs/.env`
- 创建 `runtime/logs`
- 注册 `launchd` 服务 `com.ytube-xhs.service`

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
```

如果使用外部生图 API：

```env
XHS_IMAGE_ENABLED=true
XHS_IMAGE_API_KEY=你的生图key
XHS_IMAGE_BASE_URL=https://你的-image-endpoint/v1
XHS_IMAGE_MODEL=你的生图模型
XHS_IMAGE_REQUIRE_API_KEY=auto
```

YouTube cookies：无人值守服务不建议依赖 Chrome profile。若确实需要，导出 `cookies.txt` 到：

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

配置后重启：

```bash
sudo /opt/ytube-xhs/deploy/macos/manage.sh restart
```

## 6. 验收

基础健康检查：

```bash
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

## 7. 日常运维

```bash
sudo /opt/ytube-xhs/deploy/macos/manage.sh status
sudo /opt/ytube-xhs/deploy/macos/manage.sh restart
sudo /opt/ytube-xhs/deploy/macos/manage.sh logs
sudo /opt/ytube-xhs/deploy/macos/manage.sh recover
```

日志位置：

```text
/opt/ytube-xhs/runtime/logs/uvicorn.out.log
/opt/ytube-xhs/runtime/logs/uvicorn.err.log
```

项目产物：

```text
/opt/ytube-xhs/runtime/projects/
```

## 8. 防故障建议

1. 用 `launchd` 托管，不用手动终端常驻。
2. 定期执行：

```bash
/opt/ytube-xhs/deploy/macos/healthcheck.sh --base-url http://127.0.0.1:8012 --llm
```

3. 定期恢复卡住任务：

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
export YTXHS_REF="main"
YTXHS_REF=macmini-v20260704.1 bash -c "$(curl -fsSL https://raw.githubusercontent.com/a23200/ytube-xhs/macmini-v20260704.1/install-from-github-macos.sh)"
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
