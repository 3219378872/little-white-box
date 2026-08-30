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


def test_assistant_endpoints_require_auth(anon):
    assert_error(anon.get_assistant_thread(), 401, 1006)
    assert_error(anon.list_assistant_messages(), 401, 1006)
    assert_error(anon.post_assistant_message("hello"), 401, 1006)
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
    sessions = user.client.post("/api/v2/assistant/sessions")
    assert sessions.status_code == 404, sessions.text[:200]


def test_empty_message_rejected(user):
    _grant(user.client)
    try:
        r = user.client.post_assistant_message("")
        assert_error(r, 400, 2)
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
        assert types[-1] == "done", (
            f"assistant run must complete in the live gate: {frames[-1]}")
        last_seq = frames[-1].get("seq")
        assert isinstance(last_seq, int) and last_seq > 0

        seqs = [frame.get("seq") for frame in frames]
        assert len(seqs) >= 2 and all(isinstance(seq, int) for seq in seqs)
        cursor = seqs[-2]
        replay = client.assistant_run_events(
            run_id, after_seq=max(0, cursor - 1), last_event_id=cursor)
        assert replay.status_code == 200
        replayed = parse_sse_stream(replay)
        assert replayed, "reconnect must replay events after the cursor"
        assert all(frame.get("seq", 0) > cursor for frame in replayed)
        assert replayed[-1].get("type") == "done"
    finally:
        client.set_agent_consent(False)


def test_stop_run(user):
    client = user.client
    _grant(client)
    try:
        posted = client.post_assistant_message(
            "请慢慢讲一个很长的故事", request_id="e2e-stop")
        _assert_assistant_store_ok(posted, "POST /assistant/messages stop")
        assert posted.status_code == 200, posted.text[:200]
        run_id = posted.json()["runId"]
        cancelled = client.cancel_assistant_run(run_id)
        _assert_assistant_store_ok(cancelled, "POST /assistant/runs/cancel")
        assert cancelled.status_code == 200, cancelled.text[:200]
        frames = parse_sse_stream(client.assistant_run_events(run_id))
        assert frames
        assert frames[-1].get("type") in {"done", "error"}
    finally:
        client.set_agent_consent(False)


def test_clear_history_keeps_memory(user):
    client = user.client
    _grant(client)
    try:
        posted = client.post_assistant_message(
            "记住这是第一会话", request_id="e2e-session-1")
        _assert_assistant_store_ok(posted, "POST /assistant/messages session")
        assert posted.status_code == 200, posted.text[:200]
        session_id = posted.json()["sessionId"]
        assert isinstance(session_id, int) and session_id > 0

        listed = client.list_assistant_memory()
        _assert_assistant_store_ok(listed, "GET /assistant/memory before clear")
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
        thread = client.get_assistant_thread()
        _assert_assistant_store_ok(thread, "GET /assistant/thread after clear")
        assert thread.status_code == 200
        assert thread.json().get("thread", {}).get("sessionId") == session_id
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
        posted = client.post_assistant_message(
            "回复一个字即可", request_id="e2e-confirm-owner")
        _assert_assistant_store_ok(posted, "POST /assistant/messages confirm")
        assert posted.status_code == 200, posted.text[:200]
        run_id = posted.json().get("runId")
        assert isinstance(run_id, int) and run_id > 0
        r = client.confirm_assistant_run(run_id, "no-such-call", True)
        assert_error(r, 400, 2)
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

        eventually(unread_increased, desc="watch proactive assistant unread",
                   timeout=180.0, interval=1.0)
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
