from api_client import assert_error
from poll import eventually
from support import unique_marker


def test_follow_feed_receives_new_posts_after_follow(make_user, published_post):
    a, b = make_user(), make_user()
    follow = a.client.follow(b.id)
    assert follow.status_code == 200

    title = f"feed {unique_marker()}"
    post = published_post(b.client, title=title)

    def feed_contains_post():
        resp = a.client.follow_feed(pageSize=50)
        if resp.status_code != 200:
            return False
        return any(item["postId"] == post["postId"] for item in resp.json()["items"])
    eventually(feed_contains_post, desc="follow feed fans out new post",
               timeout=90)


def test_follow_feed_cursor_pagination_no_duplicates(make_user):
    a = make_user()
    r = a.client.follow_feed(pageSize=2)
    assert r.status_code == 200
    body = r.json()
    seen = set()
    pages = 0
    while body["items"]:
        for item in body["items"]:
            assert item["postId"] not in seen, "cursor pagination produced duplicates"
            seen.add(item["postId"])
        times = [item["createdAt"] for item in body["items"]]
        assert times == sorted(times, reverse=True)
        pages += 1
        if not body["hasMore"] or pages > 25:
            break
        assert body["nextCursorCreatedAt"] > 0 or body["nextCursorPostId"] > 0
        nxt = a.client.follow_feed(pageSize=2,
                                   cursorCreatedAt=body["nextCursorCreatedAt"],
                                   cursorPostId=body["nextCursorPostId"])
        assert nxt.status_code == 200
        body = nxt.json()


def test_follow_feed_requires_auth(anon):
    r = anon.follow_feed()
    assert_error(r, 401, 1006)


def test_recommend_requires_request_id(anon):
    r = anon.get("/api/v2/feed/recommend")
    assert_error(r, 400, 2)


def test_recommend_anonymous_requires_anonymous_id(anon):
    from support import unique_key
    r = anon.recommend(request_id=unique_key("rq"))
    assert_error(r, 400, 2)


def test_recommend_anonymous_positions_from_1(anon):
    from support import unique_key
    request_id = unique_key("rq")
    r = anon.recommend(request_id=request_id,
                       anonymousId=f"anon{unique_marker()}", pageSize=5)
    assert r.status_code == 200, r.text[:200]
    body = r.json()
    assert body["requestId"] == request_id
    items = body["items"]
    assert isinstance(items, list)
    positions = [item["position"] for item in items]
    assert positions == list(range(1, len(items) + 1))
    for item in items:
        assert isinstance(item["recallSource"], str)
        assert isinstance(item["modelVersion"], str)
        assert isinstance(item["experimentId"], str)


def test_recommend_authenticated_ok(user):
    from support import unique_key
    r = user.client.recommend(request_id=unique_key("rq"), pageSize=3)
    assert r.status_code == 200, r.text[:200]
    body = r.json()
    assert isinstance(body["items"], list)
    assert isinstance(body["hasMore"], bool)
    for item in body["items"]:
        assert isinstance(item["score"], (int, float))


def test_recommend_cursor_second_page_when_more(user):
    from support import unique_key
    request_id = unique_key("rq")
    first = user.client.recommend(request_id=request_id, pageSize=2).json()
    if not first["hasMore"]:
        return
    second = user.client.recommend(request_id=request_id,
                                   pageSize=2, cursor=first["nextCursor"])
    assert second.status_code == 200, second.text[:200]
    first_ids = {item["postId"] for item in first["items"]}
    second_ids = {item["postId"] for item in second.json()["items"]}
    assert not first_ids & second_ids
