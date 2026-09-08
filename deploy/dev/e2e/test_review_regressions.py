import pytest

from api_client import assert_error
from support import unique_key, unique_marker


def _uploaded_image(user, png_bytes):
    response = user.client.upload_image(("review.png", png_bytes, "image/png"))
    assert response.status_code == 200, response.text[:200]
    return response.json()


def test_post_images_preserve_omission_and_clear_explicit_empty(user, png_bytes):
    media = _uploaded_image(user, png_bytes)
    created = user.client.create_post({
        "title": f"media presence {unique_marker()}", "content": "review body",
        "status": 1, "images": [media["url"]], "mediaIds": [media["mediaId"]],
    })
    assert created.status_code == 200, created.text[:200]
    post = created.json()
    retained_title = f"retained {unique_marker()}"
    title_only = user.client.update_post(post["postId"], {
        "title": retained_title,
        "expectedRevision": post["revision"],
    })
    assert title_only.status_code == 200, title_only.text[:200]
    assert title_only.json()["revision"] == post["revision"] + 1
    detail = user.client.post_detail(post["postId"])
    assert detail.status_code == 200, detail.text[:200]
    assert detail.json()["title"] == retained_title
    assert detail.json()["revision"] == title_only.json()["revision"]
    assert detail.json()["images"] == [media["url"]]

    cleared = user.client.update_post(post["postId"], {
        "images": [], "mediaIds": [],
        "expectedRevision": title_only.json()["revision"],
    })
    assert cleared.status_code == 200, cleared.text[:200]
    detail = user.client.post_detail(post["postId"])
    assert detail.status_code == 200, detail.text[:200]
    assert detail.json()["images"] in (None, [])
    assert detail.json()["revision"] == cleared.json()["revision"]


@pytest.mark.parametrize("with_media_id", [False, True])
def test_post_cannot_reference_another_users_media(make_user, png_bytes,
                                                with_media_id):
    owner, other = make_user(), make_user()
    media = _uploaded_image(owner, png_bytes)
    payload = {
        "title": f"media ownership {unique_marker()}", "content": "review body",
        "status": 1, "images": [media["url"]],
    }
    if with_media_id:
        payload["mediaIds"] = [media["mediaId"]]
    assert_error(other.client.create_post(payload), 400, 2)


def test_post_update_rejects_unverified_url_without_mutation(user, published_post):
    post = published_post(user.client)
    response = user.client.update_post(post["postId"], {
        "images": ["https://unowned.invalid/review-image.png"],
        "expectedRevision": post["revision"],
    })
    assert_error(response, 400, 2)
    detail = user.client.post_detail(post["postId"])
    assert detail.status_code == 200, detail.text[:200]
    assert detail.json()["revision"] == post["revision"]
    assert detail.json()["images"] in (None, [])


def test_recommend_invalid_cursor_is_not_a_successful_fallback(user):
    response = user.client.recommend(
        request_id=unique_key("review"), pageSize=2, cursor="invalid-review-cursor",
    )
    assert_error(response, 400, 2)


def test_recommend_cursor_rejects_identity_and_context_changes(make_user):
    owner, other = make_user(), make_user()
    request_id = unique_key("review")
    params = {"pageSize": 2, "scene": "home", "sessionId": unique_key("session"),
              "experimentId": "review-control"}
    first = owner.client.recommend(request_id=request_id, **params)
    assert first.status_code == 200, first.text[:200]
    body = first.json()
    assert body["requestId"] == request_id
    assert body["hasMore"] and body["nextCursor"], "seeded corpus must span pages"
    params["cursor"] = body["nextCursor"]

    for field, value in (("pageSize", 3), ("scene", "profile"),
                         ("sessionId", unique_key("other-session")),
                         ("experimentId", "review-other")):
        changed = {**params, field: value}
        response = owner.client.recommend(request_id=request_id, **changed)
        assert_error(response, 400, 2)
    assert_error(owner.client.recommend(request_id=unique_key("other"), **params),
                 400, 2)
    assert_error(other.client.recommend(request_id=request_id, **params), 400, 2)

    second = owner.client.recommend(request_id=request_id, **params)
    assert second.status_code == 200, second.text[:200]
    second_body = second.json()
    assert second_body["items"], "valid continuation must return seeded items"
    assert second_body["requestId"] == request_id
    first_ids = {item["postId"] for item in body["items"]}
    second_ids = {item["postId"] for item in second_body["items"]}
    assert not first_ids & second_ids
