
import pytest

from api_client import ApiClient, cleanup_created_posts
from support import (ADMIN_PASSWORD, ADMIN_USERNAME, BASE_URL, DEFAULT_PASSWORD,
                     User)


# Entry URL of the stack under test (E2E_BASE_URL).
@pytest.fixture(scope="session")
def base_url():
    return BASE_URL


# After the whole session, delete every post the run created; leftovers fail.
@pytest.fixture(scope="session", autouse=True)
def cleanup_run_posts():
    yield
    failures = cleanup_created_posts()
    assert not failures, "e2e post cleanup failed: " + "; ".join(failures)


# Unauthenticated client shared by the session.
@pytest.fixture(scope="session")
def anon():
    return ApiClient(BASE_URL)


# The seeded admin account (just seed-dev-user).
@pytest.fixture(scope="session")
def admin(anon):
    r = anon.login({"username": ADMIN_USERNAME, "password": ADMIN_PASSWORD,
                    "loginType": 1})
    assert r.status_code == 200, f"seeded admin login failed: {r.status_code} {r.text[:200]}"
    body = r.json()
    return User(anon.as_user(body["token"]), body["userId"], ADMIN_USERNAME,
                ADMIN_PASSWORD, body.get("refreshToken", ""))


# Factory for freshly registered users with unique names.
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


# One fresh user per test.
@pytest.fixture()
def user(make_user):
    return make_user()


# A tiny valid PNG for upload tests.
@pytest.fixture()
def png_bytes():
    from support import PNG_1X1
    return PNG_1X1


# Factory that publishes a post as AUTHOR_CLIENT; returns the create response
# body plus the title used.
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
