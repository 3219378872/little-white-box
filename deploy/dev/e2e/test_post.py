from api_client import assert_error
from support import unique_key, unique_marker


def test_create_publish_and_detail(user, anon):
    title = f"post {unique_marker()}"
    tags = [f"t{unique_marker()}"]
    r = user.client.create_post({"title": title, "content": "body text",
                                 "tags": tags, "status": 1})
    assert r.status_code == 200, r.text[:200]
    created = r.json()
    assert created["postId"] > 0
    assert created["status"] == 1
    assert created["revision"] >= 1

    detail = anon.post_detail(created["postId"]).json()
    assert detail["title"] == title
    assert detail["content"] == "body text"
    assert detail["authorId"] == user.id
    assert detail["revision"] == created["revision"]
    assert detail["status"] == 1
    assert isinstance(detail["isLiked"], bool)
    assert isinstance(detail["isFavorited"], bool)
    assert isinstance(detail["createdAt"], int)


def test_create_defaults_to_draft(user):
    marker = unique_marker()
    r = user.client.create_post({"title": f"draft {marker}",
                                 "content": "draft body"})
    assert r.status_code == 200
    assert r.json()["status"] == 0


def test_create_idempotent_exact_replay_same_post_id(user):
    key = unique_key()
    payload = {"title": f"idem {unique_marker()}", "content": "same",
               "status": 1, "idempotencyKey": key}
    first = user.client.create_post(payload)
    replay = user.client.create_post(payload)
    assert first.status_code == 200
    assert replay.status_code == 200, replay.text[:200]
    assert replay.json()["postId"] == first.json()["postId"]
    assert replay.json()["revision"] == first.json()["revision"]


def test_create_idempotency_key_conflict_on_different_payload(user):
    key = unique_key()
    first = user.client.create_post({"title": f"a {unique_marker()}",
                                     "content": "one", "status": 1,
                                     "idempotencyKey": key})
    assert first.status_code == 200
    conflict = user.client.create_post({"title": f"b {unique_marker()}",
                                        "content": "two", "status": 1,
                                        "idempotencyKey": key})
    assert_error(conflict, 409, 2008)


def test_update_requires_non_empty_content(user, published_post):
    post = published_post(user.client)
    r = user.client.update_post(post["postId"],
                                {"title": "new", "expectedRevision": post["revision"]})
    assert_error(r, 400, 2004)


def test_update_revision_increments(user, published_post, anon):
    post = published_post(user.client)
    new_title = f"updated {unique_marker()}"
    r = user.client.update_post(post["postId"],
                                {"title": new_title, "content": "updated body",
                                 "expectedRevision": post["revision"]})
    assert r.status_code == 200, r.text[:200]
    body = r.json()
    assert body["revision"] == post["revision"] + 1
    assert anon.post_detail(post["postId"]).json()["title"] == new_title


def test_update_stale_revision_conflict(user, published_post):
    post = published_post(user.client)
    r = user.client.update_post(post["postId"],
                                {"title": "x", "content": "y",
                                 "expectedRevision": post["revision"]})
    assert r.status_code == 200
    stale = user.client.update_post(post["postId"],
                                    {"title": "z", "content": "w",
                                     "expectedRevision": post["revision"]})
    assert_error(stale, 409, 2007)


def test_update_missing_expected_revision_param_error(user, published_post):
    post = published_post(user.client)
    r = user.client.update_post(post["postId"], {"title": "x", "content": "y"})
    assert_error(r, 400, 2)


def test_update_by_non_owner_forbidden(make_user, published_post):
    owner, other = make_user(), make_user()
    post = published_post(owner.client)
    r = other.client.update_post(post["postId"],
                                 {"title": "hijack", "content": "no",
                                  "expectedRevision": post["revision"]})
    assert_error(r, 403, 2002)


def test_delete_missing_revision_param_error(user, published_post):
    post = published_post(user.client)
    r = user.client.delete(f"/api/v2/post/{post['postId']}", json={})
    assert_error(r, 400, 2)


def test_delete_own_post_then_detail_gone(user, published_post, anon):
    post = published_post(user.client)
    r = user.client.delete_post(post["postId"], post["revision"])
    assert r.status_code == 200, r.text[:200]
    gone = anon.post_detail(post["postId"])
    assert gone.status_code == 404, gone.text[:200]
    assert gone.json()["code"] in {2001, 2006}


def test_delete_by_non_owner_forbidden(make_user, published_post):
    owner, other = make_user(), make_user()
    post = published_post(owner.client)
    r = other.client.delete_post(post["postId"], post["revision"])
    assert_error(r, 403, 2002)


def test_post_list_sort_modes_and_paging_echo(anon):
    for sort_by in (1, 2, 3):
        r = anon.post_list(page=1, pageSize=5, sortBy=sort_by)
        assert r.status_code == 200, r.text[:200]
        body = r.json()
        assert isinstance(body["list"], list)
        assert body["page"] == 1
        assert body["pageSize"] == 5


def test_post_list_far_page_empty(anon):
    r = anon.post_list(page=999999, pageSize=10)
    assert r.status_code == 200
    assert r.json()["list"] == []
