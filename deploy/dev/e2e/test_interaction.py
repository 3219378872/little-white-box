from api_client import assert_error
from poll import eventually
from support import unique_marker


def test_like_unlike_toggles_viewer_flag(make_user, published_post):
    viewer, owner = make_user(), make_user()
    post = published_post(owner.client)

    like = viewer.client.like(post["postId"], 1)
    assert like.status_code == 200, like.text[:200]

    def liked():
        detail = viewer.client.post_detail(post["postId"]).json()
        return detail["isLiked"] is True
    eventually(liked, desc="isLiked true after like", timeout=30)

    unlike = viewer.client.unlike(post["postId"], 1)
    assert unlike.status_code == 200

    def unliked():
        detail = viewer.client.post_detail(post["postId"]).json()
        return detail["isLiked"] is False
    eventually(unliked, desc="isLiked false after unlike", timeout=30)


def test_like_comment_target_type(make_user, published_post):
    from test_comment import _create_comment
    user = make_user()
    post = published_post(user.client)
    comment_id = _create_comment(user.client, post["postId"], "like me")
    r = user.client.like(comment_id, 2)
    assert r.status_code == 200, r.text[:200]


def test_repeat_like_is_silent_success(make_user, published_post):
    viewer, owner = make_user(), make_user()
    post = published_post(owner.client)
    first = viewer.client.like(post["postId"], 1)
    second = viewer.client.like(post["postId"], 1)
    assert first.status_code == 200
    assert second.status_code == 200, f"repeat like rejected: {second.text[:200]}"


def test_unlike_without_prior_like_silent_success(make_user, published_post):
    viewer, owner = make_user(), make_user()
    post = published_post(owner.client)
    r = viewer.client.unlike(post["postId"], 1)
    assert r.status_code == 200, f"unlike without like rejected: {r.text[:200]}"


def test_favorite_unfavorite_toggle_and_owner_list(make_user, published_post):
    owner, viewer = make_user(), make_user()
    post = published_post(owner.client)

    fav = viewer.client.favorite(post["postId"])
    assert fav.status_code == 200, fav.text[:200]

    def favorited():
        detail = viewer.client.post_detail(post["postId"]).json()
        return detail["isFavorited"] is True and detail["favoriteCount"] >= 1
    eventually(favorited, desc="isFavorited/favoriteCount reflect favorite",
               timeout=60)

    listing = viewer.client.user_favorites(viewer.id).json()["list"]
    assert any(item["id"] == post["postId"] for item in listing)

    unfav = viewer.client.unfavorite(post["postId"])
    assert unfav.status_code == 200

    def unfavorited():
        detail = viewer.client.post_detail(post["postId"]).json()
        return detail["isFavorited"] is False
    eventually(unfavorited, desc="isFavorited false after unfavorite",
               timeout=60)


def test_favorite_repeat_silent_success(make_user, published_post):
    viewer, owner = make_user(), make_user()
    post = published_post(owner.client)
    assert viewer.client.favorite(post["postId"]).status_code == 200
    again = viewer.client.favorite(post["postId"])
    assert again.status_code == 200, again.text[:200]


def test_unfavorite_without_prior_favorite_silent_success(make_user,
                                                          published_post):
    viewer, owner = make_user(), make_user()
    post = published_post(owner.client)
    r = viewer.client.unfavorite(post["postId"])
    assert r.status_code == 200, r.text[:200]


def test_self_like_allowed_by_current_behavior(user, published_post):
    marker = unique_marker()
    post = published_post(user.client, title=f"self-like {marker}")
    r = user.client.like(post["postId"], 1)
    assert r.status_code == 200, r.text[:200]


def test_like_count_syncs_to_detail_and_mysql_row(make_user, published_post):
    import dbprobe
    viewer, owner = make_user(), make_user()
    post = published_post(owner.client)

    like = viewer.client.like(post["postId"], 1)
    assert like.status_code == 200

    def count_grew():
        detail = owner.client.post_detail(post["postId"]).json()
        return detail["likeCount"] >= 1
    eventually(count_grew, desc="likeCount visible via count-sync pipeline",
               timeout=150)

    try:
        row = dbprobe.mysql(
            "xbh_content",
            f"SELECT like_count FROM post WHERE id = {post['postId']}")
    except dbprobe.DbUnavailable as exc:
        import pytest
        pytest.skip(f"MySQL probe unavailable: {exc}")
    assert int(row.split("\t")[0]) >= 1


def test_interaction_requires_auth(anon):
    assert_error(anon.like(123, 1), 401, 1006)
    assert_error(anon.unlike(123, 1), 401, 1006)
    assert_error(anon.favorite(123), 401, 1006)
    assert_error(anon.unfavorite(123), 401, 1006)


def test_cannot_like_missing_target(user):
    fake_id = 10 ** 14
    r = user.client.like(fake_id, 1)
    assert r.status_code >= 400, f"liking nonexistent target succeeded: {r.text[:200]}"
