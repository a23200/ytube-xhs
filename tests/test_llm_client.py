import pytest

from app.services import diagnostics
from app.services import llm_client as llm_client_module
from app.services.errors import PipelineError
from app.services.llm_client import LLMClient, sanitize_llm_url


def test_llm_self_test_reports_missing_key(monkeypatch):
    client = LLMClient()
    client.api_key = None

    result = client.self_test()

    assert result["ok"] is False
    assert result["error"]["code"] == "llm_unavailable"
    assert result["error"]["step"] == "llm_self_test"


def test_llm_client_can_call_no_auth_compatible_endpoint(monkeypatch):
    import httpx

    client = LLMClient()
    client.api_key = None
    client.requires_api_key = False
    client.base_url = "http://127.0.0.1:11434/v1"
    observed_headers = []

    def fake_post(url, headers, json, timeout):
        observed_headers.append(headers)
        request = httpx.Request("POST", url)
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": '{"ok": true}'}}]},
            request=request,
        )

    monkeypatch.setattr(httpx, "post", fake_post)

    content = client.chat_text([{"role": "user", "content": "hi"}], step="planning_content")

    assert content == '{"ok": true}'
    assert observed_headers == [{"Content-Type": "application/json"}]


def test_llm_parse_json_accepts_fenced_object():
    client = LLMClient()

    parsed = client._parse_json('```json\n{"ok": true}\n```')

    assert parsed == {"ok": True}


def test_llm_request_failure_redacts_api_key(monkeypatch):
    import httpx

    client = LLMClient()
    client.api_key = "secret-token"
    client.base_url = "https://example.test/v1"
    monkeypatch.setattr(llm_client_module.time, "sleep", lambda _: None)

    def fail_post(*args, **kwargs):
        raise httpx.HTTPError("Authorization: Bearer secret-token; raw secret-token")

    monkeypatch.setattr(httpx, "post", fail_post)

    with pytest.raises(PipelineError) as exc_info:
        client.chat_text([{"role": "user", "content": "hi"}], step="planning_content")

    error = exc_info.value.to_dict()
    assert error["code"] == "llm_request_failed"
    assert "secret-token" not in error["details"]["error"]
    assert "Bearer [redacted]" in error["details"]["error"]


def test_llm_request_failure_redacts_sensitive_base_url_query(monkeypatch):
    import httpx

    client = LLMClient()
    client.api_key = "secret-token"
    client.base_url = "https://example.test/v1?api_key=url-secret&keep=visible"
    monkeypatch.setattr(llm_client_module.time, "sleep", lambda _: None)

    def fail_post(*args, **kwargs):
        raise httpx.HTTPError("failed https://example.test/v1?api_key=url-secret&keep=visible")

    monkeypatch.setattr(httpx, "post", fail_post)

    with pytest.raises(PipelineError) as exc_info:
        client.chat_text([{"role": "user", "content": "hi"}], step="planning_content")

    error = exc_info.value.to_dict()
    assert "url-secret" not in error["details"]["error"]
    assert "url-secret" not in error["details"]["base_url"]
    assert error["details"]["base_url"] == "https://example.test/v1?api_key=%5Bredacted%5D&keep=visible"


def test_llm_self_test_redacts_base_url_without_key():
    client = LLMClient()
    client.api_key = None
    client.base_url = "https://example.test/v1?token=url-secret&keep=visible"

    result = client.self_test()

    assert result["ok"] is False
    assert "url-secret" not in result["base_url"]
    assert result["base_url"] == "https://example.test/v1?token=%5Bredacted%5D&keep=visible"


def test_sanitize_llm_url_preserves_non_sensitive_query_values():
    assert sanitize_llm_url("https://example.test/v1?token=secret&region=us") == (
        "https://example.test/v1?token=%5Bredacted%5D&region=us"
    )


