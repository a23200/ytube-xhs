from fastapi.testclient import TestClient

from app.main import app


def test_web_ui_serves_required_controls_and_assets():
    client = TestClient(app)

    index = client.get("/")
    dashboard = client.get("/dashboard")
    batches = client.get("/batches")
    batch_detail = client.get("/batches/example-batch")
    project_detail = client.get("/projects/example-project")
    llm_settings = client.get("/settings/llm")
    runtime_settings = client.get("/settings/runtime")
    script = client.get("/static/app.js")
    styles = client.get("/static/styles.css")

    assert index.status_code == 200
    assert dashboard.status_code == 200
    assert batches.status_code == 200
    assert batch_detail.status_code == 200
    assert project_detail.status_code == 200
    assert llm_settings.status_code == 200
    assert runtime_settings.status_code == 200
    assert script.status_code == 200
    assert styles.status_code == 200

    html = index.text
    for required in [
        'id="app"',
        'id="modal-root"',
        "Video-to-Multiplatform Content Operations Console",
        "/static/app.js",
        "/static/styles.css",
    ]:
        assert required in html

    js = script.text
    for required in [
        'id="project-form"',
        'id="batch-form"',
        'id="batch-urls"',
        'id="batch-target-platform"',
        'id="start-batch-button"',
        'id="url"',
        'id="language"',
        'id="style"',
        'id="max_frames"',
        'id="use_whisper"',
        'id="use_ocr"',
        'id="submit-button"',
        'id="logs"',
        'id="frames"',
        'id="verification"',
        'id="download-all"',
        'id="download-frames-zip"',
        'id="llm-settings-form"',
        'id="llm-self-test-result"',
        'id="image-settings-form"',
        'id="image-self-test-result"',
        "renderCreateJobPanel",
        "renderStatusTimeline",
        "renderProgressLogPanel",
        "renderProjectTable",
        "renderProjectDetail",
        "renderBatches",
        "renderBatchDetail",
        "renderBatchDetailBody",
        "生产工作台",
        "批量队列",
        "开始顺序处理",
        "下载 Word 文件夹",
        "Multi-platform Content Workbench",
        "一键分析解析",
        "一键产出图文",
        "当前阶段",
        "预计剩余",
        "实时进度日志",
        "时间",
        "阶段",
        "消息",
        "详情",
        "关键帧",
        "字幕",
        "小红书稿",
        "今日头条稿",
        "CONTENT_ROUTES",
        "ROUTE_STATUS_OVERRIDES",
        "生成今日头条稿",
        "platformFromStatusData",
        "stageOutputsForStep",
        "set-content-route",
        "produce/toutiao",
        "generate-images/toutiao",
        "toutiao-post",
        "toutiao-image-cards",
        "toutiao-cards",
        "运行诊断",
        "LLM 配置",
        "文案与生图 API 配置",
        "生图 API 配置",
        "生图自检",
        "检查生图配置",
        "真实生成测试图",
        "yt_dlp_cookies_required",
        "平台要求使用最新浏览器 Cookie",
        "密钥已配置",
        'aria-disabled="true"',
    ]:
        assert required in js

    for endpoint in [
        "/api/health",
        "/api/system/doctor",
        "/api/settings/llm",
        "/api/llm/self-test",
        "/api/settings/image",
        "/api/image/self-test",
        "/api/projects",
        "/api/batches",
        "/api/batches/${batchId}/cancel",
        "/status",
        "/verify",
        "/files/${kind}",
        "/download/frames",
        "/rerun/${endpoint}",
    ]:
        assert endpoint in js

    for rerun_endpoint in ['"downstream"', '"visuals"']:
        assert rerun_endpoint in js

    for file_kind in [
        "metadata",
        "transcript",
        "keyframes",
        "visual_analysis",
        "content_assets",
        "xhs_post_json",
        "xhs_post_md",
        "toutiao_post_json",
        "toutiao_post_md",
        "toutiao_image_prompts",
        "toutiao_image_cards",
        "asset_package",
        "image_prompts",
        "run_metadata",
    ]:
        assert f'"{file_kind}"' in js

    css = styles.text
    for required in [
        ".sidebar",
        ".topbar",
        ".progress-summary",
        ".progress-bar",
        ".progress-fill",
        ".progress-metrics",
        ".status-analysis_completed",
        ".status-producing_article",
        ".status-toutiao_completed",
        ".status-rendering_cards",
        ".route-selector",
        ".timeline",
        ".tabs",
        ".frames-grid",
        ".diagnostic-matrix",
        ".batch-layout",
        ".batch-progress",
        ".batch-current-row",
    ]:
        assert required in css
