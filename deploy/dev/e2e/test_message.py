from api_client import assert_error
from poll import eventually
from support import unique_key, unique_marker


def _send(a, b, content, **extra):
    payload = {"receiverId": b.id, "content": content, "msgType": 1,
               "idempotencyKey": unique_key("dm"), **extra}
    r = a.client.send_message(payload)
    assert r.status_code == 200, r.text[:200]
    return r.json()["messageId"]


def _conversation_with(client, other_id):
    resp = client.conversations(pageSize=50)
    assert resp.status_code == 200, resp.text[:200]
    for convo in resp.json()["conversations"]:
        if convo["targetUserId"] == other_id:
            return convo
    return None


def test_send_and_exact_replay_same_message_id(make_user):
    a, b = make_user(), make_user()
    key = unique_key("dm")
    payload = {"receiverId": b.id, "content": "hello", "msgType": 1,
               "idempotencyKey": key}
    first = a.client.send_message(payload)
    replay = a.client.send_message(payload)
    assert first.status_code == 200
    assert replay.status_code == 200, replay.text[:200]
    assert replay.json()["messageId"] == first.json()["messageId"]


def test_send_missing_idempotency_key_param_error(make_user):
    a, b = make_user(), make_user()
    r = a.client.send_message({"receiverId": b.id, "content": "x", "msgType": 1})
    assert_error(r, 400, 2)


def test_conversation_visible_both_sides_with_unread(make_user):
    a, b = make_user(), make_user()
    content = f"dm {unique_marker()}"
    message_id = _send(a, b, content)

    own = eventually(lambda: _conversation_with(a.client, b.id),
                     desc="sender sees conversation", timeout=30)
    assert own["lastMessage"] == content

    receiver_side = eventually(
        lambda: (c := _conversation_with(b.client, a.id)) and c["unreadCount"] >= 1,
        desc="receiver conversation shows unread", timeout=60)
    assert receiver_side


def test_receiver_marks_read_clears_unread(make_user):
    a, b = make_user(), make_user()
    _send(a, b, "read me")
    convo = eventually(lambda: _conversation_with(b.client, a.id),
                       desc="receiver has conversation", timeout=30)
    r = b.client.mark_conversation_read(convo["id"])
    assert r.status_code == 200, r.text[:200]

    def unread_cleared():
        current = _conversation_with(b.client, a.id)
        return current is not None and current["unreadCount"] == 0
    eventually(unread_cleared, desc="unread count cleared after mark read",
               timeout=60)

    summary = b.client.unread_summary().json()
    assert isinstance(summary["messageUnread"], int)
    assert summary["messageUnread"] == 0


def test_messages_cursor_paging_full_coverage_no_duplicates(make_user):
    a, b = make_user(), make_user()
    ids = [_send(a, b, f"msg {i} {unique_marker()}") for i in range(3)]

    collected = []
    last_id = None
    for _ in range(10):
        params = {"pageSize": 2}
        if last_id is not None:
            params["lastId"] = last_id
        resp = a.client.conversation_messages(
            _conversation_with(a.client, b.id)["id"], **params)
        assert resp.status_code == 200, resp.text[:200]
        body = resp.json()
        page_ids = [m["id"] for m in body["messages"]]
        assert len(set(page_ids) & set(collected)) == 0, "cursor paging duplicated messages"
        collected.extend(page_ids)
        if not body["hasMore"]:
            break
        last_id = min(body["messages"], key=lambda m: m["id"])["id"]
    assert set(ids) <= set(collected)


def test_third_user_denied_conversation_access(make_user):
    a, b, c = make_user(), make_user(), make_user()
    _send(a, b, "private")

    convo_a = eventually(lambda: _conversation_with(a.client, b.id),
                         desc="sender conversation exists", timeout=30)
    read = c.client.conversation_messages(convo_a["id"])
    assert_error(read, 403, 1007)
    mark = c.client.mark_conversation_read(convo_a["id"])
    assert_error(mark, 403, 1007)


def test_unread_summary_shape(user):
    summary = user.client.unread_summary().json()
    assert set(summary.keys()) >= {"messageUnread", "notificationUnread"}
    assert all(isinstance(v, int) and v >= 0 for v in summary.values())


def test_send_requires_auth(anon):
    r = anon.send_message({"receiverId": 1, "content": "x", "msgType": 1,
                           "idempotencyKey": unique_key()})
    assert_error(r, 401, 1006)
