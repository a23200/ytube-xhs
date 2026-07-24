import pytest

from app.schemas.models import BatchCreate, ProjectCreate
from app.services import source_urls


def test_project_and_batch_extract_urls_from_platform_share_text():
    project = ProjectCreate(url="3.21 复制打开抖音 https://www.douyin.com/video/7658533907561963193/ 看视频")
    batch = BatchCreate(
        urls=[
            "YouTube: https://youtu.be/jNQXAC9IVRw?t=3",
            "B站 https://www.bilibili.com/video/BV1xx411c7mD/?spm_id_from=333",
        ]
    )

    assert str(project.url).startswith("https://www.douyin.com/video/7658533907561963193")
    assert str(batch.urls[0]).startswith("https://youtu.be/jNQXAC9IVRw")
    assert str(batch.urls[1]).startswith("https://www.bilibili.com/video/BV1xx411c7mD")


@pytest.mark.parametrize(
    ("url", "platform", "normalized"),
    [
        ("https://youtu.be/jNQXAC9IVRw?t=2", "youtube", "https://www.youtube.com/watch?v=jNQXAC9IVRw"),
        (
            "https://www.douyin.com/share/video/7658533907561963193/?foo=bar",
            "douyin",
            "https://www.douyin.com/video/7658533907561963193",
        ),
        (
            "https://www.bilibili.com/video/BV1xx411c7mD/?spm_id_from=333",
            "bilibili",
            "https://www.bilibili.com/video/BV1xx411c7mD",
        ),
        ("https://www.toutiao.com/video/123456789/", "toutiao", "https://www.toutiao.com/video/123456789/"),
    ],
)
def test_prepare_source_url_normalizes_supported_direct_links(url, platform, normalized):
    result = source_urls.prepare_source_url(url)

    assert result.source_platform == platform
    assert result.normalized_url == normalized
    assert result.short_link is False


class _FakeResponse:
    def __init__(self, url, *, location=None, status=200):
        self.url = url
        self.headers = {"location": location} if location else {}
        self.is_redirect = location is not None
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def raise_for_status(self):
        if self.status >= 400:
            raise RuntimeError(f"HTTP {self.status}")


class _FakeClient:
    def __init__(self, responses, calls, **_kwargs):
        self.responses = list(responses)
        self.calls = calls

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def stream(self, method, url):
        self.calls.append((method, url))
        return self.responses.pop(0)


def test_short_link_is_resolved_and_canonicalized(monkeypatch):
    calls = []
    responses = [
        _FakeResponse("https://v.douyin.com/abc/", location="https://www.douyin.com/video/7658533907561963193/?foo=bar"),
        _FakeResponse("https://www.douyin.com/video/7658533907561963193/?foo=bar"),
    ]
    monkeypatch.setattr(source_urls.httpx, "Client", lambda **kwargs: _FakeClient(responses, calls, **kwargs))

    result = source_urls.prepare_source_url("https://v.douyin.com/abc/")

    assert result.normalized_url == "https://www.douyin.com/video/7658533907561963193"
    assert result.redirect_attempts == 1
    assert len(calls) == 2


def test_short_link_rejects_cross_platform_redirect_before_request(monkeypatch):
    calls = []
    responses = [_FakeResponse("https://b23.tv/abc", location="http://127.0.0.1:8012/api/health")]
    monkeypatch.setattr(source_urls.httpx, "Client", lambda **kwargs: _FakeClient(responses, calls, **kwargs))

    with pytest.raises(source_urls.SourceUrlError) as exc_info:
        source_urls.prepare_source_url("https://b23.tv/abc")

    assert exc_info.value.code == "source_url_redirect_mismatch"
    assert calls == [("GET", "https://b23.tv/abc")]


def test_known_platform_rejects_generic_extractor():
    assert source_urls.extractor_matches_platform("douyin", "Douyin") is True
    assert source_urls.extractor_matches_platform("bilibili", "BiliBili") is True
    assert source_urls.extractor_matches_platform("toutiao", "Ixigua") is True
    assert source_urls.extractor_matches_platform("douyin", "Generic") is False
