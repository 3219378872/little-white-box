import base64

import pytest

from api_client import ApiClient
from support import (ADMIN_PASSWORD, ADMIN_USERNAME, BASE_URL, DEFAULT_PASSWORD,
                     RUN_ID, User)


@pytest.fixture(scope="session")
def base_url():
    return BASE_URL


@pytest.fixture(scope="session")
def anon():
    return ApiClient(BASE_URL)


@pytest.fixture(scope="session")
def admin(anon):
    r = anon.login({"username": ADMIN_USERNAME, "password": ADMIN_PASSWORD,
                    "loginType": 1})
    assert r.status_code == 200, f"seeded admin login failed: {r.status_code} {r.text[:200]}"
    body = r.json()
    return User(anon.as_user(body["token"]), body["userId"], ADMIN_USERNAME,
                ADMIN_PASSWORD, body.get("refreshToken", ""))


@pytest.fixture(scope="session")
def make_user(anon):
    def _make(password=DEFAULT_PASSWORD):
        from support import unique_username
        username = unique_username()
        r = anon.register({"username": username, "password": password})
        assert r.status_code == 200, f"register failed: {r.status_code} {r.text[:200]}"
        body = r.json()
        client = anon.as_user(body["token"])
        return User(client, body["userId"], username, password,
                    body.get("refreshToken", ""))
    return _make


@pytest.fixture()
def user(make_user):
    return make_user()


@pytest.fixture()
def png_bytes():
    return base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJ"
        "AAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")


@pytest.fixture()
def published_post():
    from support import unique_marker

    def _make(author_client, title=None, tags=None):
        title = title or f"fixture post {unique_marker()}"
        payload = {"title": title, "content": f"content of {title}", "status": 1}
        if tags:
            payload["tags"] = tags
        r = author_client.create_post(payload)
        assert r.status_code == 200, f"create post failed: {r.status_code} {r.text[:200]}"
        body = r.json()
        body["title"] = title
        return body
    return _make
