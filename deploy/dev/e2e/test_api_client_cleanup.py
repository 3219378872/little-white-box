from unittest.mock import Mock

import pytest
import requests

import api_client


@pytest.fixture
def created_posts(monkeypatch):
    posts = {}
    monkeypatch.setattr(api_client, "_created_posts", posts)
    return posts


def _response(status=200, body=None):
    response = Mock(spec=requests.Response)
    response.status_code = status
    response.json.return_value = body
    return response


def _client(revision=7):
    client = Mock(spec=api_client.ApiClient)
    client.post_detail.return_value = _response(body={"revision": revision})
    client.delete_post.return_value = _response()
    return client


@pytest.mark.parametrize("phase,error", [
    ("detail", requests.Timeout("private body and Bearer sentinel-token")),
    ("detail_json", ValueError("private body and Bearer sentinel-token")),
    ("delete", requests.ConnectionError("private body and Bearer sentinel-token")),
    ("delete", ValueError("private body and Bearer sentinel-token")),
])
def test_cleanup_continues_after_request_or_json_exception(created_posts,
                                                          phase, error):
    good, broken = _client(), _client()
    created_posts.update({"1": (1, good), "2": (2, broken)})
    if phase == "detail":
        broken.post_detail.side_effect = error
    elif phase == "detail_json":
        broken.post_detail.return_value.json.side_effect = error
    else:
        broken.delete_post.side_effect = error

    failures = api_client.cleanup_created_posts()

    expected_phase = "detail" if phase == "detail_json" else phase
    assert failures == [f"{expected_phase} 2: {type(error).__name__}"]
    assert "sentinel-token" not in ";".join(failures)
    good.post_detail.assert_called_once_with(1)
    good.delete_post.assert_called_once_with(1, 7)
    if phase != "delete":
        broken.delete_post.assert_not_called()
    assert created_posts == {}


@pytest.mark.parametrize("body,reason", [
    (None, "invalid JSON object"),
    ([], "invalid JSON object"),
    ({}, "invalid revision"),
    ({"revision": 0}, "invalid revision"),
    ({"revision": True}, "invalid revision"),
    ({"revision": "private body and Bearer sentinel-token"}, "invalid revision"),
])
def test_cleanup_rejects_malformed_detail_without_deleting(created_posts,
                                                         body, reason):
    good, malformed = _client(), _client()
    malformed.post_detail.return_value = _response(body=body)
    created_posts.update({"1": (1, good), "2": (2, malformed)})

    assert api_client.cleanup_created_posts() == [f"detail 2: {reason}"]

    malformed.delete_post.assert_not_called()
    good.delete_post.assert_called_once_with(1, 7)
    assert created_posts == {}


def test_cleanup_collects_each_http_failure_and_continues(created_posts):
    good, delete_failure, detail_failure = _client(), _client(), _client()
    delete_failure.delete_post.return_value = _response(status=503)
    detail_failure.post_detail.return_value = _response(status=500)
    created_posts.update({
        "1": (1, good), "2": (2, delete_failure), "3": (3, detail_failure),
    })

    assert api_client.cleanup_created_posts() == [
        "detail 3: HTTP 500", "delete 2: HTTP 503",
    ]

    detail_failure.delete_post.assert_not_called()
    good.delete_post.assert_called_once_with(1, 7)
    assert created_posts == {}


@pytest.mark.parametrize("status", [200, 404, 410])
def test_cleanup_accepts_deleted_posts_without_parsing_delete_body(created_posts,
                                                                 status):
    gone, existing = _client(), _client()
    gone.post_detail.return_value = _response(status=404)
    existing.delete_post.return_value = _response(status=status)
    existing.delete_post.return_value.json.side_effect = ValueError("unused body")
    created_posts.update({"1": (1, gone), "2": (2, existing)})

    assert api_client.cleanup_created_posts() == []

    gone.delete_post.assert_not_called()
    existing.delete_post.assert_called_once_with(2, 7)
    existing.delete_post.return_value.json.assert_not_called()
    assert created_posts == {}