def test_llm_trim_messages_preserves_system_prompt_within_budget():
    client = LLMClient()
    client.max_chars = 18

    trimmed = client._trim_messages(
        [
            {"role": "system", "content": "system-long"},
            {"role": "user", "content": "12345678"},
            {"role": "assistant", "content": "abcd"},
        ]
    )

    assert trimmed == [
        {"role": "system", "content": "system-long"},
        {"role": "user", "content": "123"},
        {"role": "assistant", "content": "abcd"},
    ]


def test_llm_trim_messages_clips_long_content_with_head_and_tail():
    client = LLMClient()
    client.max_chars = 52

    trimmed = client._trim_messages(
        [
            {"role": "system", "content": "keep-rules"},
            {"role": "user", "content": "A" * 20 + "MIDDLE" + "Z" * 20},
        ]
    )

    user_content = trimmed[1]["content"]
    assert trimmed[0] == {"role": "system", "content": "keep-rules"}
    assert "...[trimmed]..." in user_content
    assert user_content.startswith("A")
    assert user_content.endswith("Z")
    assert "MIDDLE" not in user_content


def test_llm_json_chat_repairs_malformed_json(monkeypatch):
    client = LLMClient()
    calls = []

    def fake_chat_text(messages, step, temperature=0.3, force_json=False, **kwargs):
        calls.append({"messages": messages, "temperature": temperature, "force_json": force_json})
        if len(calls) == 1:
            return "not json"
        return '```json\n{"ok": true}\n```'

    monkeypatch.setattr(client, "chat_text", fake_chat_text)

    payload = client.json_chat([{"role": "user", "content": "return json"}], step="planning_content")

    assert payload == {"ok": True}
    assert len(calls) == 2
    assert calls[0]["force_json"] is True
    assert calls[1]["temperature"] == 0.0
    assert "Repair this response" in calls[1]["messages"][1]["content"]


def test_llm_json_parse_failure_redacts_error_excerpts(monkeypatch):
    client = LLMClient()
    client.api_key = "secret-token"

    def fake_chat_text(messages, step, temperature=0.3, force_json=False, **kwargs):
        if temperature == 0.0:
            return "still bad Bearer secret-token https://example.test/v1?token=url-secret"
        return "bad secret-token https://example.test/v1?api_key=url-secret"

    monkeypatch.setattr(client, "chat_text", fake_chat_text)

    with pytest.raises(PipelineError) as exc_info:
        client.json_chat([{"role": "user", "content": "return json"}], step="planning_content")

    error = exc_info.value.to_dict()
    assert error["code"] == "llm_json_parse_failed"
    assert "secret-token" not in error["details"]["raw"]
    assert "secret-token" not in error["details"]["repaired"]
    assert "url-secret" not in error["details"]["raw"]
    assert "url-secret" not in error["details"]["repaired"]
    assert "Bearer [redacted]" in error["details"]["repaired"]


def test_llm_chat_text_falls_back_when_response_format_is_rejected(monkeypatch):
    import httpx

    client = LLMClient()
    client.api_key = "secret-token"
    client.base_url = "https://example.test/v1"
    calls = []
    monkeypatch.setattr(llm_client_module.time, "sleep", lambda _: None)

    def fake_post(url, headers, json, timeout):
        calls.append(json.copy())
        request = httpx.Request("POST", url)
        if "response_format" in json:
            return httpx.Response(400, json={"error": {"message": "unsupported response_format"}}, request=request)
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": '{"ok": true}'}}]},
            request=request,
        )

    monkeypatch.setattr(httpx, "post", fake_post)

    content = client.chat_text([{"role": "user", "content": "hi"}], step="planning_content", force_json=True)

    assert content == '{"ok": true}'
    assert "response_format" in calls[0]
    assert "response_format" not in calls[1]


def test_diagnostics_redacts_sensitive_llm_base_url(monkeypatch):
    monkeypatch.setattr(diagnostics.settings, "llm_base_url", "https://example.test/v1?access_token=secret&region=us")

    body = diagnostics.collect_diagnostics()

    assert body["llm"]["base_url"] == "https://example.test/v1?access_token=%5Bredacted%5D&region=us"
    assert "secret" not in str(body["llm"])
