from api_client import assert_error
from support import DEFAULT_PASSWORD, unique_username


def test_register_returns_token_pair(anon):
    username = unique_username()
    r = anon.register({"username": username, "password": DEFAULT_PASSWORD})
    assert r.status_code == 200, r.text[:200]
    body = r.json()
    assert isinstance(body["userId"], int) and body["userId"] > 0
    assert body["token"].count(".") == 2
    assert body.get("refreshToken", "").count(".") == 2


def test_register_duplicate_username_conflict(anon):
    username = unique_username()
    payload = {"username": username, "password": DEFAULT_PASSWORD}
    first = anon.register(payload)
    assert first.status_code == 200
    dup = anon.register(payload)
    assert_error(dup, 409, 1002)


def test_login_password_ok(admin, anon):
    from support import ADMIN_PASSWORD, ADMIN_USERNAME
    r = anon.login({"username": ADMIN_USERNAME, "password": ADMIN_PASSWORD,
                    "loginType": 1})
    assert r.status_code == 200, r.text[:200]
    body = r.json()
    assert body["userId"] == admin.id
    assert body["token"].count(".") == 2


def test_login_wrong_password(anon):
    r = anon.login({"username": "admin", "password": "definitely-wrong",
                    "loginType": 1})
    assert_error(r, 401, 1003)


def test_login_missing_body_param_error(anon):
    r = anon.login({})
    assert_error(r, 400, 2)


def test_refresh_rotates_token_pair(anon, make_user):
    u = make_user()
    assert u.refresh_token
    r = anon.refresh(u.refresh_token)
    assert r.status_code == 200, r.text[:200]
    body = r.json()
    assert body["token"].count(".") == 2
    assert body["refreshToken"] != u.refresh_token
    me = anon.as_user(body["token"]).get_personalization()
    assert me.status_code == 200, f"refreshed token unusable: {me.text[:200]}"


def test_refresh_rejects_access_token(make_user, anon):
    u = make_user()
    r = anon.refresh(u.client.token)
    assert r.status_code >= 400, f"access token accepted as refresh: {r.text[:200]}"
    err = r.json()
    assert "code" in err


def test_verify_code_accepted(anon):
    r = anon.send_verify_code("13800138000", 2)
    assert r.status_code == 200, r.text[:200]


def test_jwt_routes_reject_anonymous(anon):
    jwt_targets = [
        ("POST", "/api/v1/user/follow", {"targetUserId": 1}),
        ("PUT", "/api/v1/user/profile", {}),
        ("POST", "/api/v2/post", {"title": "x", "content": "y"}),
        ("POST", "/api/v1/comment", {"postId": 1, "content": "x"}),
        ("POST", "/api/v1/like", {"targetId": 1, "targetType": 1}),
        ("DELETE", "/api/v1/like", {"targetId": 1, "targetType": 1}),
        ("POST", "/api/v1/favorite", {"postId": 1}),
        ("DELETE", "/api/v1/favorite", {"postId": 1}),
        ("GET", "/api/v2/feed/follow", None),
        ("GET", "/api/v2/me/personalization", None),
        ("GET", "/api/v2/messages/unread", None),
        ("POST", "/api/v2/messages", {"receiverId": 1, "content": "x",
                                      "msgType": 1, "idempotencyKey": "k"}),
    ]
    for method, path, payload in jwt_targets:
        kw = {"json": payload} if payload is not None else {}
        r = anon.request(method, path, **kw)
        assert_error(r, 401, 1006)


def test_jwt_route_garbage_token_401(anon):
    r = anon.post("/api/v1/user/follow", json={"targetUserId": 1},
                  headers={"Authorization": "Bearer junk.junk.junk"})
    assert_error(r, 401, 1006)


def test_optional_auth_state_header(admin, anon):
    states = {
        "anonymous": anon.get("/api/v1/posts"),
        "authenticated": admin.client.get("/api/v1/posts"),
        "invalid": anon.get("/api/v1/posts",
                            headers={"Authorization": "Bearer junk.junk.junk"}),
    }
    for expected, resp in states.items():
        assert resp.headers.get("X-Auth-State") == expected, \
            f"expected X-Auth-State={expected}, got {resp.headers.get('X-Auth-State')}"
