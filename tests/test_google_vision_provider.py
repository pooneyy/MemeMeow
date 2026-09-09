"""Google Vision Web Detection provider 的离线契约测试。"""

from __future__ import annotations

import base64
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.reverse_image import (
    GoogleVisionWebDetectionProvider,
    ReverseImageError,
    ReverseImageRequest,
    ReverseImageService,
    SerpApiGoogleLensProvider,
    _is_empty,
    _normalize_google_web_detection,
)


def _request() -> ReverseImageRequest:
    """构造不依赖图片解码的 provider 输入。"""
    return ReverseImageRequest(image=b"test-image", filename="meme.png", task_id="task")


def test_cache_identity_isolated_between_google_and_serpapi() -> None:
    """同一图片在两个 provider 下必须产生不同缓存身份。"""
    request = _request()
    image_sha = "a" * 64
    assert request.identity(image_sha) != request.identity(image_sha, provider="serpapi", engine="google_lens")


def test_service_selects_only_the_configured_provider(tmp_path: Path) -> None:
    """默认选择 Google，显式 SerpApi 时不创建 Google provider。"""
    google_settings = SimpleNamespace(
        reverse_image_provider="google_vision",
        google_cloud_project="test-project",
        google_application_credentials=None,
        serpapi_api_key=None,
        data_root=tmp_path / "data",
        reverse_image_cache_root=tmp_path / "cache",
    )
    google_service = ReverseImageService(google_settings, SimpleNamespace())
    assert isinstance(google_service._provider(), GoogleVisionWebDetectionProvider)

    serpapi_settings = SimpleNamespace(**{**google_settings.__dict__, "reverse_image_provider": "serpapi", "serpapi_api_key": "test-key"})
    serpapi_service = ReverseImageService(serpapi_settings, SimpleNamespace())
    assert isinstance(serpapi_service._provider(), SerpApiGoogleLensProvider)


def test_google_result_keeps_provider_groups_and_common_candidates() -> None:
    """Google 分组转换为通用候选，同时保留原始专属字段。"""
    result = _normalize_google_web_detection(
        {
            "pagesWithMatchingImages": [{"pageTitle": "网页标题", "url": "https://example.com/page"}],
            "fullMatchingImages": [{"url": "https://example.com/full.jpg"}],
            "partialMatchingImages": [{"url": "https://example.com/partial.jpg"}],
            "visuallySimilarImages": [{"url": "https://example.com/similar.jpg"}],
            "webEntities": [{"entityId": "/m/abc", "description": "实体", "score": 0.8}],
            "bestGuessLabels": [{"label": "标签", "languageCode": "zh"}],
        }
    )
    assert result["visual_matches"]
    assert result["exact_matches"] == [{"link": "https://example.com/full.jpg", "source": "https://example.com/full.jpg"}]
    assert result["web_entities"] == [{"description": "实体", "entity_id": "/m/abc", "score": 0.8}]
    assert result["best_guess_labels"] == [{"label": "标签", "language": "zh"}]
    assert result["google_vision_web_detection"]["pages_with_matching_images"][0]["pageTitle"] == "网页标题"
    assert _is_empty(_normalize_google_web_detection({})) is True


def test_google_provider_posts_web_detection_request_without_leaking_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    """provider 使用授权会话和项目 header，返回结果不包含请求认证材料。"""
    captured: dict[str, object] = {}

    class FakeResponse:
        ok = True

        def json(self) -> dict[str, object]:
            return {"responses": [{"webDetection": {"bestGuessLabels": [{"label": "标签"}]}}]}

    class FakeSession:
        def __init__(self, credentials: object):
            captured["credentials"] = credentials

        def post(self, url: str, *, headers: dict[str, str], json: dict[str, object], timeout: float) -> FakeResponse:
            captured.update({"url": url, "headers": headers, "json": json, "timeout": timeout})
            return FakeResponse()

        def close(self) -> None:
            captured["closed"] = True

    monkeypatch.setattr("google.auth.transport.requests.AuthorizedSession", FakeSession)
    provider = GoogleVisionWebDetectionProvider(project_id="test-project")
    monkeypatch.setattr(provider, "_credentials", lambda: (object(), "test-project"))

    result = provider.search(_request())
    body = captured["json"]
    assert captured["url"] == "https://vision.googleapis.com/v1/images:annotate"
    assert captured["headers"] == {"x-goog-user-project": "test-project"}
    assert body["requests"][0]["features"] == [{"type": "WEB_DETECTION", "maxResults": 20}]
    assert base64.b64decode(body["requests"][0]["image"]["content"]) == b"test-image"
    assert result["best_guess_labels"] == [{"label": "标签"}]
    assert captured["closed"] is True
    assert "credentials" not in repr(result)


def test_google_provider_http_failure_is_stable(monkeypatch: pytest.MonkeyPatch) -> None:
    """Google HTTP 失败只返回稳定 provider 错误，不泄露响应正文。"""

    class FakeResponse:
        ok = False

        def json(self) -> dict[str, object]:
            return {"error": {"message": "private response body"}}

    class FakeSession:
        def __init__(self, _credentials: object):
            pass

        def post(self, *_args: object, **_kwargs: object) -> FakeResponse:
            return FakeResponse()

        def close(self) -> None:
            pass

    monkeypatch.setattr("google.auth.transport.requests.AuthorizedSession", FakeSession)
    provider = GoogleVisionWebDetectionProvider(project_id="test-project")
    monkeypatch.setattr(provider, "_credentials", lambda: (object(), "test-project"))
    with pytest.raises(ReverseImageError) as error:
        provider.search(_request())
    assert error.value.code == "reverse_image_provider_unavailable"
    assert "private response body" not in str(error.value)
