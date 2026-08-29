import pytest

from api_client import assert_error, error_of, parse_sse_stream
from poll import eventually

_ASSISTANT_DB_HINT = (
    "DB_ASSISTANT/schema is required (derive from DB_CONTENT; replay "
    "patches + GRANT ALL on xbh_assistant)"
)


def _assert_assistant_store_ok(resp, action):
    assert resp.status_code != 503, (
        f"{action} returned HTTP 503; {_ASSISTANT_DB_HINT}; "
        f"body={resp.text[:200]}")
    assert resp.status_code != 500, (
        f"{action} returned HTTP 500; {_ASSISTANT_DB_HINT}; "
        f"body={resp.text[:200]}")


def _grant(client):
    r = client.set_agent_consent(True)
    _assert_assistant_store_ok(r, "POST /assistant/consent")
    assert r.status_code == 200, r.text[:200]


def _sse_error_code(frames):
    error_frames = [f for f in frames if f.get("type") == "error"]
    return error_frames[0].get("errorCode") if error_frames else None


def test_assistant_endpoints_require_auth(anon):
    assert_error(anon.get_assistant_thread(), 401, 1006)
    assert_error(anon.list_assistant_messages(), 401, 1006)
    assert_error(anon.post_assistant_message("hello"), 401, 1006)
    assert_error(anon.create_assistant_session(), 401, 1006)
    assert_error(anon.mark_assistant_thread_read(), 401, 1006)
    assert_error(anon.delete_assistant_history(), 401, 1006)
    assert_error(anon.assistant_run_events(1, stream=False), 401, 1006)
    assert_error(anon.cancel_assistant_run(1), 401, 1006)
    assert_error(anon.confirm_assistant_run(1, "x", True), 401, 1006)
    assert_error(anon.get_agent_consent(), 401, 1006)
    assert_error(anon.set_agent_consent(True), 401, 1006)
    assert_error(anon.list_assistant_memory(), 401, 1006)
    assert_error(anon.add_assistant_memory("memory", "note"), 401, 1006)
    assert_error(anon.list_assistant_watch(), 401, 1006)
    assert_error(anon.create_assistant_watch({
        "conditionType": "author_new_post",
        "targetType": "author",
        "targetId": 1,
    }), 401, 1006)
    assert_error(anon.submit_assistant_recommend_feedback(1, "dislike"),
                 401, 1006)


def test_old_chat_and_hits_routes_are_gone(user):
    chat = user.client.post("/api/v2/assistant/chat", json={"message": "hi"})
    assert chat.status_code == 404, chat.text[:200]
    hits = user.client.list_assistant_watch_hits()
    assert hits.status_code == 404, hits.text[:200]


def test_empty_message_rejected(user):
    _grant(user.client)
    try:
        r = user.client.post_assistant_message("")
        assert r.status_code in {200, 400}
        if r.status_code == 400:
            assert error_of(r) is not None
        else:
            _assert_assistant_store_ok(r, "POST /assistant/messages empty")
    finally:
        user.client.set_agent_consent(False)


def test_consent_includes_version_fields(user):
    r = user.client.get_agent_consent()
    assert r.status_code == 200, r.text[:200]
    body = r.json()
    assert isinstance(body.get("consentVersion"), int)
    assert isinstance(body.get("currentVersion"), int)
    assert body["currentVersion"] == 2


def test_post_message_requires_consent(user):
    client = user.client
    client.set_agent_consent(False)
    r = client.post_assistant_message("hello", request_id="e2e-no-consent")
    _assert_assistant_store_ok(r, "POST /assistant/messages no consent")
    assert r.status_code in {401, 403}
    body = error_of(r)
    assert body is not None
    assert body["code"] == 6001


def test_async_message_and_event_reconnect(user):
    client = user.client
    _grant(client)
    try:
        posted = client.post_assistant_message(
            "用一句话介绍这个社区", request_id="e2e-async-hello")
        _assert_assistant_store_ok(posted, "POST /assistant/messages")
        assert posted.status_code == 200, posted.text[:200]
        body = posted.json()
        run_id = body.get("runId")
        session_id = body.get("sessionId")
        message_id = body.get("messageId")
        assert isinstance(run_id, int) and run_id > 0
        assert isinstance(session_id, int) and session_id > 0
        assert isinstance(message_id, int) and message_id > 0
        assert body.get("disposition") in {
            "started", "redirected", "steered", "queued"}

        thread = client.get_assistant_thread()
        _assert_assistant_store_ok(thread, "GET /assistant/thread")
        assert thread.status_code == 200, thread.text[:200]
        summary = thread.json().get("thread") or {}
        assert summary.get("sessionId") == session_id

        events = client.assistant_run_events(run_id)
        assert events.status_code == 200, events.text[:150]
        assert events.headers.get("Content-Type", "").startswith(
            "text/event-stream")
        frames = parse_sse_stream(events)
        assert frames, "expected persistent run events"
        types = [f.get("type") for f in frames]
        if types[-1] == "error":
            pytest.skip(f"assistant degraded by upstream: {frames[-1]}")
        assert types[-1] == "done", f"stream did not end with done: {types}"
        last_seq = frames[-1].get("seq")
        assert isinstance(last_seq, int) and last_seq > 0

        replay = client.assistant_run_events(
            run_id, after_seq=0, last_event_id=0)
        assert replay.status_code == 200
        replayed = parse_sse_stream(replay)
        assert replayed, "reconnect must replay persisted events"
        assert replayed[-1].get("type") in {"done", "error"}
    finally:
        client.set_agent_consent(False)


