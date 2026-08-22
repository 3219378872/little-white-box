from poll import eventually
from support import unique_marker


def test_combined_search_shape(anon):
    r = anon.search("router")
    assert r.status_code == 200, r.text[:200]
    body = r.json()
    for field in ("posts", "users", "tags"):
        assert isinstance(body[field], list)
    assert isinstance(body["degraded"], bool)
    if body["degraded"]:
        assert body.get("unavailableTypes"), "degraded without unavailableTypes"


def test_new_post_eventually_searchable(anon, user, published_post):
    marker = unique_marker()
    post = published_post(user.client,
                          title=f"searchable {marker} black box",
                          tags=[marker])

    def found():
        resp = anon.search(marker, pageSize=20)
        if resp.status_code != 200:
            return False
        return any(p["id"] == post["postId"] for p in resp.json()["posts"])
    hit = eventually(found, desc=f"post {post['postId']} indexed and searchable",
                     timeout=180, interval=3)
    assert hit


def test_search_users_matches_existing_account(anon):
    from support import ADMIN_USERNAME
    r = anon.search_users(ADMIN_USERNAME)
    assert r.status_code == 200
    assert any(item["username"] == ADMIN_USERNAME for item in r.json()["users"])


def test_newly_registered_user_excluded_from_public_search(make_user, anon):
    u = make_user()
    resp = anon.search_users(u.username)
    assert resp.status_code == 200
    assert all(item["id"] != u.id for item in resp.json()["users"])


def test_search_users_total_field(anon):
    r = anon.search_users("admin")
    assert r.status_code == 200
    body = r.json()
    assert isinstance(body["total"], int)
    assert isinstance(body["users"], list)


def test_post_tag_eventually_tag_searchable(anon, user, published_post):
    tag = unique_marker()
    published_post(user.client, title=f"tagged {tag}", tags=[tag])

    def tag_found():
        resp = anon.search_tags(tag, limit=20)
        if resp.status_code != 200:
            return False
        return any(t["name"] == tag for t in resp.json()["tags"])
    eventually(tag_found, desc=f"tag {tag} searchable", timeout=120)


def test_search_empty_keyword_param_error(anon):
    r = anon.search("")
    assert r.status_code == 400
    assert r.json()["code"] == 2
