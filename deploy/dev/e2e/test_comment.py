from api_client import assert_error
from poll import eventually
from support import unique_key, unique_marker


def _create_comment(client, post_id, content, **extra):
    payload = {"postId": post_id, "content": content,
               "idempotencyKey": unique_key(), **extra}
    r = client.comment_create(payload)
    assert r.status_code == 200, r.text[:200]
    return r.json()["commentId"]


def test_comment_create_and_listed(user, anon, published_post):
    post = published_post(user.client)
    content = f"root comment {unique_marker()}"
    comment_id = _create_comment(user.client, post["postId"], content)

    listing = anon.comment_list(post["postId"]).json()
    match = [c for c in listing["list"] if c["id"] == comment_id]
    assert len(match) == 1
    assert match[0]["content"] == content
    assert match[0]["parentId"] == 0
    assert match[0]["userId"] == user.id
    assert listing["total"] >= 1


def test_comment_reply_links_parent_and_reply_user(user, published_post):
    post = published_post(user.client)
    root_id = _create_comment(user.client, post["postId"], "root")
    payload = {"postId": post["postId"], "content": "reply",
               "parentId": root_id, "replyUserId": user.id,
               "idempotencyKey": unique_key()}
    r = user.client.comment_create(payload)
    assert r.status_code == 200, r.text[:200]
    assert r.json()["commentId"] > 0

    listing = user.client.comment_list(post["postId"]).json()
    assert [c["id"] for c in listing["list"]] == [root_id]


def test_comment_newest_sort_descending(user, published_post):
    post = published_post(user.client)
    for i in range(3):
        _create_comment(user.client, post["postId"], f"c{i} {unique_marker()}")
    listing = user.client.comment_list(post["postId"], page=1, pageSize=20,
                                       sortBy=1).json()
    times = [c["createdAt"] for c in listing["list"]]
    assert times == sorted(times, reverse=True)


def test_comment_hottest_mode_ok(user, published_post):
    post = published_post(user.client)
    _create_comment(user.client, post["postId"], "hot sort target")
    r = user.client.comment_list(post["postId"], sortBy=2)
    assert r.status_code == 200
    assert "list" in r.json()


def test_comment_empty_content_rejected(user, published_post):
    post = published_post(user.client)
    r = user.client.comment_create({"postId": post["postId"], "content": ""})
    assert_error(r, 400, 2004)


def test_delete_own_comment(user, anon, published_post):
    post = published_post(user.client)
    comment_id = _create_comment(user.client, post["postId"], "to be deleted")
    r = user.client.comment_delete(comment_id)
    assert r.status_code == 200, r.text[:200]

    def removed():
        listing = anon.comment_list(post["postId"]).json()
        return all(c["id"] != comment_id for c in listing["list"])
    eventually(removed, desc="deleted comment disappears from list", timeout=60)


def test_delete_other_users_comment_forbidden(make_user, published_post):
    owner, other = make_user(), make_user()
    post = published_post(owner.client)
    comment_id = _create_comment(owner.client, post["postId"], "not yours")
    r = other.client.comment_delete(comment_id)
    assert_error(r, 403, 2002)


def test_comment_count_syncs_to_post_detail(user, published_post):
    post = published_post(user.client)
    c1 = _create_comment(user.client, post["postId"], "count me 1")
    _create_comment(user.client, post["postId"], "count me 2")

    def count_is_two():
        return user.client.post_detail(post["postId"]).json()["commentCount"] >= 2
    eventually(count_is_two, desc="commentCount reaches 2 via count sync",
               timeout=150)

    user.client.comment_delete(c1)
    eventually(lambda: user.client.post_detail(post["postId"])
               .json()["commentCount"] <= 1,
               desc="commentCount drops after deletion", timeout=150)