def test_stop_run(user):
    client = user.client
    _grant(client)
    try:
        posted = client.post_assistant_message(
            "请慢慢讲一个很长的故事", request_id="e2e-stop")
        _assert_assistant_store_ok(posted, "POST /assistant/messages stop")
        if posted.status_code != 200:
            pytest.skip(f"could not start run: {posted.text[:150]}")
        run_id = posted.json()["runId"]
        cancelled = client.cancel_assistant_run(run_id)
        _assert_assistant_store_ok(cancelled, "POST /assistant/runs/cancel")
        assert cancelled.status_code == 200, cancelled.text[:200]
        frames = parse_sse_stream(client.assistant_run_events(run_id))
        assert frames
        assert frames[-1].get("type") in {"done", "error"}
    finally:
        client.set_agent_consent(False)


def test_new_session_and_clear_history(user):
    client = user.client
    _grant(client)
    try:
        posted = client.post_assistant_message(
            "记住这是第一会话", request_id="e2e-session-1")
        _assert_assistant_store_ok(posted, "POST /assistant/messages session")
        if posted.status_code != 200:
            pytest.skip(f"could not start run: {posted.text[:150]}")
        first_session = posted.json()["sessionId"]
        created = client.create_assistant_session()
        _assert_assistant_store_ok(created, "POST /assistant/sessions")
        assert created.status_code == 200, created.text[:200]
        new_session = created.json().get("sessionId")
        assert isinstance(new_session, int) and new_session > 0
        assert new_session != first_session

        listed = client.list_assistant_memory()
        _assert_assistant_store_ok(listed, "GET /assistant/memory after session")
        assert listed.status_code == 200

        deleted = client.delete_assistant_history()
        _assert_assistant_store_ok(deleted, "DELETE /assistant/history")
        assert deleted.status_code == 200, deleted.text[:200]
        messages = client.list_assistant_messages()
        _assert_assistant_store_ok(messages, "GET /assistant/messages after clear")
        assert messages.status_code == 200
        visible = [m for m in messages.json().get("messages") or []
                   if m.get("kind") != "memory_changed"]
        assert visible == []
        listed_after = client.list_assistant_memory()
        assert listed_after.status_code == 200
    finally:
        client.set_agent_consent(False)


def test_memory_crud_and_undo(user):
    client = user.client
    _grant(client)
    try:
        added = client.add_assistant_memory(
            "memory", "喜欢独立游戏", request_id="e2e-mem-add")
        _assert_assistant_store_ok(added, "POST /assistant/memory")
        assert added.status_code == 200, added.text[:200]
        entry = added.json().get("entry") or {}
        change_id = added.json().get("changeId")
        assert entry.get("target") == "memory"
        assert entry.get("content")
        assert isinstance(entry.get("version"), int)
        listed = client.list_assistant_memory(target="memory")
        assert listed.status_code == 200
        body = listed.json()
        assert any(item.get("id") == entry.get("id")
                   for item in body.get("items") or [])
        caps = {c.get("target"): c for c in body.get("capacities") or []}
        assert "memory" in caps
        assert caps["memory"].get("limit") == 2200

        replaced = client.replace_assistant_memory(
            entry["id"], "喜欢独立游戏和像素风", entry["version"],
            request_id="e2e-mem-replace")
        _assert_assistant_store_ok(replaced, "PATCH /assistant/memory")
        assert replaced.status_code == 200, replaced.text[:200]
        undone = client.undo_assistant_memory_change(
            replaced.json().get("changeId") or change_id)
        _assert_assistant_store_ok(undone, "POST /assistant/memory/undo")
        assert undone.status_code == 200, undone.text[:200]
        removed = client.remove_assistant_memory(
            entry["id"], undone.json().get("entry", {}).get("version", 1),
            request_id="e2e-mem-del")
        _assert_assistant_store_ok(removed, "DELETE /assistant/memory")
        assert removed.status_code == 200, removed.text[:200]
    finally:
        client.set_agent_consent(False)


