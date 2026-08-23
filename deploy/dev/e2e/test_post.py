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


def test_update_title_only_keeps_content(user, published_post, anon):
    post = published_post(user.client)
    new_title = f"title-only {unique_marker()}"
    r = user.client.update_post(post["postId"],
                                {"title": new_title,
                                 "expectedRevision": post["revision"]})
    assert r.status_code == 200, r.text[:200]
    detail = anon.post_detail(post["postId"]).json()
    assert detail["title"] == new_title
    assert detail["content"] == f"content of {post['title']}"


def test_update_empty_payload_rejected(user, published_post):
    post = published_post(user.client)
    r = user.client.update_post(post["postId"], {"expectedRevision": post["revision"]})
    assert_error(r, 400, 2)


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


def test_post_list_sort_modes_cursor_shape(anon):
    for sort_by in (1, 2, 3):
        r = anon.post_list(pageSize=2, sortBy=sort_by)
        assert r.status_code == 200, r.text[:200]
        body = r.json()
        assert isinstance(body["list"], list)
        # 游标契约：不再有 total/page/pageSize 回显
        assert "total" not in body
        assert "page" not in body
        assert "nextCursor" in body


def test_post_list_cursor_walk_no_duplicates(anon):
    """语料充足时按游标走多页，断言 keyset 翻页无重复且游标推进。"""
    for sort_by in (1, 2):
        r = anon.post_list(pageSize=2, sortBy=sort_by)
        body = r.json()
        assert body["nextCursor"], f"sortBy={sort_by} 首页应返回下一页游标"
        seen, cursor = {i["id"] for i in body["list"]}, body["nextCursor"]
        for _ in range(5):
            r = anon.post_list(pageSize=2, sortBy=sort_by, cursor=cursor)
            assert r.status_code == 200, r.text[:200]
            body = r.json()
            ids = {item["id"] for item in body["list"]}
            assert not (ids & seen), f"sortBy={sort_by} 游标翻页出现重复帖子"
            seen |= ids
            cursor = body["nextCursor"]
            if not cursor:
                break


def test_post_list_full_walk_terminates(anon):
    """小页全量走链必须以空 nextCursor 终止（无 count(*) 的收敛语义）。"""
    seen, cursor, rounds = set(), "", 0
    while True:
        r = anon.post_list(pageSize=50, sortBy=1, cursor=cursor)
        assert r.status_code == 200, r.text[:200]
        body = r.json()
        ids = {item["id"] for item in body["list"]}
        assert not (ids & seen), "全量走链出现重复帖子"
        seen |= ids
        cursor = body["nextCursor"]
        rounds += 1
        if not cursor:
            break
        assert rounds < 100, "游标走链未收敛"
    assert rounds >= 2, "语料应跨多页"


def test_post_list_bad_cursor_param_error(anon):
    r = anon.post_list(pageSize=5, cursor="%%%not-base64%%%")
    assert_error(r, 400, 2)
