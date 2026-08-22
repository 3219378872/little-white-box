from api_client import assert_error
from poll import eventually
from support import unique_marker


def test_public_profile_shape(admin, anon):
    r = anon.get_user(admin.id)
    assert r.status_code == 200, r.text[:200]
    body = r.json()
    assert body["id"] == admin.id
    assert body["username"] == admin.username
    assert isinstance(body["nickname"], str)
    for field in ("followerCount", "followingCount", "postCount"):
        assert isinstance(body[field], int) and body[field] >= 0
    assert isinstance(body["favoritesVisible"], bool)


def test_profile_missing_user_404(anon):
    r = anon.get_user(10 ** 15)
    assert_error(r, 404, 1001)


def test_update_profile_roundtrip(user):
    nickname = f"nick{unique_marker()}"
    bio = f"bio {unique_marker()}"
    avatar = f"http://127.0.0.1:8333/xbh-media/original/{unique_marker()}.jpg"
    r = user.client.update_profile({"nickname": nickname, "bio": bio,
                                    "avatarUrl": avatar})
    assert r.status_code == 200, r.text[:200]
    body = user.client.get_user(user.id).json()
    assert body["nickname"] == nickname
    assert body["bio"] == bio
    assert body["avatarUrl"] == avatar


def test_user_posts_paging(user, anon):
    created = [user.client.create_post({"title": f"page {unique_marker()} {i}",
                                        "content": "body", "status": 1}).json()["postId"]
               for i in range(3)]
    p1 = anon.user_posts(user.id, page=1, pageSize=2).json()
    p2 = anon.user_posts(user.id, page=2, pageSize=2).json()
    assert p1["total"] >= 3
    assert len(p1["list"]) <= 2
    ids1 = {item["id"] for item in p1["list"]}
    ids2 = {item["id"] for item in p2["list"]}
    assert not ids1 & ids2
    assert set(created) & (ids1 | ids2)


def test_favorites_visibility_contract(make_user, published_post, admin, anon):
    owner = make_user()
    viewer = make_user()
    pid = published_post(owner.client)["postId"]
    fav = owner.client.favorite(pid)
    assert fav.status_code == 200

    own = owner.client.user_favorites(owner.id)
    assert own.status_code == 200
    assert any(item["id"] == pid for item in own.json()["list"])

    profile = anon.get_user(owner.id).json()
    other = viewer.client.user_favorites(owner.id)
    if profile["favoritesVisible"]:
        assert other.status_code == 200
    else:
        assert_error(other, 403, 3007)

    public = anon.user_favorites(admin.id)
    admin_profile = anon.get_user(admin.id).json()
    assert admin_profile["favoritesVisible"] is True
    assert public.status_code == 200


def test_follow_unfollow_roundtrip(make_user):
    a, b = make_user(), make_user()
    r = a.client.follow(b.id)
    assert r.status_code == 200, r.text[:200]
    r = a.client.unfollow(b.id)
    assert r.status_code == 200, r.text[:200]


def test_follow_self_rejected(admin):
    r = admin.client.follow(admin.id)
    assert_error(r, 400, 3006)


def test_personalization_toggle(user):
    initial = user.client.get_personalization().json()
    assert isinstance(initial["enabled"], bool)
    flipped = not initial["enabled"]
    r = user.client.set_personalization(flipped)
    assert r.status_code == 200, r.text[:200]
    after = user.client.get_personalization().json()
    assert after["enabled"] is flipped
    user.client.set_personalization(initial["enabled"])


def test_write_endpoints_require_auth(anon):
    targets = [
        ("PUT", "/api/v1/user/profile", {"nickname": "x"}),
        ("POST", "/api/v1/user/follow", {"targetUserId": 1}),
        ("DELETE", "/api/v1/user/follow", {"targetUserId": 1}),
        ("PUT", "/api/v2/me/personalization", {"enabled": True}),
    ]
    for method, path, payload in targets:
        r = anon.request(method, path, json=payload)
        assert_error(r, 401, 1006)


def test_follow_count_eventually_visible(make_user):
    a, b = make_user(), make_user()
    r = a.client.follow(b.id)
    assert r.status_code == 200

    def follower_grew():
        return a.client.get_user(b.id).json()["followerCount"] >= 1
    eventually(follower_grew, desc="follower count reflects follow", timeout=30)

    def following_grew():
        return a.client.get_user(a.id).json()["followingCount"] >= 1
    eventually(following_grew, desc="following count reflects follow", timeout=30)