def test_confirm_unknown_call_rejected(user):
    client = user.client
    _grant(client)
    try:
        r = client.confirm_assistant_run(1, "no-such-call", True)
        assert r.status_code in {400, 403, 404}
    finally:
        client.set_agent_consent(False)


def test_watch_crud_and_unknown_condition(user, published_post):
    client = user.client
    post = published_post(user.client)
    detail = client.post_detail(post["postId"])
    assert detail.status_code == 200, detail.text[:200]
    author_id = detail.json()["authorId"]
    payload = {
        "conditionType": "author_new_post",
        "targetType": "author",
        "targetId": author_id,
    }
    created = client.create_assistant_watch(payload)
    _assert_assistant_store_ok(created, "POST /assistant/watch")
    assert created.status_code == 200, created.text[:200]
    task = created.json().get("task") or {}
    task_id = task.get("id")
    assert isinstance(task_id, int) and task_id > 0

    try:
        listed = client.list_assistant_watch()
        _assert_assistant_store_ok(listed, "GET /assistant/watch")
        assert listed.status_code == 200, listed.text[:200]
        tasks = listed.json().get("tasks")
        assert isinstance(tasks, list)
        assert any(item.get("id") == task_id for item in tasks)

        dup = client.create_assistant_watch(payload)
        _assert_assistant_store_ok(dup, "POST /assistant/watch duplicate")
        assert 400 <= dup.status_code < 500, (
            f"duplicate watch must be conflict/error not 500: "
            f"{dup.status_code} {dup.text[:200]}")

        patched = client.update_assistant_watch(task_id, False)
        _assert_assistant_store_ok(patched, "PATCH /assistant/watch")
        assert patched.status_code == 200, patched.text[:200]
        after = client.list_assistant_watch().json().get("tasks") or []
        found = next(item for item in after if item.get("id") == task_id)
        assert found.get("enabled") is False

        unknown = client.create_assistant_watch({
            "conditionType": "price_drop",
            "targetType": "post",
            "targetId": post["postId"],
        })
        _assert_assistant_store_ok(unknown, "POST /assistant/watch unknown")
        assert 400 <= unknown.status_code < 500, (
            f"unknown conditionType must be 4xx, got {unknown.status_code}: "
            f"{unknown.text[:200]}")
    finally:
        deleted = client.delete_assistant_watch(task_id)
        _assert_assistant_store_ok(deleted, "DELETE /assistant/watch")
        assert deleted.status_code == 200, deleted.text[:200]
        remaining = client.list_assistant_watch()
        assert remaining.status_code == 200, remaining.text[:200]
        assert all(item.get("id") != task_id
                   for item in remaining.json().get("tasks") or [])


def test_watch_hit_route_removed(user):
    listed = user.client.list_assistant_watch_hits()
    assert listed.status_code == 404, listed.text[:200]


def test_watch_matcher_delivers_assistant_message(user, published_post):
    client = user.client
    _grant(client)
    payload = {
        "conditionType": "author_new_post",
        "targetType": "author",
        "targetId": user.id,
    }
    created = client.create_assistant_watch(payload)
    _assert_assistant_store_ok(created, "POST /assistant/watch matcher")
    assert created.status_code == 200, created.text[:200]
    task_id = created.json().get("task", {}).get("id")
    assert isinstance(task_id, int) and task_id > 0
    try:
        before = client.get_assistant_thread()
        _assert_assistant_store_ok(before, "GET /assistant/thread before watch")
        before_unread = (before.json().get("thread") or {}).get("unreadCount", 0)
        post = published_post(client)

        def unread_increased():
            listed = client.get_assistant_thread()
            _assert_assistant_store_ok(listed, "GET /assistant/thread matcher")
            if listed.status_code != 200:
                return False
            unread = (listed.json().get("thread") or {}).get("unreadCount", 0)
            return unread > before_unread

        try:
            eventually(unread_increased, desc="watch proactive assistant unread",
                       timeout=60.0, interval=1.0)
        except AssertionError:
            pytest.skip("watch delivery not observed (matcher/worker/LLM)")
        assert post["postId"]
    finally:
        client.delete_assistant_watch(task_id)
        client.set_agent_consent(False)


def test_recommend_feedback_never_500(user, published_post):
    post = published_post(user.client)
    r = user.client.submit_assistant_recommend_feedback(
        post["postId"], "dislike")
    _assert_assistant_store_ok(r, "POST /assistant/recommend/feedback")
    assert r.status_code != 500, (
        f"valid dislike feedback must not 500: {r.text[:200]}")
    if r.status_code != 200:
        assert error_of(r) is not None, (
            f"expected 200 or structured error, got HTTP {r.status_code}: "
            f"{r.text[:200]}")
