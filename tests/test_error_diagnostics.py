from app.services.error_diagnostics import diagnose_error, error_catalog


def test_ytdlp_diagnostic_preserves_actual_error_and_location():
    payload = diagnose_error(
        {
            "code": "yt_dlp_failed",
            "message": "yt-dlp failed for an unclassified reason.",
            "step": "ingest",
            "details": {
                "error": "ERROR: [Toutiao] API returned an unexpected payload",
                "platform": "toutiao",
            },
        }
    )

    diagnostic = payload["diagnostic"]
    assert diagnostic["actual_error"] == "ERROR: [Toutiao] API returned an unexpected payload"
    assert diagnostic["location"]["stage_label"] == "视频采集"
    assert diagnostic["location"]["component"] == "app/services/ingest.py / yt-dlp"
    assert diagnostic["location"]["platform"] == "toutiao"
    assert diagnostic["solutions"]


def test_contract_diagnostic_points_to_artifact_and_missing_fields():
    payload = diagnose_error(
        {
            "code": "llm_contract_invalid",
            "message": "LLM output for content-assets.json is missing required fields.",
            "step": "planning_content",
            "details": {
                "artifact": "content-assets.json",
                "missing_fields": ["source_evidence", "chapters"],
                "repair_attempts": 2,
            },
        }
    )

    location = payload["diagnostic"]["location"]
    assert location["artifact"] == "content-assets.json"
    assert location["missing_fields"] == ["source_evidence", "chapters"]
    assert payload["diagnostic"]["actual_error"] == 'missing: ["source_evidence", "chapters"]'


def test_quality_diagnostic_uses_all_visible_violations_as_actual_error():
    payload = diagnose_error(
        {
            "code": "body_too_short",
            "message": "正文只有 962 个有效字符，今日头条完成稿至少需要 1200 个。",
            "step": "validating_content",
            "details": {
                "actual_chars": 962,
                "minimum_chars": 1200,
                "quality_report": {
                    "violations": [
                        {"code": "body_too_short", "message": "正文只有 962 个有效字符。"},
                        {"code": "hook_lacks_contrast", "message": "开头缺少反差。"},
                    ]
                },
            },
        }
    )

    actual = payload["diagnostic"]["actual_error"]
    assert "body_too_short" in actual
    assert "hook_lacks_contrast" in actual
    assert payload["diagnostic"]["location"]["stage_label"] == "文章质量校验"


def test_stale_project_diagnostic_explains_recovery_not_content_failure():
    payload = diagnose_error(
        {
            "code": "stale_running_project",
            "message": "Project was left in a running state past the recovery threshold.",
            "step": "planning_content",
            "details": {"previous_status": "planning_content", "older_than_seconds": 3600},
        }
    )

    assert "服务重启" in payload["diagnostic"]["cause"]
    assert payload["diagnostic"]["location"]["previous_status"] == "planning_content"


def test_error_catalog_contains_pipeline_and_quality_failures():
    codes = {item["code"] for item in error_catalog()}
    assert {
        "yt_dlp_failed",
        "yt_dlp_cookies_required",
        "llm_contract_invalid",
        "body_too_short",
        "stale_running_project",
        "worker_process_failed",
    } <= codes
